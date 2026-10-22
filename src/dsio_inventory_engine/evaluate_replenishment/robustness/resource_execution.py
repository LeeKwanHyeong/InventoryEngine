"""Deadline-controlled Catalog calls. Large inputs never enter a startup pipe."""

from contextlib import contextmanager
import importlib
import os
import select
import signal
import subprocess
import sys
import time

from dsio_inventory_engine.infrastructure.robustness_resource_files import locked
from dsio_inventory_engine.infrastructure.robustness_resource_pool import (
    LocalRobustnessResourcePool,
)
from dsio_inventory_engine.infrastructure.robustness_resource_store import resource_store
from dsio_inventory_engine.inventory_contracts.robustness_resources import (
    ResourceControlError,
    ResourceLimits,
    invocation_seconds,
)
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    canonical_json,
    identifier,
    read_json,
    require,
)
from .source_binding import RealizedDemandPath

OPERATIONS = frozenset({"run", "recover", "inspect", "verify_recalculation"})
MAX_REPLY_BYTES = 8_000_000


def _check(deadline, cancellation):
    LocalRobustnessResourcePool._check_call(deadline, cancellation)


def _watchdog(owner_pid, deadline, reply_fd):
    os.close(reply_fd)
    while time.monotonic() < deadline:
        try:
            os.kill(owner_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(min(0.02, max(0, deadline - time.monotonic())))
    os.killpg(os.getpgrp(), signal.SIGKILL)
    os._exit(1)


@contextmanager
def _invocation_lease(resources, deadline):
    while True:
        _check(deadline, None)
        lease = locked(resources.root, resources.identity, "invocation.lock", nonblocking=True)
        try:
            lease.__enter__()
        except BlockingIOError:
            time.sleep(min(0.02, max(0, deadline - time.monotonic())))
            continue
        break
    try:
        yield
    finally:
        lease.__exit__(None, None, None)


def _factory_binding(factory):
    module = getattr(factory, "__module__", None)
    name = getattr(factory, "__qualname__", None)
    if module == "__main__":
        module = getattr(getattr(sys.modules[module], "__spec__", None), "name", None)
    require(
        type(module) is str
        and type(name) is str
        and "<" not in name
        and len(module) + len(name) <= 1024,
        "RESOURCE_FACTORY_NOT_IMPORTABLE",
    )
    return {"module": module, "name": name}


def _send(fd, value):
    payload = canonical_json(value).encode() + b"\n"
    require(len(payload) <= MAX_REPLY_BYTES, "RESOURCE_REPLY_TOO_LARGE")
    with os.fdopen(os.dup(fd), "wb") as stream:
        stream.write(payload)
        stream.flush()


def worker_main(root, raw_limit, reference, deadline, owner_pid):
    reply_fd = os.dup(1)
    os.dup2(2, 1)
    watchdog = None
    try:
        watchdog = os.fork()
        if watchdog == 0:
            _watchdog(owner_pid, deadline, reply_fd)
        resources = LocalRobustnessResourcePool(root, ResourceLimits(raw_limit))
        with _invocation_lease(resources, deadline):
            resources.inspect()
            job = resources.read_job(reference)
            require(job["operation"] in OPERATIONS, "RESOURCE_OPERATION_INVALID")
            factory = importlib.import_module(job["factory"]["module"])
            for name in job["factory"]["name"].split("."):
                factory = getattr(factory, name)
            repository = resource_store(resources, job["store_id"], job["storage_version"])
            runner = factory(repository)
            require(runner.repository is repository, "RESOURCE_REPOSITORY_NOT_BOUND")
            arguments = job["arguments"]
            if job["operation"] == "run":
                arguments["realization"] = RealizedDemandPath.from_dict(arguments["realization"])
            result = getattr(runner, job["operation"])(**arguments)
        _send(reply_fd, {"status": "SUCCEEDED", "result": result})
    except BaseException as error:
        code = str(error) if isinstance(error, InventoryInputError) else "RESOURCE_WORKER_FAILED"
        try:
            _send(reply_fd, {"status": "FAILED", "code": code})
        except (BrokenPipeError, OSError):
            pass
    finally:
        if watchdog:
            try:
                os.kill(watchdog, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(watchdog, 0)
        os.close(reply_fd)


def _stop(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        try:
            process.wait(timeout=0.1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
            raise ResourceControlError("RESOURCE_GROUP_STOP_DENIED") from None
    if process.poll() is None:
        process.kill()
    process.wait(timeout=2)


class ControlledCatalogExecutor:
    def __init__(self, resources, runner_factory, *, storage_version="3.0.0"):
        require(callable(runner_factory), "RESOURCE_FACTORY_INVALID")
        require(storage_version in ("1.0.0", "2.0.0", "3.0.0"), "RESOURCE_STORAGE_VERSION_INVALID")
        self.resources, self.factory = resources, _factory_binding(runner_factory)
        self.storage_version = storage_version

    def execute(
        self, store_id: str, operation: str, arguments: dict, *, seconds=3600, cancellation=None
    ) -> dict:
        deadline = time.monotonic() + invocation_seconds(seconds)
        identifier(store_id)
        require(type(operation) is str and operation in OPERATIONS, "RESOURCE_OPERATION_INVALID")
        require(type(arguments) is dict, "RESOURCE_ARGUMENTS_INVALID")
        require(
            cancellation is None or callable(getattr(cancellation, "is_set", None)),
            "RESOURCE_CANCELLATION_INVALID",
        )
        _check(deadline, cancellation)
        reference = self.resources.prepare_job(
            {
                "factory": self.factory,
                "store_id": store_id,
                "operation": operation,
                "storage_version": self.storage_version,
                "arguments": arguments,
            },
            deadline,
            cancellation,
        )
        try:
            return self._execute(reference, deadline, cancellation)
        finally:
            self.resources.retire_job(reference)

    def _execute(self, reference, deadline, cancellation):
        _check(deadline, cancellation)
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "dsio_inventory_engine.evaluate_replenishment.robustness.resource_worker",
                str(self.resources.root),
                str(self.resources.limits.raw_bytes),
                reference["name"],
                reference["sha256"],
                repr(deadline),
                str(os.getpid()),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        buffer = bytearray()
        try:
            os.set_blocking(process.stdout.fileno(), False)
            while True:
                _check(deadline, cancellation)
                ready, _, _ = select.select(
                    [process.stdout], [], [], min(0.02, max(0, deadline - time.monotonic()))
                )
                if not ready:
                    continue
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    _check(deadline, cancellation)
                    raise ResourceControlError("RESOURCE_WORKER_EXITED")
                buffer.extend(chunk)
                require(len(buffer) <= MAX_REPLY_BYTES, "RESOURCE_REPLY_TOO_LARGE")
                if b"\n" not in buffer:
                    continue
                require(
                    buffer.endswith(b"\n") and buffer.count(b"\n") == 1,
                    "RESOURCE_WORKER_PROTOCOL_INVALID",
                )
                reply = read_json(buffer.decode(), max_bytes=MAX_REPLY_BYTES)
                _check(deadline, cancellation)
                if reply["status"] != "SUCCEEDED":
                    raise ResourceControlError(reply["code"])
                return reply["result"]
        finally:
            _stop(process)
            process.stdout.close()

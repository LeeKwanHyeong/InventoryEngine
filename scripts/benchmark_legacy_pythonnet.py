"""Opt-in local experiment worker/preparation; no DB or operational activation."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import select
import shutil
import signal
import subprocess
import sys
from time import perf_counter

from scripts.benchmark_legacy_psi import (
    ROOT,
    requests_from_input,
    serialize_results,
    store_new,
)
from dsio_inventory_engine.infrastructure.legacy_pythonnet import LegacyPythonNetAdapter

DLL_NAME = "Inventory.Legacy.Kernel.dll"


def prepare(output: Path, source_root: Path) -> dict:
    from scripts.benchmark_legacy_psi import prepare as prepare_original

    preparation = prepare_original(source_root, output)
    staged = output / "stage"
    for name in (
        "PythonNet.Kernel.csproj",
        "PythonNetFacade.cs",
        "PythonNet.Direct.csproj",
        "PythonNetDirect.cs",
        "PythonNet.requirements.txt",
        "PythonNet.Dockerfile",
        "PythonNet.Directory.Build.props",
    ):
        shutil.copyfile(ROOT / "tests/reference/legacy_csharp" / name, staged / name)
    scripts = staged / "scripts"
    scripts.mkdir()
    shutil.copyfile(Path(__file__), scripts / Path(__file__).name)
    shutil.copyfile(ROOT / "scripts/benchmark_legacy_psi.py", scripts / "benchmark_legacy_psi.py")
    infrastructure = staged / "src/dsio_inventory_engine/infrastructure"
    infrastructure.mkdir()
    shutil.copyfile(
        ROOT / "src/dsio_inventory_engine/infrastructure/legacy_pythonnet.py",
        infrastructure / "legacy_pythonnet.py",
    )
    (infrastructure / "__init__.py").touch()
    compile_source = output / "build/source"
    shutil.copytree(staged, compile_source)
    shutil.copyfile(
        staged / "PythonNet.Directory.Build.props", compile_source / "Directory.Build.props"
    )
    existing = {}
    for directory in ("src", "schemas", "tests", "scripts", "docs"):
        for path in (ROOT / directory).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                existing[str(path.relative_to(ROOT))] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
    result = {
        "baseline": preparation,
        "preexisting_files": existing,
        "staged_files": {
            str(path.relative_to(staged)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(staged.rglob("*"))
            if path.is_file()
        },
    }
    with (output / "pythonnet-preparation.json").open("x") as target:
        json.dump(result, target, indent=2)
    return result


def get_adapter(build: Path) -> LegacyPythonNetAdapter:
    dll = build / DLL_NAME
    return LegacyPythonNetAdapter(
        dll, expected_dll_sha256=hashlib.sha256(dll.read_bytes()).hexdigest()
    )


def worker(source: Path, output: Path, build: Path, items: int, mode: str) -> dict:
    from dsio_inventory_engine.simulate_inventory.application.legacy_reference import (
        run_legacy_reference,
    )

    raw = source.read_bytes()
    source_hash = hashlib.sha256(raw).hexdigest()
    load_start = perf_counter()
    adapter = get_adapter(build) if mode == "pythonnet" else None
    load_seconds = perf_counter() - load_start
    output.mkdir(exist_ok=False)
    samples = []
    for index in range(11):
        phase = "cold" if index == 0 else "warmup" if index <= 3 else "measured"
        if adapter is not None:
            result = adapter.run(raw, items, expected_source_sha256=source_hash)
            payload = result.payload
            sample = asdict(result)
            sample.pop("payload")
        else:
            begin = perf_counter()
            data = json.loads(raw)
            requests = requests_from_input(data, items)
            prepared = perf_counter()
            results = [run_legacy_reference(request) for request in requests]
            computed = perf_counter()
            payload = serialize_results(results, requests, source_hash, items, 32, 52)
            serialized = perf_counter()
            digest = hashlib.sha256(payload).hexdigest()
            end = perf_counter()
            sample = dict(
                input_copy_seconds=0.0,
                prepare_seconds=prepared - begin,
                managed_call_seconds=serialized - prepared,
                compute_seconds=computed - prepared,
                serialize_seconds=serialized - computed,
                output_copy_seconds=0.0,
                verify_seconds=end - serialized,
                total_seconds=end - begin,
                sha256=digest,
            )
        stored = perf_counter()
        name = f"{index:02d}.bin"
        store_new(output / name, payload)
        elapsed = perf_counter() - stored
        sample.update(
            phase=phase,
            index=index,
            file=name,
            rows=items * 32 * 52,
            bytes=len(payload),
            store_seconds=elapsed,
            with_store_seconds=sample["total_seconds"] + elapsed,
        )
        samples.append(sample)
        # Do not accidentally keep all .NET object graphs or payloads alive between requests.
        if adapter is not None:
            del result
        else:
            del results, requests, data
        del payload
    return dict(
        mode=mode,
        items=items,
        input_sha256=source_hash,
        runtime=platform.python_version(),
        load_seconds=load_seconds,
        peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
        samples=samples,
    )


def probe(source: Path, build: Path, kind: str) -> dict:
    """Real CLR state isolation and cancellation, run only in disposable worker processes."""
    from System import OperationCanceledException
    from System.Threading import CancellationTokenSource

    adapter = get_adapter(build)
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    batch = adapter.prepare(raw, 16, expected_source_sha256=digest)
    if kind in ("state", "recover"):
        baseline = adapter._to_python(batch.Run(adapter.token).Payload)
        if kind == "recover":
            return dict(status="PASSED", sha256=hashlib.sha256(baseline).hexdigest())
        for _ in range(3):
            adapter.run(raw, 1, expected_source_sha256=digest)
            assert adapter._to_python(batch.Run(adapter.token).Payload) == baseline
        try:
            adapter.kernel.Prepare(adapter._to_managed(raw), 0)
        except Exception as error:
            assert "PYTHONNET_ITEM_COUNT" in str(error)
        else:
            raise AssertionError("INVALID_INPUT_ACCEPTED")
        assert adapter._to_python(batch.Run(adapter.token).Payload) == baseline
        return dict(
            status="PASSED", check="REUSED_PREPARED_INPUT_ISOLATED_AFTER_OTHER_RUN_AND_EXCEPTION"
        )
    if kind == "cooperative":
        cancel = CancellationTokenSource()
        cancel.Cancel()
        try:
            batch.Run(cancel.Token)
        except OperationCanceledException:
            pass
        else:
            raise AssertionError("PRE_CANCELLED_CALL_RETURNED_RESULT")
        cancel.Dispose()
        cancel = CancellationTokenSource()
        begin = perf_counter()
        cancel.CancelAfter(100)
        calls = 0
        try:
            while True:
                batch.Run(cancel.Token)
                calls += 1
        except OperationCanceledException:
            elapsed = perf_counter() - begin
        finally:
            cancel.Dispose()
        assert calls > 0 and elapsed < 5
        # A cancelled call did not mutate the next run's initial state.
        payload = adapter._to_python(batch.Run(adapter.token).Payload)
        return dict(
            status="PASSED",
            check="COOPERATIVE_TOKEN_NO_PARTIAL_RESULT",
            completed_calls=calls,
            observed_seconds=elapsed,
            resumed_sha256=hashlib.sha256(payload).hexdigest(),
        )
    if kind == "soak":
        # Readiness proves at least one real batch finished; termination occurs during repeated real work.
        batch.Run(adapter.token)
        print("KERNEL_RUNNING", flush=True)
        while True:
            batch.Run(adapter.token)
    raise ValueError("PYTHONNET_PROBE_KIND")


def expected_worker_exit(kind: str, code: int) -> bool:
    # CoreCLR can handle SIGTERM and exit(128 + SIGTERM), instead of a negative signal status.
    return (
        code in (-signal.SIGTERM, 128 + signal.SIGTERM)
        if kind == "sigterm"
        else code == -signal.SIGKILL
    )


def supervise(source: Path, build: Path) -> dict:
    """OS process boundary: no in-process claim that Python can abort a CLR thread."""
    command = [sys.executable, __file__, "probe", "--input", str(source), "--build", str(build)]
    checks = []
    for kind in ("sigterm", "hard_timeout", "sigkill"):
        begin = perf_counter()
        process = subprocess.Popen(
            [*command, "--kind", "soak"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            ready, _, _ = select.select([process.stdout], [], [], 20)
            if not ready or process.stdout.readline().strip() != "KERNEL_RUNNING":
                raise AssertionError("PYTHONNET_WORKER_NOT_RUNNING")
            started = perf_counter()
            if kind == "hard_timeout":
                try:
                    process.wait(timeout=0.15)
                except subprocess.TimeoutExpired:
                    pass
                else:
                    raise AssertionError("SOAK_EXITED_BEFORE_TIMEOUT")
            stop = perf_counter()
            os.killpg(process.pid, signal.SIGTERM if kind == "sigterm" else signal.SIGKILL)
            stdout, stderr = process.communicate(timeout=5)
            if not expected_worker_exit(kind, process.returncode):
                raise AssertionError(f"UNEXPECTED_WORKER_EXIT:{process.returncode}:{stderr}")
            if stdout.strip():
                raise AssertionError("INTERRUPTED_WORKER_RETURNED_RESULT")
            checks.append(
                dict(
                    kind=kind,
                    status="PASSED",
                    exit_code=process.returncode,
                    ready_seconds=started - begin,
                    stop_seconds=perf_counter() - stop,
                    deadline_seconds=0.15 if kind == "hard_timeout" else None,
                )
            )
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate(timeout=5)
            process.stdout.close()
            process.stderr.close()
        # New process after termination: no surviving CLR state or reused partial output.
        recovered = subprocess.run(
            [*command, "--kind", "recover"], capture_output=True, text=True, check=True, timeout=30
        )
        checks[-1]["recovery"] = json.loads(recovered.stdout)
    return dict(status="PASSED", checks=checks)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--source-root", type=Path, required=True)
    for command in ("worker", "probe", "supervise"):
        p = sub.add_parser(command)
        p.add_argument("--input", type=Path, required=True)
        p.add_argument("--build", type=Path, required=True)
        if command == "worker":
            p.add_argument("--output", type=Path, required=True)
            p.add_argument("--items", type=int, required=True)
            p.add_argument("--mode", choices=("pythonnet", "python_reference"), required=True)
        elif command == "probe":
            p.add_argument(
                "--kind", choices=("state", "cooperative", "soak", "recover"), required=True
            )
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.output, args.source_root)
        print(json.dumps({"prepared": str(args.output), "files": len(result["preexisting_files"])}))
    elif args.command == "worker":
        print(json.dumps(worker(args.input, args.output, args.build, args.items, args.mode)))
    elif args.command == "probe":
        # The CLR must be loaded before importing System in probe().
        get_adapter(args.build)
        print(json.dumps(probe(args.input, args.build, args.kind)))
    else:
        print(json.dumps(supervise(args.input, args.build)))


if __name__ == "__main__":
    main()

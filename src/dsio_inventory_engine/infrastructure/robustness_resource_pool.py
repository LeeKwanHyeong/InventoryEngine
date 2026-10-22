"""One filesystem-wide quota for all cooperating stores in one private root.

Admission and publication share an OS lock. A durable pending reservation is
written before any artifact bytes. Recovery retains incomplete files, never
deletes published objects, and recomputes usage from files after a process crash.
"""

from hashlib import sha256
import os
from pathlib import Path
import time
import uuid
from contextlib import contextmanager

from dsio_inventory_engine.infrastructure.robustness_compression import decode_gzip
from dsio_inventory_engine.infrastructure.robustness_resource_files import (
    ARTIFACT_NAME,
    CONTROL_TEMP,
    JOB_NAME,
    QUARANTINE_NAME,
    TEMP_NAME,
    encoded,
    locked,
    physical_hash,
    quarantine_record,
    read_bytes,
    read_document,
    regular,
    sync_directory,
    write_control,
)
from dsio_inventory_engine.inventory_contracts.robustness_resources import (
    MAX_RESOURCE_FILES,
    QUARANTINE_RETENTION_SECONDS,
    ResourceControlError,
    ResourceLimits,
)
from dsio_inventory_engine.inventory_contracts.robustness_storage import MAX_RAW_BYTES
from dsio_inventory_engine.inventory_contracts.values import identifier, require

CONTROL_FILES = {"policy.json", "pending.json", "resource.lock", "invocation.lock"}


class LocalRobustnessResourcePool:
    def __init__(
        self, root: Path, limits: ResourceLimits = ResourceLimits(), *, clock=time.time_ns
    ):
        require(root.is_dir() and not root.is_symlink(), "RESOURCE_ROOT_INVALID")
        if not (root / "policy.json").exists():
            require(
                {p.name for p in root.iterdir()} <= {"resource.lock"},
                "RESOURCE_ROOT_NOT_EMPTY",
            )
        self.root, self.limits, self.clock = root.resolve(strict=True), limits, clock
        info = self.root.stat()
        self.identity = (info.st_dev, info.st_ino)
        self._sizes = {}  # Byte counts only, not Source/PSI validation or an approval cache.
        with locked(self.root, self.identity):
            policy = self.root / "policy.json"
            if not policy.exists():
                require(
                    {p.name for p in self.root.iterdir()} <= {"resource.lock"},
                    "RESOURCE_ROOT_NOT_EMPTY",
                )
                write_control(self.root, "policy.json", limits.policy())
            self._policy()
            for name in ("quarantine", "jobs"):
                directory = self.root / name
                directory.mkdir(mode=0o700, exist_ok=True)
                require(
                    directory.is_dir() and not directory.is_symlink(), "RESOURCE_DIRECTORY_INVALID"
                )

    def _policy(self):
        require(
            encoded(read_document(self.root / "policy.json")) == encoded(self.limits.policy()),
            "RESOURCE_POLICY_MISMATCH",
        )

    def store_root(self, store_id: str) -> Path:
        identifier(store_id)
        with locked(self.root, self.identity):
            self._policy()
            root = self.root / ("artifacts-" + store_id)
            root.mkdir(mode=0o700, exist_ok=True)
            self._require_store(root)
            sync_directory(self.root)
            return root

    def _require_store(self, root: Path):
        require(
            root.parent == self.root
            and root.name.startswith("artifacts-")
            and root.is_dir()
            and not root.is_symlink(),
            "RESOURCE_STORE_OUTSIDE_POOL",
        )
        identifier(root.name.removeprefix("artifacts-"))

    def _stores(self):
        stores = []
        for entry in self.root.iterdir():
            if entry.name.startswith("artifacts-"):
                self._require_store(entry)
                stores.append(entry)
            elif entry.name in ("quarantine", "jobs"):
                require(entry.is_dir() and not entry.is_symlink(), "RESOURCE_QUARANTINE_INVALID")
            else:
                require(
                    entry.name in CONTROL_FILES or CONTROL_TEMP.fullmatch(entry.name),
                    "RESOURCE_UNEXPECTED_FILE",
                )
                regular(entry)
        return stores

    @staticmethod
    def _raw(payload: bytes, name: str) -> int:
        if name.endswith(".gz"):
            require(len(payload) >= 18, "RESOURCE_COMPRESSED_FILE_INVALID")
            return len(decode_gzip(payload, expected_bytes=int.from_bytes(payload[-4:], "little")))
        return len(payload)

    def _raw_file(self, file: Path) -> int:
        info = regular(file)
        key = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        old = self._sizes.get(str(file))
        if old is not None and old[0] == key:
            return old[1]
        size = self._raw(read_bytes(file), file.name)
        self._sizes[str(file)] = (key, size)
        return size

    def _quarantine(self, file: Path, raw_bytes: int):
        info = regular(file)
        key = sha256(str(file.relative_to(self.root)).encode()).hexdigest()
        directory = self.root / "quarantine"
        metadata = directory / ("q-" + key + ".json")
        binary = directory / ("q-" + key + ".bin")
        value = {
            "contract_id": "inventory-robustness-quarantined-temporary",
            "contract_version": "1.0.0",
            "original_name": str(file.relative_to(self.root)),
            "raw_reserved_bytes": raw_bytes,
            "stored_bytes": info.st_size,
            "stored_sha256": physical_hash(file),
            "retained_since_ns": self.clock(),
        }
        if metadata.exists():
            old = quarantine_record(metadata)
            require(
                {k: v for k, v in old.items() if k != "retained_since_ns"}
                == {k: v for k, v in value.items() if k != "retained_since_ns"},
                "RESOURCE_QUARANTINE_CONFLICT",
            )
        else:
            write_control(directory, metadata.name, value)
        require(not binary.exists(), "RESOURCE_QUARANTINE_CONFLICT")
        os.rename(file, binary)
        sync_directory(file.parent)
        sync_directory(directory)

    def _reconcile(self):
        self._policy()
        pending_file = self.root / "pending.json"
        pending = read_document(pending_file) if pending_file.exists() else None
        if pending is not None:
            require(
                set(pending) == {"store", "name", "raw_bytes", "stored_bytes", "stored_sha256"}
                and type(pending["raw_bytes"]) is int
                and 0 < pending["raw_bytes"] <= MAX_RAW_BYTES
                and type(pending["stored_bytes"]) is int
                and 0 < pending["stored_bytes"] <= MAX_RAW_BYTES
                and type(pending["store"]) is str
                and pending["store"].startswith("artifacts-")
                and type(pending["name"]) is str
                and ARTIFACT_NAME.fullmatch(pending["name"]),
                "RESOURCE_PENDING_INVALID",
            )
        for store in self._stores():
            for file in store.iterdir():
                if TEMP_NAME.fullmatch(file.name):
                    size = (
                        pending["raw_bytes"]
                        if pending and pending["store"] == store.name
                        else MAX_RAW_BYTES
                    )
                    self._quarantine(file, size)
                else:
                    require(ARTIFACT_NAME.fullmatch(file.name), "RESOURCE_UNEXPECTED_FILE")
                    regular(file)
        # Control metadata interrupted during its own atomic replace is retained too.
        for file in self.root.iterdir():
            if CONTROL_TEMP.fullmatch(file.name):
                self._quarantine(file, regular(file).st_size)
        for file in list((self.root / "quarantine").iterdir()):
            if CONTROL_TEMP.fullmatch(file.name):
                self._quarantine(file, regular(file).st_size)
        for file in list((self.root / "jobs").iterdir()):
            match = JOB_NAME.fullmatch(file.name)
            if CONTROL_TEMP.fullmatch(file.name):
                self._quarantine(file, regular(file).st_size)
                continue
            require(match is not None, "RESOURCE_JOB_NAME_INVALID")
            owner, deadline_ns = int(match[1]), int(match[2])
            require(owner > 0, "RESOURCE_JOB_OWNER_INVALID")
            alive = True
            try:
                os.kill(owner, 0)
            except ProcessLookupError:
                alive = False
            if not alive or time.monotonic_ns() >= deadline_ns:
                self._quarantine(file, regular(file).st_size)
        if pending is not None:
            pending_file.unlink()
            sync_directory(self.root)

    def _usage(self, check=None) -> dict:
        raw = physical = count = 0
        accounted = {}

        def account(file, raw_size):
            nonlocal raw, physical, count
            if check is not None:
                check()
            info = regular(file)
            inode = (info.st_dev, info.st_ino)
            previous = accounted.get(inode, (0, 0))
            raw += max(previous[0], raw_size) - previous[0]
            physical += max(previous[1], info.st_size) - previous[1]
            accounted[inode] = (max(previous[0], raw_size), max(previous[1], info.st_size))
            count += 1
            require(count <= MAX_RESOURCE_FILES, "RESOURCE_FILE_COUNT_EXCEEDED")

        for file in self.root.iterdir():
            if file.is_dir() and not file.is_symlink():
                continue
            account(file, regular(file).st_size)
        for store in self._stores():
            for file in store.iterdir():
                require(ARTIFACT_NAME.fullmatch(file.name), "RESOURCE_UNEXPECTED_FILE")
                account(file, self._raw_file(file))
        for file in (self.root / "jobs").iterdir():
            require(JOB_NAME.fullmatch(file.name), "RESOURCE_JOB_NAME_INVALID")
            account(file, regular(file).st_size)
        quarantined = 0
        directory = self.root / "quarantine"
        for file in directory.iterdir():
            require(
                QUARANTINE_NAME.fullmatch(file.name) or CONTROL_TEMP.fullmatch(file.name),
                "RESOURCE_UNEXPECTED_FILE",
            )
            size = regular(file).st_size
            if file.suffix == ".bin":
                metadata = file.with_suffix(".json")
                if metadata.exists():
                    value = quarantine_record(metadata)
                    require(
                        type(value["raw_reserved_bytes"]) is int
                        and 0 <= value["raw_reserved_bytes"] <= MAX_RAW_BYTES
                        and size == value["stored_bytes"]
                        and physical_hash(file) == value["stored_sha256"],
                        "RESOURCE_QUARANTINE_INVALID",
                    )
                    charge = value["raw_reserved_bytes"]
                else:
                    charge = MAX_RAW_BYTES  # Interrupted quarantine: never infer a smaller charge.
                quarantined += 1
            else:
                if file.suffix == ".json":
                    quarantine_record(file)
                charge = size
            account(file, charge)
        require(count <= MAX_RESOURCE_FILES, "RESOURCE_FILE_COUNT_EXCEEDED")
        return {
            "raw_bytes": raw,
            "physical_bytes": physical,
            "files": count,
            "quarantined_files": quarantined,
        }

    def _admit(self, extra=0, check=None):
        usage = self._usage(check)
        if usage["raw_bytes"] + extra > self.limits.raw_bytes:
            raise ResourceControlError("RESOURCE_RAW_BUDGET_EXCEEDED")
        return usage

    @staticmethod
    def _check_call(deadline, cancellation):
        if cancellation is not None and cancellation.is_set():
            raise ResourceControlError("RESOURCE_INVOCATION_CANCELLED")
        if time.monotonic() >= deadline:
            raise ResourceControlError("RESOURCE_INVOCATION_TIMED_OUT")

    @contextmanager
    def _call_lock(self, deadline, cancellation):
        while True:
            self._check_call(deadline, cancellation)
            lease = locked(self.root, self.identity, nonblocking=True)
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

    def prepare_job(self, value, deadline, cancellation=None):
        payload = encoded(value)
        require(0 < len(payload) <= MAX_RAW_BYTES, "RESOURCE_ARGUMENTS_TOO_LARGE")
        name = f"job-{os.getpid()}-{int(deadline * 1_000_000_000)}-{uuid.uuid4().hex}.json"
        with self._call_lock(deadline, cancellation):
            self._reconcile()

            def check():
                self._check_call(deadline, cancellation)

            usage = self._admit(len(payload) + 4096, check)
            require(usage["files"] + 4 <= MAX_RESOURCE_FILES, "RESOURCE_FILE_COUNT_EXCEEDED")
            check()
            write_control(self.root / "jobs", name, value)
        return {"name": name, "sha256": sha256(payload).hexdigest()}

    def read_job(self, reference):
        require(JOB_NAME.fullmatch(reference["name"]), "RESOURCE_JOB_NAME_INVALID")
        file = self.root / "jobs" / reference["name"]
        require(physical_hash(file) == reference["sha256"], "RESOURCE_JOB_HASH_MISMATCH")
        return read_document(file)

    def retire_job(self, reference):
        with locked(self.root, self.identity):
            self._reconcile()
            require(JOB_NAME.fullmatch(reference["name"]), "RESOURCE_JOB_NAME_INVALID")
            file = self.root / "jobs" / reference["name"]
            if file.exists():
                require(physical_hash(file) == reference["sha256"], "RESOURCE_JOB_HASH_MISMATCH")
                self._quarantine(file, regular(file).st_size)

    def inspect(self):
        with locked(self.root, self.identity):
            self._reconcile()
            return {**self._usage(), "raw_limit_bytes": self.limits.raw_bytes}

    def publish(self, store: Path, name: str, payload: bytes, publisher):
        require(ARTIFACT_NAME.fullmatch(name), "RESOURCE_ARTIFACT_NAME_INVALID")
        with locked(self.root, self.identity):
            self._require_store(store)
            self._reconcile()
            if (store / name).exists():
                self._admit()
                return publisher()  # Existing bytes still undergo the original conflict check.
            raw = self._raw(payload, name)
            require(0 < raw <= MAX_RAW_BYTES, "RESOURCE_OBJECT_TOO_LARGE")
            reservation = {
                "store": store.name,
                "name": name,
                "raw_bytes": raw,
                "stored_bytes": len(payload),
                "stored_sha256": sha256(payload).hexdigest(),
            }
            # Also reserve bounded recovery metadata before writing any payload.
            usage = self._admit(raw + len(encoded(reservation)) + 4096)
            require(usage["files"] + 4 <= MAX_RESOURCE_FILES, "RESOURCE_FILE_COUNT_EXCEEDED")
            write_control(self.root, "pending.json", reservation)
            try:
                publisher()
            finally:
                self._reconcile()

    def purge_expired_temporaries(self) -> dict:
        """Explicit cleanup of retained temporaries only; published objects are never GC'd."""
        removed = []
        with locked(self.root, self.identity):
            self._reconcile()
            self._usage()  # Validate every retained file before any deletion.
            directory = self.root / "quarantine"
            cutoff = self.clock() - QUARANTINE_RETENTION_SECONDS * 1_000_000_000
            for metadata in sorted(directory.glob("q-*.json")):
                value = quarantine_record(metadata)
                if value["retained_since_ns"] > cutoff:
                    continue
                binary = metadata.with_suffix(".bin")
                if binary.exists():
                    regular(binary)
                    binary.unlink()
                    removed.append(binary.name)
                metadata.unlink()
                removed.append(metadata.name)
            sync_directory(directory)
        return {"removed_quarantine_files": removed}

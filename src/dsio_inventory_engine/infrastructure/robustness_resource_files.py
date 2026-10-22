"""Private POSIX resource-root primitives. Not a hostile-OS security sandbox."""

from contextlib import contextmanager
from hashlib import sha256
import fcntl
import os
from pathlib import Path
import re
import stat
import uuid

from dsio_inventory_engine.inventory_contracts.robustness_storage import MAX_RAW_BYTES
from dsio_inventory_engine.inventory_contracts.values import (
    canonical_json,
    hash_value,
    read_json,
    require,
)

ARTIFACT_NAME = re.compile(
    r"(?:object-[a-f0-9]{64}(?:\.shared)?\.json(?:\.gz)?|"
    r"shared-[a-f0-9]{64}\.json\.gz|slot-[a-f0-9]{64}\.json)"
)
TEMP_NAME = re.compile(r"\.robustness-[a-f0-9]{32}")
CONTROL_TEMP = re.compile(r"control-[a-f0-9]{32}\.tmp")
QUARANTINE_NAME = re.compile(r"q-[a-f0-9]{64}\.(?:json|bin)")
JOB_NAME = re.compile(r"job-([0-9]+)-([0-9]+)-[a-f0-9]{32}\.json")


def encoded(value: dict) -> bytes:
    return (canonical_json(value) + "\n").encode()


def regular(file: Path):
    info = file.lstat()
    require(stat.S_ISREG(info.st_mode), "RESOURCE_FILE_TYPE_INVALID")
    require(info.st_size <= MAX_RAW_BYTES, "RESOURCE_FILE_TOO_LARGE")
    return info


def read_bytes(file: Path) -> bytes:
    fd = os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(
            stat.S_ISREG(info.st_mode) and info.st_size <= MAX_RAW_BYTES,
            "RESOURCE_FILE_TYPE_INVALID",
        )
        with os.fdopen(fd, "rb", closefd=False) as stream:
            value = stream.read(MAX_RAW_BYTES + 1)
        require(len(value) <= MAX_RAW_BYTES, "RESOURCE_FILE_TOO_LARGE")
        return value
    finally:
        os.close(fd)


def read_document(file: Path) -> dict:
    raw = read_bytes(file)
    value = read_json(raw.decode(), max_bytes=MAX_RAW_BYTES)
    require(encoded(value) == raw, "RESOURCE_CONTROL_NONCANONICAL")
    return value


def quarantine_record(file: Path) -> dict:
    value = read_document(file)
    require(
        set(value)
        == {
            "contract_id",
            "contract_version",
            "original_name",
            "raw_reserved_bytes",
            "stored_bytes",
            "stored_sha256",
            "retained_since_ns",
        }
        and value["contract_id"] == "inventory-robustness-quarantined-temporary"
        and value["contract_version"] == "1.0.0"
        and type(value["original_name"]) is str
        and 0 < len(value["original_name"]) <= 512,
        "RESOURCE_QUARANTINE_INVALID",
    )
    for field in ("raw_reserved_bytes", "stored_bytes"):
        require(
            type(value[field]) is int and 0 <= value[field] <= MAX_RAW_BYTES,
            "RESOURCE_QUARANTINE_INVALID",
        )
    require(
        type(value["retained_since_ns"]) is int and value["retained_since_ns"] >= 0,
        "RESOURCE_QUARANTINE_INVALID",
    )
    hash_value(value["stored_sha256"])
    return value


def sync_directory(root: Path):
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_control(root: Path, name: str, value: dict):
    """Only mutable resource metadata uses replace; never calculation artifacts."""
    destination = root / name
    if destination.exists() or destination.is_symlink():
        regular(destination)
    temporary = root / ("control-" + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        sync_directory(root)
    finally:
        if temporary.exists():
            temporary.unlink()


@contextmanager
def locked(root: Path, identity: tuple, name="resource.lock", *, nonblocking=False):
    info = root.lstat()
    require(
        stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == identity,
        "RESOURCE_ROOT_CHANGED",
    )
    fd = os.open(root / name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        require(stat.S_ISREG(os.fstat(fd).st_mode), "RESOURCE_LOCK_INVALID")
        fcntl.flock(fd, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0))
        yield
    finally:
        os.close(fd)


def physical_hash(file: Path) -> str:
    return sha256(read_bytes(file)).hexdigest()

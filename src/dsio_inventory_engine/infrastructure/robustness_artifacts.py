"""POSIX local append-only objects and exclusive logical pointers. No database.

Each document is bounded independently. Root manifests never inline all leaves.
Readers reject noncanonical bytes, symlinks, nonregular files and stale bindings.
The caller owns the private root directory; this is not a hostile-OS sandbox.
"""

from contextlib import contextmanager
import os
from pathlib import Path
import stat
import uuid

from dsio_inventory_engine.evaluate_replenishment.robustness.contracts import (
    PROTOCOL_HASH,
    clone,
    input_errors,
    sealed,
    shape,
)
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    canonical_json,
    digest,
    hash_value,
    read_json,
    require,
)

MAX_BYTES = 128_000_000
KINDS = frozenset(
    {
        "inventory-robustness-durable-input",
        "inventory-robustness-item-leaf",
        "inventory-robustness-durable-header",
        "inventory-robustness-durable-manifest",
        "inventory-robustness-durable-slot",
    }
)


def artifact_reference(value: dict) -> dict:
    shape(value, ("contract_id", "content_hash", "byte_count"))
    require(value["contract_id"] in KINDS, "DURABLE_ARTIFACT_KIND_INVALID")
    hash_value(value["content_hash"])
    require(
        type(value["byte_count"]) is int and 0 < value["byte_count"] <= MAX_BYTES,
        "DURABLE_ARTIFACT_SIZE_LIMIT",
    )
    return dict(value)


def _document(value: dict) -> dict:
    value = clone(value)
    require(
        value.get("contract_id") in KINDS
        and value.get("contract_version") == "1.0.0"
        and value.get("protocol_hash") == PROTOCOL_HASH,
        "DURABLE_ARTIFACT_CONTRACT_INVALID",
    )
    hash_value(value.get("content_hash"))
    require(
        digest({k: v for k, v in value.items() if k != "content_hash"}) == value["content_hash"],
        "DURABLE_ARTIFACT_HASH_MISMATCH",
    )
    return value


class LocalRobustnessArtifactStore:
    def __init__(self, root: Path):
        require(root.is_dir() and not root.is_symlink(), "DURABLE_ARTIFACT_ROOT_INVALID")
        self.root = root.resolve(strict=True)
        info = self.root.stat()
        self.identity = (info.st_dev, info.st_ino)

    @contextmanager
    def _directory(self):
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            require((info.st_dev, info.st_ino) == self.identity, "DURABLE_ARTIFACT_ROOT_CHANGED")
            yield fd
        finally:
            os.close(fd)

    @staticmethod
    def _read_at(directory: int, name: str) -> bytes:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            raise InventoryInputError("DURABLE_ARTIFACT_NOT_FOUND") from None
        except OSError:
            raise InventoryInputError("DURABLE_ARTIFACT_PATH_INVALID") from None
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode), "DURABLE_ARTIFACT_PATH_INVALID")
            require(0 < info.st_size <= MAX_BYTES, "DURABLE_ARTIFACT_SIZE_LIMIT")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                payload = stream.read(MAX_BYTES + 1)
        finally:
            os.close(fd)
        require(0 < len(payload) <= MAX_BYTES, "DURABLE_ARTIFACT_SIZE_LIMIT")
        return payload

    def _publish(self, name: str, payload: bytes) -> None:
        require(0 < len(payload) <= MAX_BYTES, "DURABLE_ARTIFACT_SIZE_LIMIT")
        temporary = ".robustness-" + uuid.uuid4().hex
        with self._directory() as directory:
            try:
                existing = self._read_at(directory, name)
            except InventoryInputError as error:
                if str(error) != "DURABLE_ARTIFACT_NOT_FOUND":
                    raise
            else:
                require(existing == payload, "DURABLE_ARTIFACT_CONFLICT")
                os.fsync(directory)
                return
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(
                        temporary,
                        name,
                        src_dir_fd=directory,
                        dst_dir_fd=directory,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    require(self._read_at(directory, name) == payload, "DURABLE_ARTIFACT_CONFLICT")
                os.fsync(directory)
            finally:
                os.unlink(temporary, dir_fd=directory)
                os.fsync(directory)

    @input_errors
    def put(self, document: dict) -> dict:
        value = _document(document)
        payload = (canonical_json(value) + "\n").encode()
        self._publish("object-" + value["content_hash"] + ".json", payload)
        return artifact_reference(
            {
                "contract_id": value["contract_id"],
                "content_hash": value["content_hash"],
                "byte_count": len(payload),
            }
        )

    @input_errors
    def read(self, reference: dict) -> dict:
        ref = artifact_reference(reference)
        with self._directory() as directory:
            payload = self._read_at(directory, "object-" + ref["content_hash"] + ".json")
        require(len(payload) == ref["byte_count"], "DURABLE_ARTIFACT_BYTE_COUNT_MISMATCH")
        value = _document(read_json(payload.decode(), max_bytes=MAX_BYTES))
        require(
            value["content_hash"] == ref["content_hash"]
            and value["contract_id"] == ref["contract_id"],
            "DURABLE_ARTIFACT_REFERENCE_MISMATCH",
        )
        require((canonical_json(value) + "\n").encode() == payload, "DURABLE_ARTIFACT_NONCANONICAL")
        return value

    @staticmethod
    def _slot_name(key: str) -> str:
        require(type(key) is str and 0 < len(key) <= 256, "DURABLE_SLOT_KEY_INVALID")
        return "slot-" + digest({"slot_key": key}) + ".json"

    @input_errors
    def bind(self, key: str, reference: dict) -> None:
        ref = artifact_reference(reference)
        self.read(ref)  # No pointer to an absent, malformed or different object.
        document = sealed("inventory-robustness-durable-slot", {"slot_key": key, "reference": ref})
        self._publish(self._slot_name(key), (canonical_json(document) + "\n").encode())

    @input_errors
    def lookup(self, key: str) -> dict | None:
        with self._directory() as directory:
            try:
                payload = self._read_at(directory, self._slot_name(key))
            except InventoryInputError as error:
                if str(error) == "DURABLE_ARTIFACT_NOT_FOUND":
                    return None
                raise
        value = _document(read_json(payload.decode(), max_bytes=MAX_BYTES))
        expected = sealed(
            "inventory-robustness-durable-slot",
            {"slot_key": key, "reference": artifact_reference(value["reference"])},
        )
        require(
            (canonical_json(expected) + "\n").encode() == payload, "DURABLE_SLOT_BINDING_MISMATCH"
        )
        return value["reference"]

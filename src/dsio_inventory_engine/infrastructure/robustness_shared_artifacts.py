"""Opt-in content-addressed input sharing on the existing durable Repository API.

Original JSON/result hashes are reconstructed before the application sees them.
Publication order is chunks -> document -> logical checkpoint. Old V1/V2 files
and pointers are never converted/deleted. Chunks have no recursive dependencies.
"""

from hashlib import sha256
import gzip
import os

from dsio_inventory_engine.evaluate_replenishment.robustness.contracts import input_errors, sealed
from dsio_inventory_engine.infrastructure.robustness_artifacts import (
    KINDS,
    _document,
    artifact_reference,
)
from dsio_inventory_engine.infrastructure.robustness_compression import (
    CompressedRobustnessArtifactStore,
    _reference,
    decode_gzip,
)
from dsio_inventory_engine.infrastructure.robustness_shared_layout import (
    pack_document,
    restore_document,
)
from dsio_inventory_engine.inventory_contracts.robustness_shared_inputs import (
    LAYOUT,
    SHARED_KIND,
    STORAGE_VERSION,
    shared_document_reference,
    shared_reference,
)
from dsio_inventory_engine.inventory_contracts.robustness_storage import (
    CODEC_VERSION,
    COMPRESSION_LEVEL,
    ENCODING,
    MAX_RAW_BYTES,
)
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    canonical_json,
    read_json,
    require,
)


def reference(value: dict) -> dict:
    if type(value) is dict and value.get("storage_version") == STORAGE_VERSION:
        ref = shared_document_reference(value)
        require(ref["contract_id"] in KINDS, "DURABLE_ARTIFACT_KIND_INVALID")
        return ref
    return _reference(value)


def encoded(value: object) -> bytes:
    raw = (canonical_json(value) + "\n").encode()
    require(0 < len(raw) <= MAX_RAW_BYTES, "SHARED_STORAGE_SIZE_LIMIT")
    return raw


class SharedInputRobustnessArtifactStore(CompressedRobustnessArtifactStore):
    def _optional(self, name: str) -> bytes | None:
        with self._directory() as directory:
            try:
                return self._read_at(directory, name)
            except InventoryInputError as error:
                if str(error) != "DURABLE_ARTIFACT_NOT_FOUND":
                    raise
        return None

    def _gzip(self, name: str, raw: bytes) -> bytes:
        existing = self._optional(name)
        if existing is not None:
            require(
                decode_gzip(existing, expected_bytes=len(raw)) == raw, "DURABLE_ARTIFACT_CONFLICT"
            )
            with self._directory() as directory:
                os.fsync(directory)
            return existing
        stored = gzip.compress(raw, compresslevel=COMPRESSION_LEVEL, mtime=0)
        self._publish(name, stored)
        return stored

    def _put_shared(self, value: object, raw_value: bytes) -> dict:
        raw = encoded({"contract_id": SHARED_KIND, "contract_version": "1.0.0", "value": value})
        value_hash = sha256(raw_value).hexdigest()
        stored = self._gzip("shared-" + value_hash + ".json.gz", raw)
        return shared_reference(
            {
                "value_hash": value_hash,
                "value_byte_count": len(raw_value),
                "byte_count": len(raw),
                "raw_sha256": sha256(raw).hexdigest(),
                "stored_byte_count": len(stored),
                "stored_sha256": sha256(stored).hexdigest(),
            }
        )

    def _read_compressed(self, name: str, ref: dict, *, raw_bytes: int) -> bytes:
        with self._directory() as directory:
            stored = self._read_at(directory, name)
        require(len(stored) == ref["stored_byte_count"], "STORAGE_STORED_BYTE_COUNT_MISMATCH")
        require(
            sha256(stored).hexdigest() == ref["stored_sha256"], "STORAGE_PHYSICAL_HASH_MISMATCH"
        )
        return decode_gzip(stored, expected_bytes=raw_bytes)

    def _read_shared(self, ref: dict) -> object:
        ref = shared_reference(ref)
        raw = self._read_compressed(
            "shared-" + ref["value_hash"] + ".json.gz", ref, raw_bytes=ref["byte_count"]
        )
        require(sha256(raw).hexdigest() == ref["raw_sha256"], "STORAGE_RAW_HASH_MISMATCH")
        wrapper = read_json(raw.decode(), max_bytes=MAX_RAW_BYTES)
        require(
            set(wrapper) == {"contract_id", "contract_version", "value"}
            and wrapper["contract_id"] == SHARED_KIND
            and wrapper["contract_version"] == "1.0.0"
            and type(wrapper["value"]) in (dict, list),
            "SHARED_VALUE_CONTRACT_INVALID",
        )
        value = canonical_json(wrapper["value"]).encode()
        require(
            len(value) == ref["value_byte_count"]
            and sha256(value).hexdigest() == ref["value_hash"],
            "SHARED_VALUE_HASH_MISMATCH",
        )
        require(encoded(wrapper) == raw, "DURABLE_ARTIFACT_NONCANONICAL")
        return wrapper["value"]

    @input_errors
    def put(self, document: dict) -> dict:
        value = _document(document)
        raw = encoded(value)
        prefix = "object-" + value["content_hash"]
        # Preserve the original physical reference if a V1/V2 object already exists.
        old = self._optional(prefix + ".json")
        if old is not None:
            require(old == raw, "DURABLE_ARTIFACT_CONFLICT")
            with self._directory() as directory:
                os.fsync(directory)
            return artifact_reference(
                {
                    "contract_id": value["contract_id"],
                    "content_hash": value["content_hash"],
                    "byte_count": len(raw),
                }
            )
        old = self._optional(prefix + ".json.gz")
        if old is not None:
            require(decode_gzip(old, expected_bytes=len(raw)) == raw, "DURABLE_ARTIFACT_CONFLICT")
            with self._directory() as directory:
                os.fsync(directory)
            return self._physical_reference(value, raw, old)
        # Per-call memoization only; not a trusted cross-request cache.
        cache = {}

        def persist(shared: object, raw_value: bytes) -> dict:
            key = sha256(raw_value).hexdigest()
            if key not in cache:
                cache[key] = self._put_shared(shared, raw_value)
            return cache[key]

        packed = encoded(pack_document(value, persist))
        stored = self._gzip(prefix + ".shared.json.gz", packed)
        return reference(
            {
                "storage_version": STORAGE_VERSION,
                "layout": LAYOUT,
                "contract_id": value["contract_id"],
                "content_hash": value["content_hash"],
                "byte_count": len(raw),
                "raw_sha256": sha256(raw).hexdigest(),
                "encoding": ENCODING,
                "codec_version": CODEC_VERSION,
                "compression_level": COMPRESSION_LEVEL,
                "manifest_byte_count": len(packed),
                "stored_byte_count": len(stored),
                "stored_sha256": sha256(stored).hexdigest(),
            }
        )

    @input_errors
    def read(self, artifact_reference: dict) -> dict:
        ref = reference(artifact_reference)
        if ref.get("storage_version") != STORAGE_VERSION:
            return super().read(ref)
        raw = self._read_compressed(
            "object-" + ref["content_hash"] + ".shared.json.gz",
            ref,
            raw_bytes=ref["manifest_byte_count"],
        )
        layout = read_json(raw.decode(), max_bytes=MAX_RAW_BYTES)
        require(encoded(layout) == raw, "DURABLE_ARTIFACT_NONCANONICAL")
        value = restore_document(layout, self._read_shared, expected_bytes=ref["byte_count"])
        original = encoded(value)
        require(
            len(original) == ref["byte_count"]
            and sha256(original).hexdigest() == ref["raw_sha256"],
            "SHARED_RESTORED_HASH_MISMATCH",
        )
        value = _document(value)
        require(
            value["contract_id"] == ref["contract_id"]
            and value["content_hash"] == ref["content_hash"],
            "DURABLE_ARTIFACT_REFERENCE_MISMATCH",
        )
        return value

    @input_errors
    def bind(self, key: str, artifact_reference: dict) -> None:
        ref = reference(artifact_reference)
        self.read(ref)
        slot = sealed("inventory-robustness-durable-slot", {"slot_key": key, "reference": ref})
        self._publish(self._slot_name(key), encoded(slot))

    @input_errors
    def lookup(self, key: str) -> dict | None:
        raw = self._optional(self._slot_name(key))
        if raw is None:
            return None
        value = _document(read_json(raw.decode(), max_bytes=MAX_RAW_BYTES))
        ref = reference(value["reference"])
        expected = sealed("inventory-robustness-durable-slot", {"slot_key": key, "reference": ref})
        require(encoded(expected) == raw, "DURABLE_SLOT_BINDING_MISMATCH")
        return ref

"""Explicit opt-in V2 storage. Existing V1 objects/pointers are never rewritten.

Domain result hashes stay unchanged. Storage manifests can differ because their
references contain codec/physical metadata. Old readers do not understand V2;
this reader dispatches both versions without fallback after corruption.
"""

import gzip
from hashlib import sha256
import os
import zlib

from dsio_inventory_engine.evaluate_replenishment.robustness.contracts import input_errors, sealed
from dsio_inventory_engine.infrastructure.robustness_artifacts import (
    KINDS,
    LocalRobustnessArtifactStore,
    _document,
    artifact_reference,
)
from dsio_inventory_engine.inventory_contracts.robustness_storage import (
    CODEC_VERSION,
    COMPRESSION_LEVEL,
    ENCODING,
    MAX_RAW_BYTES,
    STORAGE_VERSION,
    storage_reference,
)
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    canonical_json,
    read_json,
    require,
)


def decode_gzip(payload: bytes, *, expected_bytes: int) -> bytes:
    """Bound output before parsing JSON; reject truncation, extra members and trailing data."""
    require(
        type(expected_bytes) is int and 0 < expected_bytes <= MAX_RAW_BYTES, "STORAGE_SIZE_LIMIT"
    )
    try:
        decoder = zlib.decompressobj(wbits=31)
        raw = decoder.decompress(payload, expected_bytes + 1)
        require(len(raw) <= expected_bytes, "STORAGE_DECOMPRESSION_LIMIT")
        require(
            decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail,
            "STORAGE_GZIP_FRAME_INVALID",
        )
        require(len(raw) == expected_bytes, "STORAGE_RAW_BYTE_COUNT_MISMATCH")
        return raw
    except zlib.error:
        raise InventoryInputError("STORAGE_GZIP_FRAME_INVALID") from None


def _reference(value: dict) -> dict:
    if type(value) is dict and "storage_version" in value:
        ref = storage_reference(value)
        require(ref["contract_id"] in KINDS, "DURABLE_ARTIFACT_KIND_INVALID")
        return ref
    return artifact_reference(value)


class CompressedRobustnessArtifactStore(LocalRobustnessArtifactStore):
    @staticmethod
    def _physical_reference(value: dict, raw: bytes, stored: bytes) -> dict:
        return _reference(
            {
                "storage_version": STORAGE_VERSION,
                "contract_id": value["contract_id"],
                "content_hash": value["content_hash"],
                "byte_count": len(raw),
                "raw_sha256": sha256(raw).hexdigest(),
                "encoding": ENCODING,
                "codec_version": CODEC_VERSION,
                "compression_level": COMPRESSION_LEVEL,
                "stored_byte_count": len(stored),
                "stored_sha256": sha256(stored).hexdigest(),
            }
        )

    @input_errors
    def put(self, document: dict) -> dict:
        value = _document(document)
        raw = (canonical_json(value) + "\n").encode()
        require(len(raw) <= MAX_RAW_BYTES, "STORAGE_SIZE_LIMIT")
        legacy_name = "object-" + value["content_hash"] + ".json"
        name = legacy_name + ".gz"
        with self._directory() as directory:
            try:
                old = self._read_at(directory, legacy_name)
            except InventoryInputError as error:
                if str(error) != "DURABLE_ARTIFACT_NOT_FOUND":
                    raise
            else:
                require(old == raw, "DURABLE_ARTIFACT_CONFLICT")
                os.fsync(directory)
                return artifact_reference(
                    {
                        "contract_id": value["contract_id"],
                        "content_hash": value["content_hash"],
                        "byte_count": len(raw),
                    }
                )
            try:
                existing = self._read_at(directory, name)
            except InventoryInputError as error:
                if str(error) != "DURABLE_ARTIFACT_NOT_FOUND":
                    raise
            else:
                # Reuse a valid existing frame even across Python/zlib upgrades.
                require(
                    decode_gzip(existing, expected_bytes=len(raw)) == raw,
                    "DURABLE_ARTIFACT_CONFLICT",
                )
                os.fsync(directory)
                return self._physical_reference(value, raw, existing)
        stored = gzip.compress(raw, compresslevel=COMPRESSION_LEVEL, mtime=0)
        reference = self._physical_reference(value, raw, stored)
        self._publish(name, stored)
        return reference

    @input_errors
    def read(self, reference: dict) -> dict:
        ref = _reference(reference)
        if "storage_version" not in ref:
            return super().read(ref)
        with self._directory() as directory:
            stored = self._read_at(directory, "object-" + ref["content_hash"] + ".json.gz")
        require(len(stored) == ref["stored_byte_count"], "STORAGE_STORED_BYTE_COUNT_MISMATCH")
        require(
            sha256(stored).hexdigest() == ref["stored_sha256"], "STORAGE_PHYSICAL_HASH_MISMATCH"
        )
        raw = decode_gzip(stored, expected_bytes=ref["byte_count"])
        require(sha256(raw).hexdigest() == ref["raw_sha256"], "STORAGE_RAW_HASH_MISMATCH")
        value = _document(read_json(raw.decode(), max_bytes=MAX_RAW_BYTES))
        require(
            value["content_hash"] == ref["content_hash"]
            and value["contract_id"] == ref["contract_id"],
            "DURABLE_ARTIFACT_REFERENCE_MISMATCH",
        )
        require((canonical_json(value) + "\n").encode() == raw, "DURABLE_ARTIFACT_NONCANONICAL")
        return value

    @input_errors
    def bind(self, key: str, reference: dict) -> None:
        ref = _reference(reference)
        self.read(ref)
        slot = sealed("inventory-robustness-durable-slot", {"slot_key": key, "reference": ref})
        self._publish(self._slot_name(key), (canonical_json(slot) + "\n").encode())

    @input_errors
    def lookup(self, key: str) -> dict | None:
        with self._directory() as directory:
            try:
                payload = self._read_at(directory, self._slot_name(key))
            except InventoryInputError as error:
                if str(error) == "DURABLE_ARTIFACT_NOT_FOUND":
                    return None
                raise
        value = _document(read_json(payload.decode(), max_bytes=MAX_RAW_BYTES))
        ref = _reference(value["reference"])
        expected = sealed("inventory-robustness-durable-slot", {"slot_key": key, "reference": ref})
        require(
            (canonical_json(expected) + "\n").encode() == payload, "DURABLE_SLOT_BINDING_MISMATCH"
        )
        return ref

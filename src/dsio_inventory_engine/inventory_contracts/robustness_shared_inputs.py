"""Physical layout V3 only. Original calculation documents/hashes stay V1."""

from dsio_inventory_engine.inventory_contracts.robustness_storage import (
    CODEC_VERSION,
    COMPRESSION_LEVEL,
    ENCODING,
    MAX_RAW_BYTES,
    MAX_STORED_BYTES,
)
from dsio_inventory_engine.inventory_contracts.values import hash_value, require

STORAGE_VERSION = "3.0.0"
LAYOUT = "SHARED_INPUTS_V1"
SHARED_KIND = "inventory-robustness-shared-input"
DOCUMENT_KIND = "inventory-robustness-shared-document"
SHARED_FIELDS = frozenset({"parent_classification", "calendar", "forecast", "future_demand"})
MIN_SHARED_BYTES = 512
MAX_REFERENCES = 4096
MAX_DEPTH = 64
SHARED_REFERENCE_FIELDS = frozenset(
    {
        "value_hash",
        "value_byte_count",
        "byte_count",
        "raw_sha256",
        "stored_byte_count",
        "stored_sha256",
    }
)
V3_FIELDS = frozenset(
    {
        "storage_version",
        "layout",
        "contract_id",
        "content_hash",
        "byte_count",
        "raw_sha256",
        "encoding",
        "codec_version",
        "compression_level",
        "manifest_byte_count",
        "stored_byte_count",
        "stored_sha256",
    }
)


def bounded_integer(value: int, minimum: int, maximum: int) -> None:
    require(type(value) is int and minimum <= value <= maximum, "SHARED_STORAGE_SIZE_LIMIT")


def shared_reference(value: dict) -> dict:
    require(
        type(value) is dict and set(value) == SHARED_REFERENCE_FIELDS, "SHARED_REFERENCE_FIELDS"
    )
    for key in ("value_hash", "raw_sha256", "stored_sha256"):
        hash_value(value[key])
    bounded_integer(value["value_byte_count"], MIN_SHARED_BYTES, MAX_RAW_BYTES)
    bounded_integer(value["byte_count"], 1, MAX_RAW_BYTES)
    bounded_integer(value["stored_byte_count"], 1, MAX_STORED_BYTES)
    return dict(value)


def shared_document_reference(value: dict) -> dict:
    require(type(value) is dict and set(value) == V3_FIELDS, "SHARED_DOCUMENT_REFERENCE_FIELDS")
    require(
        value["storage_version"] == STORAGE_VERSION
        and value["layout"] == LAYOUT
        and value["encoding"] == ENCODING
        and value["codec_version"] == CODEC_VERSION
        and type(value["compression_level"]) is int
        and value["compression_level"] == COMPRESSION_LEVEL,
        "SHARED_STORAGE_VERSION_UNSUPPORTED",
    )
    require(type(value["contract_id"]) is str, "SHARED_DOCUMENT_KIND_INVALID")
    for key in ("content_hash", "raw_sha256", "stored_sha256"):
        hash_value(value[key])
    for key in ("byte_count", "manifest_byte_count"):
        bounded_integer(value[key], 1, MAX_RAW_BYTES)
    bounded_integer(value["stored_byte_count"], 1, MAX_STORED_BYTES)
    return dict(value)

"""Storage V2 is physical metadata, never a new calculation or approval contract."""

from dsio_inventory_engine.inventory_contracts.values import (
    canonical_json,
    digest,
    hash_value,
    read_json,
    require,
)

MAX_RAW_BYTES = 128_000_000
MAX_STORED_BYTES = 128_000_000
MAX_EXPERIMENT_RAW_BYTES = 20_000_000_000
MAX_INVOCATION_SECONDS = 3600
STORAGE_VERSION = "2.0.0"
ENCODING = "GZIP"
CODEC_VERSION = "RFC1952-DEFLATE-V1"
COMPRESSION_LEVEL = 1
V2_FIELDS = frozenset(
    {
        "storage_version",
        "contract_id",
        "content_hash",
        "byte_count",
        "raw_sha256",
        "encoding",
        "codec_version",
        "compression_level",
        "stored_byte_count",
        "stored_sha256",
    }
)

BENCHMARK_SPEC = {
    "contract_id": "inventory-robustness-storage-benchmark",
    "contract_version": "1.0.0",
    "scope": "MANUAL_DEVELOPMENT_STORAGE_ONLY_NOT_OFFICIAL_ROBUSTNESS_STUDY",
    "w0": "2055-01-04",
    "path_id": "HAND-CATALOG-PATH",
    "seed": 71,
    "split": "NOMINAL",
    "weeks": 65,
    "profile_id": "PRICE_BASE",
    "gate": "REVIEW",
    "modes": {"ABC_XYZ_SHARED_BUDGET": 32, "LEGACY_STYLE": 32, "WIDE_REFERENCE": 88},
    "variants": ["JSON_V1", "GZIP_V2"],
    "repetitions": 3,
    "workers": 1,
    "variant_order": "JSON_FIRST_ON_ODD_REPETITIONS_GZIP_FIRST_ON_EVEN",
    "isolation": "FRESH_PROCESS_AND_EMPTY_PRIVATE_DIRECTORY_PER_CASE_NOT_COLD_OS_CACHE",
    "phases": [
        "PREPARATION",
        "COMPUTE",
        "PUBLICATION_REPLAY",
        "PERSIST_EXCLUDING_REPLAY",
        "READ_INTEGRITY",
        "REVALIDATE_EXCLUDING_STORAGE",
    ],
    "timing": "PERF_COUNTER_NS_WALL_TIME_NO_SUM_OF_OVERLAPPING_PHASES",
    "memory": "ABSOLUTE_PROCESS_RUSAGE_PEAK_RSS_BYTES_CUMULATIVE_NOT_PHASE_ALLOCATION",
    "raw_budget": "RAW_BYTES_ONCE_PER_PHYSICAL_OBJECT_PLUS_RAW_SLOTS_ACROSS_ALL_TRIAL_DIRECTORIES",
    "physical_budget": "ALL_FILES_INCLUDING_COMPRESSION_METADATA_AND_TEMPORARY_FILES",
    "max_object_raw_bytes": MAX_RAW_BYTES,
    "max_object_stored_bytes": MAX_STORED_BYTES,
    "max_experiment_raw_bytes": MAX_EXPERIMENT_RAW_BYTES,
    "max_invocation_seconds": MAX_INVOCATION_SECONDS,
    "hash_equivalence": "RESULT_AND_RESTORED_OBJECT_BYTES_IDENTICAL_STORAGE_MANIFEST_MAY_DIFFER",
    "acceptance": "ALL_CANDIDATES_AND_PSI_ROWS_MATCH_NO_LOSS_OR_ROUNDING_CHANGE",
    "speed_claim": "THREE_REPETITIONS_PER_MODE_AND_VARIANT_REQUIRED_REPORT_EVERY_PHASE_MEDIAN",
    "operational_policy_approved": False,
    "automatic_publish_allowed": False,
    "automatic_order_allowed": False,
    "database_writes": False,
    "official_paths_generated": False,
}
BENCHMARK_SPEC_HASH = digest(BENCHMARK_SPEC)


def storage_reference(value: dict) -> dict:
    require(type(value) is dict and set(value) == V2_FIELDS, "STORAGE_REFERENCE_FIELDS")
    require(
        value["storage_version"] == STORAGE_VERSION
        and value["encoding"] == ENCODING
        and value["codec_version"] == CODEC_VERSION
        and type(value["compression_level"]) is int
        and value["compression_level"] == COMPRESSION_LEVEL,
        "STORAGE_CODEC_UNSUPPORTED",
    )
    for key in ("content_hash", "raw_sha256", "stored_sha256"):
        hash_value(value[key])
    for key, limit in (("byte_count", MAX_RAW_BYTES), ("stored_byte_count", MAX_STORED_BYTES)):
        require(type(value[key]) is int and 0 < value[key] <= limit, "STORAGE_SIZE_LIMIT")
    require(type(value["contract_id"]) is str, "STORAGE_CONTRACT_INVALID")
    return dict(value)


def benchmark_spec() -> dict:
    return read_json(canonical_json(BENCHMARK_SPEC))


def validate_benchmark_spec(value: dict, *, expected_hash: str) -> None:
    hash_value(expected_hash)
    require(
        canonical_json(value) == canonical_json(BENCHMARK_SPEC)
        and digest(value) == expected_hash == BENCHMARK_SPEC_HASH,
        "STORAGE_BENCHMARK_SPEC_MISMATCH",
    )

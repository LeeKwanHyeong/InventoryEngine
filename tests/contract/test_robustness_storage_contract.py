"""Storage changes cannot relax the frozen raw-resource or calculation contracts."""

import json
from pathlib import Path

from dsio_inventory_engine.infrastructure.robustness_artifacts import MAX_BYTES
from dsio_inventory_engine.inventory_contracts.robustness_storage import (
    BENCHMARK_SPEC_HASH,
    MAX_EXPERIMENT_RAW_BYTES,
    MAX_INVOCATION_SECONDS,
    MAX_RAW_BYTES,
    MAX_STORED_BYTES,
    benchmark_spec,
    validate_benchmark_spec,
)
from dsio_inventory_engine.inventory_contracts.values import digest

ROOT = Path(__file__).resolve().parents[2]
SPEC_HASH = "f96f089823d5c747b5cb09da516fa87ef4b0d7460f2d5033c28c44d68a11f06f"
PROTOCOL_HASH = "a3e35fcf23915e87823883ea8c1f09448b245ae7ea1fffa8c77482bab0433280"


def protocol():
    return json.loads(
        (ROOT / "docs/architecture/data/catalog-robustness-protocol-v1.json").read_text()
    )


def test_frozen_json_and_python_measurement_spec_are_identical():
    value = json.loads(
        (ROOT / "docs/architecture/data/robustness-storage-benchmark-v1.json").read_text()
    )
    validate_benchmark_spec(value, expected_hash=SPEC_HASH)
    assert value == benchmark_spec()
    assert digest(value) == BENCHMARK_SPEC_HASH == SPEC_HASH


def test_raw_size_time_and_worker_limits_match_existing_protocol():
    old, spec = protocol(), benchmark_spec()
    assert digest(old) == PROTOCOL_HASH
    limits = old["execution"]
    assert limits["max_artifact_bytes"] == MAX_BYTES == MAX_RAW_BYTES == MAX_STORED_BYTES
    assert spec["max_object_raw_bytes"] == spec["max_object_stored_bytes"] == 128_000_000
    assert (
        limits["max_bytes_per_local_experiment"]
        == MAX_EXPERIMENT_RAW_BYTES
        == spec["max_experiment_raw_bytes"]
        == 20_000_000_000
    )
    assert limits["max_wall_seconds_per_invocation"] == MAX_INVOCATION_SECONDS == 3600
    assert spec["max_invocation_seconds"] == 3600
    assert limits["workers"] == spec["workers"] == 1
    assert limits["max_simulation_weeks"] == spec["weeks"] == 65


def test_complete_comparison_requires_original_catalog_counts_and_repetitions():
    spec = benchmark_spec()
    assert spec["modes"] == {
        "ABC_XYZ_SHARED_BUDGET": 32,
        "LEGACY_STYLE": 32,
        "WIDE_REFERENCE": 88,
    }
    assert spec["repetitions"] == 3
    assert spec["variants"] == ["JSON_V1", "GZIP_V2"]
    assert spec["hash_equivalence"] == (
        "RESULT_AND_RESTORED_OBJECT_BYTES_IDENTICAL_STORAGE_MANIFEST_MAY_DIFFER"
    )


def test_manual_storage_input_does_not_authorize_official_study_or_publication():
    old, spec = protocol(), benchmark_spec()
    assert spec["w0"] == "2055-01-04"
    assert spec["w0"] not in {window["w0"] for window in old["windows"]}
    assert spec["path_id"] == "HAND-CATALOG-PATH" and spec["seed"] == 71
    assert spec["gate"] == "REVIEW"
    for key in (
        "operational_policy_approved",
        "automatic_publish_allowed",
        "automatic_order_allowed",
        "database_writes",
        "official_paths_generated",
    ):
        assert spec[key] is False

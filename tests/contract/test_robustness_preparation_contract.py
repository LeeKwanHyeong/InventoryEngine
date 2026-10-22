"""Preparation is an implementation reuse boundary, never a new policy/DB contract."""

from scripts.benchmark_robustness_preparation import SPEC
from dsio_inventory_engine.evaluate_replenishment.robustness.immutable_preparation import (
    MAX_RETAINED_BYTES,
)
from dsio_inventory_engine.inventory_contracts.robustness_storage import (
    MAX_RAW_BYTES,
    MAX_INVOCATION_SECONDS,
    MAX_EXPERIMENT_RAW_BYTES,
)


def test_comparison_preserves_full_manual_candidate_and_resource_bounds():
    assert list(SPEC["modes"].values()) == [32, 32, 88]
    assert SPEC["manual_w0"] == "2055-01-04" and SPEC["weeks"] == 65
    assert SPEC["manual_path_id"] == "HAND-CATALOG-PATH" and SPEC["manual_seed"] == 71
    assert SPEC["repetitions"] == 3 and SPEC["workers"] == 1
    assert SPEC["max_retained_bytes"] == MAX_RETAINED_BYTES == MAX_RAW_BYTES == 128_000_000
    assert SPEC["max_raw_storage_bytes"] == MAX_EXPERIMENT_RAW_BYTES == 20_000_000_000
    assert SPEC["max_invocation_seconds"] == MAX_INVOCATION_SECONDS == 3600
    assert SPEC["official_paths_generated"] is SPEC["database_writes"] is False
    assert SPEC["measures_io"] is SPEC["clears_os_cache"] is False

from copy import deepcopy

import pytest

from scripts.benchmark_robustness_storage import MeteredRepository, summarize, write_new
from dsio_inventory_engine.inventory_contracts.robustness_storage import (
    BENCHMARK_SPEC_HASH,
    benchmark_spec,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from dsio_inventory_engine.infrastructure.robustness_compression import (
    CompressedRobustnessArtifactStore,
)
from tests.unit.test_robustness_compression import sample


def records(repetitions=3):
    spec = benchmark_spec()
    return [
        {
            "mode": mode,
            "variant": variant,
            "repetition": repetition,
            "status": "VERIFIED",
            "source_hash": "a" * 64,
            "spec_hash": BENCHMARK_SPEC_HASH,
            "assembly_hash": "b" * 64,
            "realization_hash": "c" * 64,
            "parent_classification_hash": "d" * 64,
            "complete_catalog_hash": "e" * 64,
            "result_hash": "f" * 64,
            "leaf_hashes": ["1" * 64],
            "candidate_count": count,
            "psi_row_count": count * 65,
            "phase_wall_ns": {p: repetition * 1000 for p in spec["phases"]},
            "physical_bytes": 1000,
            "raw_budget_bytes": 2000,
            "peak_process_rss_bytes": 10000,
            "database_writes": False,
            "automatic_publish_allowed": False,
            "automatic_order_allowed": False,
            "official_paths_generated": False,
        }
        for mode, count in spec["modes"].items()
        for variant in spec["variants"]
        for repetition in range(1, repetitions + 1)
    ]


def test_closed_complete_matrix_and_separate_phase_medians():
    result = summarize(records())
    assert result["status"] == "COMPLETE_COMPARISON" and result["case_count"] == 18
    assert len(result["summaries"]) == 6
    assert all(set(r["phase_wall_ns_median"].values()) == {2000} for r in result["summaries"])
    assert result["mathematical_strategy_speedup_claim"] is False


def test_smoke_cannot_be_mislabeled_a_full_repeated_comparison():
    with pytest.raises(InventoryInputError, match="INCOMPLETE"):
        summarize(records(repetitions=1))
    assert summarize(records(repetitions=1), allow_smoke=True)["status"] == "SMOKE_ONLY"


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "duplicate",
        "changed_hash",
        "changed_source",
        "changed_count",
        "missing_phase",
        "negative_time",
        "changed_physical_size",
        "changed_raw_size",
        "boolean_trial",
        "published",
        "budget",
    ],
)
def test_invalid_comparison_does_not_produce_success(fault):
    rows = records()
    if fault == "missing":
        rows.pop()
    elif fault == "duplicate":
        rows[0] = deepcopy(rows[1])
    elif fault == "changed_hash":
        rows[0]["result_hash"] = "0" * 64
    elif fault == "changed_source":
        rows[0]["source_hash"] = "0" * 64
    elif fault == "changed_count":
        rows[0]["candidate_count"] = 16
    elif fault == "missing_phase":
        rows[0]["phase_wall_ns"].pop("COMPUTE")
    elif fault == "negative_time":
        rows[0]["phase_wall_ns"]["COMPUTE"] = -1
    elif fault == "changed_physical_size":
        rows[0]["physical_bytes"] += 1
    elif fault == "changed_raw_size":
        rows[0]["raw_budget_bytes"] += 1
    elif fault == "boolean_trial":
        rows[0]["repetition"] = True
    elif fault == "published":
        rows[0]["automatic_publish_allowed"] = True
    else:
        for row in rows:
            row["raw_budget_bytes"] = 2_000_000_000
    with pytest.raises(InventoryInputError):
        summarize(rows)


def test_compression_cannot_discount_raw_budget_or_count_duplicate_puts_twice(tmp_path):
    store = CompressedRobustnessArtifactStore(tmp_path)
    repository = MeteredRepository(store)
    ref = repository.put(sample())
    assert repository.put(sample()) == ref
    repository.bind("HAND", ref)
    sizes = repository.sizes()
    slot_bytes = sum(p.stat().st_size for p in tmp_path.glob("slot-*.json"))
    assert sizes["raw_budget_bytes"] == ref["byte_count"] + slot_bytes
    assert sizes["physical_bytes"] == ref["stored_byte_count"] + slot_bytes
    assert sizes["physical_bytes"] < sizes["raw_budget_bytes"]


def test_measurement_report_is_append_only(tmp_path):
    target = tmp_path / "measurement.json"
    write_new(target, {"one": 1})
    before = target.read_bytes()
    with pytest.raises(FileExistsError):
        write_new(target, {"two": 2})
    assert target.read_bytes() == before

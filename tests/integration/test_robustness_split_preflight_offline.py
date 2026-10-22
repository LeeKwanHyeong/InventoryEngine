from copy import deepcopy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from dsio_inventory_engine.evaluate_replenishment.robustness.resource_execution import (
    ControlledCatalogExecutor,
)
from dsio_inventory_engine.evaluate_replenishment.robustness.split_calibration import (
    MeasureHandCatalogResources,
)
from dsio_inventory_engine.evaluate_replenishment.robustness.split_preflight import (
    MODES,
    build_split_work_plan,
    project_split_resources,
)
from dsio_inventory_engine.infrastructure.robustness_resource_pool import (
    LocalRobustnessResourcePool,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from scripts.verify_robustness_shared_storage import EXPECTED_RESULTS
from tests.integration.test_robustness_durable_offline import prepared
from tests.support.robustness_resource_fixtures import catalog_factory
from tests.support.split_preflight_fixtures import protocol


def test_actual_all_modes_use_one_pool_full_catalog_and_preserve_original_hashes(tmp_path):
    pool = LocalRobustnessResourcePool(tmp_path)
    service = MeasureHandCatalogResources(ControlledCatalogExecutor(pool, catalog_factory))
    values = []
    for mode in MODES:
        _, assembly, path = prepared(mode=mode)
        original_assembly = deepcopy(assembly)
        value = service.execute(mode, assembly, path.to_dict(), EXPECTED_RESULTS[mode], seconds=180)
        assert assembly == original_assembly
        assert value["candidate_count"] == MODES[mode]
        assert value["psi_row_count"] == MODES[mode] * 65
        assert value["result_hash"] == EXPECTED_RESULTS[mode]
        assert value["raw_bytes_added"] > value["physical_bytes_added"] > 0
        assert value["psi_recalculation_status"] == value["integrity_status"] == "VERIFIED"
        assert all(
            value[k] is False
            for k in (
                "automatic_publish_allowed",
                "automatic_order_allowed",
                "operational_policy_approved",
                "database_writes",
                "official_paths_generated",
            )
        )
        values.append(value)
    assert len({v["pool_id"] for v in values}) == 1
    source = protocol()
    report = project_split_resources(source, build_split_work_plan(source), values)
    schema = json.loads(
        (
            Path(__file__).resolve().parents[2] / "schemas/robustness_split_preflight.schema.json"
        ).read_text()
    )
    Draft202012Validator(schema).validate(report)
    assert report["status"] == "BLOCKED_INCOMPLETE_MEASUREMENTS"
    assert report["full_raw_bytes"] is None and report["official_execution_allowed"] is False
    assert sum(v["raw_bytes_added"] for v in values) < pool.inspect()["raw_bytes"] <= 20_000_000_000
    assert len(list((tmp_path / "quarantine").glob("q-*.bin"))) == 9


@pytest.mark.parametrize(
    "fault",
    ["official_period", "official_path", "partial_catalog", "unknown_result", "storage_version"],
)
def test_invalid_manual_probe_fails_before_starting_any_worker(tmp_path, monkeypatch, fault):
    _, assembly, path = prepared()
    realization = path.to_dict()
    pool = LocalRobustnessResourcePool(tmp_path)
    executor = ControlledCatalogExecutor(pool, catalog_factory)
    monkeypatch.setattr(
        executor, "execute", lambda *args, **kwargs: pytest.fail("WORKER_MUST_NOT_START")
    )
    expected = EXPECTED_RESULTS["LEGACY_STYLE"]
    if fault == "official_period":
        assembly["mathematical_request"]["recommendation"]["canonical_input"]["context"][
            "plan_start_date"
        ] = "2037-01-05"
    elif fault == "official_path":
        realization["path_id"] = "ROBUST-A-SELECTION"
    elif fault == "partial_catalog":
        assembly["complete_candidates"].pop()
    elif fault == "unknown_result":
        expected = None
    else:
        executor.storage_version = "2.0.0"
    with pytest.raises(InventoryInputError):
        MeasureHandCatalogResources(executor).execute("INVALID", assembly, realization, expected)
    assert not list(tmp_path.glob("artifacts-*"))


def test_used_measurement_store_is_not_silently_reused_to_report_zero_storage(tmp_path):
    _, assembly, path = prepared()
    pool = LocalRobustnessResourcePool(tmp_path)
    root = pool.store_root("USED")
    # This test-owned canary must be preserved, not reset to fake an empty measurement root.
    canary = root / "keep.txt"
    canary.write_bytes(b"preserve")
    service = MeasureHandCatalogResources(ControlledCatalogExecutor(pool, catalog_factory))
    with pytest.raises(InventoryInputError, match="MEASUREMENT_STORE_NOT_EMPTY"):
        service.execute("USED", assembly, path.to_dict(), EXPECTED_RESULTS["LEGACY_STYLE"])
    assert canary.read_bytes() == b"preserve"

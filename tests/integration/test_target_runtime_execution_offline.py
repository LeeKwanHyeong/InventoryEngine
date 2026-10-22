"""Local calls, exact-pointer reads, denial and fresh-instance recovery only."""

import copy
from unittest.mock import patch

import pytest

from dsio_inventory_engine.infrastructure.sqlite import SqliteInventoryEvidenceUnitOfWork
from dsio_inventory_engine.inventory_contracts.runtime import InventoryRuntimeExecutionRequest
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from dsio_inventory_engine.inventory_evidence.target_result_link import KEY
from dsio_inventory_engine.run_inventory.target_result_execution import (
    InventoryTargetResultExecution,
)
from tests.support.runtime_fixtures import reseal_runtime_site_binding
from tests.support.target_runtime_fixtures import runtime_world
from tests.unit.test_target_execution import ReferenceKernel


def execute(w):
    return w.runtime.execute(request=w.request, sources=w.pointers)


def restart(w):
    return InventoryTargetResultExecution(
        **(w.runtime_kwargs | {"evidence_repository": SqliteInventoryEvidenceUnitOfWork(w.db_path)})
    )


@pytest.mark.parametrize(
    "mode", ["ABC_V1", "ABC_XYZ_SHARED_BUDGET", "LEGACY_STYLE", "WIDE_REFERENCE"]
)
def test_call_read_and_recover_keep_original_bundle_and_gates(tmp_path, mode):
    w = runtime_world(tmp_path, ReferenceKernel(), mode=mode)
    before = w.store.read_artifact(w.persisted.bundle_reference)
    result = execute(w)
    pointer = result["result_pointer"]
    assert pointer["sources"] == w.pointers
    assert result["verification_status"] == "RECOMPUTED_VERIFIED"
    assert result["bundle"] == w.run.result_bundle
    assert result["result"]["order_plan"]["selected_item_count"] == 1
    assert result["result"]["order_plan"]["recommended_orders"]
    assert not result["automatic_publish_allowed"] and not result["automatic_order_allowed"]
    with patch.object(w.store, "commit_artifacts", side_effect=AssertionError("read wrote")):
        assert w.runtime.read(request=w.request, result_pointer=pointer) == result
    assert w.runtime.recover(request=w.request, sources=w.pointers) == result
    assert w.store.read_artifact(w.persisted.bundle_reference) == before


@pytest.mark.parametrize("field", ["bundle", "target_input", "target_manifest"])
def test_wrong_source_pointer_fails_before_artifact_write(tmp_path, field):
    w = runtime_world(tmp_path, ReferenceKernel())
    sources = copy.deepcopy(w.pointers)
    sources[field]["content_hash"] = "0" * 64
    with patch.object(w.store, "commit_artifacts", side_effect=AssertionError("wrote")):
        with pytest.raises(InventoryInputError):
            w.runtime.recover(request=w.request, sources=sources)


@pytest.mark.parametrize(
    "field", ["site_cd", "attempt_no", "site_row_version", "planning_cycle_id"]
)
def test_changed_claim_cannot_read_or_recover(tmp_path, field):
    w = runtime_world(tmp_path, ReferenceKernel())
    pointer = execute(w)["result_pointer"]
    raw = copy.deepcopy(w.request.to_dict())
    if field in {"attempt_no", "site_row_version"}:
        raw[field] += 1
    elif field == "site_cd":
        raw["claim"]["scope"][field] = "V999"
    else:
        raw["claim"][field] = "00000000-0000-4000-8000-000000000099"
    with patch.object(w.store, "commit_artifacts", side_effect=AssertionError("wrote")):
        with pytest.raises(InventoryInputError):
            request = InventoryRuntimeExecutionRequest.from_dict(reseal_runtime_site_binding(raw))
            w.runtime.read(request=request, result_pointer=pointer)
        with pytest.raises(InventoryInputError):
            request = InventoryRuntimeExecutionRequest.from_dict(reseal_runtime_site_binding(raw))
            w.runtime.recover(request=request, sources=w.pointers)


@pytest.mark.parametrize("field", ["target_result_link", "projection"])
@pytest.mark.parametrize("key", ["artifact_reference", "content_hash"])
def test_exact_result_pointer_required(tmp_path, field, key):
    w = runtime_world(tmp_path, ReferenceKernel())
    pointer = copy.deepcopy(execute(w)["result_pointer"])
    pointer[field][key] = "0" * 64 if key == "content_hash" else "artifact:wrong"
    with pytest.raises(InventoryInputError):
        w.runtime.read(request=w.request, result_pointer=pointer)


@pytest.mark.parametrize("boundary", ["link", "projection"])
def test_response_loss_after_commit_recovers_in_new_instance(tmp_path, boundary):
    w = runtime_world(tmp_path, ReferenceKernel())
    # Fixture store path is explicit; never reuse an operational evidence database.
    commit = w.store.commit_artifacts

    def interrupted(artifacts):
        value = commit(artifacts)
        keys = {a.contract_key for a in artifacts}
        if (boundary == "link" and KEY in keys) or (
            boundary == "projection" and "inventory.dsim_projection" in keys
        ):
            raise OSError("offline response loss")
        return value

    with patch.object(w.store, "commit_artifacts", side_effect=interrupted):
        with pytest.raises(OSError):
            execute(w)
    new = restart(w)
    restored = new.recover(request=w.request, sources=w.pointers)
    assert new.read(request=w.request, result_pointer=restored["result_pointer"]) == restored


def test_missing_reader_build_or_completed_target_is_not_ignored(tmp_path):
    w = runtime_world(tmp_path, ReferenceKernel())
    wrong = copy.deepcopy(w.build)
    wrong["content_hash"] = "0" * 64
    with pytest.raises(InventoryInputError, match="BUILD_MISMATCH"):
        InventoryTargetResultExecution(**(w.runtime_kwargs | {"expected_build": wrong}))
    with patch.object(w.target_repo, "lookup", return_value=None):
        with pytest.raises(InventoryInputError, match="STORED_POINTER_MISMATCH"):
            execute(w)


def test_no_selection_and_block_remain_non_operational(tmp_path):
    w = runtime_world(tmp_path, ReferenceKernel(), mode="LEGACY_STYLE", all_failed=True)
    result = execute(w)
    assert result["result"]["order_plan"]["selected_item_count"] == 0
    assert result["result"]["order_plan"]["recommended_orders"] == []
    assert result["target_result_link"]["counts"]["candidate_count"] == 32
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    with pytest.raises(InventoryInputError, match="ITEM_POLICY_RECOMMENDATION_CALCULATION_BLOCKED"):
        runtime_world(blocked, ReferenceKernel(), fsn_action="BLOCK")


def test_operational_projection_cannot_substitute_target_projection(tmp_path):
    w = runtime_world(tmp_path, ReferenceKernel())
    result = execute(w)
    from dsio_inventory_engine.inventory_evidence.projection_source import DsimSourceProjector
    from dsio_inventory_engine.inventory_evidence.projection import PersistDsimProjectionUseCase

    projector = DsimSourceProjector(catalog=w.catalog, bundle_verifier=w.runtime.bundle_reader)
    plain = PersistDsimProjectionUseCase(projector=projector, store=w.store).execute(
        request=w.request, bundle_reference=w.persisted.bundle_reference
    )
    pointer = copy.deepcopy(result["result_pointer"])
    pointer["projection"] = {k: v for k, v in plain.items() if k != "replayed"}
    with pytest.raises(InventoryInputError):
        w.runtime.read(request=w.request, result_pointer=pointer)

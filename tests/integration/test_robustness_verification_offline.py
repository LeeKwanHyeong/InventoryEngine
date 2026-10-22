"""Actual local files and PSI replay on manual 2055 paths; no official study or DB."""

import shutil
from unittest.mock import patch

import pytest

from dsio_inventory_engine.evaluate_replenishment.robustness.contracts import sealed
from dsio_inventory_engine.evaluate_replenishment.robustness.durable import (
    RunDurableItemCatalogUseCase,
    _slot,
)
from dsio_inventory_engine.evaluate_replenishment.robustness.integrity_read import (
    InspectDurableItemCatalogUseCase,
)
from dsio_inventory_engine.infrastructure.robustness_artifacts import LocalRobustnessArtifactStore
from dsio_inventory_engine.infrastructure.robustness_compression import (
    CompressedRobustnessArtifactStore,
)
from dsio_inventory_engine.infrastructure.robustness_shared_artifacts import (
    SharedInputRobustnessArtifactStore,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from dsio_inventory_engine.recommend_replenishment.application.policy_search import (
    CandidateStrategy,
)
from scripts.verify_robustness_shared_storage import EXPECTED_RESULTS
from tests.integration.test_robustness_durable_offline import prepared, service, run, file_identity
from tests.support.robustness_verification_fixtures import (
    SnapshotRepository,
    replace_document,
    replace_manifest,
)


@pytest.fixture(scope="module", params=list(EXPECTED_RESULTS))
def completed(request, tmp_path_factory):
    case, assembly, path = prepared(mode=request.param)
    root = tmp_path_factory.mktemp("verification-" + request.param)
    runner = service(root, case, SharedInputRobustnessArtifactStore)
    receipt = run(runner, assembly, path)
    return request.param, root, case, runner, receipt


def bindings(receipt):
    return {
        "expected_input_hash": receipt["input_reference"]["content_hash"],
        "expected_result_hash": receipt["result_hash"],
    }


def test_fast_view_reads_actual_complete_artifacts_without_any_computation(completed):
    mode, root, _, runner, receipt = completed
    before = file_identity(root)
    with (
        patch(
            "dsio_inventory_engine.evaluate_replenishment.robustness.durable.prepare_context",
            side_effect=AssertionError("PREPARATION_FORBIDDEN"),
        ),
        patch.object(CandidateStrategy, "decide", side_effect=AssertionError("STRATEGY_FORBIDDEN")),
        patch(
            "dsio_inventory_engine.evaluate_replenishment.robustness.item_execution_steps.score_trace",
            side_effect=AssertionError("METRICS_FORBIDDEN"),
        ),
    ):
        # The inspector needs only a Repository: no producer/model/runner capability.
        direct = InspectDurableItemCatalogUseCase(runner.repository).execute(
            "HAND", **bindings(receipt)
        )
        view = runner.inspect("HAND", **bindings(receipt))
    assert view == direct
    assert view["result_hash"] == EXPECTED_RESULTS[mode]
    assert view["verification_level"] == "ARTIFACT_INTEGRITY"
    assert (
        view["psi_recalculation_status"]
        == view["source_recalculation_status"]
        == view["metrics_recalculation_status"]
        == "NOT_RUN"
    )
    count = 88 if mode == "WIDE_REFERENCE" else 32
    assert view["counts"]["candidate_count"] == view["counts"]["successful_count"] == count
    assert view["counts"]["psi_row_count"] == 65 * count
    assert file_identity(root) == before


def test_full_view_replays_every_candidate_and_fast_lookup_never_borrows_full_status(completed):
    _, root, _, runner, receipt = completed
    before = file_identity(root)
    actual = CandidateStrategy.decide
    with patch.object(CandidateStrategy, "decide", autospec=True, side_effect=actual) as decide:
        full = runner.verify_recalculation("HAND", **bindings(receipt))
    assert decide.call_count == full["counts"]["candidate_count"] * 65
    assert full["verification_level"] == "FULL_PSI_RECALCULATION"
    assert (
        full["source_recalculation_status"]
        == full["psi_recalculation_status"]
        == full["metrics_recalculation_status"]
        == "VERIFIED"
    )
    fast = runner.inspect("HAND", **bindings(receipt))
    assert fast["result_hash"] == full["result_hash"] == receipt["result_hash"]
    assert fast["content_hash"] != full["content_hash"]
    assert fast["psi_recalculation_status"] == "NOT_RUN"
    assert full["automatic_publish_allowed"] is full["automatic_order_allowed"] is False
    assert file_identity(root) == before


@pytest.mark.parametrize(
    "store_type",
    [
        LocalRobustnessArtifactStore,
        CompressedRobustnessArtifactStore,
        SharedInputRobustnessArtifactStore,
    ],
)
def test_old_formats_and_failed_candidates_remain_readable_without_rewriting(tmp_path, store_type):
    case, assembly, path = prepared(max_target="0")
    runner = service(tmp_path, case, store_type)
    receipt = run(runner, assembly, path)
    before = file_identity(tmp_path)
    # A V3-aware Reader still supports old V1/V2 references unchanged.
    new = service(tmp_path, case, SharedInputRobustnessArtifactStore)
    fast = new.inspect("HAND", **bindings(receipt))
    full = new.verify_recalculation("HAND", **bindings(receipt))
    assert (
        fast["counts"]
        == full["counts"]
        == {
            "candidate_count": 32,
            "failed_count": 32,
            "successful_count": 0,
            "psi_row_count": 0,
            "leaf_count": 2,
        }
    )
    assert (
        new.recover("HAND", expected_input_hash=bindings(receipt)["expected_input_hash"]) == receipt
    )
    assert new.read("HAND", **bindings(receipt))["content_hash"] == receipt["result_hash"]
    assert file_identity(tmp_path) == before


@pytest.fixture(scope="module")
def legacy_store(tmp_path_factory):
    case, assembly, path = prepared()
    root = tmp_path_factory.mktemp("verification-legacy-tamper")
    runner = service(root, case, SharedInputRobustnessArtifactStore)
    return root, case, runner, run(runner, assembly, path)


@pytest.mark.parametrize(
    "fault", ["missing_shared", "corrupt_shared", "corrupt_leaf", "missing_complete"]
)
def test_physical_damage_after_successful_lookup_is_not_hidden_by_a_cached_status(
    tmp_path, legacy_store, fault
):
    original, case, _, receipt = legacy_store
    root = tmp_path / "copy"
    shutil.copytree(original, root)
    runner = service(root, case, SharedInputRobustnessArtifactStore)
    runner.inspect("HAND", **bindings(receipt))
    if fault == "missing_complete":
        target = root / runner.repository._slot_name(_slot("HAND", "COMPLETE"))
        target.unlink()
    elif fault in ("missing_shared", "corrupt_shared"):
        target = next(root.glob("shared-*.gz"))
        if fault == "missing_shared":
            target.unlink()
        else:
            target.write_bytes(target.read_bytes()[:-3])
    else:
        manifest = runner.repository.read(receipt["manifest_reference"])
        target = root / (
            "object-" + manifest["leaves"][0]["reference"]["content_hash"] + ".shared.json.gz"
        )
        target.write_bytes(target.read_bytes()[:-3])
    before = file_identity(root)
    with pytest.raises(InventoryInputError):
        runner.inspect("HAND", **bindings(receipt))
    with pytest.raises(InventoryInputError):
        runner.verify_recalculation("HAND", **bindings(receipt))
    assert file_identity(root) == before


def test_consistently_resealed_wrong_cost_is_integrity_only_and_fails_actual_recalculation(
    legacy_store,
):
    _, _, original, receipt = legacy_store
    repository = SnapshotRepository(original.repository, receipt)
    manifest = repository.read(receipt["manifest_reference"])
    header = repository.read(manifest["header_reference"])
    root = header["result_header"]
    card = root["path_scorecards"][0]
    card["score"]["cost"]["total_cost"] = "999999999"
    root["path_scorecards"][0] = sealed(
        card["contract_id"], {k: v for k, v in card.items() if k != "content_hash"}
    )
    leaves = (repository.read(entry["reference"]) for entry in manifest["leaves"])
    root["content_hash"] = InspectDurableItemCatalogUseCase._result_hash(root, leaves)
    header["result_hash"] = manifest["result_hash"] = root["content_hash"]
    manifest["header_reference"] = replace_document(repository, header)
    replace_manifest(repository, manifest)
    runner = RunDurableItemCatalogUseCase(original.runner, repository)
    args = {**bindings(receipt), "expected_result_hash": root["content_hash"]}
    fast = runner.inspect("HAND", **args)
    assert (
        fast["artifact_integrity_status"] == "VERIFIED"
        and fast["metrics_recalculation_status"] == "NOT_RUN"
    )
    with pytest.raises(InventoryInputError, match="DURABLE_HEADER_REPLAY_MISMATCH"):
        runner.verify_recalculation("HAND", **args)


def test_changed_implementation_cannot_reuse_an_earlier_verification_view(legacy_store):
    _, _, runner, receipt = legacy_store
    runner.inspect("HAND", **bindings(receipt))
    with patch.object(
        CandidateStrategy, "decide", side_effect=RuntimeError("HAND_IMPLEMENTATION_CHANGED")
    ) as decide:
        runner.inspect("HAND", **bindings(receipt))
        assert decide.call_count == 0
        with pytest.raises(InventoryInputError):
            runner.verify_recalculation("HAND", **bindings(receipt))
        assert decide.call_count > 0


def test_fsn_block_remains_blocked_for_both_views_and_original_result(tmp_path):
    case, assembly, path = prepared(gate="BLOCK")
    runner = service(tmp_path, case, SharedInputRobustnessArtifactStore)
    receipt = run(runner, assembly, path)
    full = runner.verify_recalculation("HAND", **bindings(receipt))
    fast = runner.inspect("HAND", **bindings(receipt))
    assert full["automatic_publish_allowed"] is full["automatic_order_allowed"] is False
    assert fast["automatic_publish_allowed"] is fast["automatic_order_allowed"] is False
    result = runner.read("HAND", **bindings(receipt))
    for leaf in result["leaves"]:
        for child in leaf["candidate_executions"]:
            assert (
                child["causal_result"]["effective_policy_admission"]["effective_order_action"]
                == "BLOCK"
            )
            assert child["causal_result"]["recommended_orders"] == []

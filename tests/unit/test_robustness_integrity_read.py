from copy import deepcopy
from unittest.mock import patch

import pytest

from dsio_inventory_engine.evaluate_replenishment.robustness.durable import (
    RunDurableItemCatalogUseCase,
    _slot,
)
from dsio_inventory_engine.evaluate_replenishment.robustness.integrity_read import (
    InspectDurableItemCatalogUseCase,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from tests.integration.test_robustness_durable_offline import prepared, service, run
from tests.support.robustness_verification_fixtures import (
    SnapshotRepository,
    replace_manifest,
    replace_document,
)


@pytest.fixture(scope="module")
def original(tmp_path_factory):
    case, assembly, path = prepared(max_target="0")
    runner = service(tmp_path_factory.mktemp("integrity-unit"), case)
    receipt = run(runner, assembly, path)
    return SnapshotRepository(runner.repository, receipt), receipt


def inspect(repository, receipt, **kwargs):
    return InspectDurableItemCatalogUseCase(repository).execute(
        "HAND",
        expected_input_hash=receipt["input_reference"]["content_hash"],
        **kwargs,
    )


def test_incremental_result_hash_matches_original_with_unicode_and_escapes():
    header = {"z": '한글\\"\n', "content_hash": "not_included", "a": [None, True, "é"]}
    leaves = [{"b": "日本語", "a": 1}, {"a": "😀", "b": []}]
    assert InspectDurableItemCatalogUseCase._result_hash(header, iter(leaves)) == digest(
        {"z": header["z"], "a": header["a"], "leaves": leaves}
    )


def test_inspection_reads_every_object_without_source_or_psi_and_has_no_write_capability(original):
    repository, receipt = deepcopy(original)
    with (
        patch(
            "dsio_inventory_engine.evaluate_replenishment.robustness.item_projection.PrepareItemCatalogUseCase.verify",
            side_effect=AssertionError("SOURCE_REPLAY_FORBIDDEN"),
        ),
        patch(
            "dsio_inventory_engine.evaluate_replenishment.robustness.causal_simulation.RunCausalPsiUseCase.execute",
            side_effect=AssertionError("PSI_REPLAY_FORBIDDEN"),
        ),
        patch(
            "dsio_inventory_engine.evaluate_replenishment.robustness.item_execution_steps.score_trace",
            side_effect=AssertionError("METRIC_REPLAY_FORBIDDEN"),
        ),
    ):
        view = inspect(repository, receipt)
    assert set(repository.reads) == set(repository.objects)
    assert view["verification_level"] == "ARTIFACT_INTEGRITY"
    assert (
        view["psi_recalculation_status"]
        == view["source_recalculation_status"]
        == view["metrics_recalculation_status"]
        == "NOT_RUN"
    )
    assert view["counts"] == {
        "candidate_count": 32,
        "failed_count": 32,
        "successful_count": 0,
        "psi_row_count": 0,
        "leaf_count": 2,
    }


@pytest.mark.parametrize(
    "fault",
    [
        "missing_complete",
        "missing_checkpoint",
        "wrong_execution",
        "wrong_input_ref",
        "manifest_count",
        "boolean_count",
        "entry_count",
        "entry_hash",
        "leaf_order",
        "boolean_leaf_index",
        "parent_hash",
        "catalog_hash",
        "profile",
        "duplicate_id",
        "missing_leaf",
        "header_result",
        "header_inline_leaves",
        "trace_count",
        "scorecard_count",
        "wrong_provenance",
    ],
)
def test_resealed_structural_faults_fail_instead_of_looking_verified(original, fault):
    repository, receipt = deepcopy(original)
    manifest = repository.read(receipt["manifest_reference"])
    if fault == "missing_complete":
        del repository.slots[_slot("HAND", "COMPLETE")]
    elif fault == "missing_checkpoint":
        del repository.slots[_slot("HAND", "LEAF", 1)]
    elif fault in (
        "header_result",
        "header_inline_leaves",
        "trace_count",
        "scorecard_count",
        "wrong_provenance",
    ):
        header = repository.read(manifest["header_reference"])
        if fault == "header_result":
            header["result_hash"] = "0" * 64
        elif fault == "header_inline_leaves":
            header["result_header"]["leaves"] = []
        elif fault == "trace_count":
            header["result_header"]["evaluator_bank_fragment"]["traces"].pop()
        elif fault == "scorecard_count":
            header["result_header"]["path_scorecards"].pop()
        else:
            header["result_header"]["source_manifest"]["leaf_hashes"] = []
        manifest["header_reference"] = replace_document(repository, header)
        replace_manifest(repository, manifest)
    else:
        if fault == "wrong_execution":
            manifest["execution_id"] = "OTHER"
        elif fault == "wrong_input_ref":
            manifest["input_reference"]["content_hash"] = "0" * 64
        elif fault == "manifest_count":
            manifest["failed_count"] = 31
        elif fault == "boolean_count":
            manifest["successful_count"] = False
        elif fault == "entry_count":
            manifest["leaves"][0]["candidate_count"] = 15
        elif fault == "entry_hash":
            manifest["leaves"][0]["candidate_execution_hashes"][0] = "0" * 64
        elif fault == "leaf_order":
            manifest["leaves"].reverse()
        elif fault == "boolean_leaf_index":
            manifest["leaves"][0]["leaf_index"] = False
        elif fault == "parent_hash":
            manifest["parent_classification_hash"] = "0" * 64
        elif fault == "catalog_hash":
            manifest["complete_catalog_hash"] = "0" * 64
        elif fault == "profile":
            manifest["profile_id"] = "OTHER"
        elif fault == "duplicate_id":
            manifest["expected_candidate_ids"][1] = manifest["expected_candidate_ids"][0]
        elif fault == "missing_leaf":
            manifest["leaves"].pop()
        replace_manifest(repository, manifest)
    with pytest.raises(InventoryInputError):
        inspect(repository, receipt)


@pytest.mark.parametrize("target", ["input", "result"])
def test_wrong_expected_binding_never_reuses_a_previous_integrity_view(original, target):
    repository, receipt = deepcopy(original)
    inspect(repository, receipt)
    if target == "input":
        with pytest.raises(InventoryInputError, match="INPUT_BINDING"):
            InspectDurableItemCatalogUseCase(repository).execute(
                "HAND", expected_input_hash="0" * 64
            )
    else:
        with pytest.raises(InventoryInputError, match="RESULT_BINDING"):
            inspect(repository, receipt, expected_result_hash="0" * 64)


def test_each_lookup_rechecks_damage_instead_of_reusing_previous_verified_state(original):
    repository, receipt = deepcopy(original)
    inspect(repository, receipt)
    header_ref = repository.read(receipt["manifest_reference"])["header_reference"]
    repository.objects[header_ref["content_hash"]]["result_hash"] = "0" * 64
    with pytest.raises(InventoryInputError, match="HASH_MISMATCH"):
        inspect(repository, receipt)


def test_full_verification_rejects_a_manifest_changed_during_recalculation(original, tmp_path):
    repository, receipt = deepcopy(original)
    case, _, _ = prepared(max_target="0")
    runner = RunDurableItemCatalogUseCase(service(tmp_path, case).runner, repository)
    actual_read = runner.read

    def read_then_change_manifest(*args, **kwargs):
        result = actual_read(*args, **kwargs)
        manifest = repository.read(receipt["manifest_reference"])
        manifest["hand_read_marker"] = "CHANGED_AFTER_ACTUAL_REPLAY"
        replace_manifest(repository, manifest)
        return result

    with patch.object(runner, "read", side_effect=read_then_change_manifest) as replay:
        with pytest.raises(InventoryInputError, match="DURABLE_VERIFICATION_BINDING_CHANGED"):
            runner.verify_recalculation(
                "HAND",
                expected_input_hash=receipt["input_reference"]["content_hash"],
                expected_result_hash=receipt["result_hash"],
            )
    assert replay.call_count == 1

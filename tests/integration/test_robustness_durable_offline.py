"""Durable hand cases only; no official paths, database or operational publication."""

from copy import deepcopy
import os
import shutil
import subprocess
import sys
from unittest.mock import patch

import pytest

from dsio_inventory_engine.evaluate_replenishment.robustness.contracts import sealed
from dsio_inventory_engine.evaluate_replenishment.robustness.durable import (
    RunDurableItemCatalogUseCase,
    _slot,
)
from dsio_inventory_engine.evaluate_replenishment.robustness.item_execution import (
    RunItemCatalogUseCase,
)
from dsio_inventory_engine.evaluate_replenishment.robustness.source_binding import (
    RealizedDemandPath,
)
from dsio_inventory_engine.infrastructure.robustness_artifacts import LocalRobustnessArtifactStore
from dsio_inventory_engine.inventory_contracts.replenishment import RecommendationRequest
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, canonical_json
from tests.support.robustness_item_fixtures import DEV, item_case, prepare_case
from tests.support.robustness_source_fixtures import path_for


ORIGINAL_RESULT_HASH = "fbd77610f2019fb44b68dbf12a3667dbe6f31238e2d428909355bc7e7f1c67c6"


def prepared(**kwargs):
    case = item_case(**kwargs)
    assembly = prepare_case(case)
    rec = RecommendationRequest.from_dict(assembly["mathematical_request"]["recommendation"])
    path = path_for(rec, key="HAND-CATALOG-PATH", seed=71)
    path = RealizedDemandPath.from_dict({**path.to_dict(), "split": "NOMINAL"})
    return case, assembly, path


def service(root, case, store_type=LocalRobustnessArtifactStore):
    return RunDurableItemCatalogUseCase(
        RunItemCatalogUseCase(DEV, case["producer"]), store_type(root)
    )


def run(runner, assembly, path, execution_id="HAND", **kwargs):
    return runner.run(
        execution_id,
        assembly,
        expected_assembly_hash=assembly["content_hash"],
        realization=path,
        expected_realization_hash=path.content_hash,
        **kwargs,
    )


def file_identity(root):
    return {
        p.name: (p.stat().st_ino, p.stat().st_mtime_ns, p.stat().st_size)
        for p in root.iterdir()
        if p.is_file()
    }


def test_actual_32_candidates_preserve_original_hash_and_idempotent_receipt(tmp_path):
    case, assembly, path = prepared()
    original_parent = deepcopy(case["parent_classification"])
    runner = service(tmp_path, case)
    receipt = run(runner, assembly, path)
    assert receipt["result_hash"] == ORIGINAL_RESULT_HASH
    before = file_identity(tmp_path)
    assert run(runner, assembly, path) == receipt
    recovered = service(tmp_path, case).recover(
        "HAND", expected_input_hash=receipt["input_reference"]["content_hash"]
    )
    assert recovered == receipt
    result = service(tmp_path, case).read(
        "HAND",
        expected_input_hash=receipt["input_reference"]["content_hash"],
        expected_result_hash=ORIGINAL_RESULT_HASH,
    )
    manifest = runner.repository.read(receipt["manifest_reference"])
    assert [e["candidate_count"] for e in manifest["leaves"]] == [16, 16]
    assert (
        manifest["candidate_count"],
        manifest["successful_count"],
        manifest["failed_count"],
        manifest["psi_row_count"],
    ) == (32, 32, 0, 2080)
    assert manifest["expected_candidate_ids"] == [
        c["candidate_id"] for c in assembly["complete_candidates"]
    ]
    assert result["assembly"]["parent_classification"] == original_parent
    assert case["parent_classification"] == original_parent
    assert result["assembly"]["candidate_catalog"] == case["catalog"].to_dict()
    assert result["content_hash"] == ORIGINAL_RESULT_HASH
    assert result["durable_artifact_persisted"] is False  # Original pure hash stays unchanged.
    assert receipt["durable_local_artifact"] is True and receipt["status"] == "COMPLETE"
    assert result["official_paths_generated"] is receipt["official_paths_generated"] is False
    assert result["risk_aggregation_status"] == "NOT_RUN_REQUIRES_COMPLETE_SPLIT"
    assert result["selection_status"] == "NOT_RUN"
    for leaf in result["leaves"]:
        for child in leaf["candidate_executions"]:
            raw = child["causal_result"]
            assert raw["effective_policy_admission"]["effective_order_action"] == "REVIEW"
            assert raw["automatic_publish_allowed"] is raw["automatic_order_allowed"] is False
    assert before == file_identity(tmp_path)


def test_actual_88_candidates_remain_complete_above_flat_reader_limit(tmp_path):
    case, assembly, path = prepared(mode="WIDE_REFERENCE")
    runner = service(tmp_path, case)
    receipt = run(runner, assembly, path)
    result = runner.read("HAND", expected_input_hash=receipt["input_reference"]["content_hash"])
    manifest = runner.repository.read(receipt["manifest_reference"])
    assert [e["candidate_count"] for e in manifest["leaves"]] == [16, 16, 16, 16, 16, 8]
    assert (
        manifest["candidate_count"],
        manifest["successful_count"],
        manifest["failed_count"],
        manifest["psi_row_count"],
    ) == (88, 88, 0, 5720)
    ids = [c["candidate_id"] for c in assembly["complete_candidates"]]
    assert [i for entry in manifest["leaves"] for i in entry["candidate_ids"]] == ids
    assert len(set(ids)) == 88
    assert len(result["path_scorecards"]) == 88
    assert len(canonical_json(result).encode()) > 128_000_000
    assert max(p.stat().st_size for p in tmp_path.iterdir()) <= 128_000_000
    assert result["content_hash"] == receipt["result_hash"]


@pytest.fixture(scope="module")
def failed_store(tmp_path_factory):
    root = tmp_path_factory.mktemp("hand-failed-candidate-store")
    case, assembly, path = prepared(max_target="0")
    receipt = run(service(root, case), assembly, path)
    return root, case, assembly, path, receipt


def copied_store(tmp_path, failed_store):
    original, case, assembly, path, receipt = failed_store
    root = tmp_path / "artifacts"
    shutil.copytree(original, root)
    return root, service(root, case), assembly, path, receipt


def test_failed_candidates_are_preserved_not_dropped_from_complete_manifest(tmp_path, failed_store):
    _, runner, _, _, receipt = copied_store(tmp_path, failed_store)
    result = runner.read("HAND", expected_input_hash=receipt["input_reference"]["content_hash"])
    manifest = runner.repository.read(receipt["manifest_reference"])
    assert (
        manifest["candidate_count"],
        manifest["successful_count"],
        manifest["failed_count"],
        manifest["psi_row_count"],
    ) == (32, 0, 32, 0)
    assert len(result["path_scorecards"]) == 32
    for leaf in result["leaves"]:
        for child in leaf["candidate_executions"]:
            assert child["status"] == "FAILED" and child["causal_result"] is None
            assert child["failure_evidence"] and child["failure_reason"]


@pytest.mark.parametrize(
    "fault", ["missing_leaf", "missing_header", "corrupt_leaf", "missing_checkpoint"]
)
def test_committed_missing_or_corrupt_data_fails_closed(tmp_path, failed_store, fault):
    root, runner, _, _, receipt = copied_store(tmp_path, failed_store)
    manifest = runner.repository.read(receipt["manifest_reference"])
    ref = (
        manifest["header_reference"]
        if fault == "missing_header"
        else manifest["leaves"][0]["reference"]
    )
    target = root / ("object-" + ref["content_hash"] + ".json")
    if fault == "corrupt_leaf":
        payload = target.read_text().replace('"leaf_index":0', '"leaf_index":1')
        target.write_text(payload)
    elif fault == "missing_checkpoint":
        (root / runner.repository._slot_name(_slot("HAND", "LEAF", 0))).unlink()
    else:
        target.unlink()
    before = file_identity(root)
    with pytest.raises(InventoryInputError):
        runner.read("HAND", expected_input_hash=receipt["input_reference"]["content_hash"])
    with pytest.raises(InventoryInputError):
        runner.recover("HAND", expected_input_hash=receipt["input_reference"]["content_hash"])
    assert before == file_identity(root)  # Never silently repair a committed result.


def reseal_manifest(root, runner, receipt, value):
    forged = sealed(
        "inventory-robustness-durable-manifest",
        {
            k: v
            for k, v in value.items()
            if k not in {"content_hash", "contract_id", "contract_version", "protocol_hash"}
        },
    )
    ref = runner.repository.put(forged)
    key = _slot("HAND", "COMPLETE")
    # Simulated storage corruption in this isolated temporary test directory only.
    pointer = sealed("inventory-robustness-durable-slot", {"slot_key": key, "reference": ref})
    (root / runner.repository._slot_name(key)).write_text(canonical_json(pointer) + "\n")
    return ref


@pytest.mark.parametrize(
    "fault",
    [
        "missing_leaf",
        "duplicate_leaf",
        "reordered_leaf",
        "candidate_count",
        "candidate_ids",
        "child_hash",
        "psi_count",
        "failed_count",
        "scope",
        "world_hash",
        "header_hash",
        "extra_field",
        "boolean_count",
        "result_hash",
    ],
)
def test_self_resealed_manifest_cannot_hide_missing_candidates_or_changed_binding(
    tmp_path, failed_store, fault
):
    root, runner, _, _, receipt = copied_store(tmp_path, failed_store)
    value = runner.repository.read(receipt["manifest_reference"])
    if fault == "missing_leaf":
        value["leaves"].pop()
    elif fault == "duplicate_leaf":
        value["leaves"][1] = value["leaves"][0]
    elif fault == "reordered_leaf":
        value["leaves"].reverse()
    elif fault == "candidate_count":
        value["candidate_count"] -= 1
    elif fault == "boolean_count":
        value["successful_count"] = False  # False == 0 must not defeat canonical comparison.
    elif fault == "candidate_ids":
        value["expected_candidate_ids"].pop()
    elif fault == "child_hash":
        value["leaves"][0]["candidate_execution_hashes"][0] = "0" * 64
    elif fault == "psi_count":
        value["psi_row_count"] = 1
    elif fault == "failed_count":
        value["failed_count"] = 31
    elif fault == "scope":
        value["execution_id"] = "OTHER"
    elif fault == "world_hash":
        value["realization_hash"] = "0" * 64
    elif fault == "header_hash":
        value["header_reference"] = receipt["input_reference"]
    elif fault == "result_hash":
        value["result_hash"] = "0" * 64
    else:
        value["extra"] = "not_in_closed_contract"
    reseal_manifest(root, runner, receipt, value)
    with pytest.raises(InventoryInputError):
        runner.read("HAND", expected_input_hash=receipt["input_reference"]["content_hash"])


def test_changed_input_cannot_reuse_logical_reference(tmp_path, failed_store):
    root, runner, assembly, path, receipt = copied_store(tmp_path, failed_store)
    before = (root / runner.repository._slot_name(_slot("HAND", "COMPLETE"))).read_bytes()
    with pytest.raises(InventoryInputError, match="CONFLICT"):
        run(runner, assembly, path, profile_id="PRICE_HOLDING_HIGH")
    with pytest.raises(InventoryInputError, match="INPUT_BINDING_MISMATCH"):
        runner.recover("HAND", expected_input_hash="0" * 64)
    with pytest.raises(InventoryInputError, match="RESULT_BINDING_MISMATCH"):
        runner.read(
            "HAND",
            expected_input_hash=receipt["input_reference"]["content_hash"],
            expected_result_hash="0" * 64,
        )
    assert (root / runner.repository._slot_name(_slot("HAND", "COMPLETE"))).read_bytes() == before


@pytest.mark.parametrize("boundary", ["blob", "leaf", "header", "manifest", "complete"])
def test_interrupted_boundaries_resume_from_sealed_input(tmp_path, boundary):
    case, assembly, path = prepared(max_target="0")

    class InterruptedStore(LocalRobustnessArtifactStore):
        def put(self, value):
            ref = super().put(value)
            trigger = {
                "blob": "item-leaf",
                "header": "durable-header",
                "manifest": "durable-manifest",
            }.get(boundary)
            if trigger and value["contract_id"] == "inventory-robustness-" + trigger:
                raise OSError("HAND_INTERRUPTION")
            return ref

        def bind(self, key, ref):
            super().bind(key, ref)
            if (boundary == "leaf" and key.endswith(":LEAF:0")) or (
                boundary == "complete" and key.endswith(":COMPLETE")
            ):
                raise OSError("HAND_INTERRUPTION")

    with pytest.raises(OSError, match="HAND_INTERRUPTION"):
        run(service(tmp_path, case, InterruptedStore), assembly, path)
    runner = service(tmp_path, case)
    ref = runner.repository.lookup(_slot("HAND", "INPUT"))
    assert ref is not None
    if boundary != "complete":
        assert runner.repository.lookup(_slot("HAND", "COMPLETE")) is None
        with pytest.raises(InventoryInputError, match="INCOMPLETE"):
            runner.read("HAND", expected_input_hash=ref["content_hash"])
    before = file_identity(tmp_path)
    receipt = runner.recover("HAND", expected_input_hash=ref["content_hash"])
    assert receipt == runner.recover("HAND", expected_input_hash=ref["content_hash"])
    for key, identity in before.items():
        assert file_identity(tmp_path)[key] == identity
    result = runner.read("HAND", expected_input_hash=ref["content_hash"])
    assert len(result["path_scorecards"]) == 32
    assert len(result["leaves"]) == 2
    assert result["content_hash"] == receipt["result_hash"]


def test_actual_process_exit_after_first_checkpoint_recovers_without_live_input(tmp_path):
    code = """
import os, sys
from pathlib import Path
from tests.integration.test_robustness_durable_offline import prepared, service, run
from dsio_inventory_engine.infrastructure.robustness_artifacts import LocalRobustnessArtifactStore
class StopAfterCheckpoint(LocalRobustnessArtifactStore):
    def bind(self, key, ref):
        super().bind(key, ref)
        if key.endswith(":LEAF:0"):
            os._exit(97)
case, assembly, path = prepared()
run(service(Path(sys.argv[1]), case, StopAfterCheckpoint), assembly, path)
"""
    completed = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=120,
        env=dict(os.environ),
    )
    assert completed.returncode == 97, completed.stderr
    store = LocalRobustnessArtifactStore(tmp_path)
    input_ref = store.lookup(_slot("HAND", "INPUT"))
    assert input_ref is not None and store.lookup(_slot("HAND", "LEAF", 0)) is not None
    assert store.lookup(_slot("HAND", "LEAF", 1)) is store.lookup(_slot("HAND", "COMPLETE")) is None
    before = file_identity(tmp_path)
    # No original assembly/path survives the process. No DB/latest Source lookup.
    runner = service(tmp_path, item_case())
    receipt = runner.recover("HAND", expected_input_hash=input_ref["content_hash"])
    assert receipt["result_hash"] == ORIGINAL_RESULT_HASH
    assert all(file_identity(tmp_path)[key] == identity for key, identity in before.items())
    assert (
        runner.read("HAND", expected_input_hash=input_ref["content_hash"])["content_hash"]
        == ORIGINAL_RESULT_HASH
    )


def test_explicit_verified_write_rehydrates_missing_bytes_not_corrupt_bytes(tmp_path, failed_store):
    root, runner, assembly, path, receipt = copied_store(tmp_path, failed_store)
    result = runner.read("HAND", expected_input_hash=receipt["input_reference"]["content_hash"])
    leaf = result["leaves"][0]
    target = root / ("object-" + leaf["content_hash"] + ".json")
    target.unlink()
    restored = runner.write(
        "HAND",
        result,
        expected_result_hash=result["content_hash"],
        expected_assembly_hash=assembly["content_hash"],
        expected_realization_hash=path.content_hash,
    )
    assert restored == receipt
    target.write_bytes(b"corrupt")
    with pytest.raises(InventoryInputError, match="CONFLICT"):
        runner.write(
            "HAND",
            result,
            expected_result_hash=result["content_hash"],
            expected_assembly_hash=assembly["content_hash"],
            expected_realization_hash=path.content_hash,
        )
    assert target.read_bytes() == b"corrupt"


def test_invalid_input_stops_before_any_result_pointer(tmp_path):
    case, assembly, path = prepared()
    with pytest.raises(InventoryInputError):
        run(service(tmp_path, case), {**assembly, "content_hash": "0" * 64}, path)
    assert list(tmp_path.iterdir()) == []


def test_recovered_candidate_replays_real_strategy_not_only_self_hash(tmp_path, failed_store):
    _, runner, _, _, receipt = copied_store(tmp_path, failed_store)
    from dsio_inventory_engine.recommend_replenishment.application.policy_search import (
        CandidateStrategy,
    )

    actual, observed = CandidateStrategy.decide, []

    def replay(strategy, observation):
        observed.append(observation)
        return actual(strategy, observation)

    with patch.object(CandidateStrategy, "decide", replay):
        result = runner.read("HAND", expected_input_hash=receipt["input_reference"]["content_hash"])
    assert len(observed) == len(result["path_scorecards"]) == 32


def test_self_resealed_leaf_is_rejected_by_actual_replay(tmp_path, failed_store):
    root, runner, _, _, receipt = copied_store(tmp_path, failed_store)
    manifest = runner.repository.read(receipt["manifest_reference"])
    leaf = runner.repository.read(manifest["leaves"][0]["reference"])
    leaf["candidate_executions"][0]["failure_reason"] = "FAKE_FAILURE"
    forged = sealed(
        "inventory-robustness-item-leaf", {k: v for k, v in leaf.items() if k != "content_hash"}
    )
    reference = runner.repository.put(forged)
    key = _slot("HAND", "LEAF", 0)
    pointer = sealed("inventory-robustness-durable-slot", {"slot_key": key, "reference": reference})
    (root / runner.repository._slot_name(key)).write_text(canonical_json(pointer) + "\n")
    manifest["leaves"][0]["reference"] = reference
    reseal_manifest(root, runner, receipt, manifest)
    with pytest.raises(InventoryInputError, match="ITEM_LEAF_REPLAY_MISMATCH"):
        runner.read("HAND", expected_input_hash=receipt["input_reference"]["content_hash"])


def test_abc_xyz_complete_catalog_is_supported_without_reclassification(tmp_path):
    case, assembly, path = prepared(mode="ABC_XYZ_SHARED_BUDGET", max_target="0")
    runner = service(tmp_path, case)
    receipt = run(runner, assembly, path)
    result = runner.read("HAND", expected_input_hash=receipt["input_reference"]["content_hash"])
    manifest = runner.repository.read(receipt["manifest_reference"])
    assert manifest["candidate_count"] == 32
    assert result["assembly"]["parent_classification"] == case["parent_classification"]
    assert result["assembly"]["candidate_catalog"] == case["catalog"].to_dict()


def test_fsn_block_survives_durable_storage_and_verification(tmp_path):
    case, assembly, path = prepared(gate="BLOCK")
    runner = service(tmp_path, case)
    receipt = run(runner, assembly, path)
    result = runner.read("HAND", expected_input_hash=receipt["input_reference"]["content_hash"])
    for leaf in result["leaves"]:
        for child in leaf["candidate_executions"]:
            raw = child["causal_result"]
            assert raw["effective_policy_admission"]["effective_order_action"] == "BLOCK"
            assert raw["recommended_orders"] == []
            assert raw["automatic_publish_allowed"] is raw["automatic_order_allowed"] is False
    assert all(c["score"]["calculation_gate_allowed"] is False for c in result["path_scorecards"])


def test_mixed_candidate_failures_keep_full_count(tmp_path):
    case, assembly, path = prepared(max_target="10")
    runner = service(tmp_path, case)
    receipt = run(runner, assembly, path)
    manifest = runner.repository.read(receipt["manifest_reference"])
    assert manifest["candidate_count"] == 32
    assert 0 < manifest["failed_count"] < 32
    assert manifest["failed_count"] + manifest["successful_count"] == 32
    result = runner.read("HAND", expected_input_hash=receipt["input_reference"]["content_hash"])
    assert len(result["path_scorecards"]) == 32

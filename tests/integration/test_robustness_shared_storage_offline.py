import os
import subprocess
import sys

import pytest

from scripts.verify_robustness_shared_storage import EXPECTED_RESULTS, verify_case
from dsio_inventory_engine.infrastructure.robustness_artifacts import LocalRobustnessArtifactStore
from dsio_inventory_engine.infrastructure.robustness_compression import (
    CompressedRobustnessArtifactStore,
)
from dsio_inventory_engine.infrastructure.robustness_shared_artifacts import (
    SharedInputRobustnessArtifactStore,
)
from dsio_inventory_engine.evaluate_replenishment.robustness.durable import _slot
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from tests.integration.test_robustness_durable_offline import (
    prepared,
    service,
    run,
    file_identity,
    ORIGINAL_RESULT_HASH,
)


@pytest.mark.parametrize("mode", list(EXPECTED_RESULTS))
def test_actual_complete_catalog_original_hash_bytes_counts_and_size(tmp_path, mode):
    report = verify_case(tmp_path, mode)
    assert report["status"] == "VERIFIED" and report["result_hash"] == EXPECTED_RESULTS[mode]
    assert report["usage"]["SHARED_V3"]["shared_file_count"] > 0


@pytest.mark.parametrize(
    "old_type", [LocalRobustnessArtifactStore, CompressedRobustnessArtifactStore]
)
def test_completed_v1_v2_run_keeps_exact_receipt_and_files(tmp_path, old_type):
    case, assembly, path = prepared(max_target="0")
    receipt = run(service(tmp_path, case, old_type), assembly, path)
    before = file_identity(tmp_path)
    new = service(tmp_path, case, SharedInputRobustnessArtifactStore)
    assert (
        new.recover("HAND", expected_input_hash=receipt["input_reference"]["content_hash"])
        == receipt
    )
    assert run(new, assembly, path) == receipt and file_identity(tmp_path) == before


@pytest.mark.parametrize(
    "old_type",
    [
        LocalRobustnessArtifactStore,
        CompressedRobustnessArtifactStore,
        SharedInputRobustnessArtifactStore,
    ],
)
def test_interrupted_checkpoint_resumes_with_old_and_new_references(tmp_path, old_type):
    case, assembly, path = prepared(max_target="0")

    class Stop(old_type):
        def bind(self, key, ref):
            super().bind(key, ref)
            if key.endswith(":LEAF:0"):
                raise OSError("HAND_INTERRUPTION")

    with pytest.raises(OSError, match="HAND_INTERRUPTION"):
        run(service(tmp_path, case, Stop), assembly, path)
    before = file_identity(tmp_path)
    new = service(tmp_path, case, SharedInputRobustnessArtifactStore)
    ref = new.repository.lookup(_slot("HAND", "INPUT"))
    receipt = new.recover("HAND", expected_input_hash=ref["content_hash"])
    assert all(file_identity(tmp_path)[k] == v for k, v in before.items())
    manifest = new.repository.read(receipt["manifest_reference"])
    assert manifest["failed_count"] == manifest["candidate_count"] == 32
    assert manifest["leaves"][1]["reference"]["storage_version"] == "3.0.0"


@pytest.mark.parametrize("fault", ["missing", "truncated"])
def test_completed_run_shared_input_damage_cannot_be_auto_repaired(tmp_path, fault):
    case, assembly, path = prepared(max_target="0")
    new = service(tmp_path, case, SharedInputRobustnessArtifactStore)
    receipt = run(new, assembly, path)
    target = next(tmp_path.glob("shared-*.gz"))
    if fault == "missing":
        target.unlink()
    else:
        target.write_bytes(target.read_bytes()[:-3])
    before = file_identity(tmp_path)
    with pytest.raises(InventoryInputError):
        new.recover("HAND", expected_input_hash=receipt["input_reference"]["content_hash"])
    assert file_identity(tmp_path) == before


def test_crash_after_shared_chunk_before_document_resumes_idempotently(tmp_path):
    case, assembly, path = prepared(max_target="0")

    class Stop(SharedInputRobustnessArtifactStore):
        def _publish(self, name, payload):
            super()._publish(name, payload)
            if name.startswith("shared-"):
                raise OSError("HAND_CHUNK_CRASH")

    with pytest.raises(OSError, match="HAND_CHUNK_CRASH"):
        run(service(tmp_path, case, Stop), assembly, path)
    before = file_identity(tmp_path)
    assert list(tmp_path.glob("shared-*")) and not list(tmp_path.glob("slot-*"))
    receipt = run(service(tmp_path, case, SharedInputRobustnessArtifactStore), assembly, path)
    assert receipt["status"] == "COMPLETE" and all(
        file_identity(tmp_path)[k] == v for k, v in before.items()
    )


def test_fresh_process_recovery_after_shared_leaf_checkpoint_keeps_result(tmp_path):
    code = """
import os,sys
from pathlib import Path
from tests.integration.test_robustness_durable_offline import prepared,service,run
from dsio_inventory_engine.infrastructure.robustness_shared_artifacts import SharedInputRobustnessArtifactStore
class Stop(SharedInputRobustnessArtifactStore):
    def bind(self,key,ref):
        super().bind(key,ref)
        if key.endswith(':LEAF:0'): os._exit(97)
case,assembly,path=prepared()
run(service(Path(sys.argv[1]),case,Stop),assembly,path)
"""
    process = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        capture_output=True,
        text=True,
        env=dict(os.environ),
        timeout=180,
    )
    assert process.returncode == 97, process.stderr
    from tests.support.robustness_item_fixtures import item_case

    new = service(tmp_path, item_case(), SharedInputRobustnessArtifactStore)
    ref = new.repository.lookup(_slot("HAND", "INPUT"))
    before = file_identity(tmp_path)
    receipt = new.recover("HAND", expected_input_hash=ref["content_hash"])
    assert receipt["result_hash"] == ORIGINAL_RESULT_HASH
    assert all(file_identity(tmp_path)[k] == v for k, v in before.items())


def test_fsn_block_and_review_permission_are_not_weakened(tmp_path):
    case, assembly, path = prepared(gate="BLOCK")
    new = service(tmp_path, case, SharedInputRobustnessArtifactStore)
    receipt = run(new, assembly, path)
    result = new.read("HAND", expected_input_hash=receipt["input_reference"]["content_hash"])
    for leaf in result["leaves"]:
        for child in leaf["candidate_executions"]:
            raw = child["causal_result"]
            assert raw["recommended_orders"] == []
            assert raw["effective_policy_admission"]["effective_order_action"] == "BLOCK"
            assert raw["automatic_publish_allowed"] is raw["automatic_order_allowed"] is False
    assert all(c["score"]["calculation_gate_allowed"] is False for c in result["path_scorecards"])

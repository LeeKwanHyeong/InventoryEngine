import gzip
import os
import subprocess
import sys

import pytest

from dsio_inventory_engine.evaluate_replenishment.robustness.durable import _slot
from dsio_inventory_engine.infrastructure.robustness_artifacts import LocalRobustnessArtifactStore
from dsio_inventory_engine.infrastructure.robustness_compression import (
    CompressedRobustnessArtifactStore,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, canonical_json
from tests.integration.test_robustness_durable_offline import (
    ORIGINAL_RESULT_HASH,
    file_identity,
    prepared,
    run,
    service,
)


@pytest.mark.parametrize(
    "mode,count", [("ABC_XYZ_SHARED_BUDGET", 32), ("LEGACY_STYLE", 32), ("WIDE_REFERENCE", 88)]
)
def test_actual_full_catalog_compressed_storage_keeps_original_results_and_raw_bytes(
    tmp_path, mode, count
):
    case, assembly, path = prepared(mode=mode)
    runner = service(tmp_path, case, CompressedRobustnessArtifactStore)
    receipt = run(runner, assembly, path)
    result = runner.read(
        "HAND",
        expected_input_hash=receipt["input_reference"]["content_hash"],
        expected_result_hash=receipt["result_hash"],
    )
    if mode == "LEGACY_STYLE":
        assert result["content_hash"] == ORIGINAL_RESULT_HASH
    elif mode == "WIDE_REFERENCE":
        assert (
            result["content_hash"]
            == "0c2a92d7fdd6d835a665d3c2bf1a4865b0fe797e0851e0a9268750fbfa31759b"
        )
    manifest = runner.repository.read(receipt["manifest_reference"])
    assert manifest["candidate_count"] == manifest["successful_count"] == count
    assert manifest["psi_row_count"] == count * 65 and manifest["failed_count"] == 0
    assert result["assembly"]["parent_classification"] == case["parent_classification"]
    for entry, leaf in zip(manifest["leaves"], result["leaves"], strict=True):
        ref = entry["reference"]
        assert ref["storage_version"] == "2.0.0" and ref["content_hash"] == leaf["content_hash"]
        stored = tmp_path / ("object-" + ref["content_hash"] + ".json.gz")
        assert gzip.decompress(stored.read_bytes()) == (canonical_json(leaf) + "\n").encode()
        assert ref["stored_byte_count"] < ref["byte_count"] <= 128_000_000
        for child in leaf["candidate_executions"]:
            raw = child["causal_result"]
            assert raw["effective_policy_admission"]["effective_order_action"] == "REVIEW"
            assert raw["automatic_publish_allowed"] is raw["automatic_order_allowed"] is False
    before = file_identity(tmp_path)
    assert (
        runner.recover("HAND", expected_input_hash=receipt["input_reference"]["content_hash"])
        == receipt
    )
    assert file_identity(tmp_path) == before


def test_completed_v1_run_is_recovered_without_converting_or_rewriting_it(tmp_path):
    case, assembly, path = prepared(max_target="0")
    receipt = run(service(tmp_path, case), assembly, path)
    before = file_identity(tmp_path)
    new = service(tmp_path, case, CompressedRobustnessArtifactStore)
    assert (
        new.recover("HAND", expected_input_hash=receipt["input_reference"]["content_hash"])
        == receipt
    )
    assert run(new, assembly, path) == receipt
    assert file_identity(tmp_path) == before
    assert not list(tmp_path.glob("*.gz"))


@pytest.mark.parametrize("legacy", [False, True])
def test_partial_v1_or_v2_run_resumes_with_mixed_references_and_original_bindings(tmp_path, legacy):
    case, assembly, path = prepared(max_target="0")
    parent = LocalRobustnessArtifactStore if legacy else CompressedRobustnessArtifactStore

    class StopAfterFirst(parent):
        def bind(self, key, ref):
            super().bind(key, ref)
            if key.endswith(":LEAF:0"):
                raise OSError("HAND_INTERRUPTION")

    with pytest.raises(OSError, match="HAND_INTERRUPTION"):
        run(service(tmp_path, case, StopAfterFirst), assembly, path)
    new = service(tmp_path, case, CompressedRobustnessArtifactStore)
    input_ref = new.repository.lookup(_slot("HAND", "INPUT"))
    before = file_identity(tmp_path)
    receipt = new.recover("HAND", expected_input_hash=input_ref["content_hash"])
    assert all(file_identity(tmp_path)[name] == value for name, value in before.items())
    manifest = new.repository.read(receipt["manifest_reference"])
    assert manifest["candidate_count"] == manifest["failed_count"] == 32
    if legacy:
        assert "storage_version" not in manifest["leaves"][0]["reference"]
    assert manifest["leaves"][1]["reference"]["storage_version"] == "2.0.0"


@pytest.mark.parametrize("fault", ["missing", "truncated", "changed_metadata"])
def test_corrupt_compressed_run_does_not_silently_regenerate(tmp_path, fault):
    case, assembly, path = prepared(max_target="0")
    runner = service(tmp_path, case, CompressedRobustnessArtifactStore)
    receipt = run(runner, assembly, path)
    manifest = runner.repository.read(receipt["manifest_reference"])
    ref = manifest["leaves"][0]["reference"]
    target = tmp_path / ("object-" + ref["content_hash"] + ".json.gz")
    if fault == "missing":
        target.unlink()
    elif fault == "truncated":
        target.write_bytes(target.read_bytes()[:-2])
    else:
        payload = bytearray(target.read_bytes())
        payload[4] ^= 1
        target.write_bytes(payload)
    before = file_identity(tmp_path)
    with pytest.raises(InventoryInputError):
        runner.recover("HAND", expected_input_hash=receipt["input_reference"]["content_hash"])
    assert before == file_identity(tmp_path)


def test_actual_process_exit_after_compressed_checkpoint_recovers_same_result(tmp_path):
    code = """
import os, sys
from pathlib import Path
from tests.integration.test_robustness_durable_offline import prepared, service, run
from dsio_inventory_engine.infrastructure.robustness_compression import CompressedRobustnessArtifactStore
class StopAfterCheckpoint(CompressedRobustnessArtifactStore):
    def bind(self, key, ref):
        super().bind(key, ref)
        if key.endswith(":LEAF:0"):
            os._exit(97)
case, assembly, path = prepared()
run(service(Path(sys.argv[1]), case, StopAfterCheckpoint), assembly, path)
"""
    process = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=120,
        env=dict(os.environ),
    )
    assert process.returncode == 97, process.stderr
    input_ref = CompressedRobustnessArtifactStore(tmp_path).lookup(_slot("HAND", "INPUT"))
    # Only the pinned stored input and the unchanged installed Producer are used.
    from tests.support.robustness_item_fixtures import item_case

    receipt = service(tmp_path, item_case(), CompressedRobustnessArtifactStore).recover(
        "HAND", expected_input_hash=input_ref["content_hash"]
    )
    assert receipt["result_hash"] == ORIGINAL_RESULT_HASH

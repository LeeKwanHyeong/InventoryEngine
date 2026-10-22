"""Only a successful replay of exact sealed bytes can be reused locally."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import copy
from unittest.mock import patch

import pytest

from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    canonical_json,
)
from dsio_inventory_engine.recommend_replenishment.application.target_execution import (
    RunTargetPolicyPipelineUseCase,
)
from dsio_inventory_engine.recommend_replenishment.application.target_verification import (
    TargetReplayVerification,
)
from tests.unit.test_target_execution import ReferenceKernel
from tests.unit.test_target_pipeline_artifacts import case


def saved(root):
    durable, repo, request, binding, sources, result, run_id = case(root)
    durable.persist(result, request, binding, **sources)
    return durable, repo, result, run_id


def test_each_unscoped_read_replays_but_scoped_reads_keep_fresh_independent_objects(tmp_path):
    durable, _, expected, run_id = saved(tmp_path)
    one_pass = sum(expected["completed_transition_calls"].values())
    calls = len(durable.kernel.inputs)
    with durable._verification_scope():
        first = durable.read_verified(run_id)
        first["result"]["completed_transition_calls"].clear()
        second = durable.read_verified(run_id)
        assert second["result"] == expected
        assert len(durable.kernel.inputs) - calls == one_pass
        assert durable.read_integrity(run_id)["verification_status"] == "INTEGRITY_VERIFIED"
    for _ in range(2):
        calls = len(durable.kernel.inputs)
        assert durable.read_verified(run_id)["result"] == expected
        assert len(durable.kernel.inputs) - calls == one_pass


@pytest.mark.parametrize("attack", ["input", "dll", "profile", "gate", "fsn"])
def test_binding_and_gate_checks_are_never_replaced_by_replay_admission(tmp_path, attack):
    durable, repo, _, run_id = saved(tmp_path)
    with durable._verification_scope():
        durable.read_verified(run_id)
        calls = len(durable.kernel.inputs)
        if attack == "input":
            ref = copy.deepcopy(repo.lookup(run_id, "INPUT"))
            ref["content_hash"] = "0" * 64
            with pytest.raises(InventoryInputError, match="EXPECTED_INPUT_MISMATCH"):
                durable.read_verified(run_id, expected_input_reference=ref)
        elif attack == "dll":
            durable.kernel.dll_sha256 = "b" * 64
            with pytest.raises(InventoryInputError):
                durable.read_verified(run_id)
        else:
            # Even a cache hit must pass current source admission. Probe that
            # exact admission boundary without changing any production Gate.
            with patch(
                "dsio_inventory_engine.recommend_replenishment.application.target_durable._TargetNumerics",
                side_effect=InventoryInputError(f"{attack.upper()}_DENIED"),
            ):
                with pytest.raises(InventoryInputError, match="DENIED"):
                    durable.read_verified(run_id)
        assert len(durable.kernel.inputs) == calls


@pytest.mark.parametrize("attack", ["missing_part", "bad_count", "resealed_result"])
def test_every_read_still_checks_files_coverage_counts_and_new_manifest(tmp_path, attack):
    durable, repo, _, run_id = saved(tmp_path)
    with durable._verification_scope():
        durable.read_verified(run_id)
        manifest = repo.read(repo.lookup(run_id, "COMPLETE"))
        calls = len(durable.kernel.inputs)
        if attack == "missing_part":
            ref = manifest["parts"][0]["reference"]
            (tmp_path / f"target-object-{ref['content_hash']}.json").unlink()
        else:
            if attack == "bad_count":
                manifest["counts"]["candidate_count"] += 1
            else:
                entry = manifest["parts"][0]
                part = repo.read(entry["reference"])
                part["value"]["completed_transition_calls"]["POLICY_SELECTION"] += 1
                entry["reference"] = repo.put("PART", part)
            ref = repo.put("MANIFEST", manifest)
            slot = repo.put(
                "SLOT", {"engine_run_id": run_id, "phase": "COMPLETE", "reference": ref}
            )
            # Simulated hostile replacement, not an append-only application write.
            (tmp_path / repo._target_slot(run_id, "COMPLETE")).write_text(
                canonical_json(slot) + "\n"
            )
        with pytest.raises(InventoryInputError):
            durable.read_verified(run_id)
        if attack == "resealed_result":
            assert len(durable.kernel.inputs) > calls  # new bytes require a new replay
        else:
            assert len(durable.kernel.inputs) == calls


def test_failed_replay_is_not_cached_and_exception_clears_operation(tmp_path):
    durable, _, expected, run_id = saved(tmp_path)
    one_pass = sum(expected["completed_transition_calls"].values())
    with pytest.raises(OSError, match="interrupted"):
        with durable._verification_scope():
            with patch.object(
                RunTargetPolicyPipelineUseCase, "verify_result", side_effect=ValueError("bad")
            ):
                with pytest.raises(ValueError, match="bad"):
                    durable.read_verified(run_id)
            calls = len(durable.kernel.inputs)
            durable.read_verified(run_id)
            assert len(durable.kernel.inputs) - calls == one_pass
            raise OSError("interrupted")
    calls = len(durable.kernel.inputs)
    durable.read_verified(run_id)
    assert len(durable.kernel.inputs) - calls == one_pass


def test_same_hash_on_a_replaced_kernel_does_not_inherit_verification(tmp_path):
    durable, _, expected, run_id = saved(tmp_path)
    with durable._verification_scope():
        durable.read_verified(run_id)
        durable.kernel = ReferenceKernel()
        durable.read_verified(run_id)
        assert len(durable.kernel.inputs) == sum(expected["completed_transition_calls"].values())


def test_copied_context_cannot_transfer_admission_to_another_worker_thread():
    scope, kernel, key = TargetReplayVerification(), object(), ("sealed",)
    with scope.scope():
        scope.remember(key, kernel)
        context = copy_context()

        def other_worker():
            assert not scope.contains(key, kernel)
            with scope.scope():
                assert not scope.contains(key, kernel)
                scope.remember(key, kernel)
                assert scope.contains(key, kernel)

        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(context.run, other_worker).result()
        assert scope.contains(key, kernel)
    assert not scope.contains(key, kernel)


@pytest.mark.asyncio
async def test_child_task_and_cancelled_operation_cannot_share_admission():
    scope, kernel, key = TargetReplayVerification(), object(), ("sealed",)

    async def child():
        assert not scope.contains(key, kernel)
        with scope.scope():
            assert not scope.contains(key, kernel)
            scope.remember(key, kernel)
            assert scope.contains(key, kernel)

    with pytest.raises(asyncio.CancelledError):
        with scope.scope():
            scope.remember(key, kernel)
            await asyncio.create_task(child())
            assert scope.contains(key, kernel)
            raise asyncio.CancelledError
    with scope.scope():
        assert not scope.contains(key, kernel)

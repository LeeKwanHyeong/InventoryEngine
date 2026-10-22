"""First call creates all results from sealed inputs; no source reread on recovery."""

import copy
from unittest.mock import patch

import pytest

from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from dsio_inventory_engine.run_inventory.initial_target_execution import (
    InventoryInitialTargetExecution,
    KEY,
)
from tests.support.initial_execution_fixtures import initial_world
from tests.unit.test_target_execution import ReferenceKernel
from dsio_inventory_engine.infrastructure.target_pipeline_artifacts import (
    LocalTargetPipelineArtifactStore,
)
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.calculation_boundary import (
    CalculationBoundaryBinding,
)
from dsio_inventory_engine.recommend_replenishment.application.target_durable import (
    RunDurableTargetPolicyPipelineUseCase,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,count",
    [("ABC_V1", 2), ("ABC_XYZ_SHARED_BUDGET", 32), ("LEGACY_STYLE", 32), ("WIDE_REFERENCE", 88)],
)
async def test_first_call_calculates_stores_reads_and_replays(tmp_path, mode, count):
    kernel = ReferenceKernel()
    w = initial_world(tmp_path, kernel, mode=mode)
    assert not kernel.inputs
    assert w.store.find_outbox(w.request.engine_run_id, 1) is None
    assert w.target_repo.lookup(w.request.engine_run_id, "COMPLETE") is None
    result = await w.runtime.execute(request=w.request, target_input_reference=w.input_ref)
    assert result["target_result_link"]["counts"]["candidate_count"] == count
    assert result["result"]["order_plan"]["recommended_orders"]
    assert not result["automatic_order_allowed"] and not result["automatic_publish_allowed"]
    original = w.store.read_artifact(
        result["result_pointer"]["sources"]["bundle"]["artifact_reference"]
    )

    class NoSource:
        async def resolve(self, request):
            raise AssertionError("Source reread")

    restarted = InventoryInitialTargetExecution(
        **(w.runtime_kwargs | {"input_provider": NoSource()})
    )
    assert await restarted.execute(request=w.request, target_input_reference=w.input_ref) == result
    assert await restarted.recover(request=w.request, target_input_reference=w.input_ref) == result
    with patch.object(w.store, "commit_artifacts", side_effect=AssertionError("read wrote")):
        assert restarted.read(request=w.request, result_pointer=result["result_pointer"]) == result
    assert w.store.read_artifact(original.reference) == original


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["input_hash", "manifest", "source_disagreement", "build"])
async def test_fail_closed_before_calculation(tmp_path, case):
    kernel = ReferenceKernel()
    w = initial_world(tmp_path, kernel)
    ref = copy.deepcopy(w.input_ref)
    if case == "input_hash":
        ref["content_hash"] = "0" * 64
    elif case == "manifest":
        w.manifest["content_hash"] = "0" * 64

        async def wrong(request):
            return w.manifest

        w.provider.resolve = wrong
    elif case == "source_disagreement":
        original = w.target.read_input(ref)
        target_root = tmp_path / "different-input"
        target_root.mkdir()
        different = RunDurableTargetPolicyPipelineUseCase(
            w.runtime.deployment, kernel, LocalTargetPipelineArtifactStore(target_root)
        )
        changed = copy.deepcopy(original["config_values"])
        changed["not_in_sealed_manifest"] = True
        ref = different.seal_input(
            MathematicalPolicyRequest.from_dict(original["request"]),
            CalculationBoundaryBinding.from_dict(original["binding"]),
            config_values=changed,
            classification_snapshot=original["classification_snapshot"],
        )
        w.runtime = InventoryInitialTargetExecution(
            **(w.runtime_kwargs | {"target_storage": different})
        )
    else:
        w.runtime.expected_build["content_hash"] = "0" * 64
    with pytest.raises(ValueError):
        await w.runtime.execute(request=w.request, target_input_reference=ref)
    assert not kernel.inputs
    assert w.store.find_outbox(w.request.engine_run_id, 1) is None


@pytest.mark.asyncio
async def test_recovery_requires_checkpoint_and_block_does_not_become_shadow(tmp_path):
    kernel = ReferenceKernel()
    w = initial_world(tmp_path, kernel, fsn_action="BLOCK")
    with patch.object(w.provider, "resolve", side_effect=AssertionError("recovery fetched Source")):
        with pytest.raises(InventoryInputError, match="CHECKPOINT_REQUIRED"):
            await w.runtime.recover(request=w.request, target_input_reference=w.input_ref)
    with patch(
        "dsio_inventory_engine.run_inventory.approved_inputs.produce_policy_analysis_inputs",
        side_effect=AssertionError("BLOCK reached automatic analysis"),
    ):
        with pytest.raises(
            InventoryInputError, match="ITEM_POLICY_RECOMMENDATION_CALCULATION_BLOCKED"
        ):
            await w.runtime.execute(request=w.request, target_input_reference=w.input_ref)
    assert not kernel.inputs
    assert w.store.find_outbox(w.request.engine_run_id, 1) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["checkpoint", "bundle", "target_part", "link", "projection"])
async def test_interruption_recovers_without_source(tmp_path, phase):
    w = initial_world(tmp_path, ReferenceKernel())
    commit = w.store.commit_artifacts
    bundle_commit = w.store.commit
    put = w.target_repo.put

    def lose_artifacts(rows):
        result = commit(rows)
        key = {
            "checkpoint": KEY,
            "link": "inventory.target_result_link",
            "projection": "inventory.dsim_projection",
        }.get(phase)
        if any(a.contract_key == key for a in rows):
            raise OSError("offline response loss")
        return result

    def lose_bundle(*args, **kwargs):
        value = bundle_commit(*args, **kwargs)
        if phase == "bundle":
            raise OSError("offline response loss")
        return value

    def lose_part(kind, value):
        ref = put(kind, value)
        if phase == "target_part" and kind == "PART":
            raise OSError("offline response loss")
        return ref

    with (
        patch.object(w.store, "commit_artifacts", side_effect=lose_artifacts),
        patch.object(w.store, "commit", side_effect=lose_bundle),
        patch.object(w.target_repo, "put", side_effect=lose_part),
    ):
        with pytest.raises(OSError):
            await w.runtime.execute(request=w.request, target_input_reference=w.input_ref)
    with patch.object(w.provider, "resolve", side_effect=AssertionError("Source reread")):
        runtime = InventoryInitialTargetExecution(**w.runtime_kwargs)
        result = await runtime.recover(request=w.request, target_input_reference=w.input_ref)
        assert runtime.read(request=w.request, result_pointer=result["result_pointer"]) == result


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["attempt_no", "site_cd", "planning_cycle_id"])
async def test_changed_attempt_or_scope_cannot_reuse_initial_input(tmp_path, field):
    from dsio_inventory_engine.inventory_contracts.runtime import InventoryRuntimeExecutionRequest
    from tests.support.runtime_fixtures import reseal_runtime_site_binding

    kernel = ReferenceKernel()
    w = initial_world(tmp_path, kernel)
    await w.runtime.execute(request=w.request, target_input_reference=w.input_ref)
    raw = copy.deepcopy(w.request.to_dict())
    if field == "attempt_no":
        raw[field] += 1
    elif field == "site_cd":
        raw["claim"]["scope"][field] = "V999"
    else:
        raw["claim"][field] = "00000000-0000-4000-8000-000000000099"
    calls = len(kernel.inputs)
    with (
        patch.object(w.provider, "resolve", side_effect=AssertionError("Source reread")),
        patch.object(w.store, "commit_artifacts", side_effect=AssertionError("wrote")),
    ):
        with pytest.raises(InventoryInputError):
            request = InventoryRuntimeExecutionRequest.from_dict(reseal_runtime_site_binding(raw))
            await w.runtime.recover(request=request, target_input_reference=w.input_ref)
    assert len(kernel.inputs) == calls


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["request", "installed_build"])
async def test_resealed_checkpoint_is_not_authority(tmp_path, field):
    from dsio_inventory_engine.inventory_contracts.values import digest
    from dsio_inventory_engine.inventory_evidence.contracts import ArtifactObject

    kernel = ReferenceKernel()
    w = initial_world(tmp_path, kernel)
    await w.runtime.execute(request=w.request, target_input_reference=w.input_ref)
    ref = w.runtime.input_reference(w.request)
    checkpoint = w.store.read_artifact(ref)
    doc = checkpoint.document()
    if field == "request":
        doc[field]["attempt_no"] += 1
    else:
        doc[field]["content_hash"] = "0" * 64
    doc["content_hash"] = digest({k: v for k, v in doc.items() if k != "content_hash"})
    forged = ArtifactObject.from_document(reference=ref, contract_key=KEY, document=doc)
    read = w.store.read_artifact
    calls = len(kernel.inputs)
    with patch.object(
        w.store, "read_artifact", side_effect=lambda r: forged if r == ref else read(r)
    ):
        with pytest.raises(InventoryInputError, match="CHECKPOINT_MISMATCH"):
            await w.runtime.recover(request=w.request, target_input_reference=w.input_ref)
    assert len(kernel.inputs) == calls


@pytest.mark.asyncio
async def test_no_selected_candidate_remains_explainable_not_publishable(tmp_path):
    w = initial_world(tmp_path, ReferenceKernel(), mode="LEGACY_STYLE", all_failed=True)
    result = await w.runtime.execute(request=w.request, target_input_reference=w.input_ref)
    assert result["target_result_link"]["counts"]["candidate_count"] == 32
    assert result["result"]["order_plan"]["selected_item_count"] == 0
    assert result["result"]["order_plan"]["recommended_orders"] == []
    assert not result["automatic_publish_allowed"] and not result["automatic_order_allowed"]

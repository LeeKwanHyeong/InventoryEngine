"""Approved Manifest → actual Cost/PPO/Stress/analysis → durable recovery; no live DB writes."""

import copy
from dataclasses import replace

import pytest

from dsio_inventory_engine.inventory_contracts.result_bundle import seal_strategy_execution_plan
from dsio_inventory_engine.inventory_contracts.runtime import InventoryRuntimeExecutionRequest
from dsio_inventory_engine.inventory_contracts.runtime_inputs import (
    validate_runtime_input_manifest,
    content_hash,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from dsio_inventory_engine.infrastructure.runtime_input_files import (
    LocalRuntimeInputManifestCatalog,
)
from dsio_inventory_engine.infrastructure.sqlite import SqliteInventoryEvidenceUnitOfWork
from dsio_inventory_engine.run_inventory.approved_inputs import (
    RegisteredRuntimeImplementations,
    ApprovedInputCommandResolver,
    build_runtime_input_manifest,
)
from dsio_inventory_engine.run_inventory.runtime_composition import (
    build_approved_inventory_runtime_pipeline,
    build_approved_inventory_runtime_worker,
)
from dsio_inventory_engine.run_inventory.runtime_execution import InventoryRuntimeStageEventReceipt
from dsio_inventory_engine.run_inventory.simulation_registry import DevelopmentSimulationRegistry
from tests.integration.test_automatic_policy_analysis_offline import automatic_command
from tests.integration.test_psi_orchestrator_offline import DEPLOYMENT
from tests.support.learned_fixtures import model_fixture
from tests.support.runtime_fixtures import reseal_runtime_site_binding
from tests.support.run_cost_fixtures import bound_cost_command


def input_manifest(*, include_ppo=True, include_stress=True, landed=False):
    command, config, snapshot = automatic_command()
    dispatch = copy.deepcopy(command.runtime_request.to_dict())
    plan = dispatch["claim"]["strategy_execution_plan"]
    plan.pop("content_hash")
    registry = DevelopmentSimulationRegistry()
    plan["cost_profile_binding"] = registry.cost_profile_binding()
    plan["stress_scenario_bindings"] = (
        list(registry.stress_scenario_bindings()) if include_stress else []
    )
    models = {}
    ppo_hashes = {}
    if include_ppo:
        model = model_fixture(command.mathematical_request.to_dict(), "DEEP_RL", action=3)
        plan["shadow_challenger_bindings"] = [
            {
                "challenger_id": "approved-ppo-1",
                "strategy_type": "DEEP_RL",
                "algorithm": "PPO",
                "implementation_id": model.descriptor["implementation_id"],
                "version": model.descriptor["version"],
                "implementation_content_hash": "e" * 64,
                "model": model.reference,
                "approval_reference": "DEV-PPO-APPROVAL",
            }
        ]
        models["approved-ppo-1"] = model
        ppo_hashes[(model.descriptor["implementation_id"], model.descriptor["version"])] = "e" * 64
    cost = None
    if landed:
        other, cost = bound_cost_command(include_stress=False)
        plan["landed_cost_binding"] = other.runtime_request.strategy_execution_plan[
            "landed_cost_binding"
        ]
        dispatch["claim"]["input_bindings"] = [
            b
            for b in dispatch["claim"]["input_bindings"]
            if b["input_type"] not in {"TRADE_COST_REVISION_SET", "INVENTORY_NETWORK"}
        ]
        dispatch["claim"]["input_bindings"] += [
            b
            for b in other.runtime_request.value["claim"]["input_bindings"]
            if b["input_type"] in {"TRADE_COST_REVISION_SET", "INVENTORY_NETWORK"}
        ]
        dispatch["claim"]["input_bindings"].append(
            {
                "input_type": "INVENTORY_NETWORK",
                "source_contract_key": "inventory.network_revision",
                "source_contract_version": "1.0.0",
                "source_snapshot_id": cost.input_document["network_revision_id"],
                "source_content_hash": "f" * 64,
            }
        )
    plan = seal_strategy_execution_plan(plan)
    manifest, plan = build_runtime_input_manifest(
        plan=plan,
        mathematical_request=command.mathematical_request,
        config_values=config,
        classification_snapshot=snapshot,
        tenant_id=command.runtime_request.tenant_id,
        project_id=command.runtime_request.project_id,
        scope=dispatch["claim"]["scope"],
        snapshot_id="INPUT-MANIFEST-1",
        models=models,
        run_cost_input=(None if cost is None else cost.input_document),
    )
    dispatch["claim"]["strategy_execution_plan"] = plan
    runtime = InventoryRuntimeExecutionRequest.from_dict(reseal_runtime_site_binding(dispatch))
    implementations = RegisteredRuntimeImplementations(
        command.mathematical_implementation_content_hash,
        command.psi_simulator_implementation_content_hash,
        ppo_hashes,
    )
    return runtime, manifest, implementations, cost


async def report(**kwargs):
    return InventoryRuntimeStageEventReceipt(
        site_row_version=2, run_status="running", replayed=False
    )


@pytest.mark.asyncio
async def test_composed_worker_emits_events_and_preserves_review_publication_gate(tmp_path):
    from tests.integration.test_result_pipeline_offline import _Platform

    runtime, manifest, implementations, cost = input_manifest(landed=True)
    catalog = LocalRuntimeInputManifestCatalog(tmp_path)
    catalog.publish(manifest)
    evidence = SqliteInventoryEvidenceUnitOfWork(tmp_path / "worker.sqlite")
    platform = _Platform()

    class Projection:
        async def resolve(self, request):
            return cost.projection

    worker = build_approved_inventory_runtime_worker(
        platform=platform,
        deployment=DEPLOYMENT,
        input_provider=catalog,
        implementations=implementations,
        evidence_repository=evidence,
        cost_projection=Projection(),
    )
    result = await worker.execute(runtime)
    assert result["status"] == "review_required" and not platform.publications
    assert result["run_result_manifest_content_hash"]
    assert evidence.find_outbox(runtime.engine_run_id, 1).status == "WITHHELD_FOR_REVIEW"
    outbox = evidence.find_outbox(runtime.engine_run_id, 1)
    with pytest.raises(InventoryInputError, match="OUTBOX_PUBLICATION_NOT_ALLOWED"):
        evidence.mark_published(
            outbox.message.outbox_id,
            expected_payload_content_hash=outbox.message.payload_content_hash,
        )
    assert any(e.stage == "publication" for e in platform.events)


@pytest.mark.asyncio
async def test_registered_ppo_execution_failure_preserves_operational_math(tmp_path, monkeypatch):
    runtime, manifest, implementations, _ = input_manifest()
    catalog = LocalRuntimeInputManifestCatalog(tmp_path)
    catalog.publish(manifest)

    def fail(*args, **kwargs):
        raise RuntimeError("offline PPO execution failure")

    monkeypatch.setattr(
        "dsio_inventory_engine.run_inventory.psi_orchestrator.compile_strategy", fail
    )
    pipeline = build_approved_inventory_runtime_pipeline(
        deployment=DEPLOYMENT,
        input_provider=catalog,
        implementations=implementations,
        evidence_repository=SqliteInventoryEvidenceUnitOfWork(tmp_path / "failure.sqlite"),
    )
    result = await pipeline.execute(runtime, report)
    children = result.inventory_result_bundle["result_children"]
    assert next(c for c in children if c["strategy_type"] == "DEEP_RL")["status"] == "FAILED"
    assert all(c["status"] == "SUCCEEDED" for c in children if c["strategy_type"] != "DEEP_RL")


@pytest.mark.asyncio
@pytest.mark.parametrize("landed", [False, True])
async def test_registered_resources_execute_and_recover_without_source(tmp_path, landed):
    runtime, manifest, implementations, cost = input_manifest(landed=landed)
    catalog = LocalRuntimeInputManifestCatalog(tmp_path)
    assert catalog.publish(manifest) == catalog.publish(manifest)
    evidence = SqliteInventoryEvidenceUnitOfWork(tmp_path / "evidence.sqlite")

    class Projection:
        async def resolve(self, request):
            return cost.projection

    pipeline = build_approved_inventory_runtime_pipeline(
        deployment=DEPLOYMENT,
        input_provider=catalog,
        implementations=implementations,
        evidence_repository=evidence,
        cost_projection=Projection() if landed else None,
    )
    result = await pipeline.execute(runtime, report)
    children = result.inventory_result_bundle["result_children"]
    assert len(children) == 5
    assert all(c["status"] == "SUCCEEDED" for c in children)
    assert (
        next(c for c in children if c["strategy_type"] == "DEEP_RL")["execution_role"] == "SHADOW"
    )
    assert result.inventory_result_bundle["cost_profile_content_hash"] is not None
    assert (result.run_result_manifest is not None) == landed
    assert result.canonical_input.to_dict()["context"]["engine_run_id"] == runtime.engine_run_id

    class Missing:
        async def resolve(self, *_):
            raise AssertionError("Source must not be called on recovery")

    pipeline.command_resolver.provider = Missing()
    if landed:
        pipeline.landed_cost_resolver.provider = Missing()
    assert await pipeline.execute(runtime, report) == result


@pytest.mark.asyncio
async def test_new_attempt_only_rebinds_run_id_not_history_or_controls(tmp_path):
    runtime, manifest, implementations, _ = input_manifest(include_ppo=False, include_stress=False)
    catalog = LocalRuntimeInputManifestCatalog(tmp_path)
    catalog.publish(manifest)
    raw = copy.deepcopy(runtime.to_dict())
    raw["engine_run_id"] = "80000000-0000-4000-8000-000000000002"
    raw["attempt_no"] = 2
    raw["claim"].update(trigger_type="recovery", expected_site_row_version=3)
    raw["site_row_version"] = 4
    from tests.support.runtime_fixtures import reseal_runtime_site_binding

    retry = InventoryRuntimeExecutionRequest.from_dict(reseal_runtime_site_binding(raw))
    resolver = ApprovedInputCommandResolver(
        provider=catalog, implementations=implementations, deployment=DEPLOYMENT
    )
    first = await resolver.resolve(runtime)
    second = await resolver.resolve(retry)
    a, b = first.mathematical_request.to_dict(), second.mathematical_request.to_dict()
    assert a["policy_input"] == b["policy_input"]
    assert a["recommendation"]["execution"] == b["recommendation"]["execution"]
    a["recommendation"]["canonical_input"]["context"]["engine_run_id"] = b["recommendation"][
        "canonical_input"
    ]["context"]["engine_run_id"]
    assert a == b


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["scope", "resource_missing", "source_hash", "implementation", "ppo_implementation"]
)
async def test_source_and_registry_fail_closed(tmp_path, case):
    runtime, manifest, implementations, _ = input_manifest()
    catalog = LocalRuntimeInputManifestCatalog(tmp_path)
    if case == "scope":
        manifest["tenant_id"] = "other"
    elif case == "resource_missing":
        manifest["resources"].pop()
    elif case == "source_hash":
        manifest["resources"][0]["document"]["tampered"] = True
    elif case == "implementation":
        implementations = replace(implementations, mathematical_content_hash="0" * 64)
    elif case == "ppo_implementation":
        implementations = replace(implementations, ppo_implementations={})
    # Rehashed unapproved source is still rejected by the Claim's immutable binding.
    if case in {"scope", "resource_missing"}:
        manifest["content_hash"] = content_hash(
            {k: v for k, v in manifest.items() if k != "content_hash"}
        )
    if case == "source_hash":
        with pytest.raises(ValueError, match="DOCUMENT_HASH"):
            validate_runtime_input_manifest(manifest)
        return
    catalog.publish(manifest)
    resolver = ApprovedInputCommandResolver(
        provider=catalog, implementations=implementations, deployment=DEPLOYMENT
    )
    with pytest.raises((InventoryInputError, ValueError)):
        await resolver.resolve(runtime)

"""Actual PSI/search/selection → atomic artifacts → Run-bound, read-only projection."""

import copy
import json
from dataclasses import replace
from pathlib import Path
import sys

import pytest

from dsio_inventory_engine.inventory_contracts.effective_policy_v2 import EFFECTIVE_POLICY_V2_FIELDS
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.policy_sensitivity import PolicySensitivityRequest
from dsio_inventory_engine.inventory_contracts.replenishment import (
    replenishment_config_content_hash,
)
from dsio_inventory_engine.inventory_contracts.result_bundle import seal_strategy_execution_plan
from dsio_inventory_engine.inventory_contracts.runtime import InventoryRuntimeExecutionRequest
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from dsio_inventory_engine.inventory_evidence.policy_analysis import (
    PolicyAnalysisInputs,
    build_policy_analysis,
    run_shadow_request,
)
from dsio_inventory_engine.inventory_evidence.application import (
    PersistInventoryResultBundleUseCase,
    ReadAndVerifyInventoryResultBundleUseCase,
)
from dsio_inventory_engine.infrastructure.sqlite import SqliteInventoryEvidenceUnitOfWork
from dsio_inventory_engine.recommend_replenishment.application.policy_search import (
    RunPolicySearchUseCase,
)
from dsio_inventory_engine.recommend_replenishment.application.policy_sensitivity import (
    RunPolicySensitivityUseCase,
)
from dsio_inventory_engine.run_inventory.psi_orchestrator import RunPsiBundleUseCase
from dsio_inventory_engine.run_inventory.result_pipeline import InventoryResultPipelineHandler
from tests.integration.test_psi_orchestrator_offline import _bound_command, DEPLOYMENT
from tests.integration.test_result_pipeline_offline import _Resolver, _Platform
from tests.support.math_fixtures import reseal_math
from tests.support.runtime_fixtures import reseal_runtime_site_binding
from tests.support.policy_search_fixtures import bundle


def analysis_command(evaluation=False):
    command = _bound_command()
    raw, config, snapshot = bundle(command.mathematical_request.to_dict())
    execution = raw["recommendation"]["execution"]
    execution["effective_policy_binding"].update(
        classification_snapshot_id=snapshot["classification_snapshot_id"],
        classification_config_hash=snapshot["config_hash"],
        effective_policy_content_hash=snapshot["effective_policy_content_hash"],
    )
    execution["effective_item_policies"] = [
        {"item_id": row["item_id"], **{k: row[k] for k in EFFECTIVE_POLICY_V2_FIELDS}}
        for row in snapshot["items"]
    ]
    mathematical = MathematicalPolicyRequest.from_dict(reseal_math(raw))
    execution = mathematical.to_dict()["recommendation"]["execution"]
    dispatch = copy.deepcopy(command.runtime_request.to_dict())
    claim = dispatch["claim"]
    claim["config_hash"] = snapshot["config_hash"]
    claim["expected_automatic_publish_allowed"] = all(
        p["automatic_publish_allowed"] for p in snapshot["items"]
    )
    claim["expected_automatic_order_allowed"] = all(
        p["automatic_order_allowed"] for p in snapshot["items"]
    )
    for binding in claim["input_bindings"]:
        if binding["input_type"] == "INVENTORY_CLASSIFICATION":
            binding.update(
                source_snapshot_id=snapshot["classification_snapshot_id"],
                source_content_hash=snapshot["effective_policy_content_hash"],
            )
        if binding["input_type"] == "INVENTORY_POLICY":
            binding["source_content_hash"] = snapshot["config_hash"]
    plan = claim["strategy_execution_plan"]
    plan.pop("content_hash")
    plan.update(
        classification_config_hash=snapshot["config_hash"],
        effective_policy_content_hash=snapshot["effective_policy_content_hash"],
        shadow_challenger_bindings=[],
    )
    plan["operational_strategy"].update(
        strategy_input_binding=execution["strategy_input_binding"],
        replenishment_config_content_hash=replenishment_config_content_hash(execution),
    )
    claim["strategy_execution_plan"] = seal_strategy_execution_plan(plan)
    runtime = InventoryRuntimeExecutionRequest.from_dict(reseal_runtime_site_binding(dispatch))
    search = RunPolicySearchUseCase(DEPLOYMENT).execute(
        run_shadow_request(mathematical),
        config_values=config,
        classification_snapshot=snapshot,
    )
    result, sources = None, {}
    if evaluation:
        request = {
            "contract_id": "inventory-policy-sensitivity-request",
            "contract_version": "1.0.0",
            "evaluation_id": "RUN-BOUND-DEVELOPMENT-EVAL",
            "source_type": "SYNTHETIC_PLANNING_INPUT",
            "reference_case_id": "reference",
            "reference_cost_profile_id": "base",
            "cases": [
                {
                    "case_id": "reference",
                    "trace_id": "reference",
                    "split": "VALIDATION",
                    "seed": 7,
                    "search_result": search,
                }
            ],
            "cost_profiles": [
                {
                    "profile_id": "base",
                    "cost_assumptions": search["search_config"]["cost_assumptions"],
                }
            ],
            "terminal_pairs": [],
        }
        result = RunPolicySensitivityUseCase(DEPLOYMENT).execute(
            PolicySensitivityRequest.from_dict(request)
        )
        sources = {search["content_hash"]: search}
    return replace(
        command,
        runtime_request=runtime,
        mathematical_request=mathematical,
        ppo_challengers={},
        policy_analysis_inputs=PolicyAnalysisInputs(search, config, snapshot, result, sources),
    )


@pytest.mark.parametrize("evaluation", [False, True])
def test_atomic_companion_and_exact_read_recovery(tmp_path, evaluation):
    command = analysis_command(evaluation)
    run = RunPsiBundleUseCase(DEPLOYMENT).execute(command)
    analysis = build_policy_analysis(
        deployment=DEPLOYMENT,
        request=command.runtime_request,
        bundle=run.result_bundle,
        inputs=command.policy_analysis_inputs,
        mathematical_request=command.mathematical_request,
    )
    repository = SqliteInventoryEvidenceUnitOfWork(tmp_path / "evidence.sqlite3")
    writer = PersistInventoryResultBundleUseCase(DEPLOYMENT, repository)
    kwargs = dict(
        strategy_execution_plan=command.runtime_request.strategy_execution_plan,
        runtime_request=command.runtime_request,
        policy_analysis_evidence=analysis,
    )
    first = writer.execute(run, **kwargs)
    assert writer.execute(run, **kwargs).replayed
    restored = ReadAndVerifyInventoryResultBundleUseCase(DEPLOYMENT, repository).execute(
        first.bundle_reference,
        strategy_execution_plan=command.runtime_request.strategy_execution_plan,
        runtime_request=command.runtime_request,
    )
    assert restored.policy_analysis_evidence == analysis
    assert (
        restored.outbox.message.payload()["policy_analysis_content_hash"]
        == analysis.manifest["content_hash"]
    )
    assert not analysis.manifest["automatic_publish_allowed"]
    assert run.result_bundle == restored.result_bundle
    from jsonschema import Draft202012Validator, ValidationError

    schema = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "schemas"
            / "inventory-policy-analysis-manifest.schema.json"
        ).read_text()
    )
    validator = Draft202012Validator(schema)
    validator.validate(analysis.manifest)
    invalid = {**analysis.manifest, "automatic_order_allowed": True}
    with pytest.raises(ValidationError):
        validator.validate(invalid)


@pytest.mark.parametrize(
    "field", ["canonical_input_hash", "config_hash", "classification_snapshot_id"]
)
def test_wrong_run_input_rejected_before_persistence(field):
    command = analysis_command()
    run = RunPsiBundleUseCase(DEPLOYMENT).execute(command)
    inputs = copy.deepcopy(command.policy_analysis_inputs)
    inputs.search_result[field] = "tampered"
    with pytest.raises(InventoryInputError):
        build_policy_analysis(
            deployment=DEPLOYMENT,
            request=command.runtime_request,
            bundle=run.result_bundle,
            inputs=inputs,
            mathematical_request=command.mathematical_request,
        )


def test_evaluation_cost_anchor_cannot_replace_run_selection_cost():
    command = analysis_command(True)
    run = RunPsiBundleUseCase(DEPLOYMENT).execute(command)
    inputs = copy.deepcopy(command.policy_analysis_inputs)
    inputs.evaluation_result["evaluation_spec"]["cost_profiles"][0]["cost_assumptions"][
        "fixed_order_cost"
    ] = "999"
    with pytest.raises(InventoryInputError, match="POLICY_ANALYSIS_REFERENCE_COST_MISMATCH"):
        build_policy_analysis(
            deployment=DEPLOYMENT,
            request=command.runtime_request,
            bundle=run.result_bundle,
            inputs=inputs,
            mathematical_request=command.mathematical_request,
        )


@pytest.mark.asyncio
async def test_pipeline_companion_and_read_only_platform_projection(tmp_path):
    backend = Path(__file__).resolve().parents[3] / "dsai-platform" / "backend"
    if not backend.is_dir():
        pytest.skip("adjacent Platform source required for cross-repository offline check")
    sys.path.insert(0, str(backend))
    sys.path.insert(0, str(backend.parent))
    from platform_api.services.inventory_sqlite_artifact_reader import (
        SqliteInventoryRuntimeArtifactReader,
    )
    from platform_api.services.inventory_policy_analysis import InventoryPolicyAnalysisReader

    command = analysis_command(True)
    repository = SqliteInventoryEvidenceUnitOfWork(tmp_path / "evidence.sqlite3")
    pipeline = InventoryResultPipelineHandler(
        command_resolver=_Resolver(command),
        orchestrator=RunPsiBundleUseCase(DEPLOYMENT),
        persistence=PersistInventoryResultBundleUseCase(DEPLOYMENT, repository),
        evidence_repository=repository,
    )
    platform = _Platform()

    async def report(**_):
        from dsio_inventory_engine.run_inventory.runtime_execution import (
            InventoryRuntimeStageEventReceipt,
        )

        return InventoryRuntimeStageEventReceipt(
            site_row_version=2, run_status="running", replayed=False
        )

    result = await pipeline.execute(command.runtime_request, report)
    recovered = await pipeline.execute(command.runtime_request, report)
    assert result == recovered
    assert platform.publications == []
    manifest = repository.find_outbox(command.runtime_request.engine_run_id, 1).message.payload()
    document = repository.read_artifact(manifest["policy_analysis_reference"]).document()
    run = {
        k: document[k]
        for k in (
            "engine_run_id",
            "attempt_no",
            "tenant_id",
            "project_id",
            "company_cd",
            "subs_cd",
            "site_cd",
            "config_hash",
            "site_binding_hash",
            "classification_snapshot_id",
            "classification_effective_policy_hash",
        )
    }
    run["plan_hash"] = document["strategy_execution_plan_content_hash"]
    reader = InventoryPolicyAnalysisReader(
        SqliteInventoryRuntimeArtifactReader(tmp_path / "evidence.sqlite3")
    )
    response = await reader.read(run, limit=20, offset=0)
    assert response.status == "AVAILABLE" and response.total_items == 1
    assert response.items[0].item_id == "ITEM-A"
    assert response.evaluation is not None
    assert not response.operational_policy_approved
    assert (await reader.read({**run, "attempt_no": 2}, limit=20, offset=0)).status == "NOT_BOUND"

"""Pinned Config 2.1 → exact input catalog → real producer → durable companion/recovery."""

import copy
import hashlib
import json
import uuid
from dataclasses import replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from dsio_inventory_engine.inventory_contracts.policy_analysis_spec import (
    seal_policy_analysis_spec,
    bind_policy_analysis_spec,
    validate_policy_analysis_spec,
)
from dsio_inventory_engine.inventory_contracts.policy_search import policy_search_hash
from dsio_inventory_engine.classify_inventory.application import SNAPSHOT_NAMESPACE
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.replenishment import (
    replenishment_config_content_hash,
)
from dsio_inventory_engine.inventory_contracts.result_bundle import seal_strategy_execution_plan
from dsio_inventory_engine.inventory_contracts.runtime import InventoryRuntimeExecutionRequest
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from dsio_inventory_engine.infrastructure.policy_analysis_inputs import (
    LocalPolicyAnalysisInputCatalog,
)
from dsio_inventory_engine.infrastructure.sqlite import SqliteInventoryEvidenceUnitOfWork
from dsio_inventory_engine.inventory_evidence.application import (
    PersistInventoryResultBundleUseCase,
    ReadAndVerifyInventoryResultBundleUseCase,
)
from dsio_inventory_engine.run_inventory.policy_analysis_producer import (
    ExactMathematicalCommandResolver,
    PolicyAnalysisCommandResolver,
)
from dsio_inventory_engine.run_inventory.psi_orchestrator import RunPsiBundleUseCase
from dsio_inventory_engine.run_inventory.result_pipeline import InventoryResultPipelineHandler
from dsio_inventory_engine.run_inventory.runtime_execution import InventoryRuntimeStageEventReceipt
from tests.integration.test_policy_analysis_run_offline import analysis_command
from tests.integration.test_result_pipeline_offline import _Resolver
from tests.integration.test_psi_orchestrator_offline import DEPLOYMENT
from tests.support.runtime_fixtures import reseal_runtime_site_binding
from tests.support.math_fixtures import reseal_math
from tests.support.fixtures import reseal


def automatic_command(evaluation=False, case_request=None):
    command = analysis_command()
    config, snapshot = (
        command.policy_analysis_inputs.config_values,
        command.policy_analysis_inputs.classification_snapshot,
    )
    raw = copy.deepcopy(command.runtime_request.to_dict())
    claim = raw["claim"]
    snapshot = copy.deepcopy(snapshot)
    snapshot["config_id"] = claim["config_id"]
    snapshot["tenant_id"] = claim["tenant_id"]
    snapshot["project_id"] = claim["project_id"]
    unsigned = {
        k: v for k, v in snapshot.items() if k not in ("content_hash", "classification_snapshot_id")
    }
    snapshot["content_hash"] = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    snapshot["classification_snapshot_id"] = str(
        uuid.uuid5(SNAPSHOT_NAMESPACE, snapshot["content_hash"])
    )
    mathematical_raw = command.mathematical_request.to_dict()
    execution = mathematical_raw["recommendation"]["execution"]
    execution["effective_policy_binding"]["classification_snapshot_id"] = snapshot[
        "classification_snapshot_id"
    ]
    mathematical = MathematicalPolicyRequest.from_dict(mathematical_raw)
    for binding in claim["input_bindings"]:
        if binding["input_type"] == "INVENTORY_CLASSIFICATION":
            binding["source_snapshot_id"] = snapshot["classification_snapshot_id"]
    claim["strategy_execution_plan"]["operational_strategy"][
        "replenishment_config_content_hash"
    ] = replenishment_config_content_hash(execution)
    eval_spec = {"mode": "DISABLED"}
    if evaluation:
        eval_spec = {
            "mode": "SEALED_SYNTHETIC_CASES",
            "evaluation_id": "AUTO-EVALUATION",
            "source_type": "SYNTHETIC_PLANNING_INPUT",
            "reference_case_id": "reference",
            "reference_cost_profile_id": "base",
            "cases": [
                {
                    "case_id": "reference",
                    "trace_id": "reference",
                    "split": "VALIDATION",
                    "seed": 7,
                    "input_source": "CURRENT_RUN",
                    "input_snapshot_id": None,
                    "input_content_hash": None,
                }
            ],
            "cost_profiles": [
                {
                    "profile_id": "base",
                    "cost_assumptions": config["policy_search"]["cost_assumptions"],
                }
            ],
            "terminal_pairs": [],
        }
        if case_request is not None:
            eval_spec["cases"].append(
                {
                    "case_id": "other",
                    "trace_id": "other",
                    "split": "HOLDOUT",
                    "seed": 8,
                    "input_source": "SEALED_MATHEMATICAL_REQUEST",
                    "input_snapshot_id": "EVAL-OTHER",
                    "input_content_hash": digest(case_request),
                }
            )
    spec = seal_policy_analysis_spec(
        {
            "contract_id": "inventory-policy-analysis-spec",
            "contract_version": "1.0.0",
            "execution_mode": "DEVELOPMENT_SHADOW",
            "config_schema_version": "2.1.0",
            "config_id": claim["config_id"],
            "config_revision_id": claim["config_revision_id"],
            "config_hash": snapshot["config_hash"],
            "classification_snapshot_id": snapshot["classification_snapshot_id"],
            "classification_snapshot_content_hash": snapshot["content_hash"],
            "effective_policy_content_hash": snapshot["effective_policy_content_hash"],
            "search_spec": config["policy_search"],
            "search_spec_content_hash": policy_search_hash(config["policy_search"]),
            "evaluation_spec": eval_spec,
        }
    )
    claim["strategy_execution_plan"] = seal_strategy_execution_plan(
        bind_policy_analysis_spec(claim["strategy_execution_plan"], spec)
    )
    runtime = InventoryRuntimeExecutionRequest.from_dict(reseal_runtime_site_binding(raw))
    return (
        replace(
            command,
            runtime_request=runtime,
            mathematical_request=mathematical,
            policy_analysis_inputs=None,
        ),
        config,
        snapshot,
    )


def catalog(tmp_path, command, config, snapshot):
    store = LocalPolicyAnalysisInputCatalog(tmp_path, DEPLOYMENT)
    owner = dict(
        tenant_id=command.runtime_request.tenant_id, project_id=command.runtime_request.project_id
    )
    for kind, sid, payload in (
        ("CONFIG", command.runtime_request.value["claim"]["config_revision_id"], config),
        ("CLASSIFICATION", snapshot["classification_snapshot_id"], snapshot),
        (
            "MATHEMATICAL_BASE",
            command.mathematical_request.to_dict()["policy_input"]["snapshot_id"],
            command.mathematical_request.to_dict(),
        ),
    ):
        receipt = store.publish(kind=kind, snapshot_id=sid, payload=payload, **owner)
        assert receipt == store.publish(kind=kind, snapshot_id=sid, payload=payload, **owner)
    return store


@pytest.mark.asyncio
@pytest.mark.parametrize("evaluation", [False, True])
async def test_real_automatic_producer_artifact_and_offline_recovery(tmp_path, evaluation):
    command, config, snapshot = automatic_command(evaluation)
    store = catalog(tmp_path, command, config, snapshot)
    base = ExactMathematicalCommandResolver(
        store,
        mathematical_implementation_content_hash=command.mathematical_implementation_content_hash,
        psi_simulator_implementation_content_hash=command.psi_simulator_implementation_content_hash,
    )
    resolver = PolicyAnalysisCommandResolver(
        base_resolver=base, reader=store, deployment=DEPLOYMENT
    )
    assert command.policy_analysis_inputs is None
    resolved = await resolver.resolve(command.runtime_request)
    repeated = await resolver.resolve(command.runtime_request)
    assert resolved.policy_analysis_inputs == repeated.policy_analysis_inputs
    assert (resolved.policy_analysis_inputs.evaluation_result is not None) == evaluation
    repository = SqliteInventoryEvidenceUnitOfWork(tmp_path / "evidence.sqlite3")
    pipeline = InventoryResultPipelineHandler(
        command_resolver=resolver,
        orchestrator=RunPsiBundleUseCase(DEPLOYMENT),
        persistence=PersistInventoryResultBundleUseCase(DEPLOYMENT, repository),
        evidence_repository=repository,
    )

    async def report(**_):
        return InventoryRuntimeStageEventReceipt(
            site_row_version=2, run_status="running", replayed=False
        )

    result = await pipeline.execute(command.runtime_request, report)

    # Recovery must never resolve even if the original catalog or producer is unavailable.
    class Unavailable:
        async def resolve(self, request):
            raise AssertionError("must recover from durable artifacts")

    pipeline.command_resolver = Unavailable()
    assert await pipeline.execute(command.runtime_request, report) == result
    outbox = repository.find_outbox(command.runtime_request.engine_run_id, 1).message.payload()
    manifest = repository.read_artifact(outbox["policy_analysis_reference"]).document()
    assert manifest["evaluation_status"] == ("AVAILABLE" if evaluation else "NOT_BOUND")
    assert not manifest["operational_policy_approved"]
    source = next(c for c in manifest["children"] if c["role"] == "ACTUAL_INPUT")
    assert source["contract_version"] == "1.1.0"
    schema = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "schemas/inventory-policy-analysis-manifest.schema.json"
        ).read_text()
    )
    Draft202012Validator(schema).validate(manifest)
    assert (
        repository.read_artifact(source["artifact_reference"]).document()[
            "policy_analysis_spec_content_hash"
        ]
        == command.runtime_request.strategy_execution_plan["policy_analysis_spec"]["content_hash"]
    )


@pytest.mark.asyncio
async def test_missing_wrong_scope_or_payload_rejected(tmp_path):
    command, config, snapshot = automatic_command()
    store = catalog(tmp_path, command, config, snapshot)
    spec = command.runtime_request.strategy_execution_plan["policy_analysis_spec"]
    with pytest.raises(InventoryInputError, match="IDENTITY_MISMATCH"):
        await store.read(
            kind="CONFIG",
            snapshot_id=spec["config_revision_id"],
            content_hash=spec["config_hash"],
            tenant_id="other",
            project_id=command.runtime_request.project_id,
        )
    path = tmp_path / ("CONFIG-" + spec["config_revision_id"] + ".json")
    document = json.loads(path.read_text())
    document["payload"]["policy_search"]["enabled"] = False
    path.write_text(json.dumps(document))
    resolver = PolicyAnalysisCommandResolver(
        base_resolver=_Resolver(command), reader=store, deployment=DEPLOYMENT
    )
    with pytest.raises(InventoryInputError, match="IDENTITY_MISMATCH"):
        await resolver.resolve(command.runtime_request)
    path.unlink()
    with pytest.raises(InventoryInputError, match="NOT_FOUND"):
        await resolver.resolve(command.runtime_request)


@pytest.mark.asyncio
async def test_bound_plan_cannot_silently_skip_analysis(tmp_path):
    command, _, _ = automatic_command()
    repository = SqliteInventoryEvidenceUnitOfWork(tmp_path / "evidence.sqlite3")
    pipeline = InventoryResultPipelineHandler(
        command_resolver=_Resolver(command),
        orchestrator=RunPsiBundleUseCase(DEPLOYMENT),
        persistence=PersistInventoryResultBundleUseCase(DEPLOYMENT, repository),
        evidence_repository=repository,
    )

    async def report(**_):
        return InventoryRuntimeStageEventReceipt(
            site_row_version=2, run_status="running", replayed=False
        )

    with pytest.raises(InventoryInputError, match="PRODUCER_BINDING_REQUIRED"):
        await pipeline.execute(command.runtime_request, report)
    assert repository.find_outbox(command.runtime_request.engine_run_id, 1) is None


def test_closed_spec_and_schema_reject_disabled_evaluation_and_extra_keys():
    command, _, _ = automatic_command(True)
    spec = command.runtime_request.strategy_execution_plan["policy_analysis_spec"]
    schema = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "schemas/inventory_policy_analysis_spec.schema.json"
        ).read_text()
    )
    validator = Draft202012Validator(schema)
    validator.validate(spec)
    wrong = copy.deepcopy(spec)
    wrong["extra"] = True
    with pytest.raises(ValueError):
        validate_policy_analysis_spec(wrong)
    with pytest.raises(ValidationError):
        validator.validate(wrong)
    wrong = copy.deepcopy(spec)
    wrong["search_spec"]["enabled"] = False
    wrong["search_spec_content_hash"] = policy_search_hash(wrong["search_spec"])
    wrong.pop("content_hash")
    with pytest.raises(ValueError, match="DISABLED_SEARCH"):
        seal_policy_analysis_spec(wrong)


@pytest.mark.asyncio
async def test_sealed_evaluation_case_missing_input_fails_not_a_placeholder(tmp_path):
    base, _, _ = automatic_command()
    case = base.mathematical_request.to_dict()
    command, config, snapshot = automatic_command(True, case)
    store = catalog(tmp_path, command, config, snapshot)
    resolver = PolicyAnalysisCommandResolver(
        base_resolver=_Resolver(command), reader=store, deployment=DEPLOYMENT
    )
    with pytest.raises(InventoryInputError, match="SOURCE_NOT_FOUND"):
        await resolver.resolve(command.runtime_request)


def test_platform_engine_spec_plan_hash_and_json_schema_match():
    import sys

    backend = Path(__file__).resolve().parents[3] / "dsai-platform" / "backend"
    if not backend.is_dir():
        pytest.skip("adjacent Platform required")
    sys.path.insert(0, str(backend))
    sys.path.insert(0, str(backend.parent))
    from platform_api.inventory_engine_run_contract import InventoryStrategyExecutionPlan
    from platform_api.inventory_policy_analysis_spec import (
        validate_policy_analysis_spec as platform_validate,
    )

    command, _, _ = automatic_command(True)
    plan = command.runtime_request.strategy_execution_plan
    assert platform_validate(plan["policy_analysis_spec"]) == plan["policy_analysis_spec"]
    assert InventoryStrategyExecutionPlan.model_validate(plan).model_dump(mode="json") == plan
    schema = json.loads(
        (
            Path(__file__).resolve().parents[2] / "schemas/inventory_result_bundle.schema.json"
        ).read_text()
    )
    Draft202012Validator(schema).validate(plan)
    spec_schema = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "schemas/inventory_policy_analysis_spec.schema.json"
        ).read_text()
    )
    assert spec_schema == json.loads(
        (
            backend / "platform_api/contracts/inventory-policy-analysis-spec-v1.schema.json"
        ).read_text()
    )


def test_writer_and_recovery_cannot_omit_required_analysis(tmp_path, monkeypatch):
    from dsio_inventory_engine.inventory_evidence import application

    command, _, _ = automatic_command()
    run = RunPsiBundleUseCase(DEPLOYMENT).execute(command)
    repository = SqliteInventoryEvidenceUnitOfWork(tmp_path / "evidence.sqlite3")
    writer = PersistInventoryResultBundleUseCase(DEPLOYMENT, repository)
    args = dict(
        strategy_execution_plan=command.runtime_request.strategy_execution_plan,
        runtime_request=command.runtime_request,
    )
    with pytest.raises(InventoryInputError, match="EVIDENCE_BINDING_REQUIRED"):
        writer.execute(run, **args)
    assert repository.find_outbox(command.runtime_request.engine_run_id, 1) is None
    # Fault-inject a pre-fix incomplete durable record, then verify new Reader fails closed.
    enforce = application._require_policy_analysis_presence
    monkeypatch.setattr(application, "_require_policy_analysis_presence", lambda *args: None)
    receipt = writer.execute(run, **args)
    monkeypatch.setattr(application, "_require_policy_analysis_presence", enforce)
    reader = ReadAndVerifyInventoryResultBundleUseCase(DEPLOYMENT, repository)
    with pytest.raises(InventoryInputError, match="EVIDENCE_BINDING_REQUIRED"):
        reader.execute(receipt.bundle_reference, **args)


def test_full_classification_for_another_config_is_not_input_authority():
    from dsio_inventory_engine.run_inventory.policy_analysis_producer import (
        produce_policy_analysis_inputs,
    )

    command, config, snapshot = automatic_command()
    wrong = copy.deepcopy(snapshot)
    wrong["config_id"] = str(uuid.UUID(int=999))
    unsigned = {
        k: v for k, v in wrong.items() if k not in ("content_hash", "classification_snapshot_id")
    }
    wrong["content_hash"] = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    wrong["classification_snapshot_id"] = str(uuid.uuid5(SNAPSHOT_NAMESPACE, wrong["content_hash"]))
    spec = copy.deepcopy(command.runtime_request.strategy_execution_plan["policy_analysis_spec"])
    spec.pop("content_hash")
    spec["classification_snapshot_id"] = wrong["classification_snapshot_id"]
    spec["classification_snapshot_content_hash"] = wrong["content_hash"]
    spec = seal_policy_analysis_spec(spec)
    with pytest.raises(InventoryInputError, match="SOURCE_BINDING_MISMATCH"):
        produce_policy_analysis_inputs(
            deployment=DEPLOYMENT,
            specification=spec,
            mathematical_request=command.mathematical_request,
            config_values=config,
            classification_snapshot=wrong,
        )


@pytest.mark.asyncio
async def test_producer_runs_off_event_loop_and_rejects_precomputed_injection(
    tmp_path, monkeypatch
):
    import threading
    from dsio_inventory_engine.run_inventory import policy_analysis_producer

    command, config, snapshot = automatic_command()
    store = catalog(tmp_path, command, config, snapshot)
    actual = policy_analysis_producer.produce_policy_analysis_inputs
    threads = []
    caller_thread = threading.get_ident()

    def capture(**kwargs):
        threads.append(threading.get_ident())
        return actual(**kwargs)

    monkeypatch.setattr(policy_analysis_producer, "produce_policy_analysis_inputs", capture)
    resolver = PolicyAnalysisCommandResolver(
        base_resolver=_Resolver(command), reader=store, deployment=DEPLOYMENT
    )
    resolved = await resolver.resolve(command.runtime_request)
    assert threads and threads[0] != caller_thread
    injected = PolicyAnalysisCommandResolver(
        base_resolver=_Resolver(resolved), reader=store, deployment=DEPLOYMENT
    )
    with pytest.raises(InventoryInputError, match="PRECOMPUTED_INPUT_FORBIDDEN"):
        await injected.resolve(command.runtime_request)


@pytest.mark.asyncio
async def test_new_companion_is_readable_by_platform_and_remains_shadow(tmp_path):
    import sys

    backend = Path(__file__).resolve().parents[3] / "dsai-platform" / "backend"
    if not backend.is_dir():
        pytest.skip("adjacent Platform required")
    sys.path.insert(0, str(backend))
    sys.path.insert(0, str(backend.parent))
    from platform_api.services.inventory_sqlite_artifact_reader import (
        SqliteInventoryRuntimeArtifactReader,
    )
    from platform_api.services.inventory_policy_analysis import InventoryPolicyAnalysisReader
    from dsio_inventory_engine.inventory_evidence.policy_analysis import build_policy_analysis

    command, config, snapshot = automatic_command(True)
    store = catalog(tmp_path, command, config, snapshot)
    resolver = PolicyAnalysisCommandResolver(
        base_resolver=_Resolver(command), reader=store, deployment=DEPLOYMENT
    )
    resolved = await resolver.resolve(command.runtime_request)
    run = RunPsiBundleUseCase(DEPLOYMENT).execute(resolved)
    analysis = build_policy_analysis(
        deployment=DEPLOYMENT,
        request=command.runtime_request,
        bundle=run.result_bundle,
        inputs=resolved.policy_analysis_inputs,
        mathematical_request=command.mathematical_request,
    )
    repository = SqliteInventoryEvidenceUnitOfWork(tmp_path / "evidence.sqlite3")
    PersistInventoryResultBundleUseCase(DEPLOYMENT, repository).execute(
        run,
        strategy_execution_plan=command.runtime_request.strategy_execution_plan,
        runtime_request=command.runtime_request,
        policy_analysis_evidence=analysis,
    )
    record = {
        key: analysis.manifest[key]
        for key in (
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
    record.update(
        plan_hash=command.runtime_request.strategy_execution_plan["content_hash"],
        analysis_required=True,
        analysis_spec_hash=command.runtime_request.strategy_execution_plan["policy_analysis_spec"][
            "content_hash"
        ],
        classification_content_hash=snapshot["content_hash"],
    )
    response = await InventoryPolicyAnalysisReader(
        SqliteInventoryRuntimeArtifactReader(tmp_path / "evidence.sqlite3")
    ).read(record, limit=20, offset=0)
    assert response.status == "AVAILABLE" and response.evaluation is not None
    assert not response.operational_policy_approved and not response.automatic_publish_allowed


@pytest.mark.asyncio
async def test_evaluation_generated_from_a_sealed_new_demand_path(tmp_path):
    from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
    from dsio_inventory_engine.inventory_evidence.policy_analysis import build_policy_analysis

    base, _, _ = automatic_command()
    raw = base.mathematical_request.to_dict()
    canonical = raw["recommendation"]["canonical_input"]
    canonical["snapshots"]["forecast"]["rows"][0]["forecast_qty"] = "13"
    sealed = CanonicalInputRequest.from_dict(reseal(canonical))
    raw["recommendation"]["canonical_input"] = sealed.to_dict()
    raw["recommendation"]["execution"]["canonical_input_hash"] = sealed.input_hash
    raw["policy_input"]["context"]["canonical_input_hash"] = sealed.input_hash
    case = MathematicalPolicyRequest.from_dict(reseal_math(raw)).to_dict()
    command, config, snapshot = automatic_command(True, case)
    store = catalog(tmp_path, command, config, snapshot)
    store.publish(
        kind="MATHEMATICAL_REQUEST",
        snapshot_id="EVAL-OTHER",
        payload=case,
        tenant_id=command.runtime_request.tenant_id,
        project_id=command.runtime_request.project_id,
    )
    resolver = PolicyAnalysisCommandResolver(
        base_resolver=_Resolver(command), reader=store, deployment=DEPLOYMENT
    )
    resolved = await resolver.resolve(command.runtime_request)
    inputs = resolved.policy_analysis_inputs
    assert inputs.evaluation_requests == {"other": case}
    assert len(inputs.evaluation_sources) == 2
    run = RunPsiBundleUseCase(DEPLOYMENT).execute(resolved)
    evidence = build_policy_analysis(
        deployment=DEPLOYMENT,
        request=command.runtime_request,
        bundle=run.result_bundle,
        inputs=inputs,
        mathematical_request=command.mathematical_request,
    )
    assert evidence.manifest["evaluation_status"] == "AVAILABLE"
    source = next(
        a.document()
        for a in evidence.artifacts
        if a.contract_key == "inventory.policy_analysis_inputs"
    )
    assert source["evaluation_requests"]["other"] == case
    # Keeping an old precomputed evaluation after changing a source cannot pass replay.
    invalid = copy.deepcopy(inputs)
    invalid.evaluation_requests["other"]["recommendation"]["canonical_input"]["snapshots"][
        "forecast"
    ]["rows"][0]["forecast_qty"] = "1"
    with pytest.raises(InventoryInputError):
        build_policy_analysis(
            deployment=DEPLOYMENT,
            request=command.runtime_request,
            bundle=run.result_bundle,
            inputs=invalid,
            mathematical_request=command.mathematical_request,
        )

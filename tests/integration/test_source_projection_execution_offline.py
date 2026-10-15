"""Exact approved inputs → installed implementation → sealed projection, without DB."""

import copy
import hashlib
import json
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from dsio_inventory_engine.classify_inventory.application import SNAPSHOT_NAMESPACE, config_hash
from dsio_inventory_engine.infrastructure.runtime_build import installed_runtime_build
from dsio_inventory_engine.infrastructure.runtime_input_files import (
    LocalRuntimeInputManifestCatalog,
)
from dsio_inventory_engine.infrastructure.sqlite import SqliteInventoryEvidenceUnitOfWork
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.policy_analysis_spec import seal_policy_analysis_spec
from dsio_inventory_engine.inventory_contracts.policy_search import policy_search_hash
from dsio_inventory_engine.inventory_contracts.replenishment import (
    replenishment_config_content_hash,
)
from dsio_inventory_engine.inventory_contracts.result_bundle import seal_strategy_execution_plan
from dsio_inventory_engine.inventory_contracts.runtime import InventoryRuntimeExecutionRequest
from dsio_inventory_engine.inventory_contracts.values import digest, InventoryInputError
from dsio_inventory_engine.inventory_evidence.contracts import ArtifactObject
from dsio_inventory_engine.run_inventory.approved_inputs import build_runtime_input_manifest
from dsio_inventory_engine.run_inventory.projection_execution import (
    InventorySourceProjectionExecution,
)
from tests.integration.test_automatic_policy_analysis_offline import automatic_command
from tests.integration.test_approved_runtime_inputs_offline import report
from tests.integration.test_psi_orchestrator_offline import DEPLOYMENT
from tests.support.runtime_fixtures import reseal_runtime_site_binding
from tests.unit.test_seven_axis_segmentation import inputs

CATALOG = Path(__file__).resolve().parents[2] / "schemas/inventory-dsim-physical-contract-v1.json"


def source_fixture(*, analysis=False):
    with patch(
        "tests.support.policy_search_fixtures.inputs",
        return_value=replace(
            inputs(),
            actual_yyyyww="202639",
            decision_yyyyww="202640",
            actual_close_window_start_yyyyww="202540",
        ),
    ):
        command, config, snapshot = automatic_command()
    raw = command.runtime_request.to_dict()
    claim = raw["claim"]
    config["policy_search"]["enabled"] = analysis
    cfg_hash = config_hash(config)
    snapshot["config_hash"] = cfg_hash
    for row in snapshot["items"]:
        row["classification_config_hash"] = cfg_hash
        row["classification_config_binding_hash"] = digest(
            {
                "contract_version": "2.0.0",
                "item_id": row["item_id"],
                "classification_config_hash": cfg_hash,
                "effective_policy_hash": row["effective_policy_hash"],
            }
        )
    snapshot["content_hash"] = hashlib.sha256(
        json.dumps(
            {
                k: v
                for k, v in snapshot.items()
                if k not in {"content_hash", "classification_snapshot_id"}
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    snapshot["classification_snapshot_id"] = str(
        uuid.uuid5(SNAPSHOT_NAMESPACE, snapshot["content_hash"])
    )
    math = command.mathematical_request.to_dict()
    execution = math["recommendation"]["execution"]
    execution["effective_policy_binding"].update(
        classification_snapshot_id=snapshot["classification_snapshot_id"],
        classification_config_hash=cfg_hash,
    )
    for row in execution["effective_item_policies"]:
        source = next(i for i in snapshot["items"] if i["item_id"] == row["item_id"])
        row.update({k: source[k] for k in row})
    math = MathematicalPolicyRequest.from_dict(math)
    claim["config_hash"] = cfg_hash
    for b in claim["input_bindings"]:
        if b["input_type"] == "INVENTORY_POLICY":
            b["source_content_hash"] = cfg_hash
        if b["input_type"] == "INVENTORY_CLASSIFICATION":
            b["source_snapshot_id"] = snapshot["classification_snapshot_id"]
    plan = claim["strategy_execution_plan"]
    plan.pop("content_hash")
    plan["classification_config_hash"] = cfg_hash
    spec = {k: v for k, v in plan["policy_analysis_spec"].items() if k != "content_hash"}
    spec.update(
        config_hash=cfg_hash,
        classification_snapshot_id=snapshot["classification_snapshot_id"],
        classification_snapshot_content_hash=snapshot["content_hash"],
        search_spec=config["policy_search"],
        search_spec_content_hash=policy_search_hash(config["policy_search"]),
    )
    plan["policy_analysis_spec"] = seal_policy_analysis_spec(spec)
    build = installed_runtime_build()
    plan["operational_strategy"]["implementation_content_hash"] = build["implementations"][
        "mathematical"
    ]["implementation_content_hash"]
    plan["psi_simulator"]["implementation_content_hash"] = build["implementations"][
        "psi_simulator"
    ]["implementation_content_hash"]
    plan["operational_strategy"]["replenishment_config_content_hash"] = (
        replenishment_config_content_hash(execution)
    )
    plan = seal_strategy_execution_plan(plan)
    manifest, plan = build_runtime_input_manifest(
        plan=plan,
        mathematical_request=math,
        config_values=config,
        classification_snapshot=snapshot,
        tenant_id=claim["tenant_id"],
        project_id=claim["project_id"],
        scope=claim["scope"],
        snapshot_id="PROJECTION-EXACT-INPUT",
    )
    claim["strategy_execution_plan"] = plan
    return (
        InventoryRuntimeExecutionRequest.from_dict(reseal_runtime_site_binding(raw)),
        manifest,
        build,
    )


def assembly(tmp_path, *, analysis=False):
    request, manifest, build = source_fixture(analysis=analysis)
    source = LocalRuntimeInputManifestCatalog(tmp_path)
    source.publish(manifest)
    store = SqliteInventoryEvidenceUnitOfWork(tmp_path / "evidence.sqlite")
    runtime = InventorySourceProjectionExecution(
        deployment=DEPLOYMENT,
        input_provider=source,
        evidence_repository=store,
        catalog=json.loads(CATALOG.read_text()),
        expected_build=build,
    )
    return request, manifest, build, store, runtime


@pytest.mark.asyncio
@pytest.mark.parametrize("analysis", [False, True])
async def test_source_build_and_exact_pointers_recover_without_source(tmp_path, analysis):
    request, manifest, build, store, runtime = assembly(tmp_path, analysis=analysis)
    result = await runtime.execute(request=request, report=report, chunk_size=2)
    doc = result["document"]
    assert doc["installed_build"] == build
    original = json.loads(
        json.dumps(
            next(r["document"] for r in manifest["resources"] if r["kind"] == "CLASSIFICATION")
        )
    )
    sealed = store.read_artifact(doc["classification_source"]["artifact_reference"]).document()
    assert sealed["classification_snapshot"] == original
    outbox = store.find_outbox(request.engine_run_id, 1)
    assert ("policy_analysis_reference" in outbox.message.payload()) == analysis
    projected = runtime.verifier.execute(
        request=request,
        reference=doc["projection"]["artifact_reference"],
        expected_hash=doc["projection"]["content_hash"],
    )
    row = projected.tables["dsim.tb_io_plan_context"][0]
    assert row["classification_content_hash"] == original["content_hash"]
    binding_row = next(
        r
        for r in projected.tables["dsim.tb_io_snapshot_manifest"]
        if r["input_type"] == "INVENTORY_CLASSIFICATION"
    )
    assert (
        binding_row["source_content_hash"]
        == original["effective_policy_content_hash"]
        != original["content_hash"]
    )

    class NoSource:
        async def resolve(self, request):
            raise AssertionError("recovery must not read a Source")

    restarted = InventorySourceProjectionExecution(
        deployment=DEPLOYMENT,
        input_provider=NoSource(),
        evidence_repository=SqliteInventoryEvidenceUnitOfWork(tmp_path / "evidence.sqlite"),
        catalog=json.loads(CATALOG.read_text()),
        expected_build=build,
    )
    assert await restarted.execute(request=request, report=report, chunk_size=1) == result
    assert (
        restarted.verify(
            request=request,
            reference=result["artifact_reference"],
            expected_hash=result["content_hash"],
        )
        == result
    )
    assert outbox.status == "WITHHELD_FOR_REVIEW"  # no publication or Gate bypass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "installed_build",
        "result_bundle_content_hash",
        "runtime_input_binding",
        "classification_source",
        "projection",
    ],
)
async def test_resealed_execution_pointer_tampering_is_blocked(tmp_path, field):
    request, _, _, store, runtime = assembly(tmp_path)
    result = await runtime.execute(request=request, report=report)
    doc = copy.deepcopy(result["document"])
    if isinstance(doc[field], dict):
        doc[field][next(iter(doc[field]))] = "f" * 64
    else:
        doc[field] = "f" * 64
    doc["content_hash"] = digest({k: v for k, v in doc.items() if k != "content_hash"})
    obj = ArtifactObject.from_document(
        reference="artifact:inventory:forged-execution",
        contract_key=result["document"]["source_contract_key"],
        document=doc,
    )
    store.commit_artifacts((obj,))
    with pytest.raises((InventoryInputError, KeyError, ValueError)):
        runtime.verify(request=request, reference=obj.reference, expected_hash=obj.content_hash)


@pytest.mark.asyncio
async def test_wrong_build_stops_before_source_or_artifact_reads(tmp_path):
    request, _, build, _, runtime = assembly(tmp_path)
    runtime.expected_build["package_version"] = "foreign"
    with pytest.raises(InventoryInputError, match="RUNTIME_INSTALLED_BUILD_MISMATCH"):
        await runtime.execute(request=request, report=report)


@pytest.mark.asyncio
async def test_missing_classification_source_artifact_blocks_recovery(tmp_path):
    request, _, _, store, runtime = assembly(tmp_path)
    result = await runtime.execute(request=request, report=report)
    reference = result["document"]["classification_source"]["artifact_reference"]
    original = store.read_artifact

    def missing(ref):
        if ref == reference:
            raise ValueError("missing classification source")
        return original(ref)

    store.read_artifact = missing
    with pytest.raises(ValueError, match="missing classification source"):
        await runtime.execute(request=request, report=report)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["projection_failure", "execution_ack_loss"])
async def test_projection_failure_or_commit_response_loss_recovers_sealed_psi(tmp_path, stage):
    request, _, _, store, runtime = assembly(tmp_path)
    original_commit = store.commit_artifacts

    def fail(artifacts):
        if stage == "projection_failure":
            raise OSError("projection unavailable")
        result = original_commit(artifacts)
        if any(a.contract_key == "inventory.dsim_source_execution" for a in artifacts):
            raise OSError("execution commit response lost")
        return result

    store.commit_artifacts = fail
    with pytest.raises(OSError):
        await runtime.execute(request=request, report=report)
    outbox = store.find_outbox(request.engine_run_id, 1)
    assert outbox and outbox.message.payload()["classification_source"]
    store.commit_artifacts = original_commit

    class Missing:
        async def resolve(self, *_):
            raise AssertionError("sealed PSI recovery must not reread Source")

    runtime.pipeline.command_resolver.provider = Missing()

    def no_psi(*_):
        raise AssertionError("sealed PSI must not be recomputed")

    runtime.pipeline.orchestrator.execute = no_psi
    result = await runtime.execute(request=request, report=report)
    assert result["document"]["result_outbox_content_hash"] == outbox.message.payload_content_hash


@pytest.mark.asyncio
async def test_classification_header_change_without_new_claim_is_rejected_before_psi(tmp_path):
    request, manifest, build = source_fixture()

    class TamperedSource:
        async def resolve(self, *_):
            changed = copy.deepcopy(manifest)
            classification = next(r for r in changed["resources"] if r["kind"] == "CLASSIFICATION")
            classification["document"]["as_of_yyyyww"] = "202641"
            return changed

    store = SqliteInventoryEvidenceUnitOfWork(tmp_path / "rejected.sqlite")
    runtime = InventorySourceProjectionExecution(
        deployment=DEPLOYMENT,
        input_provider=TamperedSource(),
        evidence_repository=store,
        catalog=json.loads(CATALOG.read_text()),
        expected_build=build,
    )
    with pytest.raises(ValueError):
        await runtime.execute(request=request, report=report)
    assert store.find_outbox(request.engine_run_id, 1) is None

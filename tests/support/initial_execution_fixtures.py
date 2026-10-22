"""Uncalculated, sealed development inputs for the independent first-call path."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

from dsio_inventory_engine.infrastructure.runtime_build import installed_runtime_build
from dsio_inventory_engine.infrastructure.runtime_input_files import (
    LocalRuntimeInputManifestCatalog,
)
from dsio_inventory_engine.infrastructure.sqlite import SqliteInventoryEvidenceUnitOfWork
from dsio_inventory_engine.infrastructure.target_pipeline_artifacts import (
    LocalTargetPipelineArtifactStore,
)
from dsio_inventory_engine.inventory_contracts.policy_analysis_spec import (
    bind_policy_analysis_spec,
    seal_policy_analysis_spec,
)
from dsio_inventory_engine.inventory_contracts.policy_search import policy_search_hash
from dsio_inventory_engine.inventory_contracts.result_bundle import seal_strategy_execution_plan
from dsio_inventory_engine.inventory_contracts.runtime import InventoryRuntimeExecutionRequest
from dsio_inventory_engine.inventory_evidence.policy_analysis import run_shadow_request
from dsio_inventory_engine.recommend_replenishment.application.target_durable import (
    RunDurableTargetPolicyPipelineUseCase,
)
from dsio_inventory_engine.run_inventory.approved_inputs import build_runtime_input_manifest
from dsio_inventory_engine.run_inventory.initial_target_execution import (
    InventoryInitialTargetExecution,
)
from tests.support.target_result_link_fixtures import command_parts, DEPLOYMENT
from tests.support.target_execution_fixtures import boundary
from tests.support.catalog_search_fixtures import catalog_bundle
from tests.support.runtime_fixtures import reseal_runtime_site_binding


def initial_world(root, kernel, *, mode="ABC_V1", fsn_action="REVIEW", all_failed=False):
    command, config, snapshot = command_parts(fsn_action=fsn_action, all_failed=all_failed)
    raw = copy.deepcopy(command.runtime_request.to_dict())
    claim = raw["claim"]
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
            "evaluation_spec": {"mode": "DISABLED"},
        }
    )
    plan = bind_policy_analysis_spec(claim["strategy_execution_plan"], spec)
    plan.pop("content_hash", None)
    build = installed_runtime_build()
    plan["operational_strategy"]["implementation_content_hash"] = build["implementations"][
        "mathematical"
    ]["implementation_content_hash"]
    plan["psi_simulator"]["implementation_content_hash"] = build["implementations"][
        "psi_simulator"
    ]["implementation_content_hash"]
    manifest, plan = build_runtime_input_manifest(
        plan=seal_strategy_execution_plan(plan),
        mathematical_request=command.mathematical_request,
        config_values=config,
        classification_snapshot=snapshot,
        tenant_id=claim["tenant_id"],
        project_id=claim["project_id"],
        scope=claim["scope"],
        snapshot_id="INITIAL-SEALED-MANIFEST",
    )
    claim["strategy_execution_plan"] = plan
    request = InventoryRuntimeExecutionRequest.from_dict(reseal_runtime_site_binding(raw))
    # The actual sealed Manifest, not a pre-normalization fixture, is authoritative.
    resources = {r["kind"]: r["document"] for r in manifest["resources"]}
    config, snapshot = resources["CONFIG"], resources["CLASSIFICATION"]
    catalogs = root / "manifests"
    target_root = root / "target"
    catalogs.mkdir()
    target_root.mkdir()
    provider = LocalRuntimeInputManifestCatalog(catalogs)
    provider.publish(manifest)
    target_repo = LocalTargetPipelineArtifactStore(target_root)
    target = RunDurableTargetPolicyPipelineUseCase(DEPLOYMENT, kernel, target_repo)
    math = run_shadow_request(command.mathematical_request)
    sources = {"config_values": config, "classification_snapshot": snapshot}
    if mode != "ABC_V1":
        _, producer, catalog = catalog_bundle(mode=mode, parts=(math.to_dict(), config, snapshot))
        sources.update(
            producer=producer, catalog=catalog, expected_catalog_hash=catalog.content_hash
        )
    input_ref = target.seal_input(
        math, boundary(math.to_dict()["recommendation"], kernel.dll_sha256), **sources
    )
    db_path = root / "artifacts.sqlite"
    store = SqliteInventoryEvidenceUnitOfWork(db_path)
    catalog = json.loads(
        (
            Path(__file__).resolve().parents[2] / "schemas/inventory-dsim-physical-contract-v1.json"
        ).read_text()
    )
    kwargs = dict(
        deployment=DEPLOYMENT,
        input_provider=provider,
        target_storage=target,
        evidence_repository=store,
        catalog=catalog,
        expected_build=build,
    )
    return SimpleNamespace(
        request=request,
        input_ref=input_ref,
        manifest=manifest,
        build=build,
        target=target,
        target_repo=target_repo,
        target_root=target_root,
        provider=provider,
        store=store,
        db_path=db_path,
        runtime_kwargs=kwargs,
        runtime=InventoryInitialTargetExecution(**kwargs),
    )

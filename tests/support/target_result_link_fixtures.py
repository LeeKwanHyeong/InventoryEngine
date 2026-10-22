"""New development-only Run inputs; never alter existing Golden/config fixtures."""

import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import uuid
from unittest.mock import patch

from dsio_inventory_engine.classify_inventory.application import SNAPSHOT_NAMESPACE
from dsio_inventory_engine.infrastructure.sqlite import SqliteInventoryEvidenceUnitOfWork
from dsio_inventory_engine.infrastructure.target_pipeline_artifacts import (
    LocalTargetPipelineArtifactStore,
)
from dsio_inventory_engine.inventory_contracts.effective_policy_v2 import EFFECTIVE_POLICY_V2_FIELDS
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.replenishment import (
    replenishment_config_content_hash,
)
from dsio_inventory_engine.inventory_contracts.result_bundle import seal_strategy_execution_plan
from dsio_inventory_engine.inventory_contracts.runtime import InventoryRuntimeExecutionRequest
from dsio_inventory_engine.inventory_evidence.application import (
    PersistInventoryResultBundleUseCase,
    ReadAndVerifyInventoryResultBundleUseCase,
)
from dsio_inventory_engine.inventory_evidence.policy_analysis import (
    PolicyAnalysisInputs,
    build_policy_analysis,
    run_shadow_request,
)
from dsio_inventory_engine.recommend_replenishment.application.policy_search import (
    RunPolicySearchUseCase,
)
from dsio_inventory_engine.inventory_evidence.projection import (
    PersistDsimProjectionUseCase,
    VerifyDsimProjectionUseCase,
)
from dsio_inventory_engine.inventory_evidence.projection_source import DsimSourceProjector
from dsio_inventory_engine.inventory_evidence.target_result_link import TargetResultLinkUseCase
from dsio_inventory_engine.recommend_replenishment.application.target_durable import (
    RunDurableTargetPolicyPipelineUseCase,
)
from dsio_inventory_engine.run_inventory.psi_orchestrator import RunPsiBundleUseCase
from tests.integration.test_psi_orchestrator_offline import _bound_command, DEPLOYMENT
from tests.support.catalog_search_fixtures import catalog_bundle
from tests.support.math_fixtures import build_math, reseal_math
from tests.support.policy_search_fixtures import GOLDEN, bundle
from tests.support.runtime_fixtures import reseal_runtime_site_binding
from tests.support.target_execution_fixtures import boundary
from tests.unit.test_seven_axis_segmentation import inputs, v2_values


def command_parts(*, fsn_action="REVIEW", all_failed=False):
    command = _bound_command(action=fsn_action)
    raw = command.mathematical_request.to_dict()
    history = build_math(
        {
            "id": "target-link-history",
            "history": "constant",
            "profile": {"lookback_weeks": 26, "stddev_ddof": 0},
        }
    )["policy_input"]
    for key in ("calendar", "history", "profile"):
        raw["policy_input"][key] = copy.deepcopy(history[key])
    search = copy.deepcopy(GOLDEN["search_config"])
    search["lookback_weeks"] = 26
    config_values = v2_values()
    config_values["policy_overlays"]["fsn_order_action"]["N"] = fsn_action
    with patch(
        "tests.support.policy_search_fixtures.inputs",
        return_value=replace(
            inputs(),
            actual_yyyyww="202639",
            decision_yyyyww="202640",
            actual_close_window_start_yyyyww="202540",
        ),
    ):
        raw, config, snapshot = bundle(reseal_math(raw), search, config_values=config_values)
    snapshot = copy.deepcopy(snapshot)
    dispatch = copy.deepcopy(command.runtime_request.to_dict())
    claim = dispatch["claim"]
    snapshot.update(**{k: claim[k] for k in ("config_id", "tenant_id", "project_id")})
    unsigned = {
        k: v for k, v in snapshot.items() if k not in ("content_hash", "classification_snapshot_id")
    }
    snapshot["content_hash"] = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    snapshot["classification_snapshot_id"] = str(
        uuid.uuid5(SNAPSHOT_NAMESPACE, snapshot["content_hash"])
    )
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
    if all_failed:
        execution["item_controls"][0]["max_target_qty"] = "1"
    mathematical = MathematicalPolicyRequest.from_dict(reseal_math(raw))
    execution = mathematical.to_dict()["recommendation"]["execution"]
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
        elif binding["input_type"] == "INVENTORY_POLICY":
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
    return (
        replace(
            command,
            runtime_request=runtime,
            mathematical_request=mathematical,
            ppo_challengers={},
            policy_analysis_inputs=None,
        ),
        config,
        snapshot,
    )


def link_world(
    root,
    kernel,
    *,
    mode="ABC_V1",
    persist_target=True,
    fsn_action="REVIEW",
    all_failed=False,
    with_analysis=False,
):
    command, config, snapshot = command_parts(fsn_action=fsn_action, all_failed=all_failed)
    math = run_shadow_request(command.mathematical_request)
    sources = {"config_values": config, "classification_snapshot": snapshot}
    if mode != "ABC_V1":
        _, producer, catalog = catalog_bundle(mode=mode, parts=(math.to_dict(), config, snapshot))
        sources.update(
            producer=producer, catalog=catalog, expected_catalog_hash=catalog.content_hash
        )
    target_root = root / "target"
    target_root.mkdir()
    target_repo = LocalTargetPipelineArtifactStore(target_root)
    target = RunDurableTargetPolicyPipelineUseCase(DEPLOYMENT, kernel, target_repo)
    binding = boundary(math.to_dict()["recommendation"], kernel.dll_sha256)
    if persist_target:
        target.execute(math, binding, **sources)
    run = RunPsiBundleUseCase(DEPLOYMENT).execute(command)
    store = SqliteInventoryEvidenceUnitOfWork(root / "artifacts.sqlite")
    analysis = None
    if with_analysis:
        search = RunPolicySearchUseCase(DEPLOYMENT).execute(
            math, config_values=config, classification_snapshot=snapshot
        )
        analysis = build_policy_analysis(
            deployment=DEPLOYMENT,
            request=command.runtime_request,
            bundle=run.result_bundle,
            inputs=PolicyAnalysisInputs(search, config, snapshot),
            mathematical_request=command.mathematical_request,
        )
    persisted = PersistInventoryResultBundleUseCase(DEPLOYMENT, store).execute(
        run,
        strategy_execution_plan=command.runtime_request.strategy_execution_plan,
        runtime_request=command.runtime_request,
        classification_source=snapshot,
        policy_analysis_evidence=analysis,
    )
    link = TargetResultLinkUseCase(deployment=DEPLOYMENT, target_storage=target, repository=store)
    catalog_path = (
        Path(__file__).resolve().parents[2] / "schemas/inventory-dsim-physical-contract-v1.json"
    )
    catalog = json.loads(catalog_path.read_text())
    projector = DsimSourceProjector(
        catalog=catalog,
        bundle_verifier=ReadAndVerifyInventoryResultBundleUseCase(DEPLOYMENT, store),
        target_result_reader=link,
    )
    return SimpleNamespace(
        command=command,
        request=command.runtime_request,
        math=math,
        binding=binding,
        sources=sources,
        run=run,
        persisted=persisted,
        store=store,
        target=target,
        target_repo=target_repo,
        target_root=target_root,
        link=link,
        catalog=catalog,
        projector=projector,
        producer=PersistDsimProjectionUseCase(projector=projector, store=store),
        verifier=VerifyDsimProjectionUseCase(projector=projector, store=store),
    )

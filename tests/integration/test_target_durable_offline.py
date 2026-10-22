"""Offline application assembly; dependency probe is not a native CLR test."""

import copy

import pytest

from dsio_inventory_engine.infrastructure.target_pipeline_artifacts import (
    LocalTargetPipelineArtifactStore,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from dsio_inventory_engine.recommend_replenishment.application.target_durable import (
    RunDurableTargetPolicyPipelineUseCase,
)
from tests.support.calculation_adapter_fixtures import DEPLOYMENT
from tests.support.catalog_search_fixtures import run_bundle
from tests.support.catalog_selection_fixtures import feasible_parts
from tests.support.policy_search_fixtures import multi_item_bundle
from tests.support.selected_order_plan_fixtures import v2_parts
from tests.unit.test_target_execution import ReferenceKernel
from tests.unit.test_target_pipeline_artifacts import case


@pytest.mark.parametrize(
    "action,v2", [("REVIEW", False), ("BLOCK", False), ("REVIEW", True), ("BLOCK", True)]
)
def test_stored_and_recovered_results_do_not_upgrade_existing_fsn_gate(tmp_path, action, v2):
    _, values, _ = run_bundle()
    values = copy.deepcopy(values)
    values["policy_overlays"]["fsn_order_action"]["N"] = action
    parts = feasible_parts(values=values)
    if v2:
        parts = v2_parts(parts)
    durable, repo, request, binding, sources, expected, run_id = case(
        tmp_path, "LEGACY_STYLE", parts
    )
    written = durable.execute(request, binding, **sources)
    fresh = RunDurableTargetPolicyPipelineUseCase(
        DEPLOYMENT, ReferenceKernel(), LocalTargetPipelineArtifactStore(tmp_path)
    )
    assert fresh.recover(run_id) == written
    assert written["result"] == expected
    assert not written["automatic_publish_allowed"] and not written["automatic_order_allowed"]
    if action == "BLOCK":
        assert written["counts"]["selected_item_count"] == 0
        assert written["counts"]["order_count"] == 0


def test_full_multi_item_coverage_survives_source_object_mutation(tmp_path):
    durable, repo, request, binding, sources, expected, run_id = case(
        tmp_path, parts=multi_item_bundle()
    )
    written = durable.execute(request, binding, **sources)
    sources["classification_snapshot"]["items"] = []
    sources["config_values"].clear()
    assert durable.recover(run_id) == written
    assert written["result"] == expected
    assert written["counts"]["selection_count"] == 2


def test_different_environment_cannot_recover_development_calculation(tmp_path):
    from dsio_inventory_engine.inventory_contracts.network import DeploymentScope

    durable, repo, request, binding, sources, _, run_id = case(tmp_path)
    durable.execute(request, binding, **sources)
    wrong = RunDurableTargetPolicyPipelineUseCase(
        DeploymentScope("DSE", "PRODUCTION"), ReferenceKernel(), repo
    )
    with pytest.raises(InventoryInputError, match="DEVELOPMENT_ONLY"):
        wrong.recover(run_id)


@pytest.mark.parametrize("cause", ["no_cost", "no_demand", "all_failed"])
def test_failed_candidates_and_no_selection_reasons_survive_recovery(tmp_path, cause):
    _, values, _ = run_bundle()
    if cause == "no_cost":
        values["policy_search"]["cost_assumptions"] = None
    parts = feasible_parts(
        forecast=["0", "0", "0"] if cause == "no_demand" else None, values=values
    )
    if cause == "all_failed":
        parts[0]["recommendation"]["execution"]["item_controls"][0]["max_target_qty"] = "1"
    durable, _, request, binding, sources, expected, run_id = case(tmp_path, "LEGACY_STYLE", parts)
    written = durable.execute(request, binding, **sources)
    assert durable.recover(run_id)["result"] == expected
    assert written["result"]["order_plan"]["status"] == "NO_POLICY_SELECTED"
    assert written["counts"]["order_count"] == 0
    assert written["counts"]["not_selected_item_count"] == 1
    children = written["result"]["search_result"]["candidate_evaluations"]
    if cause == "all_failed":
        assert len(children) == 32 and all(c["status"] == "FAILED" for c in children)
    assert (
        written["result"]["order_plan"]["psi_rows"]
        == written["result"]["order_plan"]["baseline_psi_rows"]
    )

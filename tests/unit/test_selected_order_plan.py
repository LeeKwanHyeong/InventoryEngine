"""Source authenticity and safety boundaries before selected-policy execution."""

import copy
from decimal import Inexact, ROUND_FLOOR, localcontext
from unittest.mock import patch

import pytest

from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.order_planning import SelectedPolicyOrderPlanBinding
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from dsio_inventory_engine.recommend_replenishment.application import run as common_psi
from dsio_inventory_engine.recommend_replenishment.application.selected_order_plan import (
    RunSelectedPolicyOrderPlanUseCase,
)
from tests.support.selected_order_plan_fixtures import execution_case
from tests.support.catalog_selection_fixtures import feasible_parts
from tests.support.catalog_search_fixtures import run_bundle


def reseal(value):
    value["content_hash"] = digest({k: v for k, v in value.items() if k != "content_hash"})


@pytest.mark.parametrize("mode", ["ABC_V1", "LEGACY_STYLE"])
@pytest.mark.parametrize(
    "mutation", ["quantity", "arrival", "metrics", "failed", "exclusion", "baseline"]
)
def test_resealed_search_source_cannot_substitute_orders_metrics_failures_or_exclusions(
    mode, mutation
):
    runner, args = execution_case(mode=mode)
    source = args["source"]
    child = next(c for c in source["candidate_evaluations"] if c["orders"])
    if mutation == "quantity":
        child["orders"][0]["quantity"] = "999"
    elif mutation == "arrival":
        child["orders"][0]["due_date"] = "2026-10-12"
    elif mutation == "metrics":
        child["metrics"]["total_cost"] = "0"
    elif mutation == "failed":
        child.update(
            status="FAILED",
            failure_reason="MADE_UP",
            psi_rows=None,
            orders=None,
            decision_evidence=None,
            metrics=None,
            row_count=None,
        )
    elif mutation == "exclusion":
        source["excluded_items"] = [{"item_id": "ITEM-A", "reason_code": "MADE_UP"}]
    else:
        source["baseline_items"][0]["psi_rows"][0]["eoh_qty"] = "999"
    reseal(child)
    reseal(source)
    with patch.object(common_psi, "simulate", wraps=common_psi.simulate) as simulate:
        with pytest.raises(InventoryInputError):
            runner.execute(**args)
    assert simulate.call_count == 0


@pytest.mark.parametrize("mode", ["ABC_V1", "LEGACY_STYLE"])
def test_resealed_selection_cannot_change_policy_parameters_even_with_a_matching_new_reference(
    mode,
):
    runner, args = execution_case(mode=mode)
    selected = args["selection_result"]
    selected["item_selections"][0]["selected_policy"]["emos"] = "12"
    reseal(selected)
    with pytest.raises(InventoryInputError, match="REPLAY_MISMATCH|POLICY_POINTER_INVALID"):
        args["binding"] = SelectedPolicyOrderPlanBinding.from_sources(
            selected, args["request"], expected_selection_hash=selected["content_hash"]
        )
        runner.execute(**args)


@pytest.mark.parametrize("mode", ["ABC_V1", "LEGACY_STYLE"])
def test_resealed_review_downgrade_is_rejected(mode):
    runner, args = execution_case(mode=mode)
    selected = args["selection_result"]
    assert selected["item_selections"][0]["source_review_required"]
    selected["item_selections"][0]["source_review_required"] = False
    reseal(selected)
    args["binding"] = SelectedPolicyOrderPlanBinding.from_sources(
        selected, args["request"], expected_selection_hash=selected["content_hash"]
    )
    with pytest.raises(InventoryInputError, match="REPLAY_MISMATCH"):
        runner.execute(**args)


@pytest.mark.parametrize("field", ["allowed_action_types", "item_controls"])
def test_noncanonical_execution_controls_cannot_drift_behind_the_same_input_hash(field):
    runner, args = execution_case()
    raw = args["request"].to_dict()
    if field == "allowed_action_types":
        raw["recommendation"]["execution"][field] = ["HOLD"]
    else:
        raw["recommendation"]["execution"][field][0]["max_order_qty"] = "1"
    args["request"] = MathematicalPolicyRequest.from_dict(raw)
    with pytest.raises(InventoryInputError, match="ORDER_PLAN_SOURCE_REPLAY_MISMATCH"):
        runner.execute(**args)


@pytest.mark.parametrize("field", ["configuration", "classification"])
def test_original_config_and_full_classification_are_required(field):
    runner, args = execution_case()
    if field == "configuration":
        args["config_values"]["policy_search"]["minimum_fill_rate"] = "0.5"
    else:
        args["classification_snapshot"]["items"][0]["segment_key"] = "CZ"
    with pytest.raises(InventoryInputError):
        runner.execute(**args)


def test_catalog_requires_its_exact_producer_and_sealed_binding():
    runner, args = execution_case(mode="LEGACY_STYLE")
    no_producer = RunSelectedPolicyOrderPlanUseCase(runner.deployment)
    with pytest.raises(InventoryInputError, match="ORDER_PLAN_CATALOG_BINDING_REQUIRED"):
        no_producer.execute(**args)
    wrong_hash = {**args, "expected_catalog_hash": "f" * 64}
    with pytest.raises(InventoryInputError):
        runner.execute(**wrong_hash)


def test_abc_source_cannot_be_presented_with_catalog_authority():
    runner, args = execution_case()
    args["expected_catalog_hash"] = "f" * 64
    with pytest.raises(InventoryInputError, match="ORDER_PLAN_UNEXPECTED_CATALOG"):
        runner.execute(**args)


def test_malformed_source_becomes_an_input_error_not_a_partial_plan():
    runner, args = execution_case()
    args["source"] = []
    with pytest.raises(InventoryInputError, match="JSON_OBJECT_REQUIRED"):
        runner.execute(**args)


def test_non_development_environment_is_denied_without_source_execution():
    _, args = execution_case()
    runner = RunSelectedPolicyOrderPlanUseCase(DeploymentScope("DSE", "PRODUCTION"))
    with pytest.raises(InventoryInputError, match="ORDER_PLAN_DEVELOPMENT_ONLY"):
        runner.execute(**args)


def test_math_decimal_context_and_return_value_mutations_do_not_leak_into_next_execution():
    runner, args = execution_case()
    before = copy.deepcopy(args)
    expected = runner.execute(**args)
    with localcontext() as ctx:
        ctx.prec, ctx.rounding = 6, ROUND_FLOOR
        ctx.traps[Inexact] = True
        assert runner.execute(**args) == expected
    expected["recommended_orders"][0]["quantity"] = "999"
    expected["item_plans"][0]["reason_codes"].append("OTHER")
    assert runner.execute(**args)["recommended_orders"][0]["quantity"] != "999"
    assert args == before


def test_corrupt_fresh_psi_fails_closed_instead_of_falling_back_to_stored_orders():
    runner, args = execution_case()
    original = common_psi.simulate

    def corrupt(*positional, **keywords):
        rows, decisions, orders = original(*positional, **keywords)
        orders[0]["quantity"] = "999"
        return rows, decisions, orders

    with patch.object(common_psi, "simulate", side_effect=corrupt):
        with pytest.raises(InventoryInputError, match="ORDER_PLAN_PSI_REPLAY_MISMATCH"):
            runner.execute(**args)


def test_fresh_action_adjustment_reasons_must_match_not_only_the_order_quantity():
    runner, args = execution_case()
    original = common_psi.simulate

    def corrupt(*positional, **keywords):
        rows, decisions, orders = original(*positional, **keywords)
        decisions[0]["validation"]["reason_codes"] = ["MADE_UP_REASON"]
        return rows, decisions, orders

    with patch.object(common_psi, "simulate", side_effect=corrupt):
        with pytest.raises(InventoryInputError, match="ORDER_PLAN_ACTION_REPLAY_MISMATCH"):
            runner.execute(**args)


@pytest.mark.parametrize("cause", ["no_cost", "no_demand", "all_failed"])
def test_no_selection_preserves_baseline_and_reasons_without_any_strategy_fallback(cause):
    _, values, _ = run_bundle()
    if cause == "no_cost":
        values["policy_search"]["cost_assumptions"] = None
    parts = feasible_parts(
        forecast=["0", "0", "0"] if cause == "no_demand" else None, values=values
    )
    if cause == "all_failed":
        parts[0]["recommendation"]["execution"]["item_controls"][0]["max_target_qty"] = "1"
    runner, args = execution_case(parts, mode="LEGACY_STYLE")
    with patch.object(common_psi, "simulate", wraps=common_psi.simulate) as simulate:
        result = runner.execute(**args)
    assert simulate.call_count == 0
    assert result["status"] == "NO_POLICY_SELECTED"
    assert result["recommended_orders"] == result["decision_evidence"] == []
    assert result["psi_rows"] == result["baseline_psi_rows"]
    assert (
        result["item_plans"][0]["reason_codes"]
        == args["selection_result"]["item_selections"][0]["reason_codes"]
    )

"""Literal candidate PSI/cost goldens and fail-closed search contract checks."""

import copy
from decimal import Inexact, ROUND_FLOOR, localcontext

import pytest

from tests.support.policy_search_fixtures import GOLDEN, bundle
from math_fixtures import adjustment, build_math, GOLDEN_MATH, reseal_math
from fixtures import build_request
from strategy_fixtures import request as recommendation_request
from decimal import Decimal
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.policy_search import (
    candidate_policies,
    normalize_policy_search,
    policy_search_hash,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from dsio_inventory_engine.recommend_replenishment.application.policy_search import (
    RunPolicySearchUseCase,
)
from dsio_inventory_engine.classify_inventory.application import config_hash

DEPLOYMENT = DeploymentScope("DSE", "DEVELOPMENT")


def execute(parts=None):
    raw, config, classification = parts or bundle()
    return RunPolicySearchUseCase(DEPLOYMENT).execute(
        MathematicalPolicyRequest.from_dict(raw),
        config_values=config,
        classification_snapshot=classification,
    )


def test_literal_psi_service_cost_and_common_input_goldens():
    result = execute()
    assert result["search_config_hash"] == GOLDEN["normalized_search_hash"]
    baseline = result["baseline_items"][0]
    assert [row["eoh_qty"] for row in baseline["psi_rows"]] == GOLDEN["expected"]["BASELINE"]["eoh"]
    assert baseline["metrics"]["current_demand_fulfilled_qty"] == "10"
    for child in result["candidate_evaluations"]:
        expected = GOLDEN["expected"][child["candidate"]["policy_kind"]]
        assert child["status"] == "SUCCEEDED", child
        assert [row["eoh_qty"] for row in child["psi_rows"]] == expected["eoh"]
        metrics = child["metrics"]
        assert metrics["current_demand_fulfilled_qty"] == expected["fulfilled"]
        for key in (
            "ordered_qty",
            "order_count",
            "average_on_hand_eoh_qty",
            "total_cost",
            "service_feasible",
        ):
            assert metrics[key] == expected[key]
        assert child["canonical_input_hash"] == result["canonical_input_hash"]
        assert child["prepared_input_hash"] == result["prepared_input_hash"]
        assert not child["automatic_publish_allowed"] and not child["automatic_order_allowed"]
        assert child["content_hash"] == digest(
            {key: value for key, value in child.items() if key != "content_hash"}
        )
    assert result["selected_policies"] is None
    assert result["content_hash"] == digest(
        {key: value for key, value in result.items() if key != "content_hash"}
    )


def test_multiple_items_keep_their_own_inventory_and_candidate_ledgers():
    raw = build_math(GOLDEN_MATH["cases"][0])
    source = build_request(
        {
            "id": "two-item-policy-search",
            "items": [
                {"id": "ITEM-A", "boh": "10", "forecast": ["10", "10", "10"]},
                {"id": "ITEM-B", "boh": "100", "forecast": ["10", "10", "10"]},
            ],
        }
    )
    recommendation = recommendation_request(source).to_dict()
    recommendation["execution"].update(
        contract_version="1.1.0", strategy=raw["recommendation"]["execution"]["strategy"]
    )
    raw["recommendation"] = recommendation
    raw["policy_input"]["context"]["canonical_input_hash"] = recommendation["execution"][
        "canonical_input_hash"
    ]
    raw["policy_input"]["history"] += [
        {**row, "item_id": "ITEM-B"} for row in raw["policy_input"]["history"]
    ]
    search = copy.deepcopy(GOLDEN["search_config"])
    profile = search["group_profiles"][0]
    search["group_profiles"] = [
        {**profile, "segment_key": segment}
        for segment in ("AX", "AY", "AZ", "BX", "BY", "BZ", "CX", "CY", "CZ")
    ]
    result = execute(bundle(reseal_math(raw), search))
    assert len(result["baseline_items"]) == 2
    assert len(result["candidate_evaluations"]) == 4
    for child in result["candidate_evaluations"]:
        assert child["status"] == "SUCCEEDED", child
        item = child["candidate"]["item_id"]
        assert {row["item_id"] for row in child["psi_rows"]} == {item}
        if item == "ITEM-B":
            assert child["orders"] == []
            assert [row["eoh_qty"] for row in child["psi_rows"]] == ["90", "80", "70"]
        else:
            assert child["orders"]
    ids = [
        order["order_id"] for child in result["candidate_evaluations"] for order in child["orders"]
    ]
    assert len(ids) == len(set(ids))


def test_sde_p90_protection_does_not_change_physical_receipt_date():
    from datetime import date

    raw, config, _ = bundle()
    config["segmentation"]["sde"]["application_mode"] = "OPERATIONAL"
    result = execute(
        bundle(
            raw,
            config_values=config,
            metric_changes={
                "supplier_count": 1,
                "planning_lead_time_days": Decimal("7"),
                "p50_lead_time_days": Decimal("7"),
                "p90_lead_time_days": Decimal("21"),
                "on_time_delivery_rate": Decimal("0.99"),
                "receipt_sample_count": 10,
                "sde_source_status": "VERIFIED",
            },
        )
    )
    for child in result["candidate_evaluations"]:
        assert child["status"] == "SUCCEEDED", child
        evidence = child["decision_evidence"][0]["candidate_policy_evidence"]
        assert evidence["protection_lead_time_days"] == "21"
        assert evidence["physical_lead_time_days"] == 7
        for order in child["orders"]:
            assert (
                date.fromisoformat(order["due_date"]) - date.fromisoformat(order["order_date"])
            ).days == 7


def test_decimal_context_and_candidate_order_do_not_change_results():
    expected = execute()
    parts = bundle()
    with localcontext() as ctx:
        ctx.prec = 8
        ctx.rounding = ROUND_FLOOR
        ctx.traps[Inexact] = True
        assert execute(parts) == expected
    config = copy.deepcopy(GOLDEN["search_config"])
    config["group_profiles"][0]["rmos_values"] = ["1.0", "0"]
    first = policy_search_hash(config)
    config["group_profiles"][0]["rmos_values"] = ["0", "1"]
    assert policy_search_hash(config) == first
    assert candidate_policies(config, item_id="ITEM-A", segment_key="AX") == candidate_policies(
        normalize_policy_search(config), item_id="ITEM-A", segment_key="AX"
    )
    _, values, _ = bundle()
    typed_values = copy.deepcopy(values)
    typed_values["segmentation"]["sde"]["difficult_min_lead_time_days"] = 28.0
    typed_values["segmentation"]["sde"]["scarce_min_lead_time_days"] = 84.0
    assert config_hash(values) == config_hash(typed_values)
    assert type(values["segmentation"]["sde"]["difficult_min_lead_time_days"]) is int


def test_item_override_replaces_group_range_and_remains_classified():
    search = copy.deepcopy(GOLDEN["search_config"])
    profile = {
        key: value for key, value in search["group_profiles"][0].items() if key != "segment_key"
    }
    search["item_overrides"] = [
        {
            **profile,
            "item_id": "ITEM-A",
            "reason": "Development exception",
            "review_cycle_weeks": [2],
        }
    ]
    result = execute(bundle(search=search))
    assert all(
        row["candidate"]["range_source"] == "ITEM_OVERRIDE"
        for row in result["candidate_evaluations"]
    )
    assert all(
        row["candidate"]["review_cycle_weeks"] == 2 for row in result["candidate_evaluations"]
    )
    with pytest.raises(ValueError, match="UNCLASSIFIED"):
        candidate_policies(search, item_id="ITEM-A", segment_key=None)


@pytest.mark.parametrize(
    "change,code",
    [
        (lambda v: v.update(unknown=True), "CONFIG_INVALID"),
        (lambda v: v.update(max_candidates_per_item=1), "CANDIDATE_LIMIT"),
        (lambda v: v["group_profiles"][0].update(emos_values=["0"]), "DECIMAL_RANGE"),
        (lambda v: v["group_profiles"][0].update(rmos_values=["NaN"]), "DECIMAL_INVALID"),
        (lambda v: v["group_profiles"][0].update(rmos_values=["1", "1.0"]), "DUPLICATE_VALUE"),
        (lambda v: v.update(lookback_weeks=True), "LOOKBACK_INVALID"),
    ],
)
def test_closed_search_negative_contract(change, code):
    search = copy.deepcopy(GOLDEN["search_config"])
    change(search)
    with pytest.raises(ValueError, match=code):
        normalize_policy_search(search)


def test_no_demand_and_unbound_cost_are_not_fake_success_metrics():
    search = copy.deepcopy(GOLDEN["search_config"])
    search["cost_assumptions"] = None
    raw = build_math({**GOLDEN_MATH["cases"][0], "forecast": ["0", "0", "0"]})
    result = execute(bundle(raw, search))
    for child in result["candidate_evaluations"]:
        assert child["metrics"]["service_status"] == "NO_DEMAND_IN_HORIZON"
        assert child["metrics"]["current_demand_fill_rate"] is None
        assert child["metrics"]["total_cost"] is None
        assert child["metrics"]["cost_status"] == "COST_PROFILE_NOT_BOUND"


def test_insufficient_history_and_override_are_not_silently_replaced():
    raw = build_math(GOLDEN_MATH["cases"][0])
    raw["policy_input"]["history"].pop()
    result = execute(bundle(reseal_math(raw)))
    assert result["candidate_evaluations"] == []
    assert result["excluded_items"] == [
        {"item_id": "ITEM-A", "reason_code": "INSUFFICIENT_DEMAND_HISTORY"}
    ]
    raw = build_math(GOLDEN_MATH["cases"][0])
    raw["policy_input"]["adjustments"] = [adjustment()]
    assert (
        execute(bundle(reseal_math(raw)))["excluded_items"][0]["reason_code"]
        == "APPROVED_OVERRIDE_PINNED"
    )


def test_source_lots_capacity_and_guard_adjustments_are_real():
    raw = build_math(
        {
            **GOLDEN_MATH["cases"][0],
            "policy": {"moq": "20", "order_multiple": "10", "physical_max_capacity": "15"},
        }
    )
    result = execute(bundle(raw))
    for child in result["candidate_evaluations"]:
        assert child["orders"] == []
        assert any(
            row["validation"]["reason_codes"] == ["NO_FEASIBLE_ORDER_QUANTITY"]
            for row in child["decision_evidence"]
        )


def test_shadow_service_candidates_are_not_fixed_to_matrix_csl():
    search = copy.deepcopy(GOLDEN["search_config"])
    search["group_profiles"][0]["service_level_values"] = ["0.5", "0.95"]
    result = execute(bundle(build_math(), search))
    fixed = [
        row
        for row in result["candidate_evaluations"]
        if row["candidate"]["policy_kind"] == "ROP_FIXED_Q"
    ]
    by_service = {row["candidate"]["target_service_level"]: row for row in fixed}
    assert (
        by_service["0.5"]["decision_evidence"][0]["proposal"]["calculated_policy"][
            "safety_stock_qty"
        ]
        == "0"
    )
    assert (
        by_service["0.95"]["decision_evidence"][0]["proposal"]["calculated_policy"][
            "safety_stock_qty"
        ]
        == "4"
    )
    assert all(row["automatic_order_allowed"] is False for row in fixed)
    raw, values, _ = bundle(build_math(), search)
    values["segmentation"]["ved"].update(
        enabled=True,
        default_class="V",
        assignment_snapshot_id="00000000-0000-0000-0000-000000000009",
        assignment_content_hash="9" * 64,
    )
    floored = execute(bundle(raw, search, config_values=values))
    assert all(
        row["decision_evidence"][0]["candidate_policy_evidence"]["effective_csl"] == "0.99"
        for row in floored["candidate_evaluations"]
    )


def test_one_failed_candidate_keeps_other_candidate_evidence():
    search = copy.deepcopy(GOLDEN["search_config"])
    search["group_profiles"][0]["emos_values"] = ["0.1", "0.3"]
    raw = build_math(GOLDEN_MATH["cases"][0])
    raw["recommendation"]["execution"]["item_controls"][0]["max_target_qty"] = "20"
    result = execute(bundle(raw, search))
    assert {row["status"] for row in result["candidate_evaluations"]} == {"FAILED", "SUCCEEDED"}
    assert all(
        row["psi_rows"] is None and row["metrics"] is None
        for row in result["candidate_evaluations"]
        if row["status"] == "FAILED"
    )
    assert result["baseline_items"][0]["psi_rows"]


def test_fsn_block_and_review_cannot_publish_or_order():
    raw, config, snapshot = bundle()
    assert snapshot["items"][0]["effective_order_action"] == "REVIEW"
    review = execute((raw, config, snapshot))
    assert any(child["orders"] for child in review["candidate_evaluations"])
    assert all(
        not order["automatic_order_allowed"]
        for child in review["candidate_evaluations"]
        for order in child["orders"]
    )
    config["policy_overlays"]["fsn_order_action"]["N"] = "BLOCK"
    blocked = execute(bundle(raw, config_values=config))
    assert all(child["orders"] == [] for child in blocked["candidate_evaluations"])
    assert all(
        row["proposal"]["action_type"] == "HOLD"
        for child in blocked["candidate_evaluations"]
        for row in child["decision_evidence"]
    )


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("content_hash", "0" * 64, "CLASSIFICATION_HASH_MISMATCH"),
        ("classification_snapshot_id", "bad-id", "CLASSIFICATION_ID_MISMATCH"),
    ],
)
def test_tampered_classification_rejected(field, value, code):
    parts = bundle()
    parts[2][field] = value
    with pytest.raises(InventoryInputError, match=code):
        execute(parts)

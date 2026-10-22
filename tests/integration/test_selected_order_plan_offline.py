"""Actually execute selected policies with the common PSI, without DB/ERP effects."""

import copy
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from dsio_inventory_engine.inventory_contracts.values import digest, canonical_json
from dsio_inventory_engine.prepare_inventory.application.inventory_input import (
    PreparedInventoryInput,
)
from dsio_inventory_engine.recommend_replenishment.application import run as common_psi
from tests.support.selected_order_plan_fixtures import execution_case, golden_parts, v2_parts
from tests.support.catalog_selection_fixtures import feasible_parts
from tests.support.catalog_search_fixtures import run_bundle
from tests.support.policy_search_fixtures import multi_item_bundle, bundle

GOLDEN = json.loads(
    (Path(__file__).parents[1] / "fixtures/golden_selected_order_plan.json").read_text()
)


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda c: c["id"])
def test_hand_calculated_order_quantities_dates_and_adjustments(case):
    runner, args = execution_case(golden_parts(case))
    before = copy.deepcopy(args)
    with patch.object(common_psi, "simulate", wraps=common_psi.simulate) as simulate:
        result = runner.execute(**args)
    assert simulate.call_count == 1  # Actual selected-policy PSI, not copied search orders.
    assert args == before
    assert result["selected_item_count"] == 1
    assert [r["eoh_qty"] for r in result["psi_rows"]] == case["eoh"]
    assert [
        [o[k] for k in ("order_date", "due_date", "receipt_date", "quantity")]
        for o in result["recommended_orders"]
    ] == case["orders"]
    assert [d["validation"]["reason_codes"] for d in result["decision_evidence"]] == case[
        "validation_reasons"
    ]
    assert all(o["source_policy_id"] == "POLICY-ITEM-A" for o in result["recommended_orders"])
    assert all(o["status"] == "SIMULATED_RECEIVED" for o in result["recommended_orders"])
    for order in result["recommended_orders"]:
        assert order["order_id"] == "REC-" + order["decision_id"]
        assert order["automatic_order_allowed"] is False
    for field in (
        "automatic_publish_allowed",
        "automatic_order_allowed",
        "database_writes",
        "artifact_sealed",
        "operational_policy_approved",
        "run_claimed",
    ):
        assert result[field] is False


@pytest.mark.parametrize(
    "mode", ["ABC_V1", "ABC_XYZ_SHARED_BUDGET", "LEGACY_STYLE", "WIDE_REFERENCE"]
)
def test_all_selection_modes_reuse_existing_rows_actions_orders_and_hashes(mode):
    runner, args = execution_case(mode=mode)
    result = runner.execute(**args)
    chosen = args["selection_result"]["item_selections"][0]
    child = next(
        c
        for c in args["source"]["candidate_evaluations"]
        if c["candidate"]["candidate_id"] == chosen["selected_candidate_id"]
    )
    for field, original in (
        ("psi_rows", "psi_rows"),
        ("decision_evidence", "decision_evidence"),
        ("recommended_orders", "orders"),
    ):
        assert result[field] == child[original]
    assert result["order_plan_binding"] == args["binding"].to_dict()
    for name, rows in (
        ("psi", "psi_rows"),
        ("decision", "decision_evidence"),
        ("orders", "recommended_orders"),
    ):
        assert result[name + "_content_hash"] == digest(
            child["orders" if rows == "recommended_orders" else rows]
        )
    assert not {"order_plan_id", "binding_id", "content_hash"} & result.keys()
    assert runner.execute(**args) == result


def test_two_items_keep_separate_selected_policies_positions_and_pending_orders():
    runner, args = execution_case(multi_item_bundle())
    with patch.object(common_psi, "simulate", wraps=common_psi.simulate) as simulate:
        result = runner.execute(**args)
    assert simulate.call_count == 2
    assert result["selected_item_count"] == 2
    assert {o["item_id"] for o in result["recommended_orders"]} == {"ITEM-A"}
    assert [r["eoh_qty"] for r in result["psi_rows"] if r["item_id"] == "ITEM-B"] == [
        "90",
        "80",
        "70",
    ]
    ids = [d["observation"]["decision_id"] for d in result["decision_evidence"]]
    assert len(ids) == len(set(ids)) == 6


def test_selected_and_unselected_items_are_not_collapsed_into_one_success_state():
    runner, args = execution_case(multi_item_bundle(second_forecast=["0", "0", "0"]))
    result = runner.execute(**args)
    assert (result["selected_item_count"], result["not_selected_item_count"]) == (1, 1)
    plans = {r["item_id"]: r for r in result["item_plans"]}
    assert plans["ITEM-A"]["psi_scenario_type"] == "RECOMMENDED"
    assert plans["ITEM-B"]["status"] == "NOT_SELECTED"
    assert plans["ITEM-B"]["psi_scenario_type"] == "BASELINE"
    assert "NO_DEMAND_IN_HORIZON" in plans["ITEM-B"]["reason_codes"]
    assert [r["eoh_qty"] for r in result["psi_rows"] if r["item_id"] == "ITEM-B"] == [
        "100",
        "100",
        "100",
    ]
    assert {d["observation"]["item_id"] for d in result["decision_evidence"]} == {"ITEM-A"}


@pytest.mark.parametrize("action", ["REVIEW", "BLOCK"])
@pytest.mark.parametrize("v2", [False, True])
def test_fsn_gates_are_retained_without_promoting_calculated_orders(action, v2):
    _, config, _ = run_bundle()
    config["policy_overlays"]["fsn_order_action"]["N"] = action
    parts = feasible_parts(boh="10", values=config)
    if v2:
        parts = v2_parts(parts)
    runner, args = execution_case(parts, mode="LEGACY_STYLE")
    result = runner.execute(**args)
    plan = result["item_plans"][0]
    assert plan["effective_policy_admission"]["effective_order_action"] == action
    assert result["automatic_publish_allowed"] is False
    assert result["automatic_order_allowed"] is False
    if action == "BLOCK":
        assert plan["status"] == "NOT_SELECTED"
        assert result["status"] == "NO_POLICY_SELECTED"
        assert result["recommended_orders"] == result["decision_evidence"] == []
        assert result["psi_rows"] == result["baseline_psi_rows"]
        assert plan["psi_scenario_type"] == "BASELINE"
    else:
        assert plan["status"] == "REVIEW_REQUIRED"
        assert result["recommended_orders"]
        assert all(
            o["execution_disposition"] == "APPROVAL_REQUIRED" for o in result["recommended_orders"]
        )


def test_sde_protection_does_not_replace_physical_lead_time_with_a_longer_arrival():
    raw, config, _ = bundle()
    config["segmentation"]["sde"]["application_mode"] = "OPERATIONAL"
    parts = bundle(
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
    runner, args = execution_case(v2_parts(parts))
    before = copy.deepcopy(args)
    result = runner.execute(**args)
    assert result["selected_item_count"] == 1
    assert result["recommended_orders"]
    assert all(
        (date.fromisoformat(o["due_date"]) - date.fromisoformat(o["order_date"])).days == 7
        for o in result["recommended_orders"]
    )
    assert all(
        d["candidate_policy_evidence"]["protection_lead_time_days"] == "21"
        for d in result["decision_evidence"]
    )
    assert (
        PreparedInventoryInput(canonical_json(result["prepared_input"])).to_dict()
        == result["prepared_input"]
    )
    assert args == before


def test_confirmed_receipt_backorder_and_forecast_consumption_are_not_double_counted():
    from fixtures import reseal
    from math_fixtures import reseal_math
    from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest

    case = GOLDEN["cases"][0]
    raw, config, _ = golden_parts(case)
    canonical = raw["recommendation"]["canonical_input"]
    canonical["snapshots"]["inventory"]["rows"][0]["backorder_qty"] = "5"
    fields = {
        k: canonical["snapshots"]["inventory"]["rows"][0][k]
        for k in ("company_cd", "subs_cd", "site_cd", "item_id", "uom")
    }
    canonical["snapshots"]["customer_orders"]["rows"] = [
        {
            **fields,
            "yyyyww": "202640",
            "confirmed_customer_order_qty": "8",
        }
    ]
    canonical["snapshots"]["receipts"]["rows"] = [
        {
            **fields,
            "receipt_id": "CONFIRMED-20",
            "due_date": "2026-09-28",
            "due_qty": "20",
            "status": "CONFIRMED",
            "supply_type": "PURCHASE_ORDER",
        },
        {
            **fields,
            "receipt_id": "UNVERIFIED-100",
            "due_date": "2026-09-28",
            "due_qty": "100",
            "status": "UNVERIFIED_DUE_IN",
            "supply_type": "PURCHASE_ORDER",
        },
    ]
    canonical = CanonicalInputRequest.from_dict(reseal(canonical))
    raw["recommendation"]["canonical_input"] = canonical.to_dict()
    raw["recommendation"]["execution"]["canonical_input_hash"] = canonical.input_hash
    raw["policy_input"]["context"]["canonical_input_hash"] = canonical.input_hash
    runner, args = execution_case(
        bundle(reseal_math(raw), config["policy_search"], config_values=config)
    )
    result = runner.execute(**args)
    assert [r["eoh_qty"] for r in result["psi_rows"]] == ["15", "5", "3"]
    assert [(o["order_date"], o["quantity"]) for o in result["recommended_orders"]] == [
        ("2026-10-05", "8")
    ]
    first = result["psi_rows"][0]
    assert first["confirmed_customer_order_qty"] == "8" and first["net_forecast_qty"] == "2"
    assert first["confirmed_supplier_receipt_qty"] == "20"
    assert first["backorder_open_qty"] == "5" and first["backorder_close_qty"] == "0"
    unverified = next(
        r
        for r in result["prepared_input"]["receipt_decisions"]
        if r["receipt_id"] == "UNVERIFIED-100"
    )
    assert unverified["included_qty"] == "0"


def test_selected_policy_may_produce_no_orders_without_inventing_an_order():
    runner, args = execution_case(golden_parts({**GOLDEN["cases"][0], "boh": "100"}))
    result = runner.execute(**args)
    assert result["selected_item_count"] == 1
    assert result["recommended_orders"] == []
    assert [r["eoh_qty"] for r in result["psi_rows"]] == ["90", "80", "70"]
    assert all(d["proposal"]["action_type"] == "HOLD" for d in result["decision_evidence"])

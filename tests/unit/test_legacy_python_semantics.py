"""Check literal manual arithmetic and existing Python components; no C# emulator."""

import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.recommend_replenishment.application.guard import (
    map_arrival,
    policy_at,
    validate_action,
)
from dsio_inventory_engine.recommend_replenishment.application.policy_search import evaluate_rows
from dsio_inventory_engine.recommend_replenishment.application.policy_selection import (
    assess_candidate,
    rank_candidate_assessments,
)
from dsio_inventory_engine.recommend_replenishment.application.run import RunRecommendedPsiUseCase
from dsio_inventory_engine.simulate_inventory.application.step import InventoryState, advance_bucket
from strategy_fixtures import ContractProbe, canonical, request

GOLDEN = json.loads(
    (
        Path(__file__).resolve().parents[1] / "fixtures/golden_legacy_python_semantics.json"
    ).read_text()
)
DEPLOYMENT = DeploymentScope("DSE", "DEVELOPMENT")


@pytest.mark.parametrize("case", GOLDEN["transition_cases"], ids=lambda row: row["case"])
def test_existing_python_step_matches_literal_transition(case):
    given = case["input"]
    state, row = advance_bucket(
        InventoryState(Decimal(given["boh"]), Decimal(0), Decimal(given["backorder"])),
        confirmed_customer_order_qty=Decimal(given["confirmed"]),
        net_forecast_qty=Decimal(given["forecast"]),
        confirmed_supplier_receipt_qty=Decimal(given["receipt"]),
    )
    for key, expected in case["python_expected"].items():
        if key not in ("service_status", "current_demand_fill_rate"):
            assert row[key] == expected
    assert state.available_qty == Decimal(case["python_expected"]["eoh_qty"])
    if case["case"] == "no_demand_is_not_perfect_service":
        metrics = evaluate_rows(
            [{**row, "confirmed_customer_order_qty": "0", "net_forecast_qty": "0"}],
            [],
            [],
            None,
            "0.95",
            Decimal(0),
        )
        for key in ("service_status", "current_demand_fill_rate"):
            assert metrics[key] == case["python_expected"][key]
        assert metrics["observed_average_on_hand_mos"] is None
        assert metrics["cost_status"] == "COST_PROFILE_NOT_BOUND"
        assert metrics["total_cost"] is None


@pytest.mark.parametrize("case", GOLDEN["transition_cases"], ids=lambda row: row["case"])
def test_legacy_manual_transition_balances_are_consistent_not_native_verified(case):
    given, expected = case["input"], case["legacy_expected"]
    assert Decimal(given["boh"]) + Decimal(given["receipt"]) == (
        Decimal(expected["eoh"]) + Decimal(expected["stock_allocation"])
    )
    assert Decimal(given["backorder"]) + Decimal(given["confirmed"]) + Decimal(
        given["forecast"]
    ) == Decimal(expected["backorder_close"]) + Decimal(expected["stock_allocation"])


@pytest.fixture(scope="module")
def guard_observation():
    probe = ContractProbe(action="HOLD", quantity="0")
    RunRecommendedPsiUseCase(DEPLOYMENT).execute(request(canonical(boh="0")), probe)
    return probe.observations[0].to_dict()


@pytest.mark.parametrize("case", GOLDEN["lot_cases"], ids=lambda row: row["case"])
def test_python_guard_uses_absolute_multiple_not_legacy_offset(case, guard_observation):
    observation = copy.deepcopy(guard_observation)
    observation["policy"].update(moq=case["min"], order_multiple=case["step"])
    # Legacy MaxLotSize is intentionally NOT assigned to physical_max_capacity.
    result = validate_action(
        observation,
        {"action_type": "ORDER_QTY", "quantity": case["raw"], "calculated_policy": {}},
    )
    assert result["accepted_order_qty"] == case["python_expected"]
    assert result["lot_rounded_qty"] == case["python_expected"]
    assert result["accepted_order_qty"] != case["legacy_expected"]
    assert Decimal(case["legacy_expected"]) >= Decimal(case["raw"])
    assert Decimal(case["python_expected"]) % Decimal(case["step"]) == 0


@pytest.mark.parametrize("case", GOLDEN["arrival_cases"], ids=lambda row: row["case"])
def test_existing_arrival_mapper_matches_literal_dates(case, guard_observation):
    decision_date = GOLDEN["calendar"][0]["start_date"]
    due, bucket = map_arrival(decision_date, case["lead_time_days"], GOLDEN["calendar"])
    assert due == case["due_date"]
    assert (bucket["start_date"] if bucket else None) == case["python_receipt_date"]
    assert Decimal(case["legacy_internal_lead_time"]) / Decimal(28800) == case["lead_time_days"]
    if case["case"] == "outside_horizon":
        observation = copy.deepcopy(guard_observation)
        observation["policy"]["lead_time_days"] = case["lead_time_days"]
        result = validate_action(
            observation,
            {"action_type": "ORDER_QTY", "quantity": "1", "calculated_policy": {}},
        )
        assert result["accepted_order_qty"] == "0"
        assert result["reason_codes"] == ["ARRIVAL_OUTSIDE_PLAN_HORIZON"]


@pytest.mark.parametrize("case", GOLDEN["policy_cases"], ids=lambda row: row["case"])
def test_python_policy_at_week_start_preserves_half_open_period(case):
    result = policy_at(GOLDEN["policy_schedule"], case["bucket_start"])
    assert result["policy_id"] == case["python_policy"]
    assert result["rop"] == case["python_rop"]
    assert policy_at(GOLDEN["policy_schedule"], "2026-09-01")["policy_id"] == "SEPTEMBER"


@pytest.mark.parametrize("case", GOLDEN["selection_cases"], ids=lambda row: row["case"])
def test_existing_selection_components_keep_explicit_ineligibility(case):
    """Component input only; not a substitute for source replay or the C# Optimizer."""
    assessments = []
    labels = {}
    for index, candidate in enumerate(case["candidates"], 1):
        candidate_id = "PC-" + f"{index:064x}"
        labels[candidate_id] = candidate["label"]
        metrics = {
            "service_status": "AVAILABLE",
            "service_feasible": Decimal(candidate["fill_rate"])
            >= Decimal(case["minimum_fill_rate"]),
            "current_demand_fill_rate": candidate["fill_rate"],
            "ending_backorder_qty": "0",
            "cost_status": "AVAILABLE_DEVELOPMENT_ASSUMPTION"
            if case["cost_bound"]
            else "COST_PROFILE_NOT_BOUND",
            "total_cost": candidate["total_cost"],
            "currency": "USD" if case["cost_bound"] else None,
            "average_on_hand_eoh_qty": "1",
            "order_count": 1,
        }
        assessments.append(
            assess_candidate(
                {
                    "candidate": {"candidate_id": candidate_id},
                    "content_hash": "1" * 64,
                    "status": "SUCCEEDED",
                    "failure_reason": None,
                },
                {
                    "metrics": metrics,
                    "blocked": False,
                    "calculable": True,
                    "minimum_fill_rate": Decimal(case["minimum_fill_rate"]),
                },
            )
        )
    eligible, _ = rank_candidate_assessments(assessments)
    assert (labels[eligible[0]["candidate_id"]] if eligible else None) == case[
        "python_selected_label"
    ]
    reasons = sorted(
        {r for a in assessments if a["status"] == "INELIGIBLE" for r in a["reason_codes"]}
    )
    assert reasons == case["python_ineligible_reasons"]
    if not eligible:
        assert all(a["rank"] is None for a in assessments)
        assert all(a["score"] is None for a in assessments)


@pytest.mark.parametrize("case", GOLDEN["order_cases"], ids=lambda row: row["case"])
def test_legacy_literal_order_ledgers_balance_without_emulating_the_program(case):
    """Arithmetic consistency cannot certify native C# execution equivalence."""
    legacy = case["legacy_expected"]
    previous_bo, previous_po = Decimal(0), Decimal(0)
    previous_eoh = Decimal(case["input"]["boh"])
    for index in range(3):
        assert Decimal(legacy["boh"][index]) == previous_eoh
        backlog = Decimal(legacy["backorder_close"][index])
        allocated = previous_bo + Decimal(case["input"]["forecast"][index]) - backlog
        immediate = (
            Decimal(legacy["purchase_order"][index])
            if case["input"]["lead_time_days"] == 0
            else Decimal(0)
        )
        assert Decimal(legacy["eoh"][index]) == (
            previous_eoh + Decimal(legacy["due_in"][index]) + immediate - allocated
        )
        committed = Decimal(0) if immediate else Decimal(legacy["purchase_order"][index])
        assert Decimal(legacy["pending_po_close"][index]) == (
            previous_po - Decimal(legacy["due_in"][index]) + committed
        )
        previous_eoh, previous_bo, previous_po = (
            Decimal(legacy["eoh"][index]),
            backlog,
            Decimal(legacy["pending_po_close"][index]),
        )
    assert str(previous_po) == legacy["terminal_pending_po"]

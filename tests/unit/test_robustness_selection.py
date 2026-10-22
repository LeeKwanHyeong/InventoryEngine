"""Hand-checked empirical risk, two selectors and no-reselection barriers."""

from copy import deepcopy
from decimal import localcontext

import pytest

from dsio_inventory_engine.evaluate_replenishment.robustness import (
    assess_paths,
    evaluate_locked_policy,
    select_policy,
    verify_selection,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from tests.support.robustness_fixtures import at, bank, candidate, hand_trace


def assessment(value):
    return assess_paths(value)["candidate_assessments"][0]["assessment"]


@pytest.mark.parametrize(
    "fills,eligible,ratio,loss,p10",
    [
        (["0.99"] * 15 + ["0.94"], True, "0.9375", "0.035", "0.99"),
        (["0.99"] * 15 + ["0"], False, "0.9375", "0.505", "0.99"),
        (["0.95"] * 16, True, "1", "0.05", "0.95"),
        (["0.949999"] * 16, False, "0", "0.050001", "0.949999"),
        ([None] * 4 + ["0.99"] * 12, True, "1", "0.01", "0.99"),
        ([None] * 5 + ["0.99"] * 11, False, "1", "0.01", "0.99"),
    ],
)
def test_holdout_risk_matches_hand_examples(fills, eligible, ratio, loss, p10):
    result = assessment(bank(fills, split="HOLDOUT"))
    assert result["eligible"] is eligible
    assert result["empirical_path_success_ratio"] == ratio
    assert result["worst_tail_mean_loss"] == loss
    assert result["p10_fill_rate"] == p10
    assert result["demand_path_count"] == sum(f is not None for f in fills)
    if len([f for f in fills if f is not None]) < 12:
        assert result["status"] == "INSUFFICIENT_DEMAND_PATHS"


def test_selection_needs_all_eight_demand_paths_to_pass():
    result = assessment(bank(["0.99"] * 7 + ["0.94"]))
    assert result["empirical_path_success_ratio"] == "0.875"
    assert result["eligible"] is False


@pytest.mark.parametrize(
    "bad_fill,eligible,loss", [("0.8", True, "0.1"), ("0.799999", False, "0.1000005")]
)
def test_worst_tail_loss_exact_boundary(bad_fill, eligible, loss):
    result = assessment(bank(["1"] * 15 + [bad_fill], split="HOLDOUT"))
    assert result["eligible"] is eligible
    assert result["worst_tail_mean_loss"] == loss


def test_all_no_demand_and_all_technical_failures_are_not_zero_cost_successes():
    no_demand = assessment(bank([None] * 8))
    assert no_demand["status"] == "INSUFFICIENT_DEMAND_PATHS"
    assert no_demand["empirical_path_success_ratio"] is None
    assert no_demand["worst_tail_mean_loss"] is None
    assert no_demand["mean_total_cost"] == "0"  # Explicit, known zero exposure and price.
    failures = assessment(bank(changes=lambda c, i: {"failed": True}))
    assert failures["status"] == "TECHNICAL_FAILURE"
    assert failures["mean_total_cost"] is None
    assert failures["worst_tail_mean_loss"] is None


def test_natural_no_demand_cost_included_but_not_service_denominator():
    value = bank([None] * 2 + ["0.99"] * 6, changes=lambda c, i: {"reserved": 5})
    result = assessment(value)
    assert result["eligible"]
    assert (result["demand_path_count"], result["no_demand_path_count"]) == (6, 2)
    # Each of 8 paths carries 250 USD holding, including the 2 zero-demand paths.
    # Six have 990000*250 variable + 10000*250 shortage + 25 fixed = 250000025.
    assert result["mean_total_cost"] == "187500268.75"


def test_no_demand_with_old_customer_bo_cannot_hide_in_excluded_denominator():
    value = bank([None] * 2 + ["0.99"] * 6, changes=lambda c, i: {"backlog": 1})
    result = assessment(value)
    assert not result["eligible"]
    assert "UNCLEARED_CUSTOMER_BACKORDER" in result["reason_codes"]


def test_failed_path_invalidates_candidate_and_expected_cost_not_averaged_over_survivors():
    value = bank(changes=lambda c, i: {"failed": i == 7})
    result = assessment(value)
    assert result["status"] == "TECHNICAL_FAILURE"
    assert result["failed_path_count"] == 1
    assert result["mean_total_cost"] is None
    assert result["empirical_path_success_ratio"] is None
    assert result["demand_path_count"] is None
    assert "TECHNICAL_PATH_FAILURE" in result["reason_codes"]


def test_service_can_pass_but_cost_missing_still_not_eligible():
    value = bank()
    value["binding"]["unit_price"] = None
    for t in value["traces"]:
        t["binding"]["unit_price"] = None
    result = assessment(value)
    assert result["reason_codes"] == ["COST_NOT_CALCULABLE"]
    assert result["service_risk_eligible"] is True
    assert result["cost_calculable"] is False


def test_valid_large_accumulated_cost_is_not_rejected_as_a_row_quantity():
    value = bank([None] * 8, changes=lambda c, i: {"backlog": 1000000000000})
    value["binding"]["unit_price"]["unit_price"] = "1000000000000"
    for t in value["traces"]:
        t["binding"]["unit_price"]["unit_price"] = "1000000000000"
    result = assessment(value)
    assert result["eligible"] is False
    assert result["mean_total_cost"] == "52000000000000000000000000"


def test_nominal_and_risk_choose_separately_no_fallback():
    choices = [candidate("CHEAP"), candidate("ROBUST", emos="2")]

    def actions(c, i):
        qty = 950000 if c["candidate_id"] == "CHEAP" else 1000000
        return {
            "orders": [
                {
                    "order_id": "O-0",
                    "order_date": at(0),
                    "receipt_date": at(0),
                    "quantity": str(qty),
                }
            ]
        }

    nominal = bank(split="NOMINAL", choices=choices, changes=actions)
    # No Forecast penalty: otherwise both total unit costs are exactly equal here.
    n = select_policy(nominal, selector="NOMINAL_FORECAST", profile_id="PRICE_NO_FORECAST_PENALTY")
    assert n["selected_candidate"]["candidate_id"] == "CHEAP"
    risk = bank(choices=choices, changes=actions)
    # The eighth planning path has 1,010,000 demand; cheap policy now misses 95%.
    for t in risk["traces"]:
        if t["path"]["path_id"] == "HAND-SELECTION-7":
            source = hand_trace(
                choice=t["candidate"],
                split="SELECTION",
                index=7,
                forecast=[1010000] + [0] * 64,
                orders=t["orders"],
            )
            t.update(source)
    risk["expected_paths"][7] = deepcopy(risk["traces"][-1]["path"])
    r = select_policy(risk, selector="MULTIPATH_RISK", profile_id="PRICE_NO_FORECAST_PENALTY")
    assert r["selected_candidate"]["candidate_id"] == "ROBUST"
    failing = bank(["0.94"] * 8)
    assert select_policy(failing, selector="MULTIPATH_RISK")["status"] == "NO_ROBUST_CANDIDATE"
    assert (
        select_policy(bank(["0.94"], split="NOMINAL"), selector="NOMINAL_FORECAST")["status"]
        == "NO_NOMINAL_CANDIDATE"
    )


def test_numeric_semantic_tie_ignores_candidate_id_and_never_lexically_sorts_numbers():
    choices = [candidate("A-ID", rmos="10"), candidate("Z-ID", rmos="2")]
    value = bank(choices=choices)
    result = select_policy(value, selector="MULTIPATH_RISK")
    assert result["selected_candidate"]["candidate_id"] == "Z-ID"
    loser = next(
        r for r in result["candidate_assessments"] if r["candidate"]["candidate_id"] == "A-ID"
    )
    assert loser["selection_reason"] == "SEMANTIC_POLICY_TIE_BREAK"
    choices = [candidate("A-ID", policy_kind="PERIODIC_ORDER_UP_TO"), candidate("Z-ID")]
    assert (
        select_policy(bank(choices=choices), selector="MULTIPATH_RISK")["selected_candidate"][
            "candidate_id"
        ]
        == "Z-ID"
    )


def test_cost_ranking_does_not_use_display_cents_or_semantic_key_first():
    choices = [candidate("CHEAP", rmos="10"), candidate("EXPENSIVE", rmos="1")]

    def actions(c, i):
        qty = "950000" if c["candidate_id"] == "CHEAP" else "1000000"
        return {
            "orders": [
                {"order_id": "O", "order_date": at(0), "receipt_date": at(0), "quantity": qty}
            ]
        }

    value = bank(choices=choices, changes=actions)
    value["binding"]["unit_price"]["unit_price"] = "0.000000001"
    for t in value["traces"]:
        t["binding"]["unit_price"]["unit_price"] = "0.000000001"
    result = select_policy(value, selector="MULTIPATH_RISK", profile_id="PRICE_NO_FORECAST_PENALTY")
    displays = {
        a["path_scorecards"][0]["score"]["cost"]["display_total_cost"]
        for a in result["candidate_assessments"]
    }
    assert displays == {"25.00"}
    assert result["selected_candidate"]["candidate_id"] == "CHEAP"


def test_review_is_preserved_and_block_never_becomes_eligible():
    result = select_policy(bank(), selector="MULTIPATH_RISK")
    assert result["source_review_required"] is True
    assert result["operational_policy_approved"] is False
    blocked = bank(changes=lambda c, i: {"orders": [], "gate": "BLOCK"})
    result = select_policy(blocked, selector="MULTIPATH_RISK")
    assert result["status"] == "NO_ROBUST_CANDIDATE"
    assert (
        "CALCULATION_GATE_BLOCKED"
        in result["candidate_assessments"][0]["assessment"]["reason_codes"]
    )


@pytest.mark.parametrize("split", ["VALIDATION", "HOLDOUT", "STRESS"])
@pytest.mark.parametrize("selector", ["NOMINAL_FORECAST", "MULTIPATH_RISK"])
def test_validation_holdout_and_stress_cannot_select(split, selector):
    with pytest.raises(InventoryInputError, match="RESELECTION_FORBIDDEN"):
        select_policy(bank(split=split), selector=selector)


def test_locked_choice_can_fail_validation_without_reselection_or_gate_relaxation():
    original = bank()
    selected = select_policy(original, selector="MULTIPATH_RISK")
    validation = bank(["0"] * 8, split="VALIDATION")
    result = evaluate_locked_policy(
        selected, original, validation, expected_selection_hash=selected["content_hash"]
    )
    assert result["selected_candidate"] == selected["selected_candidate"]
    assert result["candidate_assessment"]["assessment"]["eligible"] is False
    assert result["reselected"] is False
    assert result["automatic_publish_allowed"] is False
    assert result["automatic_order_allowed"] is False


def test_resealed_changed_winner_or_metric_is_rejected_by_recomputation():
    value = bank()
    selected = select_policy(value, selector="MULTIPATH_RISK")
    selected["candidate_assessments"][0]["assessment"]["mean_total_cost"] = "0"
    selected["content_hash"] = digest({k: v for k, v in selected.items() if k != "content_hash"})
    with pytest.raises(InventoryInputError, match="REPLAY_MISMATCH"):
        verify_selection(selected, value, expected_selection_hash=selected["content_hash"])


def test_permutations_ambient_decimal_and_input_immutability():
    value = bank(choices=[candidate(), candidate("B", emos="2")])
    before = deepcopy(value)
    selected = select_policy(value, selector="MULTIPATH_RISK")
    assert value == before
    value["traces"].reverse()
    value["candidates"].reverse()
    value["expected_paths"].reverse()
    with localcontext() as ctx:
        ctx.prec = 8
        assert select_policy(value, selector="MULTIPATH_RISK") == selected


@pytest.mark.parametrize(
    "mutation",
    [
        lambda b: b["traces"].pop(),
        lambda b: b["traces"].append(deepcopy(b["traces"][0])),
        lambda b: b["traces"].__setitem__(0, deepcopy(b["traces"][1])),
        lambda b: b["traces"][0]["path"].update(path_content_hash="a" * 64),
        lambda b: b["traces"][0]["binding"].update(immutable_input_hash="b" * 64),
        lambda b: b["traces"][0]["gate"].update(effective_order_action="ALLOW"),
        lambda b: b["traces"][0]["candidate"].update(emos="10"),
        lambda b: b["candidates"].append(deepcopy(b["candidates"][0])),
        lambda b: b["expected_paths"][0].update(split="HOLDOUT"),
        lambda b: b["expected_paths"][0].update(seed=True),
        lambda b: b["expected_paths"][0].update(seed=b["expected_paths"][1]["seed"]),
        lambda b: b["traces"][0].update(cached_fill_rate="1"),
    ],
)
def test_incomplete_duplicate_or_changed_bindings_are_invalid_input_not_bad_performance(mutation):
    value = bank()
    mutation(value)
    with pytest.raises(InventoryInputError):
        select_policy(value, selector="MULTIPATH_RISK")


@pytest.mark.parametrize("change", ["PATH", "SEED", "GATE", "POLICY", "PRICE"])
def test_locked_evaluation_cannot_reuse_selection_source_or_change_bindings(change):
    original = bank()
    selected = select_policy(original, selector="MULTIPATH_RISK")
    evaluation = bank(split="HOLDOUT")
    if change in ("PATH", "SEED"):
        key = "path_id" if change == "PATH" else "seed"
        old = original["expected_paths"][0][key]
        evaluation["expected_paths"][0][key] = old
        evaluation["traces"][0]["path"][key] = old
    elif change == "GATE":
        for t in evaluation["traces"]:
            t["gate"]["effective_order_action"] = "ALLOW"
    elif change == "POLICY":
        evaluation["candidates"][0]["emos"] = "3"
        for t in evaluation["traces"]:
            t["candidate"]["emos"] = "3"
    elif change == "PRICE":
        evaluation["binding"]["unit_price"]["unit_price"] = "1"
        for t in evaluation["traces"]:
            t["binding"]["unit_price"]["unit_price"] = "1"
    with pytest.raises(InventoryInputError):
        evaluate_locked_policy(
            selected, original, evaluation, expected_selection_hash=selected["content_hash"]
        )

"""Independent selection goldens, exact tie hierarchy, and fail-closed evidence intake."""

import copy
from decimal import Inexact, ROUND_FLOOR, localcontext

import pytest

from tests.unit.test_policy_search import execute as search, DEPLOYMENT
from tests.support.policy_search_fixtures import bundle, GOLDEN, multi_item_bundle
from math_fixtures import build_math, GOLDEN_MATH, reseal_math, adjustment
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from dsio_inventory_engine.recommend_replenishment.application.policy_selection import (
    RunPolicySelectionUseCase,
    ranking_key,
)


def select(source=None):
    return RunPolicySelectionUseCase(DEPLOYMENT).execute(source or search())


def reseal(source):
    for child in source["candidate_evaluations"]:
        child["content_hash"] = digest({k: v for k, v in child.items() if k != "content_hash"})
    source["content_hash"] = digest({k: v for k, v in source.items() if k != "content_hash"})
    return source


def configured(*, minimum="0.95", cost=True, case=None, config_values=None):
    config = copy.deepcopy(GOLDEN["search_config"])
    config["minimum_fill_rate"] = minimum
    if not cost:
        config["cost_assumptions"] = None
    return search(
        bundle(build_math(case or GOLDEN_MATH["cases"][0]), config, config_values=config_values)
    )


def test_service_filter_precedes_cost_and_keeps_review_gates():
    source = search()
    before = copy.deepcopy(source)
    output = select(source)
    item = output["item_selections"][0]
    # The cheaper 34 USD fixed-Q policy fulfills only 23/30. The 62 USD policy fulfills 30/30.
    assert item["selected_policy"]["policy_kind"] == "PERIODIC_ORDER_UP_TO"
    assert item["selected_score"]["total_cost"] == "62"
    assert item["selected_score"]["current_demand_fill_rate"] == "1"
    assert item["source_review_required"] is True
    rejected = next(row for row in item["candidate_decisions"] if row["status"] == "INELIGIBLE")
    assert rejected["reason_codes"] == ["FILL_RATE_BELOW_MINIMUM"]
    assert rejected["rank"] is None
    assert output["selected_item_count"] == 1 and output["not_selected_item_count"] == 0
    assert not output["automatic_order_allowed"] and not output["automatic_publish_allowed"]
    assert not output["operational_policy_approved"] and not output["database_writes"]
    assert source == before and source["selected_policies"] is None
    assert item["selected_candidate_result_hash"] in {
        c["content_hash"] for c in source["candidate_evaluations"]
    }
    assert output["content_hash"] == digest(
        {k: v for k, v in output.items() if k != "content_hash"}
    )


def test_lowest_cost_wins_after_both_candidates_meet_service():
    item = select(configured(minimum="0.7"))["item_selections"][0]
    assert item["selected_policy"]["policy_kind"] == "ROP_FIXED_Q"
    assert item["selected_score"]["total_cost"] == "34"
    assert item["reason_codes"] == ["RANKED_FIRST", "LOWEST_TOTAL_COST"]
    assert sorted(r["rank"] for r in item["candidate_decisions"]) == [1, 2]
    assert next(r for r in item["candidate_decisions"] if r["rank"] == 2)["reason_codes"] == [
        "HIGHER_TOTAL_COST"
    ]


def test_selection_is_item_specific_not_a_group_average_or_global_winner():
    result = select(search(multi_item_bundle()))
    assert result["selected_item_count"] == 2
    first, second = result["item_selections"]
    assert first["selected_policy"]["item_id"] == "ITEM-A"
    assert first["selected_policy"]["policy_kind"] == "PERIODIC_ORDER_UP_TO"
    assert second["selected_policy"]["item_id"] == "ITEM-B"
    assert second["selected_score"]["order_count"] == 0
    assert first["selected_candidate_id"] != second["selected_candidate_id"]
    result = select(search(multi_item_bundle(second_forecast=["0", "0", "0"])))
    assert result["selected_item_count"] == result["not_selected_item_count"] == 1
    assert result["item_selections"][0]["status"] == "SELECTED"
    assert result["item_selections"][1]["reason_codes"] == ["NO_DEMAND_IN_HORIZON"]


def test_zero_explicit_cost_is_valid_and_average_inventory_breaks_tie():
    raw, config, _ = bundle()
    config["policy_search"]["minimum_fill_rate"] = "0.7"
    for key in (
        "holding_cost_per_unit_week",
        "backorder_cost_per_unit_week",
        "fixed_order_cost",
        "variable_order_cost_per_unit",
    ):
        config["policy_search"]["cost_assumptions"][key] = "0"
    item = select(search(bundle(raw, config["policy_search"], config_values=config)))[
        "item_selections"
    ][0]
    assert item["selected_policy"]["policy_kind"] == "ROP_FIXED_Q"
    assert item["selected_score"]["total_cost"] == "0"
    assert item["selected_score"]["average_on_hand_eoh_qty"] == "1"
    assert item["reason_codes"] == ["RANKED_FIRST", "TIE_RESOLVED_BY_AVERAGE_INVENTORY"]


def test_complete_tie_uses_candidate_id_not_input_order():
    source = configured(case={**GOLDEN_MATH["cases"][0], "boh": "100"})
    expected_id = min(c["candidate"]["candidate_id"] for c in source["candidate_evaluations"])
    item = select(source)["item_selections"][0]
    assert item["selected_candidate_id"] == expected_id
    assert item["reason_codes"] == ["RANKED_FIRST", "TIE_RESOLVED_BY_CANDIDATE_ID"]
    source["candidate_evaluations"].reverse()
    shuffled = select(reseal(source))["item_selections"][0]
    assert shuffled == item


def test_real_psi_same_cost_and_inventory_tie_uses_fewer_orders():
    raw, config, _ = bundle(build_math({**GOLDEN_MATH["cases"][0], "forecast": ["10", "10", "13"]}))
    config["policy_search"]["minimum_fill_rate"] = "0.5"
    for key in (
        "holding_cost_per_unit_week",
        "backorder_cost_per_unit_week",
        "fixed_order_cost",
        "variable_order_cost_per_unit",
    ):
        config["policy_search"]["cost_assumptions"][key] = "0"
    item = select(search(bundle(raw, config["policy_search"], config_values=config)))[
        "item_selections"
    ][0]
    assert item["selected_policy"]["policy_kind"] == "ROP_FIXED_Q"
    assert item["reason_codes"] == ["RANKED_FIRST", "TIE_RESOLVED_BY_ORDER_COUNT"]
    assert {row["score"]["total_cost"] for row in item["candidate_decisions"]} == {"0"}
    assert {row["score"]["average_on_hand_eoh_qty"] for row in item["candidate_decisions"]} == {"1"}
    assert sorted(row["score"]["order_count"] for row in item["candidate_decisions"]) == [1, 2]
    assert next(row for row in item["candidate_decisions"] if row["rank"] == 2)["reason_codes"] == [
        "MORE_ORDERS"
    ]


@pytest.mark.parametrize(
    "a,b",
    [
        (
            {
                "total_cost": "1.0000000000000000000000001",
                "average_on_hand_eoh_qty": "999",
                "order_count": 9,
                "candidate_id": "PC-" + "f" * 64,
            },
            {
                "total_cost": "1.0000000000000000000000002",
                "average_on_hand_eoh_qty": "0",
                "order_count": 0,
                "candidate_id": "PC-" + "0" * 64,
            },
        ),
        (
            {
                "total_cost": "10.00",
                "average_on_hand_eoh_qty": "1",
                "order_count": 9,
                "candidate_id": "PC-" + "f" * 64,
            },
            {
                "total_cost": "10",
                "average_on_hand_eoh_qty": "2",
                "order_count": 0,
                "candidate_id": "PC-" + "0" * 64,
            },
        ),
        (
            {
                "total_cost": "10",
                "average_on_hand_eoh_qty": "1",
                "order_count": 1,
                "candidate_id": "PC-" + "f" * 64,
            },
            {
                "total_cost": "10",
                "average_on_hand_eoh_qty": "1.0",
                "order_count": 2,
                "candidate_id": "PC-" + "0" * 64,
            },
        ),
        (
            {
                "total_cost": "10",
                "average_on_hand_eoh_qty": "1",
                "order_count": 1,
                "candidate_id": "PC-" + "0" * 64,
            },
            {
                "total_cost": "10",
                "average_on_hand_eoh_qty": "1",
                "order_count": 1,
                "candidate_id": "PC-" + "f" * 64,
            },
        ),
    ],
)
def test_ranking_hierarchy_exact_literals(a, b):
    assert ranking_key(a) < ranking_key(b)


def test_results_ignore_caller_decimal_context():
    source = search()
    expected = select(source)
    with localcontext() as context:
        context.prec = 8
        context.rounding = ROUND_FLOOR
        context.traps[Inexact] = True
        assert select(source) == expected


def test_missing_cost_is_not_zero_and_no_demand_is_not_perfect_service():
    no_cost = select(configured(cost=False))["item_selections"][0]
    assert no_cost["status"] == "NOT_SELECTED"
    assert "COST_PROFILE_NOT_BOUND" in no_cost["reason_codes"]
    assert no_cost["selected_score"] is None
    no_demand = select(configured(case={**GOLDEN_MATH["cases"][0], "forecast": ["0", "0", "0"]}))[
        "item_selections"
    ][0]
    assert no_demand["status"] == "NOT_SELECTED" and no_demand["reason_codes"] == [
        "NO_DEMAND_IN_HORIZON"
    ]


def test_ending_backorder_blocks_selection_even_with_zero_minimum_fill_rate():
    # No initial stock; confirmed backlog cannot be cleared when all incoming orders are too large for Source capacity.
    # Use a real canonical customer-order input, not a fabricated service metric.
    from fixtures import reseal as reseal_canonical

    raw, values, _ = bundle(
        build_math(
            {
                **GOLDEN_MATH["cases"][0],
                "boh": "0",
                "policy": {"moq": "20", "order_multiple": "10", "physical_max_capacity": "15"},
            }
        )
    )
    canonical = raw["recommendation"]["canonical_input"]
    canonical["snapshots"]["customer_orders"]["rows"] = [
        {
            **{key: canonical["context"][key] for key in ("company_cd", "subs_cd", "site_cd")},
            "item_id": "ITEM-A",
            "uom": "EA",
            "yyyyww": canonical["snapshots"]["calendar"]["rows"][0]["yyyyww"],
            "confirmed_customer_order_qty": "10",
        }
    ]
    canonical = reseal_canonical(canonical)
    from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest

    raw["recommendation"]["canonical_input"] = canonical
    raw["recommendation"]["execution"]["canonical_input_hash"] = CanonicalInputRequest.from_dict(
        canonical
    ).input_hash
    raw["policy_input"]["context"]["canonical_input_hash"] = raw["recommendation"]["execution"][
        "canonical_input_hash"
    ]
    values["policy_search"]["minimum_fill_rate"] = "0"
    source = search(bundle(reseal_math(raw), values["policy_search"], config_values=values))
    item = select(source)["item_selections"][0]
    assert item["status"] == "NOT_SELECTED" and item["reason_codes"] == ["ENDING_BACKORDER_REMAINS"]


def test_blocked_item_is_not_selected_even_when_stock_satisfies_all_demand():
    raw, values, _ = bundle(build_math({**GOLDEN_MATH["cases"][0], "boh": "100"}))
    values["policy_overlays"]["fsn_order_action"]["N"] = "BLOCK"
    item = select(search(bundle(raw, config_values=values)))["item_selections"][0]
    assert item["status"] == "NOT_SELECTED" and item["reason_codes"] == [
        "ORDER_GATE_BLOCKED",
        "RECOMMENDATION_CALCULATION_BLOCKED",
    ]


def test_all_failed_and_excluded_items_preserve_specific_reasons():
    raw, values, _ = bundle()
    raw["recommendation"]["execution"]["item_controls"][0]["max_target_qty"] = "1"
    item = select(search(bundle(raw)))["item_selections"][0]
    assert item["reason_codes"] == ["ALL_CANDIDATES_FAILED"]
    assert all(r["failure_reason"] and r["status"] == "FAILED" for r in item["candidate_decisions"])
    raw = build_math(GOLDEN_MATH["cases"][0])
    raw["policy_input"]["history"].pop()
    item = select(search(bundle(reseal_math(raw))))["item_selections"][0]
    assert item["reason_codes"] == ["SEARCH_ITEM_EXCLUDED"]
    assert item["search_exclusion_reason"] == "INSUFFICIENT_DEMAND_HISTORY"
    raw = build_math(GOLDEN_MATH["cases"][0])
    raw["policy_input"]["adjustments"] = [adjustment()]
    assert (
        select(search(bundle(reseal_math(raw))))["item_selections"][0]["search_exclusion_reason"]
        == "APPROVED_OVERRIDE_PINNED"
    )


def test_no_evaluations_has_no_winner_and_failed_candidates_do_not_hide_valid_winner():
    source = search()
    source["candidate_evaluations"] = []
    assert select(reseal(source))["item_selections"][0]["reason_codes"] == [
        "NO_CANDIDATES_EVALUATED"
    ]
    config = copy.deepcopy(GOLDEN["search_config"])
    config["group_profiles"][0]["emos_values"] = ["0.1", "0.3"]
    raw = build_math(GOLDEN_MATH["cases"][0])
    raw["recommendation"]["execution"]["item_controls"][0]["max_target_qty"] = "20"
    item = select(search(bundle(raw, config)))["item_selections"][0]
    assert item["status"] == "SELECTED"
    assert item["selected_policy"]["policy_kind"] == "PERIODIC_ORDER_UP_TO"
    assert item["selected_score"]["current_demand_fill_rate"] == "1"
    assert any(row["status"] == "FAILED" for row in item["candidate_decisions"])
    assert any(
        "FILL_RATE_BELOW_MINIMUM" in row["reason_codes"] for row in item["candidate_decisions"]
    )


@pytest.mark.parametrize(
    "change,code",
    [
        (
            lambda s: s["candidate_evaluations"][0]["metrics"].update(total_cost="0"),
            "METRICS_REPLAY_MISMATCH",
        ),
        (
            lambda s: s["candidate_evaluations"][0]["metrics"].update(
                policy_selection_eligible=False
            ),
            "METRICS_REPLAY_MISMATCH",
        ),
        (
            lambda s: s["candidate_evaluations"][0]["psi_rows"][0].update(eoh_qty="999"),
            "PSI_REPLAY_MISMATCH",
        ),
        (
            lambda s: s["candidate_evaluations"].append(
                copy.deepcopy(s["candidate_evaluations"][0])
            ),
            "CANDIDATE_DUPLICATE_OR_ORPHAN",
        ),
        (lambda s: s["candidate_evaluations"].pop(), "CANDIDATE_SET_MISMATCH"),
        (
            lambda s: s["candidate_evaluations"][0]["decision_evidence"][0][
                "effective_policy_admission"
            ].update(effective_policy_contract_version="1.0.0"),
            "GATE_VERSION_INVALID",
        ),
    ],
)
def test_resealed_tampering_still_fails_replay(change, code):
    source = search()
    change(source)
    with pytest.raises(InventoryInputError, match=code):
        select(reseal(source))


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "1e2", 1.0])
def test_ranking_rejects_non_decimal_or_negative_cost(value):
    with pytest.raises(InventoryInputError, match="NUMBER_INVALID"):
        ranking_key(
            {
                "candidate_id": "PC-" + "0" * 64,
                "total_cost": value,
                "average_on_hand_eoh_qty": "0",
                "order_count": 0,
            }
        )

"""Actual Catalog PSI service/cost selection, immutable gates and strict source replay."""

import copy
from decimal import Inexact, ROUND_FLOOR, localcontext

import pytest

from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from dsio_inventory_engine.recommend_replenishment.application import policy_selection
from tests.support.catalog_search_fixtures import run_bundle, two_item_bundle
from tests.support.catalog_selection_fixtures import feasible_parts, select, selection_case
from tests.support.policy_search_fixtures import bundle


def reseal_source(source):
    for child in source["candidate_evaluations"]:
        child["content_hash"] = digest({k: v for k, v in child.items() if k != "content_hash"})
    source["content_hash"] = digest({k: v for k, v in source.items() if k != "content_hash"})
    return source


@pytest.mark.parametrize(
    "mode,count", [("LEGACY_STYLE", 32), ("ABC_XYZ_SHARED_BUDGET", 32), ("WIDE_REFERENCE", 88)]
)
def test_actual_psi_service_filters_cheaper_understocked_policy_and_preserves_baseline(mode, count):
    case = selection_case(mode=mode)
    source, _, _, bindings = case
    before = copy.deepcopy(source)
    original_config = copy.deepcopy(bindings["config_values"])
    original_classification = copy.deepcopy(bindings["classification_snapshot"])
    result = select(case)
    item = result["item_selections"][0]
    assert result["selected_item_count"] == 1 and result["not_selected_item_count"] == 0
    assert len(item["candidate_decisions"]) == count
    assert item["selected_policy"]["policy_kind"] == "PERIODIC_ORDER_UP_TO"
    assert item["selected_policy"]["rmos"] == "0" and item["selected_policy"]["emos"] == "0.25"
    assert item["selected_policy"]["review_cycle_weeks"] == 1
    selected = next(row for row in item["candidate_decisions"] if row["status"] == "SELECTED")
    # Hand calculation: BOH=10, demand=10/week, ROP=10, Q=11, S=21.
    # Orders 11 then 10 arrive in the next buckets; EOH=0,1,1.
    # Cost = holding 2 + 2 orders * 5 + quantity 21 * 2 = 54 USD.
    assert selected["verified_metrics"]["current_demand_fulfilled_qty"] == "30"
    assert selected["verified_metrics"]["ordered_qty"] == "21"
    assert selected["verified_metrics"]["total_cost"] == "54"
    assert selected["verified_metrics"]["order_count"] == 2
    assert selected["baseline_comparison"]["forecast_shortage_qty_delta"] == "-20"
    assert selected["baseline_comparison"]["total_cost_delta"] == "54"
    cheaper = [
        row
        for row in item["candidate_decisions"]
        if row["verified_metrics"] and row["verified_metrics"]["total_cost"] == "28"
    ]
    assert cheaper and all(row["reason_codes"] == ["FILL_RATE_BELOW_MINIMUM"] for row in cheaper)
    assert item["source_review_required"] is True
    assert result["candidate_mode"] == mode
    assert result["candidate_catalog_hash"] == source["candidate_catalog_hash"]
    assert result["source_result_reference"] == "OCPSI-" + source["content_hash"]
    assert not result["automatic_order_allowed"] and not result["automatic_publish_allowed"]
    assert not result["operational_policy_approved"] and not result["database_writes"]
    assert source == before and source["selected_policies"] is None
    assert bindings["config_values"] == original_config
    assert bindings["classification_snapshot"] == original_classification


@pytest.mark.parametrize("action", ["BLOCK", "REVIEW"])
def test_fsn_gate_is_not_relaxed_even_when_all_current_demand_can_be_fulfilled(action):
    _, values, _ = run_bundle()
    values["policy_overlays"]["fsn_order_action"]["N"] = action
    case = selection_case(mode="WIDE_REFERENCE", parts=feasible_parts(boh="100", values=values))
    source = copy.deepcopy(case[0])
    item = select(case)["item_selections"][0]
    if action == "BLOCK":
        assert item["status"] == "NOT_SELECTED"
        assert item["reason_codes"] == ["ORDER_GATE_BLOCKED", "RECOMMENDATION_CALCULATION_BLOCKED"]
        assert all(
            row["verified_metrics"]["current_demand_fill_rate"] == "1"
            and row["status"] == "INELIGIBLE"
            for row in item["candidate_decisions"]
        )
    else:
        assert item["status"] == "SELECTED" and item["source_review_required"] is True
    assert case[0] == source


@pytest.mark.parametrize(
    "forecast,cost,reason",
    [
        (["0", "0", "0"], True, "NO_DEMAND_IN_HORIZON"),
        (["10", "10", "10"], False, "COST_PROFILE_NOT_BOUND"),
    ],
)
def test_unavailable_service_or_cost_does_not_create_a_fake_zero_cost_winner(
    forecast, cost, reason
):
    _, values, _ = run_bundle()
    if not cost:
        values["policy_search"]["cost_assumptions"] = None
    result = select(selection_case(parts=feasible_parts(forecast=forecast, values=values)))
    item = result["item_selections"][0]
    assert item["status"] == "NOT_SELECTED" and reason in item["reason_codes"]
    assert item["selected_score"] is None and item["selected_policy"] is None
    if not cost:
        assert all(
            row["verified_metrics"]["total_cost"] is None
            and row["baseline_comparison"]["total_cost_delta"] is None
            for row in item["candidate_decisions"]
        )
    else:
        assert all(
            row["verified_metrics"]["current_demand_fill_rate"] is None
            and row["baseline_comparison"]["current_demand_fill_rate_delta"] is None
            for row in item["candidate_decisions"]
        )


@pytest.mark.parametrize("maximum,all_failed", [("25", False), ("1", True)])
def test_candidate_failures_are_retained_without_changing_baseline_or_other_candidates(
    maximum, all_failed
):
    parts = feasible_parts()
    parts[0]["recommendation"]["execution"]["item_controls"][0]["max_target_qty"] = maximum
    case = selection_case(parts=parts)
    item = select(case)["item_selections"][0]
    failed = [row for row in item["candidate_decisions"] if row["status"] == "FAILED"]
    assert failed
    assert all(
        row["reason_codes"] == ["CANDIDATE_FAILED"]
        and row["failure_reason"] == "STRATEGY_EXECUTION_FAILED"
        and row["verified_metrics"] is None
        and row["baseline_comparison"] is None
        for row in failed
    )
    assert item["baseline_metrics"] == case[0]["baseline_items"][0]["metrics"]
    if all_failed:
        assert item["status"] == "NOT_SELECTED" and item["reason_codes"] == [
            "ALL_CANDIDATES_FAILED"
        ]
    else:
        assert item["status"] == "SELECTED"


def test_missing_factor_exclusion_keeps_reason_baseline_and_no_selected_pointer():
    case = selection_case(feature_changes=lambda v: v["sources"]["unit_cost"].update(rows=[]))
    item = select(case)["item_selections"][0]
    assert item["reason_codes"] == ["SEARCH_ITEM_EXCLUDED"]
    assert item["search_exclusion_reason"] == "LS_MISSING_REQUIRED_FACTOR"
    assert item["candidate_decisions"] == [] and item["selected_candidate_id"] is None
    assert item["baseline_metrics"] == case[0]["baseline_items"][0]["metrics"]


def test_selection_is_per_item_and_two_item_ledgers_are_not_aggregated():
    result = select(selection_case(mode="WIDE_REFERENCE", parts=two_item_bundle()))
    first, second = result["item_selections"]
    assert first["status"] == "SELECTED" and first["selected_policy"]["item_id"] == "ITEM-A"
    assert first["selected_score"]["order_count"] > 0
    assert second["status"] == "SELECTED" and second["selected_policy"]["item_id"] == "ITEM-B"
    assert second["selected_score"]["order_count"] == 0
    assert result["selected_item_count"] == 2 and result["not_selected_item_count"] == 0


def test_fill_above_minimum_still_rejects_a_remaining_confirmed_customer_backorder():
    from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
    from fixtures import reseal
    from math_fixtures import reseal_math

    raw, values, _ = feasible_parts(boh="30")
    canonical = raw["recommendation"]["canonical_input"]
    fields = {
        key: canonical["snapshots"]["master"]["rows"][0][key]
        for key in ("company_cd", "subs_cd", "site_cd", "item_id", "uom")
    }
    canonical["snapshots"]["customer_orders"]["rows"] = [
        {
            **fields,
            "yyyyww": canonical["snapshots"]["calendar"]["rows"][-1]["yyyyww"],
            "confirmed_customer_order_qty": "11",
        }
    ]
    canonical = reseal(canonical)
    raw["recommendation"]["canonical_input"] = canonical
    raw["recommendation"]["execution"]["canonical_input_hash"] = CanonicalInputRequest.from_dict(
        canonical
    ).input_hash
    raw["recommendation"]["execution"]["item_controls"][0]["order_dates"] = []
    raw["policy_input"]["context"]["canonical_input_hash"] = raw["recommendation"]["execution"][
        "canonical_input_hash"
    ]
    parts = bundle(reseal_math(raw), values["policy_search"], config_values=values)
    item = select(selection_case(parts=parts))["item_selections"][0]
    # 10 + 10 + 11 current units; 30 fulfilled gives >95%, but the final actual order owes 1.
    assert item["status"] == "NOT_SELECTED" and item["reason_codes"] == ["ENDING_BACKORDER_REMAINS"]
    assert all(
        row["verified_metrics"]["current_demand_qty"] == "31"
        and row["verified_metrics"]["current_demand_fulfilled_qty"] == "30"
        and row["verified_metrics"]["ending_backorder_qty"] == "1"
        for row in item["candidate_decisions"]
    )


def test_exact_candidate_id_tie_break_and_new_prefix_are_explicit_not_abc_masquerading():
    result = select(selection_case(parts=feasible_parts(boh="100")))
    item = result["item_selections"][0]
    assert item["selected_candidate_id"] == min(
        row["candidate_id"] for row in item["candidate_decisions"]
    )
    assert item["reason_codes"] == ["RANKED_FIRST", "TIE_RESOLVED_BY_CANDIDATE_ID"]
    assert all("segment_key" not in row for row in [item["selected_policy"]])
    with pytest.raises(InventoryInputError, match="CANDIDATE_ID_INVALID"):
        policy_selection.ranking_key(item["selected_score"])
    policy_selection.ranking_key(item["selected_score"], candidate_prefix="OCCP-")


def test_shared_exact_ranking_preserves_cost_inventory_order_and_id_priority():
    def score(cost, inventory, orders, token):
        return {
            "total_cost": cost,
            "average_on_hand_eoh_qty": inventory,
            "order_count": orders,
            "candidate_id": "OCCP-" + token * 64,
        }

    scores = [
        score("1.0000000000000000000001", "999", 99, "f"),
        score("1.0000000000000000000002", "0", 0, "0"),
        score("2", "1", 3, "f"),
        score("2", "1", 2, "f"),
        score("2", "1", 2, "0"),
    ]
    ordered = sorted(
        scores, key=lambda row: policy_selection.ranking_key(row, candidate_prefix="OCCP-")
    )
    assert ordered == [scores[0], scores[1], scores[4], scores[3], scores[2]]


@pytest.mark.parametrize(
    "change",
    [
        lambda v: v["candidate_evaluations"].pop(),
        lambda v: v["candidate_evaluations"].append(copy.deepcopy(v["candidate_evaluations"][0])),
        lambda v: v["candidate_evaluations"].clear(),
        lambda v: v["candidate_evaluations"][0]["metrics"].update(total_cost="0"),
        lambda v: v["candidate_evaluations"][0]["metrics"].update(current_demand_fill_rate="0.95"),
        lambda v: v["candidate_evaluations"][0]["metrics"].update(policy_selection_eligible=False),
        lambda v: v["candidate_evaluations"][0]["psi_rows"][0].update(eoh_qty="999"),
        lambda v: v["baseline_items"][0]["psi_rows"][0].update(eoh_qty="999"),
        lambda v: v["baseline_items"][0]["metrics"].update(total_cost="999"),
        lambda v: v["candidate_evaluations"][0]["decision_evidence"][0][
            "effective_policy_admission"
        ].update(no_order_gate=True),
        lambda v: v["candidate_evaluations"][0]["orders"][0].update(quantity="999"),
        lambda v: v.update(excluded_items=[{"item_id": "ITEM-A", "reason_code": "MANUAL_SKIP"}]),
        lambda v: v.update(automatic_order_allowed=True),
        lambda v: v.update(extra=True),
        lambda v: v["context"].update(site_cd="OTHER"),
        lambda v: v.update(candidate_mode="WIDE_REFERENCE"),
    ],
)
def test_rehashed_source_rows_metrics_gate_cost_and_candidate_coverage_are_replayed(change):
    source, selector, request, bindings = selection_case()
    change(source)
    reseal_source(source)
    # Even replacing the expected pin cannot make a changed result match the fixed actual inputs.
    bindings["expected_source_hash"] = source["content_hash"]
    with pytest.raises(InventoryInputError, match="CATALOG_PSI_REPLAY_MISMATCH"):
        selector.execute(source, request, **bindings)


def test_success_cannot_be_resealed_as_failed_to_hide_a_better_candidate():
    source, selector, request, bindings = selection_case()
    child = source["candidate_evaluations"][0]
    child.update(
        status="FAILED",
        failure_reason="FAKE_FAILURE",
        row_count=None,
        psi_rows=None,
        decision_evidence=None,
        orders=None,
        metrics=None,
    )
    reseal_source(source)
    bindings["expected_source_hash"] = source["content_hash"]
    with pytest.raises(InventoryInputError, match="REPLAY_MISMATCH"):
        selector.execute(source, request, **bindings)


def test_wrong_hash_catalog_scope_and_operational_environment_fail_closed():
    source, selector, request, bindings = selection_case()
    for change in ({"expected_source_hash": "0" * 64}, {"expected_catalog_hash": "0" * 64}):
        with pytest.raises(InventoryInputError, match="HASH_MISMATCH"):
            selector.execute(source, request, **{**bindings, **change})
    selector.deployment = DeploymentScope("DSE", "PRODUCTION")
    with pytest.raises(InventoryInputError, match="DEVELOPMENT_ONLY"):
        selector.execute(source, request, **bindings)


def test_source_verification_can_run_without_selecting_or_ranking(monkeypatch):
    source, selector, request, bindings = selection_case()
    monkeypatch.setattr(
        selector, "_select", lambda value: pytest.fail("Holdout intake must not select")
    )
    assert selector.verify_source(source, request, **bindings) is None


def test_host_decimal_context_does_not_change_selection_or_comparisons():
    case = selection_case()
    expected = select(case)
    with localcontext() as ctx:
        ctx.prec = 6
        ctx.rounding = ROUND_FLOOR
        ctx.traps[Inexact] = True
        assert select(case) == expected


def test_existing_abc_classification_search_and_selection_hashes_are_unchanged():
    from tests.unit.test_policy_search import execute, DEPLOYMENT
    from dsio_inventory_engine.recommend_replenishment.application.policy_selection import (
        RunPolicySelectionUseCase,
    )

    _, _, classification = bundle()
    source = execute()
    assert (
        classification["content_hash"]
        == "a42c083bd1d9268535b8271babd678c9d36a5f93d2582163c12ee4f91814b47d"
    )
    assert (
        source["content_hash"] == "5e1808cb4bde77ee48feb065679065614f3513bcb72326a3882d3d59cd45fc75"
    )
    assert (
        RunPolicySelectionUseCase(DEPLOYMENT).execute(source)["content_hash"]
        == "1dc99a92df87938682b845042f4d48f9a23ae731bf90f7ba97583736b4a9ae17"
    )

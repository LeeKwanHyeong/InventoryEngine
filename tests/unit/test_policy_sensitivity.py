"""Independent numeric expectations plus scope, comparison and holdout safety tests."""

import copy

import pytest

from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.policy_sensitivity import PolicySensitivityRequest
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from dsio_inventory_engine.recommend_replenishment.application.policy_sensitivity import (
    RunPolicySensitivityUseCase,
)
from tests.support.policy_sensitivity_fixtures import experiment, source
from tests.unit.test_policy_selection import configured
from math_fixtures import GOLDEN_MATH

DEPLOYMENT = DeploymentScope("DSE", "DEVELOPMENT")


def run(data):
    return RunPolicySensitivityUseCase(DEPLOYMENT).execute(PolicySensitivityRequest.from_dict(data))


def golden_data():
    result = configured(minimum="0.7")
    return experiment(
        [
            {
                "case_id": "validation-13",
                "trace_id": "golden",
                "split": "VALIDATION",
                "seed": 7,
                "search_result": result,
            }
        ]
    )


def test_literal_repricing_and_service_stays_identical():
    data = golden_data()
    before = copy.deepcopy(data)
    result = run(data)
    base = next(r for r in result["matrix"] if r["cost_profile_id"] == "base")
    holding = next(r for r in result["matrix"] if r["cost_profile_id"] == "holding-x5")
    fixed_id = next(
        c["candidate"]["candidate_id"]
        for c in data["cases"][0]["search_result"]["candidate_evaluations"]
        if c["candidate"]["policy_kind"] == "ROP_FIXED_Q"
    )

    def metric(row):
        return next(c["metrics"] for c in row["candidate_metrics"] if c["candidate_id"] == fixed_id)

    # Literal PSI EOH [0,3,0]; one order 13 EA: 3*1+5+13*2=34; 3*5+5+13*2=46.
    assert metric(base)["total_cost"] == "34"
    assert metric(holding)["total_cost"] == "46"
    assert metric(base)["cost_per_week"] == "11.33333333333333333333333333333333333333"
    assert metric(base)["current_demand_fill_rate"] == metric(holding)["current_demand_fill_rate"]
    assert base["selected_candidate_id"] == fixed_id
    missing = next(r for r in result["matrix"] if r["cost_profile_id"] == "not-bound")
    assert missing["selected_candidate_id"] is None
    assert missing["reason_codes"] == ["COST_PROFILE_NOT_BOUND"]
    assert data == before


def test_order_and_decimal_context_do_not_change_hash():
    from decimal import ROUND_FLOOR, localcontext

    data = golden_data()
    result = run(data)
    data["cost_profiles"].reverse()
    with localcontext() as ctx:
        ctx.prec = 6
        ctx.rounding = ROUND_FLOOR
        assert run(data) == result


def test_literal_common_window_includes_committed_open_order_cost():
    from dsio_inventory_engine.recommend_replenishment.application.policy_sensitivity import (
        _metrics,
    )
    from dsio_inventory_engine.recommend_replenishment.application.policy_search import NUMBERS
    from decimal import localcontext

    data = golden_data()
    source_result = data["cases"][0]["search_result"]
    child = next(
        c
        for c in source_result["candidate_evaluations"]
        if c["candidate"]["policy_kind"] == "PERIODIC_ORDER_UP_TO"
    )
    with localcontext(NUMBERS):
        metrics = _metrics(child, data["cost_profiles"][0]["cost_assumptions"], "0.7", 2)
    # First 2 weeks: EOH [0,3], arrivals 13, orders 13+10, second order arrives in week 3.
    assert metrics["horizon_weeks"] == 2
    assert metrics["current_demand_fulfilled_qty"] == "20"
    assert metrics["ending_on_hand_qty"] == "3"
    assert metrics["ordered_qty"] == "23"
    assert metrics["open_recommended_qty_at_boundary"] == "10"
    assert metrics["total_cost"] == "59"  # 3 holding + 2*5 fixed + 23*2 variable.
    assert metrics["cost_per_week"] == "29.5"


def test_no_demand_is_not_free_service_or_a_policy_selection():
    data = golden_data()
    data["cases"][0]["search_result"] = configured(
        minimum="0.7", case={**GOLDEN_MATH["cases"][0], "forecast": ["0"] * 3}
    )
    result = run(data)
    assert all(r["selected_candidate_id"] is None for r in result["matrix"])
    assert all("NO_DEMAND_IN_HORIZON" in r["reason_codes"] for r in result["matrix"])
    assert all(
        m["metrics"]["cost_per_current_demand_unit"] is None
        for r in result["matrix"]
        for m in r["candidate_metrics"]
    )


def test_holdout_policy_locked_and_not_reselected(monkeypatch):
    data = experiment(
        [
            {
                "case_id": "validation-13",
                "trace_id": "validation",
                "split": "VALIDATION",
                "seed": 7,
                "search_result": source(),
            },
            {
                "case_id": "holdout",
                "trace_id": "holdout",
                "split": "HOLDOUT",
                "seed": 17,
                "search_result": source(seed=17),
            },
        ]
    )
    import dsio_inventory_engine.recommend_replenishment.application.policy_selection as selector

    # Intake must replay source evidence, not secretly run a holdout optimizer and discard it.
    def forbidden(*args, **kwargs):
        pytest.fail("Source verifier attempted a policy selection")

    monkeypatch.setattr(selector, "rank_candidate_assessments", forbidden)
    result = run(data)
    selected = result["reference_selection"][0]["selected_candidate_id"]
    assert selected is not None
    assert all(r["locked_candidate_id"] == selected for r in result["holdout_evaluations"])
    assert all(
        r["selected_candidate_id"] is None for r in result["matrix"] if r["split"] == "HOLDOUT"
    )
    assert all(
        a["rank"] is None
        for r in result["matrix"]
        if r["split"] == "HOLDOUT"
        for a in r["candidate_assessments"]
    )
    assert not result["operational_policy_approved"] and not result["database_writes"]


def test_terminal_extension_compares_same_prefix_and_open_orders():
    data = experiment(
        [
            {
                "case_id": "validation-13",
                "trace_id": "trace",
                "split": "VALIDATION",
                "seed": 7,
                "search_result": source(13),
            },
            {
                "case_id": "extended",
                "trace_id": "trace",
                "split": "VALIDATION",
                "seed": 7,
                "search_result": source(17),
            },
        ],
        pairs=[{"short_case_id": "validation-13", "extended_case_id": "extended"}],
    )
    result = run(data)
    assert len(result["terminal_diagnostics"]) == 4
    assert any(d["horizon_dependent_actions"] for d in result["terminal_diagnostics"])
    assert any(
        float(d["extended_common_window_metrics"]["open_recommended_qty_at_boundary"]) > 0
        for d in result["terminal_diagnostics"]
    )
    assert all(
        d["common_window_weeks"] == 13 and d["extended_full_metrics"]["horizon_weeks"] == 17
        for d in result["terminal_diagnostics"]
    )


@pytest.mark.parametrize(
    "mutation,error",
    [
        (lambda d: d.update(reference_case_id="unknown"), "REFERENCE_INVALID"),
        (lambda d: d["cases"][0].update(split="HOLDOUT"), "REFERENCE_INVALID"),
        (
            lambda d: d["cost_profiles"][1]["cost_assumptions"].update(currency="EUR"),
            "CURRENCY_MISMATCH",
        ),
        (
            lambda d: d["cost_profiles"][0]["cost_assumptions"].update(fixed_order_cost="-1"),
            "COST_INVALID",
        ),
        (lambda d: d["cases"].append(copy.deepcopy(d["cases"][0])), "DUPLICATE"),
        (
            lambda d: d["terminal_pairs"].append(
                {"short_case_id": "validation-13", "extended_case_id": "validation-13"}
            ),
            "TERMINAL_PAIR_INVALID",
        ),
    ],
)
def test_closed_request_negative(mutation, error):
    data = golden_data()
    mutation(data)
    with pytest.raises(InventoryInputError, match=error):
        run(data)


@pytest.mark.parametrize(
    "other,error",
    [
        (lambda: source(17, boh="31"), "INITIAL_CONDITIONS_MISMATCH"),
        (lambda: source(17, lead=14), "INITIAL_CONDITIONS_MISMATCH"),
        (lambda: source(17, seed=29), "TRACE_PREFIX_MISMATCH"),
        (lambda: source(17, order_stride=2), "ORDER_CALENDAR_MISMATCH"),
    ],
)
def test_comparability_mismatch_rejected(other, error):
    data = experiment(
        [
            {
                "case_id": "validation-13",
                "trace_id": "trace",
                "split": "VALIDATION",
                "seed": 7,
                "search_result": source(13),
            },
            {
                "case_id": "extended",
                "trace_id": "trace",
                "split": "VALIDATION",
                "seed": 7,
                "search_result": other(),
            },
        ]
    )
    with pytest.raises(InventoryInputError, match=error):
        run(data)


def test_holdout_same_seed_or_reused_path_rejected():
    for seed, other in ((7, source(seed=17)), (17, source())):
        data = experiment(
            [
                {
                    "case_id": "validation-13",
                    "trace_id": "validation",
                    "split": "VALIDATION",
                    "seed": 7,
                    "search_result": source(),
                },
                {
                    "case_id": "holdout",
                    "trace_id": "holdout",
                    "split": "HOLDOUT",
                    "seed": seed,
                    "search_result": other,
                },
            ]
        )
        with pytest.raises(InventoryInputError, match="HOLDOUT_REUSED"):
            run(data)


def test_production_and_scope_are_rejected():
    request = PolicySensitivityRequest.from_dict(golden_data())
    with pytest.raises(InventoryInputError, match="DEVELOPMENT_ONLY"):
        RunPolicySensitivityUseCase(DeploymentScope("DSE", "PRODUCTION")).execute(request)
    with pytest.raises(InventoryInputError, match="SOURCE_SCOPE_INVALID"):
        RunPolicySensitivityUseCase(DeploymentScope("OTHER", "DEVELOPMENT")).execute(request)

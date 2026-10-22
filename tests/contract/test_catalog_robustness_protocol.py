"""Static study-contract checks and hand arithmetic, NOT a PSI generator or risk selector."""

from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING, localcontext
import json
from pathlib import Path

import pytest

from dsio_inventory_engine.inventory_contracts.values import digest

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = ROOT / "docs/architecture/data/catalog-robustness-protocol-v1.json"
FROZEN_HASH = "a3e35fcf23915e87823883ea8c1f09448b245ae7ea1fffa8c77482bab0433280"


def protocol():
    def closed_pairs(pairs):
        assert len({key for key, _ in pairs}) == len(pairs), "DUPLICATE_PROTOCOL_FIELD"
        return dict(pairs)

    return json.loads(PROTOCOL.read_text(), object_pairs_hook=closed_pairs)


def test_specification_is_pinned_and_is_not_an_implemented_or_executed_experiment():
    value = protocol()
    assert digest(value) == FROZEN_HASH
    assert value["status"] == "CONDITIONS_FROZEN_NOT_IMPLEMENTED_OR_EXECUTED"
    assert value["execution"]["implementation_status"] == "NOT_IMPLEMENTED"
    assert value["execution"]["official_paths_generated"] is False
    assert value["execution"]["official_runs_executed"] is False
    assert value["execution"]["execution_authorized_by_this_contract_change"] is False
    assert value["generation"]["generate_any_official_path_in_this_change"] is False


@pytest.mark.parametrize("index", [0, 1])
def test_complete_history_and_simulation_do_not_reuse_seen_periods(index):
    value = protocol()
    window = value["windows"][index]
    w0 = date.fromisoformat(window["w0"])
    assert w0.weekday() == 0
    start = w0 - timedelta(weeks=value["calendar"]["history_weeks"])
    end = w0 + timedelta(weeks=value["calendar"]["simulation_weeks"], days=-1)
    for previous in value["previously_used_periods"]:
        assert end < date.fromisoformat(previous["start_date"]) or start > date.fromisoformat(
            previous["end_date"]
        )
    other = date.fromisoformat(value["windows"][1 - index]["w0"])
    assert end < other - timedelta(weeks=104) or start > other + timedelta(weeks=65, days=-1)
    expected = [
        ("2035-01-08", "2038-01-03", "2038-04-04"),
        ("2042-01-06", "2045-01-01", "2045-04-02"),
    ][index]
    assert (
        start.isoformat(),
        (w0 + timedelta(weeks=52, days=-1)).isoformat(),
        end.isoformat(),
    ) == expected


def test_seeds_are_explicit_disjoint_and_only_lt_variants_share_a_demand_stream():
    value = protocol()
    all_seeds = []
    for window in value["windows"]:
        all_seeds.append(window["history_seed"])
        for key, count in (
            ("selection_seeds", 8),
            ("validation_seeds", 8),
            ("holdout_seeds", 16),
            ("diagnostic_seeds", 4),
        ):
            seeds = window[key]
            assert len(seeds) == count and all(type(seed) is int for seed in seeds)
            all_seeds.extend(seeds)
    assert len(set(all_seeds)) == len(all_seeds)
    assert not set(all_seeds) & {2033010301, 2034070301, 440001}
    left, right = value["items"][1:]
    assert left["demand_stream_group"] == right["demand_stream_group"]
    assert {key: val for key, val in left.items() if key not in ("item_id", "lead_time_days")} == {
        key: val for key, val in right.items() if key not in ("item_id", "lead_time_days")
    }
    assert [left["lead_time_days"], right["lead_time_days"]] == [7, 49]
    assert value["generation"]["shared_lt_pair_is_not_two_independent_statistical_samples"] is True


def test_tail_covers_scored_orders_and_prefixes_are_not_separate_resets():
    value = protocol()
    calendar = value["calendar"]
    assert calendar["score_weeks"] + calendar["tail_weeks"] == calendar["simulation_weeks"] == 65
    assert calendar["diagnostic_prefix_weeks"] == [13, 26, 52]
    for item in value["items"]:
        last_scored_arrival_index = calendar["score_weeks"] - 1 + (item["lead_time_days"] + 6) // 7
        assert last_scored_arrival_index < calendar["simulation_weeks"]
    assert calendar["tail_ordering"] == "CONTINUE_SAME_POLICY_NOT_DRAIN_DOWN"
    assert calendar["warmup_weeks"] == 52
    assert (
        value["initial_state"]["warmup_orders_arriving_after_w0"]
        == "ALLOW_AND_PRESERVE_PIPELINE_NOT_FINITE_WARMUP_REJECTION"
    )


@pytest.mark.parametrize(
    "items,candidates,accepted", [(1, 32, True), (1, 88, True), (2, 88, False)]
)
def test_observation_bound_is_not_raised_to_enable_long_horizon(items, candidates, accepted):
    value = protocol()
    horizon = value["calendar"]["simulation_weeks"]
    work = items * candidates * horizon * (horizon + 1) // 2
    assert (work <= value["execution"]["max_observation_work_per_call"]) is accepted
    assert value["execution"]["wide_single_item_work"] == 188760
    assert value["execution"]["max_items_per_call"] == 1
    assert 88 * 67 * 68 // 2 > 200000


def test_original_rules_hash_profile_counts_and_parent_classification_are_preserved():
    value = protocol()
    candidate = value["candidate_contract"]
    rules = json.loads(
        (ROOT / "docs/architecture/data/legacy-style-development-rules-v1.json").read_text()
    )
    assert (
        digest(rules["profile"]) == candidate["rules_profile_hash"] == rules["profile_content_hash"]
    )
    assert candidate["counts_per_item"] == [32, 32, 88]
    assert candidate["full_population_classification_before_item_sharding"] is True
    assert candidate["original_parent_classification_id_hash_and_items_preserved"] is True
    assert candidate["candidate_profiles_changed"] is False
    assert candidate["reclassify_after_w0"] is False
    assert candidate["item_exceptions"] == []


@pytest.mark.parametrize(
    "profile_id,changed",
    [
        ("PRICE_HOLDING_HIGH", "holding_coefficient"),
        ("PRICE_ORDER_HIGH", "fixed_order_cost"),
        ("PRICE_NO_FORECAST_PENALTY", "forecast_shortage_price_multiplier"),
    ],
)
def test_price_sensitivities_change_one_factor_not_several(profile_id, changed):
    costs = protocol()["cost"]
    profiles = {row["profile_id"]: row for row in costs["profiles"]}
    base, other = profiles["PRICE_BASE"], profiles[profile_id]
    assert {key for key in base if base[key] != other[key]} == {"profile_id", changed}
    assert costs["primary_profile"] == "PRICE_BASE"
    assert costs["original_source_total_cost_overwritten_or_added_to_repriced_cost"] is False


def test_price_cost_units_and_component_sum_match_hand_example():
    cost = protocol()["cost"]
    base = next(row for row in cost["profiles"] if row["profile_id"] == "PRICE_BASE")
    with localcontext() as context:
        context.prec = cost["calculation_precision"]
        price = Decimal("250")
        holding = price * Decimal(base["holding_coefficient"]) / cost["week_basis"]
        # 260 EOH unit-weeks, 2 customer-BO unit-weeks, 2 orders/10EA, 1 lost Forecast EA.
        components = (
            holding * 260,
            price * Decimal(base["backorder_coefficient"]) * 2,
            Decimal(base["fixed_order_cost"]) * 2,
            price * Decimal(base["variable_order_coefficient"]) * 10,
            price * Decimal(base["forecast_shortage_price_multiplier"]),
        )
        assert holding != holding.quantize(Decimal("0.01"))
        assert tuple(component.quantize(Decimal("0.01")) for component in components) == tuple(
            map(Decimal, ("250", "500", "50", "2500", "250"))
        )
        assert sum(components).quantize(Decimal("0.01")) == Decimal("3550")
    assert cost["forecast_shortage_carried_as_customer_backorder"] is False
    assert cost["tail_cost_or_terminal_pipeline_value_added_to_primary_cost"] is False
    assert cost["round_before_ranking"] is False


def hand_risk_example(fills, end_bos=None):
    """Hand example oracle only. No production reducer, Artifact or PSI is implemented here."""
    rule = protocol()["risk"]
    if "FAILED" in fills:
        return "TECHNICAL_FAILURE", None, None
    end_bos = end_bos or [0] * len(fills)
    if any(fill is None and bo for fill, bo in zip(fills, end_bos, strict=True)):
        return "UNCLEARED_CUSTOMER_BACKORDER", None, None
    paths = [
        (Decimal(fill), bo) for fill, bo in zip(fills, end_bos, strict=True) if fill is not None
    ]
    if len(paths) < rule["minimum_demand_path_count"]["HOLDOUT"]:
        return "INSUFFICIENT_DEMAND_PATHS", None, None
    success = sum(fill >= Decimal(rule["path_minimum_fill_rate"]) and bo == 0 for fill, bo in paths)
    ratio = Decimal(success) / len(paths)
    count = int(
        (Decimal(rule["tail_fraction"]) * len(paths)).to_integral_value(rounding=ROUND_CEILING)
    )
    worst = sorted((1 - fill for fill, _ in paths), reverse=True)[:count]
    tail_loss = sum(worst) / count
    eligible = ratio >= Decimal(
        rule["minimum_empirical_path_success_ratio"]
    ) and tail_loss <= Decimal(rule["maximum_empirical_tail_loss"])
    return "ELIGIBLE" if eligible else "RISK_NOT_MET", ratio, tail_loss


@pytest.mark.parametrize(
    "fills,status,ratio,loss",
    [
        (["0.99"] * 15 + ["0.94"], "ELIGIBLE", "0.9375", "0.035"),
        (["0.99"] * 15 + ["0"], "RISK_NOT_MET", "0.9375", "0.505"),
        (["0.95"] * 16, "ELIGIBLE", "1", "0.05"),
        (["0.949999"] * 16, "RISK_NOT_MET", "0", "0.050001"),
        ([None] * 4 + ["0.99"] * 12, "ELIGIBLE", "1", "0.01"),
        ([None] * 5 + ["0.99"] * 11, "INSUFFICIENT_DEMAND_PATHS", None, None),
        ([None] * 16, "INSUFFICIENT_DEMAND_PATHS", None, None),
        (["0.99"] * 15 + ["FAILED"], "TECHNICAL_FAILURE", None, None),
    ],
)
def test_risk_examples_do_not_hide_single_catastrophe_no_demand_or_failure(
    fills, status, ratio, loss
):
    assert hand_risk_example(fills) == (
        status,
        Decimal(ratio) if ratio is not None else None,
        Decimal(loss) if loss is not None else None,
    )


def test_customer_backorder_is_not_ignored_even_with_high_fill_or_no_current_demand():
    assert hand_risk_example(["1"] * 16, [1, 1] + [0] * 14)[0] == "RISK_NOT_MET"
    assert (
        hand_risk_example([None] + ["1"] * 15, [1] + [0] * 15)[0] == "UNCLEARED_CUSTOMER_BACKORDER"
    )


def test_aggregate_selection_and_final_evaluation_are_not_the_current_single_path_selector():
    value = protocol()
    selection = value["selection"]
    assert selection["arms"] == ["NOMINAL_FORECAST", "MULTIPATH_RISK"]
    assert selection["last_tie_rule"] == "SOURCE_INDEPENDENT_NUMERIC_SEMANTIC_POLICY_KEY"
    assert selection["existing_production_selection_rule_changed"] is False
    for flag in (
        "reselect_using_validation",
        "reselect_using_holdout_or_stress",
        "silent_nominal_or_legacy_fallback_allowed",
    ):
        assert selection[flag] is False
    assert selection["strategy_action_future_prefix_invariance_required"] is True
    assert value["generation"]["generate_holdout_after_selection_locks"] is True
    assert value["generation"]["future_truth_model_or_pattern_label_visible_to_selector"] is False
    assert value["risk"]["statistical_or_operational_reliability_guarantee"] is False


def test_full_workload_and_chunk_coverage_are_explicit_without_cost_profile_resimulation():
    value = protocol()
    calls = sum(
        (
            1
            + len(window["selection_seeds"])
            + len(window["validation_seeds"])
            + len(window["holdout_seeds"])
        )
        * len(value["items"])
        for window in value["windows"]
    )
    execution = value["execution"]
    assert calls == execution["primary_wide_psi_banks"] == 198
    assert (
        len(value["windows"])
        * len(value["diagnostics"]["stress_applies_to_items"])
        * len(value["diagnostics"]["stress_scenarios"])
        == execution["diagnostic_stress_wide_psi_banks"]
        == 16
    )
    assert execution["max_candidates_per_evidence_leaf"] == 16
    assert execution["max_artifact_bytes"] == 128000000
    assert execution["max_bytes_per_local_experiment"] == 20000000000
    assert execution["missing_duplicate_or_changed_candidate_in_manifest"] == "REJECT"
    assert execution["cache_savings_presented_as_method_speedup"] is False
    assert execution["workers"] == 1
    assert execution["full_run_size_estimate_must_fit_budget_before_official_generation"] is True


def test_preservation_and_previous_holdout_are_still_pinned():
    value = protocol()
    assert all(flag is False for flag in value["preservation"].values())
    previous = json.loads(
        (ROOT / "docs/architecture/data/catalog-holdout-comparison-v1.json").read_text()
    )
    evidence = json.loads(
        (ROOT / "docs/architecture/evidence/catalog-holdout-comparison-20261019.json").read_text()
    )
    assert digest(previous) == value["previous_experiment"]["protocol_hash"]
    assert (
        evidence["result"]["semantic_content_hash"]
        == value["previous_experiment"]["result_semantic_hash"]
    )

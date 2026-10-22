"""Pinned same-input Catalog comparison. Planning locks precede all realized evaluation."""

import copy
from datetime import date, timedelta
from decimal import Decimal, localcontext
from statistics import median
from time import perf_counter_ns

from dsio_inventory_engine.classify_inventory.application import build_snapshot
from dsio_inventory_engine.classify_inventory.candidate_catalog import CandidateCatalogProducer
from dsio_inventory_engine.classify_inventory.legacy_features import feature_input_binding
from dsio_inventory_engine.classify_inventory.legacy_rules import RULES_PROFILE_HASH
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.values import (
    digest,
    quantity_text,
    require,
)
from dsio_inventory_engine.recommend_replenishment.application.catalog_search import (
    RunCatalogPolicySearchUseCase,
)
from dsio_inventory_engine.recommend_replenishment.application.catalog_selection import (
    RunCatalogPolicySelectionUseCase,
)
from dsio_inventory_engine.recommend_replenishment.application.policy_search import NUMBERS
from dsio_inventory_engine.recommend_replenishment.application.policy_selection import ranking_key
from .policy_segmentation import _validate_case

OFFICIAL_PROTOCOL_HASH = "693f51040fed2a91b08a81979a5ceb0433b74c9e2a23651b9d536ce35a70f8fb"
MODES = ("ABC_XYZ_SHARED_BUDGET", "LEGACY_STYLE", "WIDE_REFERENCE")
PARAMETERS = ("policy_kind", "rmos", "emos", "target_service_level", "review_cycle_weeks")


def policy_key(candidate):
    return tuple(candidate[key] for key in PARAMETERS)


def _eligible(child):
    return (
        child["status"] == "SUCCEEDED"
        and child["metrics"]["policy_selection_eligible"]
        and child["metrics"]["total_cost"] is not None
    )


def _numeric_rank(child):
    metric = child["metrics"]
    return ranking_key(
        {
            **{
                key: metric[key] for key in ("total_cost", "average_on_hand_eoh_qty", "order_count")
            },
            "candidate_id": child["candidate"]["candidate_id"],
        },
        candidate_prefix="OCCP-",
    )[:3]


def good_policies(children):
    eligible = [child for child in children if _eligible(child)]
    if not eligible:
        return set(), None
    best = min(map(_numeric_rank, eligible))
    good = {policy_key(child["candidate"]) for child in eligible if _numeric_rank(child) == best}
    return good, best[0]


def validate_protocol(protocol, *, expected_protocol_hash):
    require(digest(protocol) == expected_protocol_hash, "CATALOG_COMPARISON_PROTOCOL_HASH")
    require(
        protocol["contract_id"] == "inventory-catalog-comparison-protocol"
        and protocol["contract_version"] == "1.0.0"
        and protocol["source_type"] == "SYNTHETIC_DEVELOPMENT_HOLDOUT"
        and tuple(protocol["candidate_modes"]) == MODES
        and protocol["rules_profile_hash"] == RULES_PROFILE_HASH
        and protocol["candidate_counts"] == dict(zip(MODES, (32, 32, 88), strict=True))
        and protocol["minimum_fill_rate"] == "0.95"
        and protocol["ending_backorder_qty_required"] == "0"
        and protocol["mathematical_lookback_weeks"] == 26
        and protocol["classification_lookback_weeks"] == 52,
        "CATALOG_COMPARISON_PROTOCOL_INVALID",
    )
    require(
        all(
            protocol[key] is False
            for key in (
                "holdout_reselection_allowed",
                "parameter_tuning_allowed",
                "database_writes",
                "automatic_publish_allowed",
                "automatic_order_allowed",
                "operational_policy_approved",
            )
        ),
        "CATALOG_COMPARISON_BOUNDARY",
    )
    require(
        type(protocol["timing_repetitions"]) is int
        and 1 <= protocol["timing_repetitions"] <= 3
        and type(protocol["horizon_weeks"]) is int
        and 1 <= protocol["horizon_weeks"] <= 13
        and 1 <= len(protocol["items"]) <= 8
        and 1 <= len(protocol["periods"]) <= 2,
        "CATALOG_COMPARISON_WORKLOAD",
    )
    require(protocol["cost_assumptions"] is not None, "CATALOG_COMPARISON_COST_REQUIRED")
    ranges = []
    for period in protocol["periods"]:
        start = date.fromisoformat(period["start_date"])
        end = start + timedelta(weeks=protocol["horizon_weeks"], days=-1)
        require(
            start.weekday() == 0 and type(period["seed"]) is int,
            "CATALOG_COMPARISON_PERIOD_INVALID",
        )
        for previous in protocol["previously_used_periods"]:
            require(
                end < date.fromisoformat(previous["start_date"])
                or start > date.fromisoformat(previous["end_date"]),
                "CATALOG_COMPARISON_REUSED_HOLDOUT",
            )
        require(
            all(end < lo or start > hi for lo, hi in ranges),
            "CATALOG_COMPARISON_OVERLAPPING_PERIODS",
        )
        ranges.append((start, end))


def _validate_inputs(cases, protocol):
    require(len(cases) == len(protocol["periods"]), "CATALOG_COMPARISON_CASES")
    require(len({case.case_id for case, _ in cases}) == len(cases), "CATALOG_COMPARISON_CASES")
    periods = {row["case_id"]: row for row in protocol["periods"]}
    expected_items = {item["item_id"] for item in protocol["items"]}
    for case, feature in cases:
        _validate_case(case)
        require(case.split == "HOLDOUT" and case.case_id in periods, "CATALOG_COMPARISON_CASES")
        canonical = case.planning_request["recommendation"]["canonical_input"]
        require(
            canonical["context"]["plan_start_date"] == periods[case.case_id]["start_date"]
            and len(canonical["snapshots"]["calendar"]["rows"]) == protocol["horizon_weeks"]
            and all(canonical["context"][key] == val for key, val in protocol["scope"].items())
            and {row["item_id"] for row in canonical["snapshots"]["master"]["rows"]}
            == expected_items
            and all(row["uom"] == "EA" for row in canonical["snapshots"]["master"]["rows"]),
            "CATALOG_COMPARISON_INPUT_SCOPE",
        )
        search = case.classification_inputs.config_values["policy_search"]
        require(
            search["cost_assumptions"] == protocol["cost_assumptions"]
            and search["minimum_fill_rate"] == protocol["minimum_fill_rate"],
            "CATALOG_COMPARISON_COST_OR_SERVICE_DRIFT",
        )
        require(feature["item_exceptions"] == [], "CATALOG_COMPARISON_OVERRIDE_UNSUPPORTED")


def _records(case, sources, selections, realized):
    wide_by_item = {}
    replay_by_item = {}
    replay_bank = {}
    for child in sources["WIDE_REFERENCE"]["candidate_evaluations"]:
        wide_by_item.setdefault(child["candidate"]["item_id"], []).append(child)
    for child in realized["candidate_evaluations"]:
        item = child["candidate"]["item_id"]
        replay_by_item.setdefault(item, []).append(child)
        key = (item, policy_key(child["candidate"]))
        require(key not in replay_bank, "CATALOG_COMPARISON_SEMANTIC_DUPLICATE")
        replay_bank[key] = child
    baselines = {row["item_id"]: row["metrics"] for row in realized["baseline_items"]}
    output = []
    for mode in MODES:
        bank = {
            (row["candidate"]["item_id"], policy_key(row["candidate"])): row
            for row in sources[mode]["candidate_evaluations"]
        }
        for selected in selections[mode]["item_selections"]:
            item = selected["item_id"]
            good, planning_best_cost = good_policies(wide_by_item.get(item, []))
            actual_good, hindsight_cost = good_policies(replay_by_item.get(item, []))
            available = {key for candidate_item, key in bank if candidate_item == item}
            chosen = selected["selected_policy"]
            policy = policy_key(chosen) if chosen else None
            actual = replay_bank.get((item, policy)) if chosen else None
            require(
                chosen is None or actual is not None and actual["status"] == "SUCCEEDED",
                "CATALOG_COMPARISON_LOCKED_POLICY_NOT_REPLAYABLE",
            )
            metric = actual["metrics"] if actual else baselines[item]
            feasible = bool(actual and metric["policy_selection_eligible"])
            regret = (
                Decimal(metric["total_cost"]) - hindsight_cost
                if feasible and hindsight_cost is not None
                else None
            )
            require(regret is None or regret >= 0, "CATALOG_COMPARISON_REFERENCE_DOMINANCE")
            plan_cost = selected["selected_score"]["total_cost"] if chosen else None
            output.append(
                {
                    "case_id": case.case_id,
                    "item_id": item,
                    "mode": mode,
                    "selected": chosen is not None,
                    "selection_reason_codes": selected["reason_codes"],
                    "search_exclusion_reason": selected["search_exclusion_reason"],
                    "source_review_required": selected["source_review_required"],
                    "locked_policy": chosen,
                    "planning_selected_score": selected["selected_score"],
                    "planning_candidate_count": len(available),
                    "realized_metrics": metric,
                    "realized_service_feasible": feasible,
                    "realized_candidate_hash": actual["content_hash"] if actual else None,
                    "planning_good_candidate_count": len(good),
                    "planning_good_candidate_retained": bool(good & available) if good else None,
                    "planning_good_candidate_recall": quantity_text(
                        Decimal(len(good & available)) / len(good)
                    )
                    if good
                    else None,
                    "planning_cost_regret": quantity_text(Decimal(plan_cost) - planning_best_cost)
                    if plan_cost is not None and planning_best_cost is not None
                    else None,
                    "realized_good_candidate_retained": bool(actual_good & available)
                    if actual_good
                    else None,
                    "realized_feasible_opportunity_missed": bool(actual_good and not feasible),
                    "realized_cost_regret": quantity_text(regret) if regret is not None else None,
                    "hindsight_reference_cost": quantity_text(hindsight_cost)
                    if hindsight_cost is not None
                    else None,
                }
            )
    return output


def _assert_same_inputs(sources):
    reference = sources["WIDE_REFERENCE"]
    for mode in MODES:
        source = sources[mode]
        require(
            all(
                source[key] == reference[key]
                for key in (
                    "context",
                    "canonical_input_hash",
                    "prepared_input_hash",
                    "mathematical_input_hash",
                    "config_hash",
                    "classification_snapshot_id",
                    "classification_content_hash",
                    "baseline_items",
                    "search_config_hash",
                    "search_config",
                    "terminal_condition",
                )
            ),
            "CATALOG_COMPARISON_INPUT_DRIFT",
        )
        wide = {
            (row["candidate"]["item_id"], policy_key(row["candidate"])): row
            for row in reference["candidate_evaluations"]
        }
        for child in source["candidate_evaluations"]:
            peer = wide.get((child["candidate"]["item_id"], policy_key(child["candidate"])))
            require(
                peer is not None
                and peer["status"] == child["status"]
                and peer["failure_reason"] == child["failure_reason"]
                and peer["metrics"] == child["metrics"]
                and peer["psi_rows"] == child["psi_rows"],
                "CATALOG_COMPARISON_SHARED_POLICY_DRIFT",
            )


def summarize_comparison(records):
    with localcontext(NUMBERS):
        summary = {}
        for mode in MODES:
            rows = [row for row in records if row["mode"] == mode]
            demand = sum(Decimal(row["realized_metrics"]["current_demand_qty"]) for row in rows)
            fulfilled = sum(
                Decimal(row["realized_metrics"]["current_demand_fulfilled_qty"]) for row in rows
            )
            comparable = [
                row for row in rows if row["planning_good_candidate_retained"] is not None
            ]
            hindsight = [row for row in rows if row["realized_good_candidate_retained"] is not None]
            summary[mode] = {
                "item_period_count": len(rows),
                "selected_count": sum(row["selected"] for row in rows),
                "realized_service_feasible_count": sum(
                    row["realized_service_feasible"] for row in rows
                ),
                "demand_weighted_realized_fill_rate": quantity_text(fulfilled / demand)
                if demand
                else None,
                "descriptive_total_cost_including_unselected_baseline": quantity_text(
                    sum(Decimal(row["realized_metrics"]["total_cost"]) for row in rows)
                ),
                "sum_item_average_on_hand_eoh_qty": quantity_text(
                    sum(Decimal(row["realized_metrics"]["average_on_hand_eoh_qty"]) for row in rows)
                ),
                "ending_backorder_qty": quantity_text(
                    sum(Decimal(row["realized_metrics"]["ending_backorder_qty"]) for row in rows)
                ),
                "planning_good_candidate_comparable_count": len(comparable),
                "planning_good_candidate_missed_count": sum(
                    not row["planning_good_candidate_retained"] for row in comparable
                ),
                "mean_planning_good_candidate_recall": quantity_text(
                    sum(Decimal(row["planning_good_candidate_recall"]) for row in comparable)
                    / len(comparable)
                )
                if comparable
                else None,
                "realized_reference_comparable_count": len(hindsight),
                "realized_range_miss_count": sum(
                    not row["realized_good_candidate_retained"] for row in hindsight
                ),
                "realized_feasible_opportunity_missed_count": sum(
                    row["realized_feasible_opportunity_missed"] for row in rows
                ),
            }
        by_key = {(row["case_id"], row["item_id"], row["mode"]): row for row in records}
        pairs = {}
        for challenger in MODES[1:]:
            shared = [
                (left, by_key[(case, item, challenger)])
                for (case, item, mode), left in by_key.items()
                if mode == MODES[0]
            ]
            eligible = [
                (left, right)
                for left, right in shared
                if left["realized_service_feasible"] and right["realized_service_feasible"]
            ]
            left_cost = sum(Decimal(left["realized_metrics"]["total_cost"]) for left, _ in eligible)
            right_cost = sum(
                Decimal(right["realized_metrics"]["total_cost"]) for _, right in eligible
            )
            pairs[challenger + "_VS_ABC"] = {
                "jointly_feasible_pair_count": len(eligible),
                "excluded_pair_count": len(shared) - len(eligible),
                "abc_total_cost": quantity_text(left_cost) if eligible else None,
                "challenger_total_cost": quantity_text(right_cost) if eligible else None,
                "cost_change_ratio": quantity_text((right_cost - left_cost) / left_cost)
                if eligible and left_cost
                else None,
                "abc_sum_average_inventory": quantity_text(
                    sum(
                        Decimal(left["realized_metrics"]["average_on_hand_eoh_qty"])
                        for left, _ in eligible
                    )
                )
                if eligible
                else None,
                "challenger_sum_average_inventory": quantity_text(
                    sum(
                        Decimal(right["realized_metrics"]["average_on_hand_eoh_qty"])
                        for _, right in eligible
                    )
                )
                if eligible
                else None,
            }
        return {"modes": summary, "paired_service_feasible_comparisons": pairs}


def run_catalog_comparison(
    cases,
    protocol,
    *,
    expected_protocol_hash,
    feature_producer,
    classifier,
    artifact_sink=None,
    progress=None,
):
    """No model fitting or holdout reselection. Timed runs are serial, rotated and hash-checked."""
    with localcontext(NUMBERS):
        validate_protocol(protocol, expected_protocol_hash=expected_protocol_hash)
        _validate_inputs(cases, protocol)
        return _run(
            cases,
            protocol,
            expected_protocol_hash,
            feature_producer,
            classifier,
            artifact_sink,
            progress or (lambda message: None),
        )


def _run(cases, protocol, protocol_hash, feature_producer, classifier, sink, progress):
    cases = copy.deepcopy(cases)
    inputs_before = digest(
        [
            {
                "planning": case.planning_request,
                "replay": case.replay_request,
                "feature": feature,
                "config": case.classification_inputs.config_values,
            }
            for case, feature in cases
        ]
    )
    producer = CandidateCatalogProducer(classifier)
    deployment = DeploymentScope(protocol["scope"]["company_cd"], "DEVELOPMENT")
    search = RunCatalogPolicySearchUseCase(deployment, producer)
    selector = RunCatalogPolicySelectionUseCase(deployment, producer)
    records, case_bindings, timings = [], [], []
    for case_index, (case, feature) in enumerate(cases):
        progress({"phase": "COMMON_PREPARATION", "case_id": case.case_id})
        start = perf_counter_ns()
        snapshot = build_snapshot(case.classification_inputs)
        detailed = classifier.classify(
            feature_producer.produce(feature, expected_binding=feature_input_binding(feature))
        )
        common_ns = perf_counter_ns() - start
        plan = MathematicalPolicyRequest.from_dict(case.planning_request)
        replay_request = MathematicalPolicyRequest.from_dict(case.replay_request)
        config = case.classification_inputs.config_values
        sources, selections, catalogs, samples, hashes = {}, {}, {}, {}, {}
        for repeat in range(protocol["timing_repetitions"]):
            shift = (case_index + repeat) % len(MODES)
            for mode in MODES[shift:] + MODES[:shift]:
                progress(
                    {
                        "phase": "TIMED_PLANNING",
                        "case_id": case.case_id,
                        "mode": mode,
                        "repeat": repeat + 1,
                    }
                )
                before = perf_counter_ns()
                catalog = producer.produce(
                    detailed, mode=mode, expected_result_hash=detailed.content_hash
                )
                bindings = {
                    "catalog": catalog,
                    "expected_catalog_hash": catalog.content_hash,
                    "config_values": config,
                    "classification_snapshot": snapshot,
                }
                catalog_ns = perf_counter_ns() - before
                before = perf_counter_ns()
                source = search.execute(plan, **bindings)
                search_ns = perf_counter_ns() - before
                before = perf_counter_ns()
                selected = selector.execute(
                    source, plan, expected_source_hash=source["content_hash"], **bindings
                )
                selection_ns = perf_counter_ns() - before
                identity = (catalog.content_hash, source["content_hash"], selected["content_hash"])
                require(
                    mode not in hashes or hashes[mode] == identity,
                    "CATALOG_COMPARISON_NONDETERMINISTIC",
                )
                hashes[mode] = identity
                samples.setdefault(mode, []).append(
                    {
                        "catalog_ns": catalog_ns,
                        "search_ns": search_ns,
                        "selection_with_replay_ns": selection_ns,
                        "total_ns": catalog_ns + search_ns + selection_ns,
                    }
                )
                if repeat == 0:
                    sources[mode], selections[mode], catalogs[mode] = source, selected, catalog
        _assert_same_inputs(sources)
        # This is the choice barrier: no realized result is computed before all plan policies are locked.
        locks = {mode: selections[mode]["content_hash"] for mode in MODES}
        progress(
            {"phase": "CHOICES_LOCKED_REALIZED_REPLAY", "case_id": case.case_id, "locks": locks}
        )
        before = perf_counter_ns()
        wide_catalog = catalogs["WIDE_REFERENCE"]
        replay_bindings = {
            "catalog": wide_catalog,
            "expected_catalog_hash": wide_catalog.content_hash,
            "config_values": config,
            "classification_snapshot": snapshot,
        }
        realized = search.execute(replay_request, **replay_bindings)
        selector.verify_source(
            realized,
            replay_request,
            expected_source_hash=realized["content_hash"],
            **replay_bindings,
        )
        realized_ns = perf_counter_ns() - before
        records.extend(_records(case, sources, selections, realized))
        require(
            locks == {mode: selections[mode]["content_hash"] for mode in MODES},
            "CATALOG_COMPARISON_RESELECTION",
        )
        case_bindings.append(
            {
                "case_id": case.case_id,
                "canonical_input_hash": sources["WIDE_REFERENCE"]["canonical_input_hash"],
                "prepared_input_hash": sources["WIDE_REFERENCE"]["prepared_input_hash"],
                "config_hash": snapshot["config_hash"],
                "classification_hash": snapshot["content_hash"],
                "feature_hash": detailed.to_dict()["feature_snapshot"]["content_hash"],
                "gate_hash": digest(
                    [
                        {
                            key: row.get(key)
                            for key in (
                                "item_id",
                                "effective_policy_hash",
                                "effective_order_action",
                                "recommendation_calculation_allowed",
                                "automatic_publish_allowed",
                                "automatic_order_allowed",
                            )
                        }
                        for row in snapshot["items"]
                    ]
                ),
                "catalog_source_selection_hashes": {mode: list(hashes[mode]) for mode in MODES},
                "realized_wide_hash": realized["content_hash"],
            }
        )
        before = perf_counter_ns()
        if sink is not None:
            progress({"phase": "ARTIFACT_PERSISTENCE", "case_id": case.case_id})
            for mode in MODES:
                sink(
                    case.case_id,
                    mode,
                    sources[mode],
                    selections[mode],
                    selector,
                    plan,
                    {
                        "catalog": catalogs[mode],
                        "expected_catalog_hash": catalogs[mode].content_hash,
                        "config_values": config,
                        "classification_snapshot": snapshot,
                    },
                )
            sink(
                case.case_id,
                "REALIZED_WIDE_NO_RESELECTION",
                realized,
                None,
                selector,
                replay_request,
                replay_bindings,
            )
        persistence_ns = perf_counter_ns() - before if sink is not None else None
        timings.append(
            {
                "case_id": case.case_id,
                "common_preparation_ns": common_ns,
                "realized_wide_replay_ns": realized_ns,
                "artifact_persistence_ns": persistence_ns,
                "samples": samples,
                "median_total_ns": {
                    mode: int(median(row["total_ns"] for row in samples[mode])) for mode in MODES
                },
            }
        )
    require(
        inputs_before
        == digest(
            [
                {
                    "planning": case.planning_request,
                    "replay": case.replay_request,
                    "feature": feature,
                    "config": case.classification_inputs.config_values,
                }
                for case, feature in cases
            ]
        ),
        "CATALOG_COMPARISON_INPUT_MUTATION",
    )
    body = {
        "contract_id": "inventory-catalog-holdout-comparison-result",
        "contract_version": "1.0.0",
        "protocol_hash": protocol_hash,
        "source_type": protocol["source_type"],
        "case_bindings": case_bindings,
        "records": records,
        "summary": summarize_comparison(records),
        "database_writes": False,
        "automatic_publish_allowed": False,
        "automatic_order_allowed": False,
        "operational_policy_approved": False,
        "holdout_reselection_performed": False,
        "parameter_tuning_performed": False,
    }
    return {**body, "semantic_content_hash": digest(body), "timing": timings}

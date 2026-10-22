"""Bounded synthetic Offline experiment. Never a Platform producer or Claim adapter.

All planning arms execute the existing search and selector independently. Only after
their choices are fixed is a shared realized-demand replay bank built for evaluation.
Replay timing is reported separately; it is not a candidate-search speedup.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, localcontext
from time import perf_counter_ns

from dsio_inventory_engine.classify_inventory.application import (
    ClassificationInputs,
    build_snapshot,
    config_hash,
)
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.policy_search import candidate_policies
from dsio_inventory_engine.inventory_contracts.values import digest, require
from dsio_inventory_engine.recommend_replenishment.application.policy_search import (
    NUMBERS,
    RunPolicySearchUseCase,
)
from dsio_inventory_engine.recommend_replenishment.application.policy_selection import (
    RunPolicySelectionUseCase,
)
from dsio_inventory_engine.training.policy_segmentation import (
    fit_profile_tree,
    number,
    predict_profile,
    routing_loss,
)

ARMS = ("ABC_XYZ", "ML_PROFILE", "WIDE_REFERENCE")
SEGMENTS = ("AX", "AY", "AZ", "BX", "BY", "BZ", "CX", "CY", "CZ")
PARAMETERS = ("policy_kind", "rmos", "emos", "target_service_level", "review_cycle_weeks")
GATES = (
    "classification_status",
    "effective_order_action",
    "recommendation_calculation_allowed",
    "automatic_publish_allowed",
    "automatic_order_allowed",
    "policy_gate_reason_codes",
    "effective_axis_projection",
)


@dataclass(frozen=True)
class ExperimentCase:
    case_id: str
    split: str
    planning_request: dict
    replay_request: dict
    classification_inputs: ClassificationInputs


def policy_key(candidate: dict) -> str:
    """Search-range provenance changes Candidate IDs, but not the policy parameters."""
    return digest({key: candidate[key] for key in PARAMETERS})


def _canonical(request):
    return request["recommendation"]["canonical_input"]


def _validate_case(case: ExperimentCase) -> None:
    require(case.split in {"TRAIN", "VALIDATION", "HOLDOUT"}, "SEGMENTATION_SPLIT")
    for raw in (case.planning_request, case.replay_request):
        MathematicalPolicyRequest.from_dict(raw)
        require(
            raw["recommendation"]["execution"]["execution_mode"] == "LOCAL_SHADOW",
            "SEGMENTATION_OFFLINE_ONLY",
        )
        require(
            _canonical(raw)["snapshots"]["inventory"]["metadata"]["position_source_type"]
            == "SYNTHETIC_BOH",
            "SEGMENTATION_SYNTHETIC_ONLY",
        )
    planning, replay = _canonical(case.planning_request), _canonical(case.replay_request)
    require(planning["context"] == replay["context"], "SEGMENTATION_REPLAY_CONTEXT")
    require(planning["quantity_rules"] == replay["quantity_rules"], "SEGMENTATION_REPLAY_UOM")
    left_execution = copy.deepcopy(case.planning_request["recommendation"]["execution"])
    right_execution = copy.deepcopy(case.replay_request["recommendation"]["execution"])
    for execution in (left_execution, right_execution):
        execution.pop("canonical_input_hash")
        execution.pop("strategy_input_binding")
    require(left_execution == right_execution, "SEGMENTATION_REPLAY_EXECUTION_GUARD")
    for key in planning["snapshots"]:
        if key != "forecast":
            require(
                planning["snapshots"][key] == replay["snapshots"][key],
                "SEGMENTATION_REPLAY_IMMUTABLE_INPUT",
            )
    left_forecast, right_forecast = (
        copy.deepcopy(planning["snapshots"]["forecast"]),
        copy.deepcopy(replay["snapshots"]["forecast"]),
    )
    for forecast in (left_forecast, right_forecast):
        forecast.pop("content_hash")
        forecast["rows"] = sorted(
            ({k: v for k, v in row.items() if k != "forecast_qty"} for row in forecast["rows"]),
            key=lambda row: (row["item_id"], row["yyyyww"]),
        )
    require(left_forecast == right_forecast, "SEGMENTATION_REPLAY_DEMAND_COORDINATES")
    left, right = (
        copy.deepcopy(case.planning_request["policy_input"]),
        copy.deepcopy(case.replay_request["policy_input"]),
    )
    for data in (left, right):
        data.pop("content_hash")
        data["context"].pop("canonical_input_hash")
    require(left == right, "SEGMENTATION_REPLAY_HISTORY_OR_POLICY")
    end = planning["context"]["plan_end_date"]
    start = planning["context"]["plan_start_date"]
    require(date.fromisoformat(start) <= date.fromisoformat(end), "SEGMENTATION_CASE_PERIOD")
    for row in case.planning_request["policy_input"]["calendar"]:
        require(row["end_date"] < start, "SEGMENTATION_FEATURE_LOOKAHEAD")
    values = case.classification_inputs.config_values
    require(
        case.classification_inputs.config_hash == config_hash(values),
        "SEGMENTATION_SOURCE_CONFIG_HASH",
    )
    require(values["policy_search"]["cost_assumptions"] is not None, "SEGMENTATION_COST_REQUIRED")
    require(not values["policy_search"]["item_overrides"], "SEGMENTATION_EXISTING_OVERRIDE")


def _snapshot(case, values):
    return build_snapshot(
        replace(case.classification_inputs, config_values=values, config_hash=config_hash(values))
    )


def _run(case, values, *, replay=False):
    snapshot = _snapshot(case, values)
    raw = case.replay_request if replay else case.planning_request
    deployment = DeploymentScope("DSE", "DEVELOPMENT")
    result = RunPolicySearchUseCase(deployment).execute(
        MathematicalPolicyRequest.from_dict(raw),
        config_values=values,
        classification_snapshot=snapshot,
    )
    selection = RunPolicySelectionUseCase(deployment).execute(result)
    require(
        not result["automatic_order_allowed"]
        and not result["automatic_publish_allowed"]
        and not result["database_writes"]
        and not selection["operational_policy_approved"],
        "SEGMENTATION_OPERATIONAL_SIDE_EFFECT",
    )
    return snapshot, result, selection


def _broad_values(case, profiles):
    values = copy.deepcopy(case.classification_inputs.config_values)
    fields = (
        "policy_kinds",
        "rmos_values",
        "emos_values",
        "service_level_values",
        "review_cycle_weeks",
    )
    combined = {key: sorted({v for p in profiles.values() for v in p[key]}) for key in fields}
    values["policy_search"]["group_profiles"] = [
        {"segment_key": segment, **copy.deepcopy(combined)} for segment in SEGMENTS
    ]
    # Reject libraries whose Cartesian union would silently introduce extra policies.
    search = values["policy_search"]
    expected = set()
    for profile in profiles.values():
        single = {**search, "group_profiles": [{"segment_key": "AX", **profile}]}
        expected.update(
            policy_key(c) for c in candidate_policies(single, item_id="P", segment_key="AX")
        )
    actual = {policy_key(c) for c in candidate_policies(search, item_id="P", segment_key="AX")}
    require(actual == expected and len(actual) <= 32, "SEGMENTATION_REFERENCE_LIBRARY")
    return values


def _features(case, snapshot):
    raw = case.planning_request
    canonical = _canonical(raw)
    policies = {r["item_id"]: r for r in canonical["snapshots"]["policies"]["rows"]}
    positions = {r["item_id"]: r for r in canonical["snapshots"]["inventory"]["rows"]}
    cost = case.classification_inputs.config_values["policy_search"]["cost_assumptions"]
    output = {}
    with localcontext(NUMBERS):
        for item in snapshot["items"]:
            item_id = item["item_id"]
            history = [
                number(r["demand_qty"])
                for r in raw["policy_input"]["history"]
                if r["item_id"] == item_id
            ]
            require(bool(history), "SEGMENTATION_MISSING_HISTORY")
            mean = sum(history) / len(history)
            if mean == 0 or item["classification_status"] != "CLASSIFIED":
                continue
            variance = sum((v - mean) ** 2 for v in history) / len(history)
            features = {
                "mean_weekly_demand": str(mean),
                "cv2": str(variance / mean**2),
                "positive_week_ratio": str(Decimal(sum(v > 0 for v in history)) / len(history)),
                "lead_time_days": str(policies[item_id]["lead_time_days"]),
                "moq": policies[item_id]["moq"],
                "initial_cover_weeks": str(number(positions[item_id]["available_qty"]) / mean),
                "ordering_to_holding_ratio": str(
                    number(cost["fixed_order_cost"])
                    / max(Decimal("0.000001"), number(cost["holding_cost_per_unit_week"]))
                ),
                **{f"abc_{code}": str(int(item["abc_class"] == code)) for code in "ABC"},
                **{f"xyz_{code}": str(int(item["xyz_class"] == code)) for code in "XYZ"},
            }
            output[item_id] = features
    return output


def _gates(snapshot):
    return {row["item_id"]: {key: row[key] for key in GATES} for row in snapshot["items"]}


def _bank(result):
    return {
        (r["candidate"]["item_id"], policy_key(r["candidate"])): r
        for r in result["candidate_evaluations"]
    }


def _winner(rows, tie_ids=None):
    eligible = [
        row
        for row in rows
        if row["status"] == "SUCCEEDED"
        and row["metrics"]["policy_selection_eligible"]
        and row["metrics"]["total_cost"] is not None
    ]
    return min(
        eligible,
        key=lambda row: (
            number(row["metrics"]["total_cost"]),
            number(row["metrics"]["average_on_hand_eoh_qty"]),
            row["metrics"]["order_count"],
            (tie_ids or {}).get(policy_key(row["candidate"]), row["candidate"]["candidate_id"]),
        ),
        default=None,
    )


def _training_rows(case, profiles, broad, replay, snapshot):
    replay_bank = _bank(replay)
    features = _features(case, snapshot)
    assignments = {r["item_id"]: r for r in snapshot["items"]}
    rows = []
    excluded = [
        {
            "case_id": case.case_id,
            "item_id": item_id,
            "reason_code": "CLASSIFICATION_OR_ZERO_HISTORY_NOT_SUPPORTED",
        }
        for item_id in sorted(assignments)
        if item_id not in features
    ]
    for item_id, item_features in features.items():
        outcomes = {}
        for profile_id, profile in sorted(profiles.items()):
            search = {
                **broad["search_config"],
                "item_overrides": [
                    {
                        "item_id": item_id,
                        "reason": "OFFLINE_ML_SEARCH_RANGE_NOT_POLICY_APPROVAL",
                        **profile,
                    }
                ],
            }
            candidates = candidate_policies(
                search, item_id=item_id, segment_key=assignments[item_id]["segment_key"]
            )
            tie_ids = {policy_key(c): c["candidate_id"] for c in candidates}
            # Select on the archived planning Forecast, not on the later realized path.
            winner = _winner(
                [
                    row
                    for row in broad["candidate_evaluations"]
                    if row["candidate"]["item_id"] == item_id
                    and policy_key(row["candidate"]) in tie_ids
                ],
                tie_ids,
            )
            realized = (
                replay_bank.get((item_id, policy_key(winner["candidate"]))) if winner else None
            )
            feasible = bool(
                realized
                and realized["status"] == "SUCCEEDED"
                and realized["metrics"]["policy_selection_eligible"]
            )
            outcomes[profile_id] = {
                "feasible": feasible,
                "cost": realized["metrics"]["total_cost"] if feasible else None,
            }
        if any(v["feasible"] for v in outcomes.values()):
            rows.append(
                {
                    "row_id": f"{case.case_id}:{item_id}",
                    "case_id": case.case_id,
                    "features": item_features,
                    "profile_outcomes": outcomes,
                    "planning_input_hash": broad["canonical_input_hash"],
                    "replay_input_hash": replay["canonical_input_hash"],
                }
            )
        else:
            excluded.append(
                {
                    "case_id": case.case_id,
                    "item_id": item_id,
                    "reason_code": "NO_FEASIBLE_PROFILE_ON_REALIZED_TRAINING_PATH",
                }
            )
    return rows, excluded


def _arm_values(case, profiles, abc_profiles, arm, model, reference_snapshot):
    values = copy.deepcopy(case.classification_inputs.config_values)
    if arm == "WIDE_REFERENCE":
        return _broad_values(case, profiles), {}
    values["policy_search"]["group_profiles"] = [
        {"segment_key": segment, **copy.deepcopy(profiles[abc_profiles[segment]])}
        for segment in SEGMENTS
    ]
    routes = {}
    if arm == "ML_PROFILE":
        for item, features in _features(case, reference_snapshot).items():
            profile = predict_profile(model, features)
            routes[item] = profile
            values["policy_search"]["item_overrides"].append(
                {
                    "item_id": item,
                    "reason": "OFFLINE_ML_SEARCH_RANGE_NOT_POLICY_APPROVAL",
                    **copy.deepcopy(profiles[profile]),
                }
            )
    return values, routes


def _compare(case, arm, result, selection, replay, reference, routes):
    replay_bank = _bank(replay)
    selected = {r["item_id"]: r for r in selection["item_selections"]}
    baselines = {r["item_id"]: r["metrics"] for r in replay["baseline_items"]}
    output = []
    for item_id in sorted(baselines):
        choice = selected[item_id]
        chosen = choice["selected_policy"] if choice["status"] == "SELECTED" else None
        realized = replay_bank.get((item_id, policy_key(chosen))) if chosen else None
        require(realized is None or realized["status"] == "SUCCEEDED", "SEGMENTATION_REPLAY_FAILED")
        metrics = realized["metrics"] if realized else baselines[item_id]
        reference_rows = [
            r for r in reference["candidate_evaluations"] if r["candidate"]["item_id"] == item_id
        ]
        hindsight_rows = [
            r for r in replay["candidate_evaluations"] if r["candidate"]["item_id"] == item_id
        ]
        oracle = _winner(hindsight_rows)
        best = _winner(reference_rows)
        available = {
            policy_key(r["candidate"])
            for r in result["candidate_evaluations"]
            if r["candidate"]["item_id"] == item_id
        }
        good = {
            policy_key(r["candidate"])
            for r in reference_rows
            if r["status"] == "SUCCEEDED"
            and r["metrics"]["policy_selection_eligible"]
            and best
            and number(r["metrics"]["total_cost"]) == number(best["metrics"]["total_cost"])
        }
        feasible = bool(chosen and metrics["service_feasible"])
        regret = (
            (number(metrics["total_cost"]) - number(oracle["metrics"]["total_cost"]))
            if feasible and oracle
            else None
        )
        require(regret is None or regret >= 0, "SEGMENTATION_REFERENCE_DOMINANCE")
        output.append(
            {
                "case_id": case.case_id,
                "item_id": item_id,
                "arm": arm,
                "profile_id": routes.get(item_id),
                "selected": chosen is not None,
                "selection_reason_codes": choice["reason_codes"],
                "locked_policy": chosen,
                "realized_metrics": metrics,
                "realized_service_feasible": feasible,
                "planning_candidate_count": len(available),
                "best_cost_candidate_retained": bool(good & available) if good else None,
                "best_cost_candidate_recall": str(Decimal(len(good & available)) / len(good))
                if good
                else None,
                "realized_feasible_opportunity_missed": bool(oracle and not feasible),
                "realized_cost_regret": str(regret) if regret is not None else None,
                "hindsight_best_cost": oracle["metrics"]["total_cost"] if oracle else None,
                "replay_candidate_hash": realized["content_hash"] if realized else None,
            }
        )
    return output


def run_experiment(
    cases: list[ExperimentCase],
    profiles: dict,
    abc_profiles: dict,
    *,
    artifact_sink=None,
    timing_repeats: int = 3,
) -> dict:
    """TRAIN fits, VALIDATION selects depth, HOLDOUT is evaluated once with a frozen model."""
    require(3 <= len(cases) <= 12 and 2 <= len(profiles) <= 4, "SEGMENTATION_EXPERIMENT_SIZE")
    require(len({c.case_id for c in cases}) == len(cases), "SEGMENTATION_DUPLICATE_CASE")
    require(set(abc_profiles) == set(SEGMENTS), "SEGMENTATION_ABC_PROFILE_COVERAGE")
    require(set(abc_profiles.values()) <= set(profiles), "SEGMENTATION_ABC_UNKNOWN_PROFILE")
    require(type(timing_repeats) is int and 1 <= timing_repeats <= 5, "SEGMENTATION_TIMING_REPEATS")
    cases = sorted(
        cases, key=lambda c: _canonical(c.planning_request)["context"]["plan_start_date"]
    )
    split_order = {"TRAIN": 0, "VALIDATION": 1, "HOLDOUT": 2}
    previous_end, previous_split = "", -1
    currencies = set()
    for case in cases:
        _validate_case(case)
        context = _canonical(case.planning_request)["context"]
        require(previous_end < context["plan_start_date"], "SEGMENTATION_OVERLAPPING_PERIODS")
        require(split_order[case.split] >= previous_split, "SEGMENTATION_SPLIT_ORDER")
        previous_end, previous_split = context["plan_end_date"], split_order[case.split]
        currencies.add(
            case.classification_inputs.config_values["policy_search"]["cost_assumptions"][
                "currency"
            ]
        )
    require(len(currencies) == 1, "SEGMENTATION_MIXED_CURRENCIES")
    require({c.split for c in cases} == set(split_order), "SEGMENTATION_REQUIRED_SPLITS")
    original_hash = digest([c.planning_request for c in cases] + [c.replay_request for c in cases])
    with localcontext(NUMBERS):
        return _experiment(
            cases, profiles, abc_profiles, original_hash, artifact_sink, timing_repeats
        )


def _experiment(cases, profiles, abc_profiles, original_hash, artifact_sink, timing_repeats):
    started = perf_counter_ns()
    persistence_ns = 0

    def persist(result, selection):
        nonlocal persistence_ns
        if artifact_sink is not None:
            began = perf_counter_ns()
            artifact_sink(result, selection)
            persistence_ns += perf_counter_ns() - began

    training, validation, source_rows, learning_exclusions = [], [], [], []
    for case in cases:
        if case.split == "HOLDOUT":
            continue
        values = _broad_values(case, profiles)
        snapshot, broad, selection = _run(case, values)
        _, replay, replay_selection = _run(case, values, replay=True)
        rows, excluded = _training_rows(case, profiles, broad, replay, snapshot)
        learning_exclusions.extend({**r, "split": case.split} for r in excluded)
        (training if case.split == "TRAIN" else validation).extend(rows)
        source_rows.append(
            {
                "case_id": case.case_id,
                "split": case.split,
                "planning_hash": broad["content_hash"],
                "replay_hash": replay["content_hash"],
                "planning_selection_hash": selection["content_hash"],
                "replay_selection_hash": replay_selection["content_hash"],
            }
        )
        persist(broad, selection)
        persist(replay, replay_selection)
    require(bool(validation), "SEGMENTATION_NO_VALIDATION_ROWS")
    models = [fit_profile_tree(training, max_depth=depth) for depth in (0, 1, 2)]
    scored = [
        (
            routing_loss(
                validation, [predict_profile(model, row["features"]) for row in validation]
            ),
            model["max_depth"],
            model,
        )
        for model in models
    ]
    _, _, model = min(scored, key=lambda entry: (entry[0], entry[1]))
    model_hash = model["content_hash"]
    training_ns = perf_counter_ns() - started - persistence_ns
    rows, timings, receipts = [], [], []
    for index, case in enumerate(c for c in cases if c.split == "HOLDOUT"):
        reference_snapshot = _snapshot(case, _broad_values(case, profiles))
        expected_gates = _gates(reference_snapshot)
        plans = {}
        # Rotate arm order to avoid consistently giving one arm the cold-start position.
        for trial in range(timing_repeats):
            offset = (index + trial) % len(ARMS)
            for arm in ARMS[offset:] + ARMS[:offset]:
                began = perf_counter_ns()
                values, routes = _arm_values(
                    case, profiles, abc_profiles, arm, model, reference_snapshot
                )
                snapshot, result, selection = _run(case, values)
                elapsed = perf_counter_ns() - began
                require(_gates(snapshot) == expected_gates, "SEGMENTATION_GATE_CHANGED")
                if arm in plans:
                    require(
                        plans[arm][0]["content_hash"] == result["content_hash"]
                        and plans[arm][1]["content_hash"] == selection["content_hash"],
                        "SEGMENTATION_REPEAT_RESULT_MISMATCH",
                    )
                else:
                    plans[arm] = (result, selection, routes)
                    persist(result, selection)
                timings.append(
                    {
                        "case_id": case.case_id,
                        "arm": arm,
                        "trial": trial,
                        "elapsed_ns": elapsed,
                        "evaluated_candidates": len(result["candidate_evaluations"]),
                    }
                )
        require(
            len({p[0]["canonical_input_hash"] for p in plans.values()}) == 1,
            "SEGMENTATION_ARM_INPUT_MISMATCH",
        )
        require(
            len({p[0]["prepared_input_hash"] for p in plans.values()}) == 1,
            "SEGMENTATION_ARM_PREPARED_MISMATCH",
        )
        # No holdout realized outcomes are consulted before all three choices are fixed.
        began = perf_counter_ns()
        _, replay, replay_selection = _run(case, _broad_values(case, profiles), replay=True)
        replay_ns = perf_counter_ns() - began
        persist(replay, replay_selection)
        reference = plans["WIDE_REFERENCE"][0]
        for arm in ARMS:
            result, selection, routes = plans[arm]
            rows.extend(_compare(case, arm, result, selection, replay, reference, routes))
        receipts.append(
            {
                "case_id": case.case_id,
                "planning_canonical_hash": reference["canonical_input_hash"],
                "planning_prepared_hash": reference["prepared_input_hash"],
                "realized_canonical_hash": replay["canonical_input_hash"],
                "gate_projection_hash": digest(expected_gates),
                "gate_projection": expected_gates,
                "feature_snapshot": {
                    "as_of_date": case.planning_request["policy_input"]["context"]["as_of_date"],
                    "history_input_hash": case.planning_request["policy_input"]["content_hash"],
                    "items": _features(case, reference_snapshot),
                },
                "arm_search_hashes": {a: plans[a][0]["content_hash"] for a in ARMS},
                "arm_selection_hashes": {a: plans[a][1]["content_hash"] for a in ARMS},
                "replay_hash": replay["content_hash"],
                "shared_replay_elapsed_ns": replay_ns,
                "replay_selection_hash": replay_selection["content_hash"],
            }
        )
    require(model["content_hash"] == model_hash, "SEGMENTATION_HOLDOUT_MODEL_MUTATION")
    require(
        digest([c.planning_request for c in cases] + [c.replay_request for c in cases])
        == original_hash,
        "SEGMENTATION_SOURCE_MUTATION",
    )
    semantic = {
        "contract_id": "inventory-policy-segmentation-offline-experiment",
        "contract_version": "1.0.0",
        "source_type": "SYNTHETIC_DEVELOPMENT_FIXTURE",
        "trace_semantics": "INDEPENDENT_NONREPEATING_WINDOWS_NOT_CONTINUOUS_OPERATIONAL_BACKTEST",
        "evaluation_semantics": "LOCKED_POLICY_REALIZED_DEMAND_REPLAY_IN_FORECAST_CHANNEL",
        "profile_library_hash": digest(profiles),
        "input_hash": original_hash,
        "profile_library": profiles,
        "abc_profile_mapping": abc_profiles,
        "source_cases": [
            {
                "case_id": c.case_id,
                "split": c.split,
                "start_date": _canonical(c.planning_request)["context"]["plan_start_date"],
                "end_date": _canonical(c.planning_request)["context"]["plan_end_date"],
            }
            for c in cases
        ],
        "training_row_count": len(training),
        "validation_row_count": len(validation),
        "learning_exclusions": learning_exclusions,
        "training_source_results": source_rows,
        "model": model,
        "validation_scores": [
            {"depth": depth, "infeasible_rows": loss[0], "normalized_regret": str(loss[1])}
            for loss, depth, _ in scored
        ],
        "holdout_reselection_allowed": False,
        "rows": rows,
        "receipts": [
            {k: v for k, v in r.items() if k != "shared_replay_elapsed_ns"} for r in receipts
        ],
        "automatic_publish_allowed": False,
        "automatic_order_allowed": False,
        "database_writes": False,
        "operational_policy_approved": False,
        "optimality_scope": "FINITE_PROFILE_LIBRARY_ONLY",
        "terminal_condition": "FINITE_HORIZON_NO_TERMINAL_VALUE_ARRIVALS_OUTSIDE_HORIZON_REJECTED",
    }
    return {
        **semantic,
        "semantic_content_hash": digest(semantic),
        "timing": {
            "scope": "PROFILE_INFERENCE_CONFIG_CLASSIFICATION_PLANNING_SEARCH_AND_SELECTION",
            "training_and_validation_elapsed_ns": training_ns,
            "arm_measurements": timings,
            "shared_realized_replay": [
                {"case_id": r["case_id"], "elapsed_ns": r["shared_replay_elapsed_ns"]}
                for r in receipts
            ],
            "repeat_count": timing_repeats,
            "warmup_performed": False,
            "production_speedup_proven": False,
            "artifact_persistence_elapsed_ns": persistence_ns,
        },
    }


def summarize_report(report: dict) -> dict:
    """Report service/selection failures, not just the survivors' attractive costs."""
    with localcontext(NUMBERS):
        return _summarize_report(report)


def _summarize_report(report: dict) -> dict:
    require(
        report["semantic_content_hash"]
        == digest(
            {k: v for k, v in report.items() if k not in {"semantic_content_hash", "timing"}}
        ),
        "SEGMENTATION_REPORT_HASH",
    )
    summary = {}
    for arm in ARMS:
        rows = [r for r in report["rows"] if r["arm"] == arm]
        demand = sum(
            (number(r["realized_metrics"]["current_demand_qty"]) for r in rows), Decimal(0)
        )
        fulfilled = sum(
            (number(r["realized_metrics"]["current_demand_fulfilled_qty"]) for r in rows),
            Decimal(0),
        )
        measured = [r for r in report["timing"]["arm_measurements"] if r["arm"] == arm]
        medians = [
            sorted(r["elapsed_ns"] for r in measured if r["case_id"] == case_id)
            for case_id in sorted({r["case_id"] for r in measured})
        ]
        summary[arm] = {
            "item_periods": len(rows),
            "selected_item_periods": sum(r["selected"] for r in rows),
            "realized_service_feasible_item_periods": sum(
                r["realized_service_feasible"] for r in rows
            ),
            "realized_weighted_fill_rate": str(fulfilled / demand) if demand else None,
            "descriptive_cost_including_unselected_baselines": str(
                sum((number(r["realized_metrics"]["total_cost"]) for r in rows), Decimal(0))
            ),
            "descriptive_mean_inventory_including_unselected_baselines": str(
                sum(
                    (number(r["realized_metrics"]["average_on_hand_eoh_qty"]) for r in rows),
                    Decimal(0),
                )
                / len(rows)
            )
            if rows
            else None,
            "best_cost_candidate_misses": sum(
                r["best_cost_candidate_retained"] is False for r in rows
            ),
            "best_cost_candidate_comparable_periods": sum(
                r["best_cost_candidate_retained"] is not None for r in rows
            ),
            "missed_realized_feasible_opportunities": sum(
                r["realized_feasible_opportunity_missed"] for r in rows
            ),
            "evaluated_candidates_per_pass": sum(
                r["evaluated_candidates"] for r in measured if r["trial"] == 0
            ),
            "planning_sum_of_case_median_seconds": str(
                Decimal(sum(v[len(v) // 2] for v in medians)) / Decimal(10**9)
            ),
        }
    abc = {(r["case_id"], r["item_id"]): r for r in report["rows"] if r["arm"] == "ABC_XYZ"}
    ml = {(r["case_id"], r["item_id"]): r for r in report["rows"] if r["arm"] == "ML_PROFILE"}
    pairs = [
        key
        for key in abc
        if abc[key]["realized_service_feasible"] and ml[key]["realized_service_feasible"]
    ]
    costs = [
        sum((number(source[key]["realized_metrics"]["total_cost"]) for key in pairs), Decimal(0))
        for source in (abc, ml)
    ]
    inventory = [
        sum(
            (number(source[key]["realized_metrics"]["average_on_hand_eoh_qty"]) for key in pairs),
            Decimal(0),
        )
        for source in (abc, ml)
    ]
    summary["paired_ABC_ML"] = {
        "comparable_service_feasible_pairs": len(pairs),
        "excluded_pairs": len(abc) - len(pairs),
        "abc_realized_cost": str(costs[0]) if pairs else None,
        "ml_realized_cost": str(costs[1]) if pairs else None,
        "ml_cost_change_pct": str((costs[1] - costs[0]) / costs[0] * 100)
        if pairs and costs[0]
        else None,
        "abc_sum_of_average_inventory": str(inventory[0]) if pairs else None,
        "ml_sum_of_average_inventory": str(inventory[1]) if pairs else None,
        "warning": "Paired costs exclude failures; always read service and missed-opportunity counts too.",
    }
    return summary

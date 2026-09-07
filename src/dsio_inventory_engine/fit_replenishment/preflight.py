"""TRAIN-only horizon/terminal sensitivity, executed before PPO updates."""

from copy import deepcopy
from datetime import date, timedelta

from dsio_inventory_engine.evaluate_replenishment.application import (
    EvaluateMathematicalStrategyUseCase,
)
from dsio_inventory_engine.evaluate_replenishment.demo import build_evaluation_request
from dsio_inventory_engine.inventory_contracts.values import digest, require
from dsio_inventory_engine.learned.comparison import evaluate_hold, summarize
from .data import repackage


def train_replay_probe(original: dict, horizon: int) -> dict:
    """Known TRAIN sequence replay, explicitly NOT long held-out evidence."""
    data = deepcopy(original)
    start = original["w0_index"]
    train = original["train_weeks"]
    data["train_weeks"] = horizon
    total = start + horizon + data["validation_weeks"] + data["test_weeks"]
    first = date.fromisoformat(original["calendar"][0]["start_date"])
    calendar = []
    for i in range(total):
        day = first + timedelta(weeks=i)
        calendar.append(
            {
                "yyyyww": day.strftime("%G%V"),
                "start_date": day.isoformat(),
                "end_date": (day + timedelta(days=6)).isoformat(),
            }
        )
    # Original Calendar prefix must match this development-only weekly extension.
    require(
        calendar[: start + train] == original["calendar"][: start + train],
        "TRAIN_REPLAY_CALENDAR_UNSUPPORTED",
    )
    demand = {(r["item_id"], r["yyyyww"]): r for r in original["demand"]}
    data["calendar"] = calendar
    data["demand"] = []
    for i, bucket in enumerate(calendar):
        source = i if i < start else start + (i - start) % train
        for item in original["items"]:
            row = demand[item["item_id"], original["calendar"][source]["yyyyww"]]
            data["demand"].append({**row, "yyyyww": bucket["yyyyww"]})
    data["dataset_id"] = "TRAIN-REPLAY-" + digest([original["dataset_id"], horizon])[:24]
    return data


def preflight(training, deployment) -> dict:
    original = training.to_dict()
    long = original["train_weeks"]
    short = max(13, max(i["lead_time_weeks"] for i in original["items"]) + 1)
    require(short < long, "HORIZON_SENSITIVITY_WINDOW_UNAVAILABLE")
    rows = []
    for horizon in (short, long):
        for terminal in ("SOURCE", "ZERO"):
            data = deepcopy(original)
            data["train_weeks"] = horizon
            data["validation_weeks"] += long - horizon
            for scenario in data["scenarios"]:
                if terminal == "ZERO":
                    scenario["cost"]["terminal_backlog_per_unit"] = "0"
            training_case = repackage(data)
            # All scenarios: transport delays can magnify end-of-horizon effects.
            for scenario in data["scenarios"]:
                request = build_evaluation_request(
                    training_case, deployment, split="TRAIN", scenario_id=scenario["scenario_id"]
                )
                math = summarize(EvaluateMathematicalStrategyUseCase(deployment).execute(request))
                hold = summarize(evaluate_hold(request, deployment))
                require(
                    math["initial_state_hash"] == hold["initial_state_hash"],
                    "PREFLIGHT_INITIAL_STATE_MISMATCH",
                )
                rows.append(
                    {
                        "horizon_weeks": horizon,
                        "data_kind": "ORIGINAL_TRAIN",
                        "terminal_cost": terminal,
                        "scenario_id": scenario["scenario_id"],
                        "MATHEMATICAL": math,
                        "NO_NEW_SUPPLY_CONTROL": hold,
                    }
                )
    # Zero delay and worst fixed delay: long probes isolate horizon effects without
    # reading original VALIDATION/TEST actuals or expanding the PPO training data.
    probe_scenarios = []
    for kind in ("NO_DELAY", "FIXED_DELAY"):
        candidates = [s for s in original["scenarios"] if s["delay_kind"] == kind]
        if candidates:
            probe_scenarios.append(
                sorted(candidates, key=lambda s: (-s["delay_weeks"], s["scenario_id"]))[0][
                    "scenario_id"
                ]
            )
    for horizon in (52, 104):
        if horizon <= long:
            continue
        for terminal in ("SOURCE", "ZERO"):
            data = train_replay_probe(original, horizon)
            for scenario in data["scenarios"]:
                if terminal == "ZERO":
                    scenario["cost"]["terminal_backlog_per_unit"] = "0"
            case = repackage(data)
            for scenario_id in probe_scenarios:
                request = build_evaluation_request(
                    case, deployment, split="TRAIN", scenario_id=scenario_id
                )
                math = summarize(EvaluateMathematicalStrategyUseCase(deployment).execute(request))
                hold = summarize(evaluate_hold(request, deployment))
                require(
                    math["initial_state_hash"] == hold["initial_state_hash"],
                    "PREFLIGHT_INITIAL_STATE_MISMATCH",
                )
                rows.append(
                    {
                        "horizon_weeks": horizon,
                        "data_kind": "SYNTHETIC_TRAIN_REPLAY_STRESS",
                        "terminal_cost": terminal,
                        "scenario_id": scenario_id,
                        "MATHEMATICAL": math,
                        "NO_NEW_SUPPLY_CONTROL": hold,
                    }
                )
    result = {
        "status": "STRUCTURAL_PREFLIGHT_PASSED",
        "split": "TRAIN",
        "short_weeks": short,
        "long_weeks": long,
        "synthetic_stress_horizons": sorted(
            {r["horizon_weeks"] for r in rows if r["data_kind"] == "SYNTHETIC_TRAIN_REPLAY_STRESS"}
        ),
        "cases": rows,
        "ppo_updates_before_preflight": 0,
        "test_actuals_used": False,
        "terminal_rule": "FINITE_HORIZON_TERMINAL_BACKLOG_COST_NO_SALVAGE",
        "operational_robustness_approved": False,
        "horizon_sensitivity_review_required": True,
    }
    result["content_hash"] = digest(result)
    return result

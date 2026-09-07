"""One known-at-decision feature contract; never reads World truth or labels."""

import math
from decimal import Decimal

from dsio_inventory_engine.inventory_contracts.values import require

FEATURE_CONTRACT = "io-learned-observation-v1"
FEATURE_NAMES = [
    "available_over_mean",
    "backorder_over_mean",
    "pending_over_mean",
    "position_over_mean",
    "target_over_mean",
    "rop_over_mean",
    "stddev_over_mean",
    "lead_weeks_over_52",
    "service_level",
    "zero_demand_share",
    "remaining_weeks_over_52",
    "overdue_over_mean",
    "protected_demand_over_mean",
    "next_demand_over_mean",
]
ML_INDICES = [6, 7, 8, 9]
ACTIONS = ["HOLD", "TARGET_075", "TARGET_100", "TARGET_125"]


def vector(observation: dict, policy: dict, stats: dict) -> tuple[list[float], float]:
    rule = observation["quantity_rule"]
    # Keep the learned feature normalization floor in PHYSICAL units (v1 semantics).
    quantum = Decimal(1).scaleb(-rule.get("physical_scale", rule.get("scale")))
    scale = max(float(stats["mean_weekly_demand"] or 0), float(quantum))
    effective = policy["effective_policy"] or {"target_inventory_qty": "0", "rop_qty": "0"}
    state = observation["state"]
    pending = math.fsum(float(p["quantity"]) for p in observation["pending_supply"])
    overdue = math.fsum(
        float(p["quantity"])
        for p in observation["pending_supply"]
        if p["due_date"] < observation["decision_date"]
    )
    lead = (observation["policy"]["lead_time_days"] + 6) // 7
    cycle = policy["calculation_trace"].get("replenishment_cycle_weeks", 1)
    known_demand = [
        float(r["net_forecast_qty"]) + float(r["confirmed_customer_order_qty"])
        for r in observation["future_demand"]
    ]
    values = [
        float(state["available_qty"]) / scale,
        float(state["backorder_qty"]) / scale,
        pending / scale,
        float(state["inventory_position_qty"]) / scale,
        float(effective["target_inventory_qty"]) / scale,
        float(effective["rop_qty"]) / scale,
        float(stats["stddev"] or 0) / scale,
        ((observation["policy"]["lead_time_days"] + 6) // 7) / 52,
        float(observation["policy"]["approved_service_level"]),
        1 - stats["positive_demand_weeks"] / max(1, stats["observation_count"]),
        len(observation["calendar"]) / 52,
        overdue / scale,
        math.fsum(known_demand[: lead + cycle]) / scale,
        known_demand[0] / scale,
    ]
    require(all(math.isfinite(v) for v in values), "NONFINITE_MODEL_FEATURE")
    return values, scale


def fit_normalizer(rows: list[list[float]]) -> dict:
    require(bool(rows) and all(len(r) == len(FEATURE_NAMES) for r in rows), "MODEL_FEATURE_SHAPE")
    mean = [math.fsum(r[i] for r in rows) / len(rows) for i in range(len(FEATURE_NAMES))]
    std = [
        max(1e-6, math.sqrt(math.fsum((r[i] - mean[i]) ** 2 for r in rows) / len(rows)))
        for i in range(len(mean))
    ]
    return {
        "mean": [format(x, ".17g") for x in mean],
        "std": ["0.000001" if x == 1e-6 else format(x, ".17g") for x in std],
    }


def normalized(values: list[float], normalizer: dict) -> list[float]:
    require(len(values) == len(FEATURE_NAMES), "MODEL_FEATURE_SHAPE")
    return [
        max(-10.0, min(10.0, (x - float(m)) / float(s)))
        for x, m, s in zip(values, normalizer["mean"], normalizer["std"], strict=True)
    ]

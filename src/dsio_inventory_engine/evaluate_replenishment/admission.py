"""Cross-input checks, not implicit source discovery or replacement of production gates."""

from decimal import Decimal, ROUND_HALF_UP, localcontext
from typing import cast

from dsio_inventory_engine.inventory_contracts.training import VERSION
from dsio_inventory_engine.inventory_contracts.values import (
    canonical_json,
    quantity_text as q,
    require,
)
from dsio_inventory_engine.recommend_replenishment.application.guard import policy_at
from dsio_inventory_engine.training.dataset import splits
from dsio_inventory_engine.training.reference import NUMBERS, demand_series

SERVICE_LEVEL = {
    "SL_90": "0.9",
    "SL_95": "0.95",
    "SL_975": "0.975",
    "SL_98": "0.98",
    "SL_99": "0.99",
}


def episode_range(training: dict, split: str) -> tuple[int, int]:
    selected = [(a, b) for name, a, b in splits(training) if name == split]
    require(len(selected) == 1, "UNKNOWN_SPLIT")
    return selected[0]


def frozen_mean(training: dict, item: dict, origin: int, lookback: int) -> str:
    history = demand_series(training, item)[origin - lookback : origin]
    require(len(history) == lookback and None not in history, "INSUFFICIENT_HISTORY")
    with localcontext(NUMBERS):
        mean = sum(cast(list[Decimal], history), Decimal(0)) / lookback
        return q(mean.quantize(Decimal(item["quantity_step"]), rounding=ROUND_HALF_UP))


def validate_binding(data: dict, prepared: dict, world: dict) -> None:
    training = data["training_request"]
    math = data["mathematical_request"]
    source = math["recommendation"]["canonical_input"]
    snaps = source["snapshots"]
    origin, end = episode_range(training, data["split"])
    context = prepared["context"]
    require(
        all(context[k] == v for k, v in training["context"].items()), "EVALUATION_CONTEXT_MISMATCH"
    )
    require(
        [{k: row[k] for k in ("yyyyww", "start_date", "end_date")} for row in prepared["calendar"]]
        == training["calendar"][origin:end],
        "EVALUATION_CALENDAR_MISMATCH",
    )
    master = {r["item_id"]: r for r in prepared["master"]}
    require(
        set(master) == {i["item_id"] for i in training["items"]}, "EVALUATION_UNIVERSE_MISMATCH"
    )
    inventory_meta = snaps["inventory"]["metadata"]
    require(
        inventory_meta["position_source_type"] == "SYNTHETIC_BOH"
        and inventory_meta["simulation_run_id"] == training["simulation_run_id"]
        and inventory_meta["generator_version"] == VERSION,
        "EVALUATION_SYNTHETIC_LINEAGE",
    )
    require(
        not inventory_meta["adjustments"] and not inventory_meta["source_events"],
        "EVALUATION_ADJUSTMENT_UNSUPPORTED",
    )
    scenario = next(s for s in training["scenarios"] if s["scenario_id"] == data["scenario_id"])
    positions = {p["item_id"]: p for p in prepared["positions"]}
    rules = {r["uom"]: r for r in prepared["manifest"]["quantity_rules"]}
    states = {i["item_id"]: i for i in world["items"]}
    lookback = math["policy_input"]["profile"]["lookback_weeks"]
    require(
        math["policy_input"]["calendar"]
        == sorted(training["calendar"][origin - lookback : origin], key=canonical_json),
        "EVALUATION_HISTORY_CALENDAR_MISMATCH",
    )
    require(
        math["policy_input"]["context"]["history_source_type"] == "SYNTHETIC_DEMAND",
        "EVALUATION_HISTORY_SOURCE",
    )
    # Missing production history remains missing, so approved fallback/exclusion is exercised.
    actual_history = {
        (i["item_id"], c["yyyyww"]): v
        for i in training["items"]
        for c, v in zip(training["calendar"], demand_series(training, i), strict=True)
    }
    for row in math["policy_input"]["history"]:
        require(
            Decimal(row["demand_qty"]) == actual_history.get((row["item_id"], row["yyyyww"])),
            "EVALUATION_HISTORY_MISMATCH",
        )
    for item in training["items"]:
        key, uom = item["item_id"], item["uom"]
        require(master[key]["uom"] == uom, "EVALUATION_UOM_MISMATCH")
        require(
            Decimal(item["quantity_step"]) == Decimal(1).scaleb(-rules[uom]["scale"]),
            "EVALUATION_UOM_STEP_UNSUPPORTED",
        )
        state, position = states[key], positions[key]
        require(
            Decimal(position["reserved_qty"]) == 0
            and Decimal(position["available_qty"]) == Decimal(state["boh_qty"])
            and Decimal(position["backorder_qty"]) == Decimal(state["backorder_qty"]),
            "EVALUATION_BOH_MISMATCH",
        )
        policy = policy_at(
            [p for p in prepared["policies"] if p["item_id"] == key], context["plan_start_date"]
        )
        pairs = {
            "lead_time_days": item["lead_time_weeks"] * 7,
            "moq": item["moq"],
            "order_multiple": item["lot_multiple"],
            "physical_max_capacity": item["physical_capacity_qty"],
            "approved_service_level": SERVICE_LEVEL[
                scenario["service_level_code"] or item["service_level_code"]
            ],
        }
        require(all(policy[k] == v for k, v in pairs.items()), "EVALUATION_INITIAL_POLICY_MISMATCH")
        forecast = frozen_mean(training, item, origin, lookback)
        for row in (r for r in prepared["demands"] if r["item_id"] == key):
            require(
                Decimal(row["confirmed_customer_order_qty"]) == 0
                and row["gross_forecast_qty"] is not None
                and Decimal(row["gross_forecast_qty"]) == Decimal(forecast)
                and Decimal(row["net_forecast_qty"]) == Decimal(forecast),
                "EVALUATION_FORECAST_MISMATCH",
            )
    pending = {
        (i["item_id"], o["order_id"]): (o["due_date"], Decimal(o["quantity"]))
        for i in world["items"]
        for o in i["pending_supply"]
    }
    rows = prepared["receipt_decisions"]
    require(
        all(r["supply_type"] == "SYNTHETIC_PURCHASE_ORDER" for r in snaps["receipts"]["rows"]),
        "EVALUATION_SUPPLY_SOURCE",
    )
    require(
        all(Decimal(r["included_qty"]) > 0 for r in rows),
        "EVALUATION_INITIAL_SUPPLY_OUTSIDE_HORIZON",
    )
    require(
        {(r["item_id"], r["receipt_id"]): (r["due_date"], Decimal(r["included_qty"])) for r in rows}
        == pending,
        "EVALUATION_PIPELINE_MISMATCH",
    )

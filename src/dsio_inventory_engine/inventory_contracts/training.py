"""Versioned development-only reference data, never an operational inventory source."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Context, Decimal, localcontext

from .values import (
    canonical_json,
    choice,
    day,
    decimal_string,
    digest,
    hash_value,
    identifier,
    integer,
    optional,
    read_json,
    records,
    require,
    shape,
    text,
    yyyyww,
)

VERSION = "1.0.0"
SERVICE_Z = {
    "SL_90": "1.2816",
    "SL_95": "1.6449",
    "SL_975": "1.9600",
    "SL_98": "2.0537",
    "SL_99": "2.3263",
}
CONTEXT = {
    k: identifier
    for k in (
        "company_cd",
        "subs_cd",
        "site_cd",
        "planning_cycle_id",
        "planning_cycle_revision_id",
        "cycle_site_execution_id",
        "configuration_revision",
    )
} | {"plan_type": choice("POSM", "TGSM")}
CALENDAR = {"yyyyww": yyyyww, "start_date": day, "end_date": day}
ITEM = {
    "item_id": identifier,
    "uom": identifier,
    "quantity_step": decimal_string,
    "active_from_index": integer,
    "lead_time_weeks": integer,
    "policy_source": choice("SOURCE_MASTER", "SYNTHETIC_PROFILE"),
    "policy_reference": identifier,
    "service_level_code": choice(*SERVICE_Z),
    "moq": decimal_string,
    "lot_multiple": decimal_string,
    "physical_capacity_qty": optional(decimal_string),
    "legacy_target_inventory_qty": optional(decimal_string),
}
DEMAND = {"item_id": identifier, "uom": identifier, "yyyyww": yyyyww, "demand_qty": decimal_string}
COST = {
    "currency": choice("USD", "KRW", "EUR"),
    **{
        k: decimal_string
        for k in (
            "holding_per_unit_week",
            "backlog_per_unit_week",
            "fixed_per_order",
            "purchase_per_unit",
            "terminal_backlog_per_unit",
        )
    },
}
SCENARIO = {
    "scenario_id": identifier,
    "delay_kind": choice("NO_DELAY", "FIXED_DELAY", "SELECTED_ORDER_DELAY", "DISRUPTION_WINDOW"),
    "delay_weeks": integer,
    "selected_order_ids": lambda v: _ids(v),
    "disruption_from_index": integer,
    "disruption_to_index": integer,
    "service_level_code": optional(choice(*SERVICE_Z)),
    "cost": lambda v: shape(v, COST),
}
FIELDS = {
    "contract_id": choice("io-training-foundation-v1"),
    "contract_version": choice(VERSION),
    "dataset_id": identifier,
    "simulation_run_id": identifier,
    "content_hash": hash_value,
    "context": lambda v: shape(v, CONTEXT),
    "calendar_snapshot_id": identifier,
    "demand_snapshot_id": identifier,
    "policy_snapshot_id": identifier,
    "w0_index": integer,
    "warmup_weeks": integer,
    "lookback_weeks": integer,
    "lookback_reason": text,
    "replenishment_cycle_weeks": integer,
    "missing_history": choice("REJECT", "MISSING_ACTIVE_WEEK_IS_ZERO"),
    "train_weeks": integer,
    "validation_weeks": integer,
    "test_weeks": integer,
    "calendar": lambda v: records(v, CALENDAR, 520),
    "items": lambda v: records(v, ITEM, 100),
    "demand": lambda v: records(v, DEMAND, 52_000),
    "scenarios": lambda v: records(v, SCENARIO, 20),
}


def _ids(value: list) -> list:
    require(type(value) is list and len(value) <= 1000, "ROW_LIMIT")
    result = sorted(identifier(v) for v in value)
    require(len(set(result)) == len(result), "DUPLICATE_ORDER_ID")
    return result


def content(value: dict) -> dict:
    return {k: v for k, v in value.items() if k != "content_hash"}


def normalize(value: dict) -> dict:
    result = shape(value, FIELDS)
    result["calendar"].sort(key=lambda r: r["start_date"])
    for key in ("items", "demand", "scenarios"):
        result[key].sort(key=canonical_json)
    return result


@dataclass(frozen=True)
class TrainingRequest:
    document_json: str

    @classmethod
    def from_dict(cls, value: dict) -> "TrainingRequest":
        data = normalize(value)
        require(data["content_hash"] == digest(content(data)), "TRAINING_INPUT_HASH_MISMATCH")
        with localcontext(Context(prec=40)):
            _validate(data)
        return cls(canonical_json(data))

    def to_dict(self) -> dict:
        return read_json(self.document_json)


def _validate(data: dict) -> None:
    calendar, items = data["calendar"], data["items"]
    n, w0, warmup = len(calendar), data["w0_index"], data["warmup_weeks"]
    require(bool(items) and bool(data["scenarios"]), "EMPTY_TRAINING_UNIVERSE")
    require(52 <= warmup <= 260 and warmup + 13 <= w0 < n, "INSUFFICIENT_HISTORY")
    require(data["lookback_weeks"] in (13, 26), "LOOKBACK_WEEKS")
    require(1 <= data["replenishment_cycle_weeks"] <= 52, "REVIEW_PERIOD")
    splits = [data[k] for k in ("train_weeks", "validation_weeks", "test_weeks")]
    require(all(v > 0 for v in splits) and sum(splits) == n - w0, "TIME_SPLIT_RANGE")
    require(n * len(items) * len(data["scenarios"]) <= 20_000, "TRAINING_WORK_LIMIT")
    weeks = {row["yyyyww"]: i for i, row in enumerate(calendar)}
    require(len(weeks) == n, "DUPLICATE_WEEK")
    for i, row in enumerate(calendar):
        start, end = date.fromisoformat(row["start_date"]), date.fromisoformat(row["end_date"])
        require(end == start + timedelta(days=6), "FULL_WEEK_REQUIRED")
        if i:
            require(
                start == date.fromisoformat(calendar[i - 1]["end_date"]) + timedelta(days=1),
                "CALENDAR_GAP",
            )
    by_item = {row["item_id"]: row for row in items}
    require(len(by_item) == len(items), "DUPLICATE_ITEM")
    for item in items:
        require(item["active_from_index"] <= w0 - warmup - 13, "INSUFFICIENT_HISTORY")
        require(1 <= item["lead_time_weeks"] <= 52, "LEAD_TIME_RANGE")
        step, lot = Decimal(item["quantity_step"]), Decimal(item["lot_multiple"])
        require(step > 0 and lot >= step and lot % step == 0, "QUANTITY_STEP")
        require(Decimal(item["moq"]) % step == 0, "QUANTITY_STEP")
        if item["physical_capacity_qty"] is not None:
            require(Decimal(item["physical_capacity_qty"]) % step == 0, "QUANTITY_STEP")
    seen = set()
    for row in data["demand"]:
        require(row["item_id"] in by_item and row["yyyyww"] in weeks, "ORPHAN_DEMAND")
        item = by_item[row["item_id"]]
        require(row["uom"] == item["uom"], "UOM_MISMATCH")
        require(weeks[row["yyyyww"]] >= item["active_from_index"], "INACTIVE_DEMAND")
        require(Decimal(row["demand_qty"]) % Decimal(item["quantity_step"]) == 0, "QUANTITY_STEP")
        key = (row["item_id"], row["yyyyww"])
        require(key not in seen, "DUPLICATE_DEMAND")
        seen.add(key)
    for item in items:
        for i in range(item["active_from_index"], n):
            if (item["item_id"], calendar[i]["yyyyww"]) not in seen:
                # Future labels must never be silently inferred from missing actuals.
                require(
                    i < w0 and data["missing_history"] == "MISSING_ACTIVE_WEEK_IS_ZERO",
                    "MISSING_DEMAND",
                )
    ids = [s["scenario_id"] for s in data["scenarios"]]
    require(len(set(ids)) == len(ids), "DUPLICATE_SCENARIO")
    for s in data["scenarios"]:
        require(s["delay_weeks"] <= 52, "DELAY_RANGE")
        kind = s["delay_kind"]
        require((s["delay_weeks"] == 0) == (kind == "NO_DELAY"), "DELAY_CONFIGURATION")
        require(
            bool(s["selected_order_ids"]) == (kind == "SELECTED_ORDER_DELAY"), "DELAY_CONFIGURATION"
        )
        require(
            (0 <= s["disruption_from_index"] < s["disruption_to_index"] <= n + 104)
            if kind == "DISRUPTION_WINDOW"
            else s["disruption_from_index"] == s["disruption_to_index"] == 0,
            "DELAY_CONFIGURATION",
        )

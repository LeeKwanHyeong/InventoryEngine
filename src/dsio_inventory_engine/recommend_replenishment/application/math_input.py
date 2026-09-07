"""Admission for history, time-aware approvals and policy profile; no IO or defaults."""

from datetime import date, datetime, timedelta
from decimal import Decimal

from dsio_inventory_engine.inventory_contracts.canonical import SCOPE
from dsio_inventory_engine.inventory_contracts.values import require
from dsio_inventory_engine.prepare_inventory.application.validation import check_uom, unique


def validate_math_input(snapshot: dict, recommendation: dict, prepared: dict) -> None:
    context, profile = snapshot["context"], snapshot["profile"]
    expected = prepared["context"]
    require(snapshot["status"] == "SEALED", "UNSEALED_POLICY_INPUT")
    require(snapshot["history_row_count"] == len(snapshot["history"]), "HISTORY_ROW_COUNT_MISMATCH")
    require(all(context[k] == expected[k] for k in SCOPE), "POLICY_SCOPE_MISMATCH")
    require(
        context["canonical_input_hash"] == recommendation["execution"]["canonical_input_hash"],
        "POLICY_CANONICAL_BINDING_MISMATCH",
    )
    require(
        context["configuration_revision"] == expected["configuration_revision"],
        "POLICY_CONFIGURATION_MISMATCH",
    )
    w0 = date.fromisoformat(expected["plan_start_date"])
    require(context["as_of_date"] == (w0 - timedelta(days=1)).isoformat(), "HISTORY_AS_OF_MISMATCH")
    require(
        instant(context["available_at"]) <= instant(expected["inventory_cutoff_at"]),
        "FUTURE_POLICY_INFORMATION",
    )
    require(context["quantity_semantics"] == "UNCENSORED_DEMAND", "CENSORED_HISTORY_NOT_SUPPORTED")
    require(profile["lookback_weeks"] in (13, 26), "INVALID_POLICY_LOOKBACK")
    require(profile["stddev_ddof"] in (0, 1), "INVALID_STDDEV_DDOF")
    require(1 <= profile["replenishment_cycle_weeks"] <= 52, "INVALID_REPLENISHMENT_CYCLE")
    calendar = sorted(snapshot["calendar"], key=lambda r: r["start_date"])
    require(len(calendar) == profile["lookback_weeks"], "HISTORY_CALENDAR_SIZE")
    unique(calendar, ("yyyyww",), "DUPLICATE_HISTORY_WEEK")
    start = w0 - timedelta(weeks=profile["lookback_weeks"])
    for i, row in enumerate(calendar):
        require(
            row["start_date"] == (start + timedelta(weeks=i)).isoformat()
            and row["end_date"] == (start + timedelta(weeks=i, days=6)).isoformat(),
            "HISTORY_CALENDAR_GAP_OR_OVERLAP",
        )
    require(
        not ({r["yyyyww"] for r in calendar} & {r["yyyyww"] for r in prepared["calendar"]}),
        "HISTORY_FUTURE_WEEK",
    )
    # This first policy profile requires complete 7-day planning buckets, including W0.
    require(
        all(
            (date.fromisoformat(r["end_date"]) - date.fromisoformat(r["start_date"])).days == 6
            for r in prepared["calendar"]
        ),
        "MATH_REQUIRES_FULL_WEEK_BUCKETS",
    )
    master = {r["item_id"]: r for r in prepared["master"]}
    rules = {r["uom"]: r for r in prepared["manifest"]["quantity_rules"]}
    weeks = {r["yyyyww"] for r in calendar}
    unique(snapshot["history"], ("item_id", "yyyyww"), "DUPLICATE_HISTORY_ITEM_WEEK")
    for row in snapshot["history"]:
        check_uom(row, master, rules, ("demand_qty",), planning=True)
        require(row["yyyyww"] in weeks, "HISTORY_OUTSIDE_LOOKBACK")
    for policy in prepared["policies"]:
        require(
            Decimal("0.5") <= Decimal(policy["approved_service_level"]) < 1,
            "MATH_SERVICE_LEVEL_RANGE",
        )
        require(policy["lead_time_days"] <= 3660, "LEAD_TIME_RANGE")
    validate_adjustments(snapshot, master, rules)


def validate_adjustments(snapshot: dict, master: dict, rules: dict) -> None:
    rows = snapshot["adjustments"]
    unique(rows, ("adjustment_id",), "DUPLICATE_POLICY_ADJUSTMENT")
    groups: dict[tuple, list] = {}
    for row in rows:
        check_uom(row, master, rules, ())
        check_uom({**row, **row["values"]}, master, rules, tuple(row["values"]), planning=True)
        require(row["effective_from"] < row["effective_to"], "ADJUSTMENT_EFFECTIVE_RANGE")
        require(
            instant(row["approved_at"]) <= instant(snapshot["context"]["available_at"]),
            "ADJUSTMENT_NOT_KNOWN_AT_CUTOFF",
        )
        values = row["values"]
        require(
            Decimal(values["safety_stock_qty"])
            <= Decimal(values["rop_qty"])
            <= Decimal(values["target_inventory_qty"]),
            "INVALID_EFFECTIVE_POLICY_ORDER",
        )
        if row["kind"] == "LEGACY_FALLBACK":
            require(snapshot["profile"]["allow_legacy_fallback"], "LEGACY_FALLBACK_NOT_ALLOWED")
        groups.setdefault((row["item_id"], row["kind"]), []).append(row)
    for group in groups.values():
        ordered = sorted(group, key=lambda r: r["effective_from"])
        for left, right in zip(ordered, ordered[1:]):
            require(
                left["effective_to"] <= right["effective_from"], "OVERLAPPING_POLICY_ADJUSTMENTS"
            )


def instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

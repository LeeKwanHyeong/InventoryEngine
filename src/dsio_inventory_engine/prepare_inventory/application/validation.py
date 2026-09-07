"""Admission checks for scope, sealed content, calendar and item universe."""

from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dsio_inventory_engine.inventory_contracts.canonical import SCOPE, snapshot_content
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.values import digest, require


def unique(rows: list[dict], keys: tuple[str, ...], code: str) -> None:
    seen = set()
    for row in rows:
        key = tuple(row[k] for k in keys)
        require(key not in seen, code)
        seen.add(key)


def same_scope(row: dict, context: dict) -> None:
    require(all(row[k] == context[k] for k in SCOPE), "ROW_SCOPE_MISMATCH")


def verify_snapshots(data: dict, deployment: DeploymentScope) -> None:
    context, snapshots = data["context"], data["snapshots"]
    require(context["company_cd"] == deployment.company_cd, "DEPLOYMENT_COMPANY_MISMATCH")
    for kind, snapshot in snapshots.items():
        require(
            snapshot["status"] == "SEALED",
            "UNSEALED_INPUT",
            [{"snapshot_type": kind, "status": snapshot["status"]}],
        )
        require(snapshot["row_count"] == len(snapshot["rows"]), "SNAPSHOT_ROW_COUNT_MISMATCH")
        require(
            snapshot["content_hash"] == digest(snapshot_content(kind, snapshot)),
            "SNAPSHOT_HASH_MISMATCH",
            [{"snapshot_type": kind}],
        )
        require(
            data["input_bindings"][kind]
            == {"snapshot_id": snapshot["snapshot_id"], "content_hash": snapshot["content_hash"]},
            "PINNED_INPUT_MISMATCH",
            [{"snapshot_type": kind}],
        )
        same_scope(snapshot["metadata"], context)
    require(
        len({s["snapshot_id"] for s in snapshots.values()}) == len(snapshots),
        "DUPLICATE_SNAPSHOT_ID",
    )
    require(
        snapshots["master"]["metadata"]["master_snapshot_revision"]
        == context["master_snapshot_revision"],
        "MASTER_REVISION_MISMATCH",
    )
    forecast = snapshots["forecast"]["metadata"]
    require(
        forecast["demand_run_id"] == context["demand_run_id"]
        and forecast["demand_run_status"] == "SUCCEEDED"
        and forecast["evidence_status"] == "VERIFIED",
        "FORECAST_NOT_VERIFIED",
    )
    require(
        forecast["calendar_snapshot_id"] == snapshots["calendar"]["snapshot_id"]
        and forecast["calendar_content_hash"] == snapshots["calendar"]["content_hash"],
        "DEMAND_CALENDAR_MISMATCH",
    )
    require(
        forecast["master_snapshot_revision"] == context["master_snapshot_revision"]
        and forecast["master_content_hash"] == snapshots["master"]["content_hash"],
        "DEMAND_MASTER_MISMATCH",
    )
    for kind in ("customer_orders", "receipts"):
        require(
            snapshots[kind]["metadata"]["coverage"] == "COMPLETE",
            "SOURCE_COVERAGE_UNAVAILABLE",
            [{"snapshot_type": kind}],
        )


def validate_calendar(data: dict) -> list[dict]:
    context = data["context"]
    rows = sorted(data["snapshots"]["calendar"]["rows"], key=lambda row: row["seq"])
    require(0 < len(rows) <= 520, "CALENDAR_SIZE")
    unique(rows, ("yyyyww",), "DUPLICATE_WEEK")
    require([row["seq"] for row in rows] == list(range(len(rows))), "INVALID_CALENDAR_BUCKET")
    require(
        rows[0]["start_date"] == context["plan_start_date"]
        and rows[-1]["end_date"] == context["plan_end_date"]
        and rows[0]["yyyyww"] == context["plan_yyyyww"],
        "PLAN_CALENDAR_MISMATCH",
    )
    try:
        ZoneInfo(context["business_timezone"])
    except (ZoneInfoNotFoundError, ValueError):
        require(False, "INVALID_BUSINESS_TIMEZONE")
    previous_end = None
    for seq, row in enumerate(rows):
        start, end = date.fromisoformat(row["start_date"]), date.fromisoformat(row["end_date"])
        require(row["seq"] == seq and 0 <= (end - start).days <= 6, "INVALID_CALENDAR_BUCKET")
        require(
            previous_end is None or start == previous_end + timedelta(days=1),
            "CALENDAR_GAP_OR_OVERLAP",
        )
        # Business YYYYWW comes from the pinned calendar, never an ISO-week conversion.
        require(
            len(row["base_month"]) == 6
            and row["base_month"].isdigit()
            and 1 <= int(row["base_month"][4:]) <= 12,
            "INVALID_BASE_MONTH",
        )
        previous_end = end
    return rows


def quantity_rules(data: dict) -> dict:
    rows = data["quantity_rules"]
    unique(rows, ("uom",), "DUPLICATE_UOM_RULE")
    require(bool(rows), "MISSING_UOM_RULE")
    for row in rows:
        planning = row.get("planning_scale", row.get("scale"))
        physical = row.get("physical_scale", row.get("scale"))
        require(0 <= physical <= planning <= 6, "INVALID_UOM_SCALE")
        if row["uom"] == "EA":
            require(
                physical == 0 and Decimal(row["tolerance_qty"]) == 0,
                "EA_REQUIRES_EXACT_INTEGER",
            )
        require(
            Decimal(row["tolerance_qty"]).as_tuple().exponent >= -physical,
            "TOLERANCE_PRECISION",
        )
    return {row["uom"]: row for row in rows}


def check_uom(
    row: dict, master: dict, rules: dict, fields: tuple[str, ...], *, planning: bool = False
) -> None:
    require(row["item_id"] in master, "ORPHAN_ITEM", [{"item_id": row["item_id"]}])
    require(row["uom"] == master[row["item_id"]]["uom"], "UOM_MISMATCH")
    require(row["uom"] in rules, "MISSING_UOM_RULE")
    rule = rules[row["uom"]]
    scale = rule.get("planning_scale" if planning else "physical_scale", rule.get("scale"))
    for key in fields:
        if row[key] is not None:
            require(
                Decimal(row[key]).as_tuple().exponent >= -scale,
                "UOM_QUANTITY_PRECISION",
            )


QUANTITY_FIELDS = {
    "forecast": ("forecast_qty", "upstream_gross_forecast_qty", "upstream_consumed_qty"),
    "customer_orders": ("confirmed_customer_order_qty",),
    "inventory": ("on_hand_qty", "reserved_qty", "available_qty", "backorder_qty"),
    "prior_inventory": ("eoh_qty",),
    "receipts": ("due_qty",),
    "policies": (
        "moq",
        "order_multiple",
        "physical_max_capacity",
        "source_target_inventory_qty",
        "source_rop_qty",
    ),
}


def validate_universe(
    data: dict, calendar: list[dict], deployment: DeploymentScope
) -> tuple[dict, dict]:
    snapshots, context = data["snapshots"], data["context"]
    rules = quantity_rules(data)
    all_master = snapshots["master"]["rows"]
    unique(all_master, ("item_id",), "DUPLICATE_MASTER_ITEM")
    for row in all_master:
        same_scope(row, context)
    master = {r["item_id"]: r for r in all_master if r["active"] and r["stock_managed"]}
    require(bool(master), "EMPTY_BUFFER_UNIVERSE")
    require(len(master) * len(calendar) <= 200_000, "PSI_GRID_LIMIT")
    weeks = {row["yyyyww"] for row in calendar}
    forecast_rows = snapshots["forecast"]["rows"]
    forecast_keys = {(row["item_id"], row["yyyyww"]) for row in forecast_rows}
    expected_forecast_keys = {(item, week) for item in master for week in weeks}
    universe_evidence = _forecast_universe_evidence(
        master=set(master),
        forecast_keys=forecast_keys,
        expected_keys=expected_forecast_keys,
    )
    require(
        universe_evidence["ineligible_forecast_item_count"] == 0,
        "ORPHAN_ITEM",
        [universe_evidence],
    )
    for kind, fields in QUANTITY_FIELDS.items():
        for row in snapshots[kind]["rows"]:
            same_scope(row, context)
            if kind == "policies":
                check_uom(row, master, rules, fields[:3])
                check_uom(row, master, rules, fields[3:], planning=True)
            else:
                check_uom(row, master, rules, fields, planning=kind == "forecast")
            if "yyyyww" in row:
                require(row["yyyyww"] in weeks, "UNKNOWN_BUCKET")
    for kind in ("inventory", "prior_inventory"):
        rows = snapshots[kind]["rows"]
        unique(rows, ("item_id",), "DUPLICATE_POSITION")
        require({row["item_id"] for row in rows} == set(master), "MISSING_POSITION")
    for kind in ("forecast", "customer_orders"):
        unique(snapshots[kind]["rows"], ("item_id", "yyyyww"), "DUPLICATE_DEMAND_BUCKET")
    require(
        forecast_keys == expected_forecast_keys,
        "MISSING_FORECAST_BUCKET",
        [universe_evidence],
    )
    unique(snapshots["receipts"]["rows"], ("receipt_id",), "DUPLICATE_RECEIPT")
    validate_policies(snapshots["policies"]["rows"], master, calendar, deployment)
    return master, rules


def _forecast_universe_evidence(
    *,
    master: set[str],
    forecast_keys: set[tuple[str, str]],
    expected_keys: set[tuple[str, str]],
) -> dict:
    forecast_items = {item for item, _week in forecast_keys}
    missing_items = master - forecast_items
    ineligible_items = forecast_items - master
    missing_buckets = expected_keys - forecast_keys
    unexpected_buckets = forecast_keys - expected_keys
    evidence = {
        "contract_id": "io-inventory-universe-comparison-v1",
        "status": (
            "REJECTED_INELIGIBLE_FORECAST_ITEMS"
            if ineligible_items
            else ("REJECTED_MISSING_FORECAST_BUCKETS" if missing_buckets else "VERIFIED")
        ),
        "inventory_eligible_item_count": len(master),
        "forecast_item_count": len(forecast_items),
        "matched_item_count": len(master & forecast_items),
        "missing_forecast_item_count": len(missing_items),
        "missing_forecast_item_hash": digest(sorted(missing_items)),
        "missing_forecast_item_sample": sorted(missing_items)[:100],
        "ineligible_forecast_item_count": len(ineligible_items),
        "ineligible_forecast_item_hash": digest(sorted(ineligible_items)),
        "ineligible_forecast_item_sample": sorted(ineligible_items)[:100],
        "missing_forecast_bucket_count": len(missing_buckets),
        "missing_forecast_bucket_hash": digest(sorted(missing_buckets)),
        "missing_forecast_bucket_sample": [
            {"item_id": item, "yyyyww": week} for item, week in sorted(missing_buckets)[:100]
        ],
        "unexpected_forecast_bucket_count": len(unexpected_buckets),
        "uom_rule": "FIXED_EA_V1",
    }
    evidence["content_hash"] = digest(evidence)
    return evidence


def validate_policies(
    rows: list[dict], master: dict, calendar: list[dict], deployment: DeploymentScope
) -> None:
    unique(rows, ("item_id", "policy_id", "effective_from"), "DUPLICATE_POLICY")
    by_item: dict[str, list[dict]] = {item: [] for item in master}
    for row in rows:
        require(row["effective_from"] < row["effective_to"], "POLICY_EFFECTIVE_RANGE")
        require(
            Decimal(row["order_multiple"]) > 0 and 0 < Decimal(row["approved_service_level"]) < 1,
            "INVALID_POLICY_INPUT",
        )
        require(
            deployment.environment == "DEVELOPMENT"
            or row["policy_source_type"] != "SYNTHETIC_POLICY",
            "SYNTHETIC_POLICY_FORBIDDEN",
        )
        by_item[row["item_id"]].append(row)
    for item, policies in by_item.items():
        policies.sort(key=lambda row: row["effective_from"])
        for previous, current in zip(policies, policies[1:]):
            require(previous["effective_to"] <= current["effective_from"], "OVERLAPPING_POLICIES")
        for bucket in calendar:
            applicable = [
                r
                for r in policies
                if r["effective_from"] <= bucket["start_date"] < r["effective_to"]
            ]
            require(
                len(applicable) == 1,
                "MISSING_POLICY",
                [{"item_id": item, "yyyyww": bucket["yyyyww"]}],
            )

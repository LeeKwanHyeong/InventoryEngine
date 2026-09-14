"""Package hand-written inputs; NEVER calculate PSI expectations or import Engine code."""

import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = json.loads((ROOT / "tests/fixtures/golden_baseline.json").read_text())


def sha(value: object) -> str:
    content = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def utc(stamp: datetime) -> str:
    return stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def reseal(request: dict, *, update_bindings: bool = True) -> dict:
    """Test packaging only. Not a production seal or approval service."""
    snapshots = request["snapshots"]
    for kind in (
        "calendar",
        "master",
        "prior_inventory",
        "forecast",
        "customer_orders",
        "receipts",
        "policies",
        "inventory",
    ):
        snapshot = snapshots[kind]
        if kind == "forecast":
            snapshot["metadata"].update(
                calendar_content_hash=snapshots["calendar"]["content_hash"],
                master_content_hash=snapshots["master"]["content_hash"],
            )
        if kind == "inventory":
            snapshot["metadata"]["prior_content_hash"] = snapshots["prior_inventory"][
                "content_hash"
            ]
            for key in ("adjustments", "source_events"):
                snapshot["metadata"][key].sort(
                    key=lambda row: json.dumps(row, sort_keys=True, separators=(",", ":"))
                )
        snapshot["rows"].sort(
            key=lambda row: json.dumps(row, sort_keys=True, separators=(",", ":"))
        )
        snapshot["row_count"] = len(snapshot["rows"])
        snapshot["content_hash"] = sha(
            {
                "snapshot_type": kind,
                "snapshot_id": snapshot["snapshot_id"],
                "metadata": snapshot["metadata"],
                "rows": snapshot["rows"],
            }
        )
        if update_bindings:
            request["input_bindings"][kind] = {
                "snapshot_id": snapshot["snapshot_id"],
                "content_hash": snapshot["content_hash"],
            }
    return request


def build_request(case: dict) -> dict:
    start = date.fromisoformat(case.get("start_date", "2026-09-28"))
    end = start + timedelta(days=20)
    weeks = case.get("weeks", ["202640", "202641", "202642"])
    scope = {"company_cd": "DSE", "subs_cd": "C100", "site_cd": "V101"}
    source = case.get("source_type", "ACTUAL_BOH")
    synthetic = source == "SYNTHETIC_BOH"
    simulation_id = "MANUAL-SYNTHETIC-" + case["id"] if synthetic else None
    generator = "hand-written-fixture-v1" if synthetic else None
    tz = timezone(timedelta(hours=9))
    w0 = datetime.combine(start, time.min, tz)
    cutoff = utc(w0 - timedelta(seconds=1))
    context = {
        **scope,
        "engine_run_id": "IO-GOLDEN-" + case["id"],
        "planning_cycle_id": "PC-GOLDEN",
        "planning_cycle_revision_id": "PC-GOLDEN-R1",
        "cycle_site_execution_id": "PC-GOLDEN-R1:V101",
        "plan_id": "PLAN-GOLDEN",
        "plan_type": case.get("plan_type", "POSM"),
        "plan_yyyyww": weeks[0],
        "plan_start_date": start.isoformat(),
        "plan_end_date": end.isoformat(),
        "master_as_of_date": start.isoformat(),
        "master_snapshot_revision": "MASTER-R1",
        "demand_run_id": "DEMAND-GOLDEN",
        "configuration_revision": "CONFIG-GOLDEN-R1",
        "business_timezone": "Asia/Seoul",
        "inventory_cutoff_at": cutoff,
        "inventory_source_watermark": "WATERMARK-" + case["id"],
    }
    kinds = (
        "calendar",
        "master",
        "forecast",
        "customer_orders",
        "inventory",
        "prior_inventory",
        "receipts",
        "policies",
    )
    snapshots = {
        kind: {
            "snapshot_id": case["id"] + ":" + kind,
            "status": "SEALED",
            "content_hash": "0" * 64,
            "row_count": 0,
            "metadata": dict(scope),
            "rows": [],
        }
        for kind in kinds
    }
    snapshots["master"]["metadata"]["master_snapshot_revision"] = "MASTER-R1"
    snapshots["forecast"]["metadata"].update(
        demand_run_id="DEMAND-GOLDEN",
        demand_run_status="SUCCEEDED",
        evidence_status="VERIFIED",
        calendar_snapshot_id=snapshots["calendar"]["snapshot_id"],
        calendar_content_hash="0" * 64,
        master_snapshot_revision="MASTER-R1",
        master_content_hash="0" * 64,
    )
    snapshots["customer_orders"]["metadata"]["coverage"] = "COMPLETE"
    snapshots["receipts"]["metadata"].update(
        coverage="COMPLETE", simulation_run_id=simulation_id, generator_version=generator
    )
    snapshots["prior_inventory"]["metadata"].update(
        as_of_date=(start - timedelta(days=1)).isoformat(),
        position_source_type=source,
        simulation_run_id=simulation_id,
        generator_version=generator,
    )
    snapshots["inventory"]["metadata"].update(
        position_source_type=source,
        business_timezone="Asia/Seoul",
        cutoff_at=cutoff,
        source_watermark=context["inventory_source_watermark"],
        complete_through=utc(w0 + timedelta(hours=2)),
        extracted_at=utc(w0 + timedelta(hours=3)),
        sealed_at=utc(w0 + timedelta(hours=4)),
        prior_snapshot_id=snapshots["prior_inventory"]["snapshot_id"],
        prior_content_hash="0" * 64,
        reserved_treatment="UNAVAILABLE_EXCLUDED",
        simulation_run_id=simulation_id,
        generator_version=generator,
        adjustments=[],
        source_events=[],
    )
    for index, week in enumerate(weeks):
        begin = start + timedelta(days=index * 7)
        snapshots["calendar"]["rows"].append(
            {
                "yyyyww": week,
                "seq": index,
                "start_date": begin.isoformat(),
                "end_date": (begin + timedelta(days=6)).isoformat(),
                "base_month": (begin + timedelta(days=2)).strftime("%Y%m"),
            }
        )
    for item in case["items"]:
        fields = {**scope, "item_id": item["id"], "uom": item.get("uom", "EA")}
        boh, reserved = item["boh"], item.get("reserved", "0")
        snapshots["master"]["rows"].append({**fields, "active": True, "stock_managed": True})
        snapshots["inventory"]["rows"].append(
            {
                **fields,
                "position_date": start.isoformat(),
                "on_hand_qty": boh,
                "reserved_qty": reserved,
                "available_qty": str(Decimal(boh) - Decimal(reserved)),
                "backorder_qty": item.get("backorder", "0"),
            }
        )
        snapshots["prior_inventory"]["rows"].append({**fields, "eoh_qty": item.get("prior", boh)})
        snapshots["policies"]["rows"].append(
            {
                **fields,
                "policy_id": "POLICY-" + item["id"],
                "effective_from": start.isoformat(),
                "effective_to": (end + timedelta(days=1)).isoformat(),
                "lead_time_days": 7,
                "moq": "0",
                "order_multiple": "1",
                "physical_max_capacity": None,
                "approved_service_level": "0.95",
                "source_target_inventory_qty": item.get("target", "100"),
                "source_rop_qty": "20",
                "policy_source_type": "SYNTHETIC_POLICY" if synthetic else "SOURCE_MASTER",
                "source_semantics_cd": "TGSM_SEGMENTATION_MAX_QTY"
                if context["plan_type"] == "TGSM"
                else "SOURCE_TARGET_FOR_COMPARISON",
            }
        )
        for index, week in enumerate(weeks):
            snapshots["forecast"]["rows"].append(
                {
                    **fields,
                    "yyyyww": week,
                    "forecast_qty": item["forecast"][index],
                    "forecast_netting_mode": item.get("mode", "SAME_BUCKET_CONSUMPTION"),
                    "upstream_gross_forecast_qty": None,
                    "upstream_consumed_qty": None,
                }
            )
            order = item.get("orders", ["0", "0", "0"])[index]
            if order != "0":
                snapshots["customer_orders"]["rows"].append(
                    {**fields, "yyyyww": week, "confirmed_customer_order_qty": order}
                )
        for index, receipt in enumerate(item.get("receipts", [])):
            snapshots["receipts"]["rows"].append(
                {
                    **fields,
                    "receipt_id": item["id"] + ":RECEIPT:" + str(index),
                    "due_date": (start + timedelta(days=receipt["week"] * 7)).isoformat(),
                    "due_qty": receipt["qty"],
                    "status": receipt["status"],
                    "supply_type": "SYNTHETIC_PURCHASE_ORDER" if synthetic else "PURCHASE_ORDER",
                }
            )
        if "adjustment" in item:
            adjustment = {
                **fields,
                "adjustment_id": "ADJ-" + item["id"],
                "quantity": item["adjustment"],
                "reason": "Approved test stock count correction",
                "approved_by": "GOLDEN_REVIEWER",
                "approved_at": utc(w0 + timedelta(hours=1)),
                "source_document_id": "COUNT-" + item["id"],
            }
            adjustment["event_hash"] = sha(adjustment)
            snapshots["inventory"]["metadata"]["adjustments"].append(adjustment)
    uoms = sorted({item.get("uom", "EA") for item in case["items"]})
    request = {
        "contract_id": "io-canonical-input-v1",
        "contract_version": "1.0.0",
        "context": context,
        "quantity_rules": [
            {
                "uom": uom,
                "scale": 0 if uom == "EA" else 3,
                "tolerance_qty": "0" if uom == "EA" else "0.001",
                "approval_reference": "GOLDEN-UOM-R1",
            }
            for uom in uoms
        ],
        "input_bindings": {},
        "snapshots": snapshots,
    }
    return reseal(request)


def golden_case(case_id: str = "normal_consumption") -> dict:
    return next(case for case in GOLDEN["cases"] if case["id"] == case_id)

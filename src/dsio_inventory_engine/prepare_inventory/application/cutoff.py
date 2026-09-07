"""Verify closing-stock continuity and source completeness before PSI admission."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.values import digest, quantity_text, require

from .validation import check_uom, same_scope, unique


def instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def verify_cutoff(data: dict, master: dict, rules: dict, deployment: DeploymentScope) -> list[dict]:
    context, snapshots = data["context"], data["snapshots"]
    inventory, prior = snapshots["inventory"], snapshots["prior_inventory"]
    meta, prior_meta = inventory["metadata"], prior["metadata"]
    require(
        meta["prior_snapshot_id"] == prior["snapshot_id"]
        and meta["prior_content_hash"] == prior["content_hash"],
        "PRIOR_SNAPSHOT_MISMATCH",
    )
    previous_day = date.fromisoformat(context["plan_start_date"]) - timedelta(days=1)
    require(prior_meta["as_of_date"] == previous_day.isoformat(), "PRIOR_PERIOD_MISMATCH")
    require(
        meta["business_timezone"] == context["business_timezone"]
        and meta["cutoff_at"] == context["inventory_cutoff_at"]
        and meta["source_watermark"] == context["inventory_source_watermark"],
        "CUTOFF_BINDING_MISMATCH",
    )
    source = meta["position_source_type"]
    require(source != "POLICY_PROXY", "POLICY_PROXY_NOT_BASELINE")
    require(prior_meta["position_source_type"] == source, "PRIOR_SOURCE_MISMATCH")
    cut, complete, extracted, sealed = (
        instant(meta[k]) for k in ("cutoff_at", "complete_through", "extracted_at", "sealed_at")
    )
    w0 = datetime.combine(
        date.fromisoformat(context["plan_start_date"]),
        time.min,
        ZoneInfo(context["business_timezone"]),
    )
    require(cut <= w0 and complete <= extracted <= sealed, "INVALID_SEAL_TIMELINE")
    if source == "SYNTHETIC_BOH":
        require(deployment.environment == "DEVELOPMENT", "SYNTHETIC_BOH_FORBIDDEN")
        require(
            meta["simulation_run_id"] is not None and meta["generator_version"] is not None,
            "SYNTHETIC_LINEAGE_REQUIRED",
        )
        require(
            all(prior_meta[k] == meta[k] for k in ("simulation_run_id", "generator_version")),
            "SYNTHETIC_LINEAGE_MISMATCH",
        )
        require(not meta["source_events"], "SYNTHETIC_ERP_EVENTS_FORBIDDEN")
    else:
        require(
            all(
                meta[k] is None and prior_meta[k] is None
                for k in ("simulation_run_id", "generator_version")
            ),
            "ACTUAL_SOURCE_LINEAGE_MISMATCH",
        )
    # Synthetic watermarks are generator completion, not ERP completion; both must cover W0.
    require(cut <= complete, "SOURCE_WATERMARK_INCOMPLETE")
    evidence = validate_events(meta, context, master, rules, cut, extracted)
    adjustments = {item: Decimal(0) for item in master}
    unique(meta["adjustments"], ("adjustment_id",), "DUPLICATE_ADJUSTMENT")
    for row in meta["adjustments"]:
        same_scope(row, context)
        check_uom(row, master, rules, ("quantity",))
        require(
            row["event_hash"] == digest({k: v for k, v in row.items() if k != "event_hash"}),
            "ADJUSTMENT_HASH_MISMATCH",
        )
        require(instant(row["approved_at"]) <= sealed, "ADJUSTMENT_APPROVAL_AFTER_SEAL")
        adjustments[row["item_id"]] += Decimal(row["quantity"])
    previous = {row["item_id"]: Decimal(row["eoh_qty"]) for row in prior["rows"]}
    failed = False
    for row in inventory["rows"]:
        item = row["item_id"]
        require(row["position_date"] == context["plan_start_date"], "POSITION_DATE_MISMATCH")
        on_hand, reserved, available = (
            Decimal(row[k]) for k in ("on_hand_qty", "reserved_qty", "available_qty")
        )
        require(on_hand - reserved == available, "AVAILABLE_ON_HAND_RECONCILIATION_FAILED")
        require(
            reserved == 0 or meta["reserved_treatment"] == "UNAVAILABLE_EXCLUDED",
            "RESERVED_SEMANTICS_UNSUPPORTED",
        )
        expected = previous[item] + adjustments[item]
        require(expected >= 0, "NEGATIVE_EXPECTED_BOH")
        variance = on_hand - expected
        tolerance = Decimal(rules[row["uom"]]["tolerance_qty"])
        passed = abs(variance) <= tolerance
        failed |= not passed
        evidence.append(
            {
                "reconciliation_type": "EOH_BOH",
                "item_id": item,
                "uom": row["uom"],
                "previous_actual_eoh_qty": quantity_text(previous[item]),
                "approved_adjustment_qty": quantity_text(adjustments[item]),
                "expected_boh_qty": quantity_text(expected),
                "reported_boh_qty": row["on_hand_qty"],
                "variance_qty": quantity_text(variance),
                "tolerance_qty": quantity_text(tolerance),
                "status": "PASS" if passed else "FAIL",
                "prior_snapshot_id": prior["snapshot_id"],
                "source_document_count": len(
                    {a["source_document_id"] for a in meta["adjustments"] if a["item_id"] == item}
                ),
            }
        )
    require(not failed, "EOH_BOH_RECONCILIATION_FAILED", evidence)
    return evidence


def validate_events(
    meta: dict, context: dict, master: dict, rules: dict, cut: datetime, extracted: datetime
) -> list[dict]:
    unique(meta["source_events"], ("event_id",), "DUPLICATE_SOURCE_EVENT")
    evidence = []
    for row in meta["source_events"]:
        same_scope(row, context)
        check_uom(row, master, rules, ("quantity",))
        require(
            row["event_hash"] == digest({k: v for k, v in row.items() if k != "event_hash"}),
            "MOVEMENT_HASH_MISMATCH",
        )
        business, posted, ingested = (
            instant(row[k]) for k in ("business_occurred_at", "erp_posted_at", "ingested_at")
        )
        require(business <= posted <= ingested <= extracted, "INVALID_EVENT_TIMELINE")
        if business <= cut and posted > cut:
            evidence.append(
                {
                    "reconciliation_type": "LATE_POSTING",
                    "event_id": row["event_id"],
                    "item_id": row["item_id"],
                    "quantity": row["quantity"],
                    "status": "FAIL",
                    "action": "NEW_SNAPSHOT_AND_CYCLE_REVISION_REQUIRED",
                }
            )
    require(not evidence, "LATE_POSTING_DETECTED", evidence)
    return [
        {
            "reconciliation_type": "SOURCE_CUTOFF",
            "status": "PASS",
            "late_event_count": 0,
            "source_watermark": meta["source_watermark"],
            "source_event_count": len(meta["source_events"]),
        }
    ]

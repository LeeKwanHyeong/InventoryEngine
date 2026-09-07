"""Prepare sealed Canonical snapshots for a single-site Baseline PSI."""

import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, localcontext

from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.values import (
    canonical_json,
    digest,
    quantity_text,
    require,
)

from .cutoff import verify_cutoff
from .validation import validate_calendar, validate_universe, verify_snapshots


@dataclass(frozen=True)
class PreparedInventoryInput:
    document_json: str

    def to_dict(self) -> dict:
        return json.loads(self.document_json)


class PrepareInventoryInputUseCase:
    def __init__(self, deployment: DeploymentScope):
        self.deployment = deployment

    def execute(self, request: CanonicalInputRequest) -> PreparedInventoryInput:
        # Revalidate even if a caller constructed the dataclass directly.
        request = CanonicalInputRequest.from_dict(request.to_dict())
        data = request.to_dict()
        with localcontext() as ctx:
            ctx.prec = 40
            verify_snapshots(data, self.deployment)
            calendar = validate_calendar(data)
            master, rules = validate_universe(data, calendar, self.deployment)
            reconciliation = verify_cutoff(data, master, rules, self.deployment)
            demands = prepare_demand(data)
            receipt_decisions = prepare_receipts(data, calendar, self.deployment)
        content = {
            "context": data["context"],
            "calendar": calendar,
            "master": [master[item] for item in sorted(master)],
            "positions": data["snapshots"]["inventory"]["rows"],
            "policies": data["snapshots"]["policies"]["rows"],
            "demands": demands,
            "receipt_decisions": receipt_decisions,
            "reconciliation": reconciliation,
            "canonical_snapshots": data["snapshots"],
        }
        manifest = {
            "engine_run_id": data["context"]["engine_run_id"],
            "input_content_hash": request.input_hash,
            "input_bindings": data["input_bindings"],
            "configuration_revision": data["context"]["configuration_revision"],
            "quantity_rules": data["quantity_rules"],
            "deployment_environment": self.deployment.environment,
            "input_admission_status": "INPUT_SEALS_VERIFIED_LOCALLY",
            "persistence_status": "PREPARED_IN_MEMORY",
            "reconciliation_hash": digest(reconciliation),
            "receipt_decisions_hash": digest(receipt_decisions),
        }
        return PreparedInventoryInput(canonical_json({"manifest": manifest, **content}))


def prepare_demand(data: dict) -> list[dict]:
    snapshots = data["snapshots"]
    orders = {
        (r["item_id"], r["yyyyww"]): Decimal(r["confirmed_customer_order_qty"])
        for r in snapshots["customer_orders"]["rows"]
    }
    result = []
    for row in snapshots["forecast"]["rows"]:
        confirmed = orders.get((row["item_id"], row["yyyyww"]), Decimal(0))
        qty = Decimal(row["forecast_qty"])
        gross: Decimal | None
        consumed: Decimal | None
        if row["forecast_netting_mode"] == "SAME_BUCKET_CONSUMPTION":
            require(
                row["upstream_gross_forecast_qty"] is None and row["upstream_consumed_qty"] is None,
                "AMBIGUOUS_FORECAST_FIELDS",
            )
            gross, consumed = qty, min(qty, confirmed)
            net = qty - consumed
        else:
            gross_raw, consumed_raw = (
                row["upstream_gross_forecast_qty"],
                row["upstream_consumed_qty"],
            )
            require(
                (gross_raw is None) == (consumed_raw is None),
                "INCOMPLETE_UPSTREAM_NETTING_EVIDENCE",
            )
            gross = None if gross_raw is None else Decimal(gross_raw)
            consumed = None if consumed_raw is None else Decimal(consumed_raw)
            net = qty
            if gross is not None and consumed is not None:
                require(gross >= consumed and gross - consumed == net, "UPSTREAM_NETTING_MISMATCH")
        result.append(
            {
                "item_id": row["item_id"],
                "uom": row["uom"],
                "yyyyww": row["yyyyww"],
                "gross_forecast_qty": None if gross is None else quantity_text(gross),
                "forecast_consumed_qty": None if consumed is None else quantity_text(consumed),
                "net_forecast_qty": quantity_text(net),
                "confirmed_customer_order_qty": quantity_text(confirmed),
                "forecast_netting_mode": row["forecast_netting_mode"],
                "source_snapshot_id": snapshots["forecast"]["snapshot_id"],
                "source_content_hash": snapshots["forecast"]["content_hash"],
            }
        )
    return sorted(result, key=lambda row: (row["item_id"], row["yyyyww"]))


def prepare_receipts(data: dict, calendar: list[dict], deployment: DeploymentScope) -> list[dict]:
    dates = {}
    for bucket in calendar:
        start, end = (
            date.fromisoformat(bucket["start_date"]),
            date.fromisoformat(bucket["end_date"]),
        )
        for offset in range((end - start).days + 1):
            dates[(start + timedelta(days=offset)).isoformat()] = bucket["yyyyww"]
    snapshots = data["snapshots"]
    inv_meta, receipt_meta = snapshots["inventory"]["metadata"], snapshots["receipts"]["metadata"]
    excluded = {
        "PLANNED": "SUPPLY_NOT_COMMITTED",
        "ORDERED": "VENDOR_COMMITMENT_NOT_VERIFIED",
        "UNVERIFIED_DUE_IN": "VENDOR_COMMITMENT_NOT_VERIFIED",
        "RECEIVED": "ALREADY_INCLUDED_IN_BOH",
        "CANCELLED": "SUPPLY_CANCELLED",
        "LEGACY_ASSUMED_CONFIRMED": "LEGACY_ONLY_SUPPLY",
    }
    result = []
    for row in snapshots["receipts"]["rows"]:
        if row["supply_type"] == "SYNTHETIC_PURCHASE_ORDER":
            require(
                deployment.environment == "DEVELOPMENT"
                and inv_meta["position_source_type"] == "SYNTHETIC_BOH",
                "SYNTHETIC_SUPPLY_FORBIDDEN",
            )
            require(
                all(
                    receipt_meta[k] == inv_meta[k]
                    for k in ("simulation_run_id", "generator_version")
                ),
                "SYNTHETIC_SUPPLY_LINEAGE_MISMATCH",
            )
        week = dates.get(row["due_date"])
        reason = excluded.get(row["status"])
        if reason is None and week is None:
            require(
                row["due_date"] >= data["context"]["plan_start_date"],
                "OVERDUE_CONFIRMED_RECEIPT",
                [
                    {
                        "receipt_id": row["receipt_id"],
                        "due_date": row["due_date"],
                        "action": "REVISED_COMMITTED_DUE_DATE_REQUIRED",
                    }
                ],
            )
            reason = "OUTSIDE_PLAN_HORIZON"
        result.append(
            {
                **row,
                "yyyyww": week,
                "included_qty": row["due_qty"] if reason is None else "0",
                "exclusion_reason": reason,
            }
        )
    return sorted(result, key=lambda row: row["receipt_id"])

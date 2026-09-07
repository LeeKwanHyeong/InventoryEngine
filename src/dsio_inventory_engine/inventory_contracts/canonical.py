"""Versioned Canonical snapshots. No source queries or implicit defaults."""

from dataclasses import dataclass
from typing import Any, Callable

from .values import (
    boolean,
    canonical_json,
    choice,
    day,
    decimal_string,
    digest,
    hash_value,
    identifier,
    integer,
    optional,
    records,
    read_json,
    require,
    shape,
    text,
    timestamp,
    yyyyww,
)

CONTRACT_ID = "io-canonical-input-v1"
CONTRACT_VERSION = "1.0.0"
V2_ID = "io-canonical-input-v2"
V2_VERSION = "2.0.0"
V2_MAX_BYTES = 128_000_000
SCOPE = {"company_cd": identifier, "subs_cd": identifier, "site_cd": identifier}
ITEM = {**SCOPE, "item_id": identifier, "uom": identifier}
SOURCE_TYPE = choice("ACTUAL_BOH", "SYNTHETIC_BOH", "POLICY_PROXY")


def signed(value: Any) -> str:
    return decimal_string(value, signed=True)


ADJUSTMENT = {
    **ITEM,
    "adjustment_id": identifier,
    "quantity": signed,
    "reason": text,
    "approved_by": identifier,
    "approved_at": timestamp,
    "source_document_id": identifier,
    "event_hash": hash_value,
}
MOVEMENT = {
    **ITEM,
    "event_id": identifier,
    "quantity": signed,
    "business_occurred_at": timestamp,
    "erp_posted_at": timestamp,
    "ingested_at": timestamp,
    "source_document_id": identifier,
    "event_hash": hash_value,
}
ROW_FIELDS: dict[str, dict[str, Callable]] = {
    "calendar": {
        "yyyyww": yyyyww,
        "seq": integer,
        "start_date": day,
        "end_date": day,
        "base_month": identifier,
    },
    "master": {**ITEM, "active": boolean, "stock_managed": boolean},
    "forecast": {
        **ITEM,
        "yyyyww": yyyyww,
        "forecast_qty": decimal_string,
        "forecast_netting_mode": choice("SAME_BUCKET_CONSUMPTION", "UPSTREAM_NETTED"),
        "upstream_gross_forecast_qty": optional(decimal_string),
        "upstream_consumed_qty": optional(decimal_string),
    },
    "customer_orders": {**ITEM, "yyyyww": yyyyww, "confirmed_customer_order_qty": decimal_string},
    "inventory": {
        **ITEM,
        "position_date": day,
        "on_hand_qty": decimal_string,
        "reserved_qty": decimal_string,
        "available_qty": decimal_string,
        "backorder_qty": decimal_string,
    },
    "prior_inventory": {**ITEM, "eoh_qty": decimal_string},
    "receipts": {
        **ITEM,
        "receipt_id": identifier,
        "due_date": day,
        "due_qty": decimal_string,
        "status": choice(
            "PLANNED",
            "ORDERED",
            "CONFIRMED",
            "IN_TRANSIT",
            "RECEIVED",
            "CANCELLED",
            "UNVERIFIED_DUE_IN",
            "LEGACY_ASSUMED_CONFIRMED",
        ),
        "supply_type": choice("PURCHASE_ORDER", "SYNTHETIC_PURCHASE_ORDER"),
    },
    "policies": {
        **ITEM,
        "policy_id": identifier,
        "effective_from": day,
        "effective_to": day,
        "lead_time_days": integer,
        "moq": decimal_string,
        "order_multiple": decimal_string,
        "physical_max_capacity": optional(decimal_string),
        "approved_service_level": decimal_string,
        "source_target_inventory_qty": optional(decimal_string),
        "source_rop_qty": optional(decimal_string),
        "policy_source_type": choice("SOURCE_MASTER", "SYNTHETIC_POLICY"),
        "source_semantics_cd": identifier,
    },
}
METADATA_FIELDS: dict[str, dict[str, Callable]] = {
    "calendar": SCOPE,
    "master": {**SCOPE, "master_snapshot_revision": identifier},
    "forecast": {
        **SCOPE,
        "demand_run_id": identifier,
        "demand_run_status": choice("SUCCEEDED", "FAILED", "RUNNING"),
        "evidence_status": choice("VERIFIED", "UNVERIFIED"),
        "calendar_snapshot_id": identifier,
        "calendar_content_hash": hash_value,
        "master_snapshot_revision": identifier,
        "master_content_hash": hash_value,
    },
    "customer_orders": {**SCOPE, "coverage": choice("COMPLETE", "UNAVAILABLE")},
    "inventory": {
        **SCOPE,
        "position_source_type": SOURCE_TYPE,
        "business_timezone": text,
        "cutoff_at": timestamp,
        "source_watermark": text,
        "complete_through": timestamp,
        "extracted_at": timestamp,
        "sealed_at": timestamp,
        "prior_snapshot_id": identifier,
        "prior_content_hash": hash_value,
        "reserved_treatment": choice("UNAVAILABLE_EXCLUDED", "CUSTOMER_ORDER_ALLOCATED", "UNKNOWN"),
        "simulation_run_id": optional(identifier),
        "generator_version": optional(identifier),
        "adjustments": lambda value: records(value, ADJUSTMENT),
        "source_events": lambda value: records(value, MOVEMENT),
    },
    "prior_inventory": {
        **SCOPE,
        "as_of_date": day,
        "position_source_type": SOURCE_TYPE,
        "simulation_run_id": optional(identifier),
        "generator_version": optional(identifier),
    },
    "receipts": {
        **SCOPE,
        "coverage": choice("COMPLETE", "UNAVAILABLE"),
        "simulation_run_id": optional(identifier),
        "generator_version": optional(identifier),
    },
    "policies": SCOPE,
}
CONTEXT_FIELDS = {
    **SCOPE,
    "engine_run_id": identifier,
    "planning_cycle_id": identifier,
    "planning_cycle_revision_id": identifier,
    "cycle_site_execution_id": identifier,
    "plan_type": choice("POSM", "TGSM"),
    "plan_yyyyww": yyyyww,
    "plan_start_date": day,
    "plan_end_date": day,
    "master_as_of_date": day,
    "master_snapshot_revision": identifier,
    "demand_run_id": identifier,
    "configuration_revision": identifier,
    "business_timezone": text,
    "inventory_cutoff_at": timestamp,
    "inventory_source_watermark": text,
}


def read_canonical_envelope(document: str, path: tuple[str, ...]) -> dict:
    """V2 payloads keep their explicit byte budget through strategy envelopes.

    No expansion of v1 or unrelated training/model JSON contracts is implied.
    """
    result = read_json(document, max_bytes=V2_MAX_BYTES)
    nested = result
    for key in path:
        nested = nested.get(key, {}) if isinstance(nested, dict) else {}
    v2 = nested.get("contract_id") == V2_ID and nested.get("contract_version") == V2_VERSION
    require(len(document.encode()) <= (V2_MAX_BYTES if v2 else 8_000_000), "INPUT_SIZE_LIMIT")
    return result


def snapshot_content(kind: str, snapshot: dict) -> dict:
    """Hash includes identity and all content; status/count are checked separately."""
    return {
        "snapshot_type": kind,
        "snapshot_id": snapshot["snapshot_id"],
        "metadata": snapshot["metadata"],
        "rows": snapshot["rows"],
    }


def normalize_snapshot(kind: str, value: Any, *, row_limit: int = 100_000) -> dict:
    result = shape(
        value,
        {
            "snapshot_id": identifier,
            "content_hash": hash_value,
            "status": choice(
                "COLLECTING", "CUT_OFF_REACHED", "RECONCILING", "SEALED", "REJECTED", "SUPERSEDED"
            ),
            "row_count": integer,
            "metadata": lambda meta: shape(meta, METADATA_FIELDS[kind]),
            "rows": lambda rows: records(rows, ROW_FIELDS[kind], limit=row_limit),
        },
    )
    result["rows"].sort(key=canonical_json)
    if kind == "inventory":
        for name in ("adjustments", "source_events"):
            result["metadata"][name].sort(key=canonical_json)
    return result


@dataclass(frozen=True)
class CanonicalInputRequest:
    """Immutable, validated wire structure; admission is a separate operation."""

    document_json: str

    @classmethod
    def from_dict(cls, value: dict) -> "CanonicalInputRequest":
        v2 = value.get("contract_id") == V2_ID
        rule_fields = {
            "uom": identifier,
            "tolerance_qty": decimal_string,
            "approval_reference": identifier,
            **(
                {"planning_scale": integer, "physical_scale": integer} if v2 else {"scale": integer}
            ),
        }

        def snapshots(data: Any) -> dict:
            require(type(data) is dict and set(data) == set(ROW_FIELDS), "SNAPSHOT_SET")
            return {
                kind: normalize_snapshot(kind, data[kind], row_limit=200_000 if v2 else 100_000)
                for kind in ROW_FIELDS
            }

        def bindings(data: Any) -> dict:
            require(type(data) is dict and set(data) == set(ROW_FIELDS), "INPUT_BINDINGS_REQUIRED")
            return {
                kind: shape(data[kind], {"snapshot_id": identifier, "content_hash": hash_value})
                for kind in ROW_FIELDS
            }

        result = shape(
            value,
            {
                "contract_id": choice(V2_ID if v2 else CONTRACT_ID),
                "contract_version": choice(V2_VERSION if v2 else CONTRACT_VERSION),
                "context": lambda data: shape(data, CONTEXT_FIELDS),
                "quantity_rules": lambda data: records(
                    data,
                    rule_fields,
                    limit=100,
                ),
                "input_bindings": bindings,
                "snapshots": snapshots,
            },
        )
        result["quantity_rules"].sort(key=lambda row: row["uom"])
        document = canonical_json(result)
        require(len(document.encode()) <= (V2_MAX_BYTES if v2 else 8_000_000), "INPUT_SIZE_LIMIT")
        return cls(document)

    def to_dict(self) -> dict:
        result = read_json(self.document_json, max_bytes=V2_MAX_BYTES)
        if result.get("contract_id") != V2_ID:
            require(len(self.document_json.encode()) <= 8_000_000, "INPUT_SIZE_LIMIT")
        return result

    @property
    def input_hash(self) -> str:
        value = self.to_dict()
        del value["context"]["engine_run_id"]
        return digest(value)

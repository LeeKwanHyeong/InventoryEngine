"""Additive, sealed policy/history input. Canonical v1 and v1.0 strategies stay valid."""

from dataclasses import dataclass
from typing import Any, Callable

from .canonical import SCOPE, read_canonical_envelope
from .replenishment import RecommendationRequest
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
    records,
    require,
    shape,
    text,
    timestamp,
    yyyyww,
)

MATH_DESCRIPTOR = {
    "strategy_type": "MATHEMATICAL",
    "implementation_id": "historical-normal-r-s",
    "version": "1.0.0",
    "model": None,
}
PROFILE_FIELDS = {
    "profile_id": identifier,
    "revision": identifier,
    "formula": choice("HISTORICAL_NORMAL_R_S_V1"),
    "history_mode": choice("FROZEN_PRE_W0"),
    "lookback_weeks": integer,
    "stddev_ddof": integer,
    "replenishment_cycle_weeks": integer,
    "service_level_metric": choice("CYCLE_SERVICE_LEVEL"),
    "lead_time_mapping": choice("CEIL_CALENDAR_WEEKS"),
    "allow_legacy_fallback": boolean,
    "approval_reference": identifier,
}
HISTORY_CALENDAR_FIELDS = {"yyyyww": yyyyww, "start_date": day, "end_date": day}
HISTORY_FIELDS = {
    "item_id": identifier,
    "uom": identifier,
    "yyyyww": yyyyww,
    "demand_qty": decimal_string,
}
POLICY_VALUE_FIELDS = {
    "safety_stock_qty": decimal_string,
    "rop_qty": decimal_string,
    "target_inventory_qty": decimal_string,
}
ADJUSTMENT_FIELDS = {
    "adjustment_id": identifier,
    "item_id": identifier,
    "uom": identifier,
    "kind": choice("APPROVED_OVERRIDE", "SOURCE_FALLBACK", "LEGACY_FALLBACK"),
    "effective_from": day,
    "effective_to": day,
    "values": lambda v: shape(v, POLICY_VALUE_FIELDS),
    "reason": text,
    "approved_by": identifier,
    "approved_at": timestamp,
    "approval_reference": identifier,
    "source_document_id": identifier,
}
INPUT_CONTEXT_FIELDS = {
    **SCOPE,
    "canonical_input_hash": hash_value,
    "configuration_revision": identifier,
    "as_of_date": day,
    "available_at": timestamp,
    "history_source_type": choice("OBSERVED_DEMAND", "SYNTHETIC_DEMAND"),
    "quantity_semantics": choice("UNCENSORED_DEMAND", "FULFILLED_SALES"),
}
POLICY_INPUT_FIELDS: dict[str, Callable[..., Any]] = {
    "contract_id": choice("io-mathematical-policy-input-v1"),
    "contract_version": choice("1.0.0"),
    "snapshot_id": identifier,
    "content_hash": hash_value,
    "status": choice("SEALED", "COLLECTING", "REJECTED"),
    "history_row_count": integer,
    "context": lambda v: shape(v, INPUT_CONTEXT_FIELDS),
    "profile": lambda v: shape(v, PROFILE_FIELDS),
    "calendar": lambda v: records(v, HISTORY_CALENDAR_FIELDS, limit=26),
    "history": lambda v: records(v, HISTORY_FIELDS),
    "adjustments": lambda v: records(v, ADJUSTMENT_FIELDS, limit=10_000),
}


def policy_input_content(snapshot: dict) -> dict:
    return {
        k: v
        for k, v in snapshot.items()
        if k not in ("content_hash", "status", "history_row_count")
    }


def policy_input_binding(snapshot: dict) -> dict:
    return {
        k: snapshot[k] for k in ("contract_id", "contract_version", "snapshot_id", "content_hash")
    }


def normalize_policy_input(value: dict) -> dict:
    result = shape(value, POLICY_INPUT_FIELDS)
    for key in ("calendar", "history", "adjustments"):
        result[key].sort(key=canonical_json)
    return result


@dataclass(frozen=True)
class MathematicalPolicyRequest:
    document_json: str

    @classmethod
    def from_dict(cls, value: dict) -> "MathematicalPolicyRequest":
        result = shape(
            value,
            {
                "recommendation": lambda v: RecommendationRequest.from_dict(v).to_dict(),
                "policy_input": normalize_policy_input,
            },
        )
        execution = result["recommendation"]["execution"]
        snapshot = result["policy_input"]
        require(execution["contract_version"] == "1.1.0", "MATH_REQUIRES_BOUND_EXECUTION")
        require(execution["strategy"] == MATH_DESCRIPTOR, "MATH_STRATEGY_BINDING_MISMATCH")
        require(
            execution["strategy_input_binding"] == policy_input_binding(snapshot),
            "POLICY_INPUT_BINDING_MISMATCH",
        )
        require(
            snapshot["content_hash"] == digest(policy_input_content(snapshot)),
            "POLICY_INPUT_HASH_MISMATCH",
        )
        return cls(canonical_json(result))

    def to_dict(self) -> dict:
        return read_canonical_envelope(self.document_json, ("recommendation", "canonical_input"))

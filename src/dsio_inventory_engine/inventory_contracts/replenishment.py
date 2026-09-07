"""Three strategy families share one immutable local execution/decision contract.

Approval references and model hashes are pinned evidence, not authentication or a
model loader. Only trusted, explicitly injected adapters execute; no dynamic imports.
Canonical v1 remains unchanged. This extension is development-only until Run binding.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Protocol

from .canonical import CanonicalInputRequest, read_canonical_envelope
from .values import (
    canonical_json,
    choice,
    day,
    decimal_string,
    digest,
    hash_value,
    identifier,
    optional,
    read_json,
    records,
    require,
    shape,
)

STRATEGY_TYPES = ("MATHEMATICAL", "PREDICTIVE_ML", "DEEP_RL")
MODEL_FIELDS = {"model_id": identifier, "version": identifier, "content_hash": hash_value}
DESCRIPTOR_FIELDS = {
    "strategy_type": choice(*STRATEGY_TYPES),
    "implementation_id": identifier,
    "version": identifier,
    "model": optional(lambda v: shape(v, MODEL_FIELDS)),
}


def descriptor(value: dict) -> dict:
    result = shape(value, DESCRIPTOR_FIELDS)
    require(
        (result["model"] is None) == (result["strategy_type"] == "MATHEMATICAL"),
        "STRATEGY_MODEL_BINDING_REQUIRED",
    )
    return result


def codes(value: list) -> list[str]:
    require(type(value) is list and 0 < len(value) <= 20, "DECISION_REASONS_REQUIRED")
    result = [identifier(v) for v in value]
    require(len(set(result)) == len(result), "DUPLICATE_REASON")
    return result


def order_dates(value: list) -> list[str]:
    require(type(value) is list and len(value) <= 520, "ORDER_DATE_LIMIT")
    result = [day(v) for v in value]
    require(len(set(result)) == len(result), "DUPLICATE_ORDER_DATE")
    return sorted(result)


def action_types(value: list) -> list[str]:
    require(type(value) is list and 1 <= len(value) <= 3, "INVALID_ACTION_ALLOWLIST")
    result = [choice("HOLD", "ORDER_QTY", "ORDER_UP_TO")(v) for v in value]
    require(len(set(result)) == len(result) and "HOLD" in result, "INVALID_ACTION_ALLOWLIST")
    return sorted(result)


CONTROL_FIELDS = {
    "item_id": identifier,
    "uom": identifier,
    "max_order_qty": decimal_string,
    "min_target_qty": decimal_string,
    "max_target_qty": decimal_string,
    "order_dates": order_dates,
}
EXECUTION_FIELDS: dict[str, Callable[..., Any]] = {
    "contract_id": choice("io-replenishment-v1"),
    "contract_version": choice("1.0.0"),
    "psi_scenario_type": choice("RECOMMENDED"),
    "execution_mode": choice("LOCAL_SHADOW"),
    "configuration_revision": identifier,
    "canonical_input_hash": hash_value,
    "strategy": descriptor,
    "approval_reference": identifier,
    "allowed_action_types": action_types,
    "decision_timing": choice("BUCKET_START_BEFORE_RECEIPTS"),
    "receipt_mapping": choice("NEXT_BUCKET_START_ON_OR_AFTER_DUE_DATE"),
    "capacity_mode": choice("CONSERVATIVE_NO_DEMAND_CREDIT"),
    "item_controls": lambda v: records(v, CONTROL_FIELDS, limit=10_000),
}
STRATEGY_INPUT_BINDING_FIELDS = {
    "contract_id": identifier,
    "contract_version": identifier,
    "snapshot_id": identifier,
    "content_hash": hash_value,
}
EXECUTION_FIELDS_V1_1 = {
    **EXECUTION_FIELDS,
    "contract_version": choice("1.1.0"),
    "strategy_input_binding": lambda v: shape(v, STRATEGY_INPUT_BINDING_FIELDS),
}


def execution_config(value: dict) -> dict:
    require(type(value) is dict, "CONTRACT_FIELDS")
    fields = EXECUTION_FIELDS_V1_1 if value.get("contract_version") == "1.1.0" else EXECUTION_FIELDS
    return shape(value, fields)


POLICY_FIELDS = {
    "safety_stock_qty": optional(decimal_string),
    "rop_qty": optional(decimal_string),
    "target_inventory_qty": optional(decimal_string),
}
PROPOSAL_FIELDS: dict[str, Callable[..., Any]] = {
    "contract_id": choice("io-replenishment-decision-v1"),
    "decision_id": identifier,
    "observation_hash": hash_value,
    "strategy": descriptor,
    "action_type": choice("HOLD", "ORDER_QTY", "ORDER_UP_TO"),
    "quantity": decimal_string,
    "calculated_policy": lambda v: shape(v, POLICY_FIELDS),
    "reason_codes": codes,
}


@dataclass(frozen=True)
class RecommendationRequest:
    document_json: str

    @classmethod
    def from_dict(cls, value: dict) -> "RecommendationRequest":
        value = shape(
            value,
            {
                "canonical_input": lambda v: CanonicalInputRequest.from_dict(v).to_dict(),
                "execution": execution_config,
            },
        )
        canonical = CanonicalInputRequest.from_dict(value["canonical_input"])
        execution = value["execution"]
        require(
            execution["canonical_input_hash"] == canonical.input_hash,
            "RECOMMENDATION_INPUT_BINDING_MISMATCH",
        )
        require(
            execution["configuration_revision"]
            == value["canonical_input"]["context"]["configuration_revision"],
            "RECOMMENDATION_CONFIGURATION_MISMATCH",
        )
        controls = execution["item_controls"]
        require(len({r["item_id"] for r in controls}) == len(controls), "DUPLICATE_ITEM_CONTROL")
        for row in controls:
            require(
                Decimal(row["min_target_qty"]) <= Decimal(row["max_target_qty"]),
                "INVALID_TARGET_BOUNDS",
            )
        controls.sort(key=lambda r: r["item_id"])
        return cls(canonical_json(value))

    def to_dict(self) -> dict:
        return read_canonical_envelope(self.document_json, ("canonical_input",))

    @property
    def input_hash(self) -> str:
        value = self.to_dict()
        # Canonical hash already binds everything except the physical attempt ID.
        return digest(value["execution"])


@dataclass(frozen=True)
class ReplenishmentObservation:
    """Engine-produced view, detached from mutable simulation state on every read."""

    document_json: str

    def to_dict(self) -> dict:
        return read_json(self.document_json)

    @property
    def content_hash(self) -> str:
        return digest(self.to_dict())


@dataclass(frozen=True)
class ReplenishmentProposal:
    document_json: str

    @classmethod
    def from_dict(cls, value: dict) -> "ReplenishmentProposal":
        value = shape(value, PROPOSAL_FIELDS)
        require(
            value["action_type"] != "HOLD" or Decimal(value["quantity"]) == 0,
            "HOLD_QUANTITY_MUST_BE_ZERO",
        )
        return cls(canonical_json(value))

    def to_dict(self) -> dict:
        return read_json(self.document_json)


class ReplenishmentStrategy(Protocol):
    """Inference only. Training, approved model loading and fallback selection are separate."""

    @property
    def descriptor(self) -> dict: ...

    def decide(self, observation: ReplenishmentObservation) -> ReplenishmentProposal: ...


class BoundReplenishmentStrategy(ReplenishmentStrategy, Protocol):
    """v1.1 adapters bind additional immutable strategy inputs, not only model versions."""

    @property
    def input_binding(self) -> dict: ...

"""Three strategy families share one immutable local execution/decision contract.

Approval references and model hashes are pinned evidence, not authentication or a
model loader. Only trusted, explicitly injected adapters execute; no dynamic imports.
Canonical v1 remains unchanged. This extension is development-only until Run binding.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Mapping, Protocol

from .canonical import CanonicalInputRequest, read_canonical_envelope
from .effective_policy_v2 import (
    EFFECTIVE_POLICY_V2_FIELDS,
    EFFECTIVE_POLICY_V2_CONTRACT_VERSION,
    effective_policy_content_hash,
    validate_effective_item_policy_v2,
)
from .values import (
    canonical_json,
    choice,
    day,
    decimal_string,
    digest,
    hash_value,
    identifier,
    item_identifier,
    optional,
    read_json,
    records,
    require,
    shape,
)

STRATEGY_TYPES = ("MATHEMATICAL", "PREDICTIVE_ML", "DEEP_RL")
MODEL_FIELDS = {"model_id": identifier, "version": identifier, "content_hash": hash_value}
MODEL_APPROVAL_FIELDS = {
    "approval_reference": identifier,
    "status": choice("APPROVED"),
    **MODEL_FIELDS,
}
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
    "item_id": item_identifier,
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
EFFECTIVE_POLICY_RUN_BINDING_FIELDS = {
    "contract_id": choice("inventory-effective-policy-run-binding-v2"),
    "contract_version": choice(EFFECTIVE_POLICY_V2_CONTRACT_VERSION),
    "classification_snapshot_id": identifier,
    "classification_config_hash": hash_value,
    "effective_policy_content_hash": hash_value,
}
EXECUTION_FIELDS_V1_1 = {
    **EXECUTION_FIELDS,
    "contract_version": choice("1.1.0"),
    "strategy_input_binding": lambda v: shape(v, STRATEGY_INPUT_BINDING_FIELDS),
}
EXECUTION_FIELDS_V2 = {
    **EXECUTION_FIELDS,
    "contract_version": choice(EFFECTIVE_POLICY_V2_CONTRACT_VERSION),
    "execution_mode": choice("LOCAL_SHADOW", "PLATFORM_BOUND"),
    "strategy_input_binding": lambda v: shape(v, STRATEGY_INPUT_BINDING_FIELDS),
    "execution_purpose": choice("OPERATIONAL", "SHADOW"),
    "model_approval": optional(lambda v: shape(v, MODEL_APPROVAL_FIELDS)),
    "effective_policy_binding": lambda v: shape(v, EFFECTIVE_POLICY_RUN_BINDING_FIELDS),
    "effective_item_policies": lambda v: _effective_item_policies(v),
}


def _effective_item_policies(value: Any) -> list[dict[str, Any]]:
    require(type(value) is list and 0 < len(value) <= 10_000, "EFFECTIVE_POLICY_SET_INVALID")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        require(isinstance(item, Mapping), "EFFECTIVE_POLICY_SET_INVALID")
        require(
            set(item) == {"item_id", *EFFECTIVE_POLICY_V2_FIELDS},
            "EFFECTIVE_POLICY_SET_INVALID",
        )
        item_id = item_identifier(item.get("item_id"))
        require(item_id not in seen, "EFFECTIVE_POLICY_ITEM_DUPLICATE")
        seen.add(item_id)
        result.append(
            {
                "item_id": item_id,
                **validate_effective_item_policy_v2(item),
            }
        )
    return sorted(result, key=lambda item: item["item_id"])


def execution_config(value: dict) -> dict:
    require(type(value) is dict, "CONTRACT_FIELDS")
    if value.get("contract_version") == EFFECTIVE_POLICY_V2_CONTRACT_VERSION:
        fields = EXECUTION_FIELDS_V2
    elif value.get("contract_version") == "1.1.0":
        fields = EXECUTION_FIELDS_V1_1
    else:
        fields = EXECUTION_FIELDS
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
        if execution["contract_version"] == EFFECTIVE_POLICY_V2_CONTRACT_VERSION:
            strategy = execution["strategy"]
            model_approval = execution["model_approval"]
            require(
                (
                    execution["execution_purpose"] == "OPERATIONAL"
                    and strategy["strategy_type"] == "MATHEMATICAL"
                    and model_approval is None
                )
                or (
                    execution["execution_purpose"] == "SHADOW"
                    and (
                        (strategy["strategy_type"] == "MATHEMATICAL" and model_approval is None)
                        or (
                            strategy["strategy_type"] != "MATHEMATICAL"
                            and model_approval is not None
                            and {
                                key: model_approval[key]
                                for key in ("model_id", "version", "content_hash")
                            }
                            == strategy["model"]
                        )
                    )
                ),
                "REPLENISHMENT_MODEL_APPROVAL_BINDING_MISMATCH",
            )
            policies = execution["effective_item_policies"]
            policy_by_item = {row["item_id"]: row for row in policies}
            require(
                set(policy_by_item) == {row["item_id"] for row in controls},
                "EFFECTIVE_POLICY_UNIVERSE_MISMATCH",
            )
            binding = execution["effective_policy_binding"]
            require(
                all(
                    row["classification_config_hash"] == binding["classification_config_hash"]
                    for row in policies
                ),
                "EFFECTIVE_POLICY_CONFIG_HASH_MISMATCH",
            )
            require(
                effective_policy_content_hash(policies) == binding["effective_policy_content_hash"],
                "EFFECTIVE_POLICY_CONTENT_HASH_MISMATCH",
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

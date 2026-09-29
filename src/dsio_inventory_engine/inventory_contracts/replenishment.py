"""Three strategy families share one immutable local execution/decision contract.

Approval references and model hashes are pinned evidence, not authentication or a
model loader. Only trusted, explicitly injected adapters execute; no dynamic imports.
Canonical v1 remains unchanged. This extension is development-only until Run binding.
"""

from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Any, Callable, Mapping, Protocol

from .canonical import (
    CONTEXT_FIELDS,
    ROW_FIELDS,
    CanonicalInputRequest,
    read_canonical_envelope,
)
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
    integer,
    item_identifier,
    optional,
    read_json,
    records,
    require,
    shape,
    yyyyww,
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


def replenishment_config_content_hash(execution: dict) -> str:
    """Hash the Run policy/config surface without circular canonical bindings.

    The canonical input hash is verified independently against the Runtime Claim,
    and the mathematical policy snapshot is an explicit Strategy Plan binding.
    Every remaining execution control is sealed here.
    """

    normalized = execution_config(execution)
    return digest(
        {
            key: value
            for key, value in normalized.items()
            if key not in {"canonical_input_hash", "strategy_input_binding"}
        }
    )


POLICY_FIELDS = {
    "safety_stock_qty": optional(decimal_string),
    "rop_qty": optional(decimal_string),
    "target_inventory_qty": optional(decimal_string),
}

OBSERVATION_BUCKET_FIELDS = {
    "yyyyww": yyyyww,
    "seq": integer,
    "start_date": day,
    "end_date": day,
    "base_month": identifier,
}
OBSERVATION_DEMAND_FIELDS = {
    "item_id": item_identifier,
    "uom": identifier,
    "yyyyww": yyyyww,
    "gross_forecast_qty": optional(decimal_string),
    "forecast_consumed_qty": optional(decimal_string),
    "net_forecast_qty": decimal_string,
    "confirmed_customer_order_qty": decimal_string,
    "forecast_netting_mode": choice("SAME_BUCKET_CONSUMPTION", "UPSTREAM_NETTED"),
    "source_snapshot_id": identifier,
    "source_content_hash": hash_value,
}
OBSERVATION_STATE_FIELDS = {
    "available_qty": decimal_string,
    "reserved_qty": decimal_string,
    "on_hand_qty": decimal_string,
    "backorder_qty": decimal_string,
    "inventory_position_qty": lambda value: decimal_string(value, signed=True),
}
OBSERVATION_PENDING_SUPPLY_FIELDS = {
    "supply_id": identifier,
    "supply_kind": choice("CONFIRMED", "RECOMMENDED"),
    "due_date": day,
    "receipt_bucket": yyyyww,
    "quantity": decimal_string,
}
_OBSERVATION_POLICY_V2_FIELDS = {
    "classification_effective_policy_hash": hash_value,
    "classification_config_hash": hash_value,
    "source_approved_service_level": decimal_string,
    "effective_review_cycle_weeks": integer,
}
_OBSERVATION_POLICY_LEAD_TIME_FIELDS = {
    "effective_protection_lead_time_basis": choice("P50", "P90"),
    "effective_protection_lead_time_days": integer,
}


def _observation_quantity_rule(value: Any) -> dict[str, Any]:
    require(type(value) is dict, "OBSERVATION_QUANTITY_RULE_INVALID")
    common = {
        "uom": identifier,
        "tolerance_qty": decimal_string,
        "approval_reference": identifier,
    }
    fields = (
        {**common, "scale": integer}
        if "scale" in value
        else {**common, "planning_scale": integer, "physical_scale": integer}
    )
    result = shape(value, fields)
    planning = result.get("planning_scale", result.get("scale"))
    physical = result.get("physical_scale", result.get("scale"))
    require(0 <= physical <= planning <= 6, "OBSERVATION_QUANTITY_RULE_INVALID")
    return result


def _observation_policy(value: Any) -> dict[str, Any]:
    require(type(value) is dict, "OBSERVATION_POLICY_INVALID")
    base = ROW_FIELDS["policies"]
    extra_keys = set(value) - set(base)
    allowed_extras = (
        set() | set(_OBSERVATION_POLICY_V2_FIELDS) | set(_OBSERVATION_POLICY_LEAD_TIME_FIELDS)
    )
    require(not (extra_keys - allowed_extras), "OBSERVATION_POLICY_INVALID")
    if extra_keys:
        require(
            set(_OBSERVATION_POLICY_V2_FIELDS) <= extra_keys
            and (
                not (extra_keys & set(_OBSERVATION_POLICY_LEAD_TIME_FIELDS))
                or set(_OBSERVATION_POLICY_LEAD_TIME_FIELDS) <= extra_keys
            ),
            "OBSERVATION_POLICY_INVALID",
        )
    fields = {
        **base,
        **(
            {
                key: _OBSERVATION_POLICY_V2_FIELDS[key]
                for key in extra_keys
                if key in _OBSERVATION_POLICY_V2_FIELDS
            }
        ),
        **(
            {
                key: _OBSERVATION_POLICY_LEAD_TIME_FIELDS[key]
                for key in extra_keys
                if key in _OBSERVATION_POLICY_LEAD_TIME_FIELDS
            }
        ),
    }
    return shape(value, fields)


def _observation_document(value: Any) -> dict[str, Any]:
    require(type(value) is dict, "OBSERVATION_CONTRACT_INVALID")
    base_fields = {
        "contract_id": choice("io-replenishment-observation-v1"),
        "decision_id": identifier,
        "input_content_hash": hash_value,
        "context": lambda item: shape(item, CONTEXT_FIELDS),
        "item_id": item_identifier,
        "uom": identifier,
        "decision_date": day,
        "bucket": lambda item: shape(item, OBSERVATION_BUCKET_FIELDS),
        "calendar": lambda item: records(item, OBSERVATION_BUCKET_FIELDS, limit=520),
        "future_demand": lambda item: records(item, OBSERVATION_DEMAND_FIELDS, limit=520),
        "policy": _observation_policy,
        "policy_schedule": lambda item: _observation_policies(item),
        "control": lambda item: shape(item, CONTROL_FIELDS),
        "quantity_rule": _observation_quantity_rule,
        "state": lambda item: shape(item, OBSERVATION_STATE_FIELDS),
        "pending_supply": lambda item: records(
            item, OBSERVATION_PENDING_SUPPLY_FIELDS, limit=10_000
        ),
        "strategy": descriptor,
        "approval_reference": identifier,
        "allowed_action_types": action_types,
        "capacity_mode": choice("CONSERVATIVE_NO_DEMAND_CREDIT"),
    }
    if "strategy_input_binding" in value:
        base_fields["strategy_input_binding"] = lambda item: shape(
            item, STRATEGY_INPUT_BINDING_FIELDS
        )
    result = shape(value, base_fields)
    _validate_observation_semantics(result)
    return result


def _observation_policies(value: Any) -> list[dict[str, Any]]:
    require(type(value) is list and 0 < len(value) <= 520, "OBSERVATION_POLICY_SCHEDULE_INVALID")
    return [_observation_policy(row) for row in value]


def _validate_observation_semantics(value: Mapping[str, Any]) -> None:
    calendar = value["calendar"]
    demands = value["future_demand"]
    require(
        calendar
        and calendar[0] == value["bucket"]
        and value["decision_date"] == value["bucket"]["start_date"],
        "OBSERVATION_BUCKET_BINDING_MISMATCH",
    )
    require(
        len(calendar) == len(demands)
        and all(
            demand["item_id"] == value["item_id"]
            and demand["uom"] == value["uom"]
            and demand["yyyyww"] == bucket["yyyyww"]
            for demand, bucket in zip(demands, calendar, strict=True)
        ),
        "OBSERVATION_DEMAND_BINDING_MISMATCH",
    )
    require(
        value["control"]["item_id"] == value["item_id"]
        and value["control"]["uom"] == value["uom"]
        and value["quantity_rule"]["uom"] == value["uom"],
        "OBSERVATION_ITEM_BINDING_MISMATCH",
    )
    policy = value["policy"]
    applicable = [
        row
        for row in value["policy_schedule"]
        if row["effective_from"] <= value["decision_date"] < row["effective_to"]
    ]
    require(
        len(applicable) == 1
        and applicable[0] == policy
        and policy["item_id"] == value["item_id"]
        and policy["uom"] == value["uom"]
        and all(
            policy[key] == value["context"][key] for key in ("company_cd", "subs_cd", "site_cd")
        ),
        "OBSERVATION_POLICY_BINDING_MISMATCH",
    )
    require(
        len({row["supply_id"] for row in value["pending_supply"]}) == len(value["pending_supply"]),
        "OBSERVATION_PENDING_SUPPLY_DUPLICATE",
    )
    with localcontext() as context:
        context.prec = 40
        state = value["state"]
        available = Decimal(state["available_qty"])
        reserved = Decimal(state["reserved_qty"])
        backorder = Decimal(state["backorder_qty"])
        pending = sum((Decimal(row["quantity"]) for row in value["pending_supply"]), Decimal(0))
        require(
            Decimal(state["on_hand_qty"]) == available + reserved
            and Decimal(state["inventory_position_qty"]) == available + pending - backorder,
            "OBSERVATION_STATE_CONSERVATION_INVALID",
        )


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

    @classmethod
    def from_dict(cls, value: dict) -> "ReplenishmentObservation":
        """Normalize the complete decision-time view as a closed contract."""

        return cls(canonical_json(_observation_document(value)))

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

"""Admit deterministic item policy before strategy-specific replenishment execution."""

from __future__ import annotations

from typing import Any, Mapping

from dsio_inventory_engine.inventory_contracts.classification import (
    validate_effective_item_policy,
)
from dsio_inventory_engine.inventory_contracts.effective_policy_v2 import (
    EFFECTIVE_POLICY_V2_CONTRACT_VERSION,
    validate_effective_item_policy_v2,
)
from dsio_inventory_engine.inventory_contracts.replenishment import (
    MODEL_APPROVAL_FIELDS,
    descriptor,
)
from dsio_inventory_engine.inventory_contracts.values import (
    choice,
    hash_value,
    require,
    shape,
)


def admit_effective_item_policy(
    item: Mapping[str, Any],
    *,
    config_hash: str,
    execution_purpose: str,
    strategy_descriptor: Mapping[str, Any],
    model_approval: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind one item policy to an operational or approved-model shadow strategy."""

    purpose = choice("OPERATIONAL", "SHADOW")(execution_purpose)
    hash_value(config_hash)
    policy_version = choice("1.0.0", EFFECTIVE_POLICY_V2_CONTRACT_VERSION)(
        item.get("effective_policy_contract_version", "1.0.0")
    )
    if policy_version == EFFECTIVE_POLICY_V2_CONTRACT_VERSION:
        policy = validate_effective_item_policy_v2(item)
        require(
            policy["classification_config_hash"] == config_hash,
            "ITEM_POLICY_CONFIG_HASH_MISMATCH",
        )
    else:
        policy = validate_effective_item_policy(item, config_hash=config_hash)
    require(policy["effective_strategy"] is not None, "ITEM_POLICY_UNCLASSIFIED")
    bound_strategy = descriptor(dict(strategy_descriptor))
    require(
        policy["effective_strategy"] == bound_strategy["strategy_type"],
        "ITEM_POLICY_STRATEGY_MISMATCH",
    )
    if purpose == "OPERATIONAL":
        require(policy["operational_io_eligible"], "ITEM_POLICY_NOT_OPERATIONALLY_ELIGIBLE")
        require(
            bound_strategy["strategy_type"] == "MATHEMATICAL" and bound_strategy["model"] is None,
            "ITEM_POLICY_OPERATIONAL_STRATEGY_BLOCKED",
        )
        require(model_approval is None, "ITEM_POLICY_UNEXPECTED_MODEL_APPROVAL")
    else:
        if bound_strategy["strategy_type"] == "MATHEMATICAL":
            require(model_approval is None, "ITEM_POLICY_UNEXPECTED_MODEL_APPROVAL")
        else:
            require(model_approval is not None, "ITEM_POLICY_SHADOW_MODEL_APPROVAL_REQUIRED")
            approval = shape(dict(model_approval), MODEL_APPROVAL_FIELDS)
            require(
                {
                    "model_id": approval["model_id"],
                    "version": approval["version"],
                    "content_hash": approval["content_hash"],
                }
                == bound_strategy["model"],
                "ITEM_POLICY_MODEL_APPROVAL_MISMATCH",
            )
    admitted = {
        "item_id": item["item_id"],
        "execution_purpose": purpose,
        "effective_target_service_level": policy["effective_target_service_level"],
        "effective_review_cycle_weeks": policy["effective_review_cycle_weeks"],
        "effective_strategy": policy["effective_strategy"],
        "effective_policy_hash": policy["effective_policy_hash"],
        "strategy": bound_strategy,
        "model_approval_reference": (
            model_approval["approval_reference"] if model_approval is not None else None
        ),
    }
    if policy_version == EFFECTIVE_POLICY_V2_CONTRACT_VERSION:
        require(
            purpose != "OPERATIONAL" or policy["recommendation_calculation_allowed"],
            "ITEM_POLICY_RECOMMENDATION_CALCULATION_BLOCKED",
        )
        admitted.update(
            {
                "effective_policy_contract_version": policy_version,
                "classification_config_hash": policy["classification_config_hash"],
                "classification_config_binding_hash": policy["classification_config_binding_hash"],
                "policy_adjustment_reasons": policy["policy_adjustment_reasons"],
                "policy_gate_reason_codes": policy["policy_gate_reason_codes"],
                "effective_order_action": policy["effective_order_action"],
                "effective_protection_lead_time_basis": policy[
                    "effective_protection_lead_time_basis"
                ],
                "effective_protection_lead_time_days": policy[
                    "effective_protection_lead_time_days"
                ],
                "effective_approval_level": policy["effective_approval_level"],
                "recommendation_calculation_allowed": policy["recommendation_calculation_allowed"],
                "evidence_storage_allowed": policy["evidence_storage_allowed"],
                "order_execution_allowed": policy["order_execution_allowed"],
                "no_order_gate": policy["no_order_gate"],
                "automatic_publish_allowed": policy["automatic_publish_allowed"],
                "automatic_order_allowed": policy["automatic_order_allowed"],
            }
        )
    return admitted

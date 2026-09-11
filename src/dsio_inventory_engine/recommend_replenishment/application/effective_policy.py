"""Admit deterministic item policy before strategy-specific replenishment execution."""

from __future__ import annotations

from typing import Any, Mapping

from dsio_inventory_engine.inventory_contracts.classification import (
    validate_effective_item_policy,
)
from dsio_inventory_engine.inventory_contracts.replenishment import descriptor
from dsio_inventory_engine.inventory_contracts.values import choice, require


def admit_effective_item_policy(
    item: Mapping[str, Any],
    *,
    config_hash: str,
    execution_purpose: str,
    strategy_descriptor: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind one item policy to an operational or approved-model shadow strategy."""

    purpose = choice("OPERATIONAL", "SHADOW")(execution_purpose)
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
    else:
        require(
            bound_strategy["strategy_type"] == "MATHEMATICAL"
            or bound_strategy["model"] is not None,
            "ITEM_POLICY_SHADOW_MODEL_REQUIRED",
        )
    return {
        "item_id": item["item_id"],
        "execution_purpose": purpose,
        "effective_target_service_level": policy["effective_target_service_level"],
        "effective_review_cycle_weeks": policy["effective_review_cycle_weeks"],
        "effective_strategy": policy["effective_strategy"],
        "effective_policy_hash": policy["effective_policy_hash"],
        "strategy": bound_strategy,
    }

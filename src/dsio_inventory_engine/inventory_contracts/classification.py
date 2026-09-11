"""Deterministic item-level policy derived from ABC-XYZ-VED classification."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from .values import (
    boolean,
    choice,
    decimal_string,
    digest,
    hash_value,
    identifier,
    integer,
    optional,
    require,
    shape,
)


EFFECTIVE_POLICY_CONTRACT_VERSION = "1.0.0"
STRATEGY_TYPES = ("MATHEMATICAL", "PREDICTIVE_ML", "DEEP_RL")
POLICY_SOURCES = (
    "ABC_XYZ_POLICY_MATRIX",
    "ABC_XYZ_POLICY_MATRIX_WITH_VED_FLOOR",
    "UNCLASSIFIED",
)
POLICY_ADJUSTMENT_REASONS = ("VED_SERVICE_LEVEL_FLOOR_APPLIED",)
EFFECTIVE_POLICY_FIELDS = {
    "effective_target_service_level": optional(decimal_string),
    "effective_review_cycle_weeks": optional(integer),
    "effective_strategy": optional(choice(*STRATEGY_TYPES)),
    "policy_source": choice(*POLICY_SOURCES),
    "policy_adjustment_reason": optional(choice(*POLICY_ADJUSTMENT_REASONS)),
    "operational_io_eligible": boolean,
    "effective_policy_hash": hash_value,
}


def derive_effective_item_policy(
    *,
    item_id: str,
    classification_status: str,
    segment_key: str | None,
    ved_class: str | None,
    unclassified_reason_code: str | None,
    policy_cell: Mapping[str, Any] | None,
    ved_service_level_floor: Mapping[str, Any],
    config_hash: str,
) -> dict[str, Any]:
    """Apply one approved matrix cell without invoking a model or optimizer."""

    identifier(item_id)
    hash_value(config_hash)
    require(classification_status in {"CLASSIFIED", "UNCLASSIFIED"}, "ITEM_POLICY_STATUS")
    if classification_status == "UNCLASSIFIED":
        require(
            segment_key is None and policy_cell is None and unclassified_reason_code is not None,
            "ITEM_POLICY_CLASSIFICATION_MISMATCH",
        )
        body = {
            "effective_target_service_level": None,
            "effective_review_cycle_weeks": None,
            "effective_strategy": None,
            "policy_source": "UNCLASSIFIED",
            "policy_adjustment_reason": None,
            "operational_io_eligible": False,
        }
    else:
        require(
            segment_key is not None
            and policy_cell is not None
            and unclassified_reason_code is None,
            "ITEM_POLICY_CLASSIFICATION_MISMATCH",
        )
        require(policy_cell.get("segment_key") == segment_key, "ITEM_POLICY_SEGMENT_MISMATCH")
        base_level = Decimal(str(policy_cell["target_service_level"]))
        floor = Decimal(str(ved_service_level_floor[ved_class])) if ved_class is not None else None
        floor_applied = floor is not None and floor > base_level
        strategy = policy_cell["strategy"]
        body = {
            "effective_target_service_level": _decimal_text(floor if floor_applied else base_level),
            "effective_review_cycle_weeks": policy_cell["review_cycle_weeks"],
            "effective_strategy": strategy,
            "policy_source": (
                "ABC_XYZ_POLICY_MATRIX_WITH_VED_FLOOR" if floor_applied else "ABC_XYZ_POLICY_MATRIX"
            ),
            "policy_adjustment_reason": (
                "VED_SERVICE_LEVEL_FLOOR_APPLIED" if floor_applied else None
            ),
            "operational_io_eligible": strategy == "MATHEMATICAL",
        }
    normalized = shape({**body, "effective_policy_hash": "0" * 64}, EFFECTIVE_POLICY_FIELDS)
    normalized["effective_policy_hash"] = _policy_digest(
        config_hash=config_hash,
        item_id=item_id,
        classification_status=classification_status,
        segment_key=segment_key,
        ved_class=ved_class,
        unclassified_reason_code=unclassified_reason_code,
        policy=body,
    )
    return normalized


def validate_effective_item_policy(item: Mapping[str, Any], *, config_hash: str) -> dict[str, Any]:
    """Validate a persisted item policy and its self-contained deterministic hash."""

    hash_value(config_hash)
    require(
        all(
            key in item
            for key in (
                "item_id",
                "classification_status",
                "segment_key",
                "ved_class",
                "unclassified_reason_code",
                *EFFECTIVE_POLICY_FIELDS,
            )
        ),
        "ITEM_POLICY_FIELDS_MISSING",
    )
    identifier(item["item_id"])
    require(
        item["classification_status"] in {"CLASSIFIED", "UNCLASSIFIED"}
        and (
            item["segment_key"] is None
            or item["segment_key"] in {"AX", "AY", "AZ", "BX", "BY", "BZ", "CX", "CY", "CZ"}
        )
        and (item["ved_class"] is None or item["ved_class"] in {"V", "E", "D"}),
        "ITEM_POLICY_CLASSIFICATION_MISMATCH",
    )
    normalized = shape(
        {key: item.get(key) for key in EFFECTIVE_POLICY_FIELDS},
        EFFECTIVE_POLICY_FIELDS,
    )
    status = item["classification_status"]
    if status == "CLASSIFIED":
        require(
            normalized["effective_target_service_level"] is not None
            and Decimal("0.5")
            <= Decimal(normalized["effective_target_service_level"])
            <= Decimal("0.9999")
            and normalized["effective_review_cycle_weeks"] is not None
            and 1 <= normalized["effective_review_cycle_weeks"] <= 13
            and normalized["effective_strategy"] is not None
            and normalized["policy_source"] != "UNCLASSIFIED"
            and normalized["operational_io_eligible"]
            == (normalized["effective_strategy"] == "MATHEMATICAL")
            and (normalized["policy_source"] == "ABC_XYZ_POLICY_MATRIX_WITH_VED_FLOOR")
            == (normalized["policy_adjustment_reason"] == "VED_SERVICE_LEVEL_FLOOR_APPLIED"),
            "ITEM_POLICY_SEMANTICS_INVALID",
        )
    else:
        require(
            status == "UNCLASSIFIED"
            and isinstance(item["unclassified_reason_code"], str)
            and bool(item["unclassified_reason_code"])
            and normalized
            == {
                "effective_target_service_level": None,
                "effective_review_cycle_weeks": None,
                "effective_strategy": None,
                "policy_source": "UNCLASSIFIED",
                "policy_adjustment_reason": None,
                "operational_io_eligible": False,
                "effective_policy_hash": normalized["effective_policy_hash"],
            },
            "ITEM_POLICY_SEMANTICS_INVALID",
        )
    expected_hash = _policy_digest(
        config_hash=config_hash,
        item_id=item["item_id"],
        classification_status=status,
        segment_key=item["segment_key"],
        ved_class=item["ved_class"],
        unclassified_reason_code=item["unclassified_reason_code"],
        policy={
            key: normalized[key]
            for key in EFFECTIVE_POLICY_FIELDS
            if key != "effective_policy_hash"
        },
    )
    require(normalized["effective_policy_hash"] == expected_hash, "ITEM_POLICY_HASH_MISMATCH")
    return normalized


def _decimal_text(value: Decimal) -> str:
    return "0" if value == 0 else format(value.normalize(), "f")


def _policy_digest(
    *,
    config_hash: str,
    item_id: str,
    classification_status: str,
    segment_key: str | None,
    ved_class: str | None,
    unclassified_reason_code: str | None,
    policy: Mapping[str, Any],
) -> str:
    return digest(
        {
            "contract_version": EFFECTIVE_POLICY_CONTRACT_VERSION,
            "config_hash": config_hash,
            "item_id": item_id,
            "classification_status": classification_status,
            "segment_key": segment_key,
            "ved_class": ved_class,
            "unclassified_reason_code": unclassified_reason_code,
            **policy,
        }
    )

"""Effective item policy derived from operational seven-axis results.

The effective hash deliberately excludes the full configuration document and all
SHADOW/disabled axis results.  Those inputs remain part of the classification
snapshot evidence, while this contract seals only values that can change an
operational replenishment decision.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from .values import (
    InventoryInputError,
    boolean,
    choice,
    decimal_string,
    digest,
    hash_value,
    identifier,
    item_identifier,
    integer,
    optional,
    require,
    shape,
)


EFFECTIVE_POLICY_V2_CONTRACT_VERSION = "2.0.0"
AXIS_ORDER = ("ABC", "XYZ", "VED", "FSN", "SDE", "HML", "PLC")
AXIS_STATUSES = (
    "CLASSIFIED",
    "UNCLASSIFIED",
    "UNVERIFIED",
    "SYNTHETIC",
    "NOT_APPLICABLE",
)
ORDER_ACTIONS = ("ALLOW", "REVIEW", "BLOCK")
LEAD_TIME_BASES = ("P50", "P90")
APPROVAL_LEVELS = ("AUTO", "STANDARD", "HIGH_VALUE")
POLICY_SOURCES = (
    "ABC_XYZ_POLICY_MATRIX",
    "ABC_XYZ_POLICY_MATRIX_WITH_VED_FLOOR",
    "UNCLASSIFIED",
)


def _codes(value: Any) -> list[str]:
    require(type(value) is list and len(value) <= 16, "ITEM_POLICY_REASON_CODES_INVALID")
    result = [identifier(code) for code in value]
    require(len(result) == len(set(result)), "ITEM_POLICY_REASON_CODES_INVALID")
    return result


def _projection(value: Any) -> list[dict[str, Any]]:
    require(type(value) is list and len(value) <= len(AXIS_ORDER), "ITEM_POLICY_AXIS_PROJECTION")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    previous_index = -1
    for row in value:
        require(
            type(row) is dict
            and set(row) == {"axis", "class_code", "source_contract_key", "policy_effect"},
            "ITEM_POLICY_AXIS_PROJECTION",
        )
        axis = choice(*AXIS_ORDER)(row["axis"])
        require(axis not in seen, "ITEM_POLICY_AXIS_PROJECTION")
        index = AXIS_ORDER.index(axis)
        require(index > previous_index, "ITEM_POLICY_AXIS_PROJECTION")
        previous_index = index
        seen.add(axis)
        class_code = identifier(row["class_code"])
        source_contract_key = identifier(row["source_contract_key"])
        effect = _projection_effect(axis, row["policy_effect"])
        result.append(
            {
                "axis": axis,
                "class_code": class_code,
                "source_contract_key": source_contract_key,
                "policy_effect": effect,
            }
        )
    return result


EFFECTIVE_POLICY_V2_FIELDS = {
    "effective_policy_contract_version": choice(EFFECTIVE_POLICY_V2_CONTRACT_VERSION),
    "classification_config_hash": hash_value,
    "classification_config_binding_hash": hash_value,
    "effective_target_service_level": optional(decimal_string),
    "effective_review_cycle_weeks": optional(integer),
    "effective_strategy": optional(choice("MATHEMATICAL", "PREDICTIVE_ML", "DEEP_RL")),
    "policy_source": choice(*POLICY_SOURCES),
    "policy_adjustment_reason": optional(choice("VED_SERVICE_LEVEL_FLOOR_APPLIED")),
    "policy_adjustment_reasons": _codes,
    "policy_gate_reason_codes": _codes,
    "operational_io_eligible": boolean,
    "recommendation_calculation_allowed": boolean,
    "evidence_storage_allowed": boolean,
    "effective_order_action": choice(*ORDER_ACTIONS),
    "order_execution_allowed": boolean,
    "no_order_gate": boolean,
    "effective_protection_lead_time_basis": optional(choice(*LEAD_TIME_BASES)),
    "effective_protection_lead_time_days": optional(decimal_string),
    "effective_approval_level": optional(choice(*APPROVAL_LEVELS)),
    "automatic_publish_allowed": boolean,
    "automatic_order_allowed": boolean,
    "effective_axis_projection": _projection,
    "effective_policy_hash": hash_value,
}


def derive_effective_item_policy_v2(
    *,
    item_id: str,
    classification_config_hash: str,
    classification_status: str,
    segment_key: str | None,
    unclassified_reason_code: str | None,
    policy_cell: Mapping[str, Any] | None,
    ved_service_level_floor: Mapping[str, Any],
    axis_results: Sequence[Mapping[str, Any]],
    policy_overlays: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive one item policy without binding SHADOW axes or a full config hash."""

    item_identifier(item_id)
    hash_value(classification_config_hash)
    require(classification_status in {"CLASSIFIED", "UNCLASSIFIED"}, "ITEM_POLICY_STATUS")
    axes = _axis_results(axis_results)
    effective_axes = [
        axes[axis]
        for axis in AXIS_ORDER
        if axes[axis]["application_mode"] == "OPERATIONAL" and axes[axis]["status"] == "CLASSIFIED"
    ]
    unavailable_operational_axes = [
        (axis, axes[axis]["status"])
        for axis in AXIS_ORDER
        if axes[axis]["application_mode"] == "OPERATIONAL" and axes[axis]["status"] != "CLASSIFIED"
    ]

    body = _base_policy(
        classification_status=classification_status,
        segment_key=segment_key,
        unclassified_reason_code=unclassified_reason_code,
        policy_cell=policy_cell,
        ved_service_level_floor=ved_service_level_floor,
        effective_axes=effective_axes,
    )
    projection = _build_projection(effective_axes, policy_overlays)
    projected = {row["axis"]: row for row in projection}

    configured_actions = [
        projected[axis]["policy_effect"]["order_action"]
        for axis in ("FSN", "PLC")
        if axis in projected
    ]
    effective_order_action = _strictest_action(configured_actions)
    strategy_is_operational = body["effective_strategy"] == "MATHEMATICAL"
    eligible = (
        classification_status == "CLASSIFIED"
        and not unavailable_operational_axes
        and strategy_is_operational
    )
    if not eligible:
        effective_order_action = "BLOCK"

    sde_effect = projected.get("SDE", {}).get("policy_effect", {})
    hml_effect = projected.get("HML", {}).get("policy_effect", {})
    lead_time_basis = sde_effect.get("lead_time_basis")
    lead_time_days = sde_effect.get("lead_time_days")
    approval_level = hml_effect.get("approval_level")
    calculation_allowed = eligible and effective_order_action != "BLOCK"
    automatic_publish_allowed = eligible and effective_order_action == "ALLOW"
    automatic_order_allowed = automatic_publish_allowed and approval_level in {None, "AUTO"}
    reasons = _adjustment_reasons(projection, body["policy_adjustment_reason"])
    gate_reasons = _gate_reason_codes(
        unavailable_operational_axes=unavailable_operational_axes,
        classification_status=classification_status,
        strategy=body["effective_strategy"],
        order_action=effective_order_action,
        approval_level=approval_level,
    )

    policy = {
        "effective_policy_contract_version": EFFECTIVE_POLICY_V2_CONTRACT_VERSION,
        "classification_config_hash": classification_config_hash,
        **body,
        "policy_adjustment_reasons": reasons,
        "policy_gate_reason_codes": gate_reasons,
        "operational_io_eligible": eligible,
        "recommendation_calculation_allowed": calculation_allowed,
        "evidence_storage_allowed": True,
        "effective_order_action": effective_order_action,
        "order_execution_allowed": automatic_order_allowed,
        "no_order_gate": effective_order_action == "BLOCK",
        "effective_protection_lead_time_basis": lead_time_basis,
        "effective_protection_lead_time_days": lead_time_days,
        "effective_approval_level": approval_level,
        "automatic_publish_allowed": automatic_publish_allowed,
        "automatic_order_allowed": automatic_order_allowed,
        "effective_axis_projection": projection,
    }
    effective_policy_hash = _policy_digest(item_id=item_id, policy=policy)
    return shape(
        {
            **policy,
            "classification_config_binding_hash": _config_binding_digest(
                item_id=item_id,
                classification_config_hash=classification_config_hash,
                effective_policy_hash=effective_policy_hash,
            ),
            "effective_policy_hash": effective_policy_hash,
        },
        EFFECTIVE_POLICY_V2_FIELDS,
    )


def validate_effective_item_policy_v2(item: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a persisted V2 policy and its effective-only deterministic hash."""

    require(
        all(key in item for key in ("item_id", *EFFECTIVE_POLICY_V2_FIELDS)),
        "ITEM_POLICY_FIELDS_MISSING",
    )
    item_id = item_identifier(item["item_id"])
    normalized = shape(
        {key: item.get(key) for key in EFFECTIVE_POLICY_V2_FIELDS},
        EFFECTIVE_POLICY_V2_FIELDS,
    )
    _validate_semantics(normalized)
    payload = {
        key: normalized[key] for key in EFFECTIVE_POLICY_V2_FIELDS if key != "effective_policy_hash"
    }
    require(
        normalized["effective_policy_hash"] == _policy_digest(item_id=item_id, policy=payload),
        "ITEM_POLICY_HASH_MISMATCH",
    )
    require(
        normalized["classification_config_binding_hash"]
        == _config_binding_digest(
            item_id=item_id,
            classification_config_hash=normalized["classification_config_hash"],
            effective_policy_hash=normalized["effective_policy_hash"],
        ),
        "ITEM_POLICY_CONFIG_BINDING_HASH_MISMATCH",
    )
    return normalized


def effective_policy_content_hash(items: Sequence[Mapping[str, Any]]) -> str:
    """Seal the ordered item/hash projection used by Classification Snapshot V2."""

    require(
        isinstance(items, Sequence) and not isinstance(items, (str, bytes)),
        "EFFECTIVE_POLICY_SET_INVALID",
    )
    projection: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in sorted(items, key=lambda row: str(row.get("item_id", ""))):
        item_id = item_identifier(item.get("item_id"))
        require(item_id not in seen, "EFFECTIVE_POLICY_ITEM_DUPLICATE")
        seen.add(item_id)
        policy = validate_effective_item_policy_v2(item)
        projection.append(
            {
                "item_id": item_id,
                "effective_policy_hash": policy["effective_policy_hash"],
            }
        )
    document = json.dumps(
        projection,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(document.encode("utf-8")).hexdigest()


def _axis_results(value: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    require(
        isinstance(value, Sequence) and not isinstance(value, (str, bytes)),
        "ITEM_POLICY_AXIS_RESULTS_INVALID",
    )
    result: dict[str, dict[str, Any]] = {}
    for row in value:
        require(type(row) is dict, "ITEM_POLICY_AXIS_RESULTS_INVALID")
        require(
            all(
                key in row
                for key in (
                    "axis",
                    "status",
                    "class_code",
                    "application_mode",
                    "source_contract_key",
                    "evidence",
                )
            ),
            "ITEM_POLICY_AXIS_RESULTS_INVALID",
        )
        axis = choice(*AXIS_ORDER)(row["axis"])
        require(axis not in result, "ITEM_POLICY_AXIS_RESULTS_INVALID")
        status = choice(*AXIS_STATUSES)(row["status"])
        mode = choice("SHADOW", "OPERATIONAL")(row["application_mode"])
        class_code = row["class_code"]
        require(
            (status == "CLASSIFIED" and isinstance(class_code, str) and bool(class_code))
            or (
                status == "SYNTHETIC"
                and axis == "PLC"
                and isinstance(class_code, str)
                and bool(class_code)
            )
            or (status not in {"CLASSIFIED", "SYNTHETIC"} and class_code is None)
            or (status == "SYNTHETIC" and axis != "PLC" and class_code is None),
            "ITEM_POLICY_AXIS_RESULTS_INVALID",
        )
        require(type(row["evidence"]) is dict, "ITEM_POLICY_AXIS_RESULTS_INVALID")
        result[axis] = {
            "axis": axis,
            "status": status,
            "class_code": class_code,
            "application_mode": mode,
            "source_contract_key": identifier(row["source_contract_key"]),
            "evidence": dict(row["evidence"]),
        }
    require(set(result) == set(AXIS_ORDER), "ITEM_POLICY_AXIS_RESULTS_INVALID")
    return result


def _base_policy(
    *,
    classification_status: str,
    segment_key: str | None,
    unclassified_reason_code: str | None,
    policy_cell: Mapping[str, Any] | None,
    ved_service_level_floor: Mapping[str, Any],
    effective_axes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if classification_status == "UNCLASSIFIED":
        require(
            segment_key is None and policy_cell is None and bool(unclassified_reason_code),
            "ITEM_POLICY_CLASSIFICATION_MISMATCH",
        )
        return {
            "effective_target_service_level": None,
            "effective_review_cycle_weeks": None,
            "effective_strategy": None,
            "policy_source": "UNCLASSIFIED",
            "policy_adjustment_reason": None,
        }

    require(
        segment_key is not None and policy_cell is not None and unclassified_reason_code is None,
        "ITEM_POLICY_CLASSIFICATION_MISMATCH",
    )
    require(policy_cell.get("segment_key") == segment_key, "ITEM_POLICY_SEGMENT_MISMATCH")
    axis_classes = {row["axis"]: row["class_code"] for row in effective_axes}
    if "ABC" in axis_classes and "XYZ" in axis_classes:
        require(
            f"{axis_classes['ABC']}{axis_classes['XYZ']}" == segment_key,
            "ITEM_POLICY_SEGMENT_MISMATCH",
        )
    base_level = _decimal(policy_cell.get("target_service_level"), "ITEM_POLICY_MATRIX_INVALID")
    require(Decimal("0.5") <= base_level <= Decimal("0.9999"), "ITEM_POLICY_MATRIX_INVALID")
    review_cycle = policy_cell.get("review_cycle_weeks")
    strategy = policy_cell.get("strategy")
    require(type(review_cycle) is int and 1 <= review_cycle <= 13, "ITEM_POLICY_MATRIX_INVALID")
    choice("MATHEMATICAL", "PREDICTIVE_ML", "DEEP_RL")(strategy)
    ved_class = axis_classes.get("VED")
    floor = (
        _decimal(ved_service_level_floor.get(ved_class), "ITEM_POLICY_VED_FLOOR_INVALID")
        if ved_class is not None
        else None
    )
    floor_applied = floor is not None and floor > base_level
    return {
        "effective_target_service_level": _decimal_text(floor if floor_applied else base_level),
        "effective_review_cycle_weeks": review_cycle,
        "effective_strategy": strategy,
        "policy_source": (
            "ABC_XYZ_POLICY_MATRIX_WITH_VED_FLOOR" if floor_applied else "ABC_XYZ_POLICY_MATRIX"
        ),
        "policy_adjustment_reason": ("VED_SERVICE_LEVEL_FLOOR_APPLIED" if floor_applied else None),
    }


def _build_projection(
    effective_axes: Sequence[Mapping[str, Any]], policy_overlays: Mapping[str, Any]
) -> list[dict[str, Any]]:
    by_axis = {row["axis"]: row for row in effective_axes}
    projection: list[dict[str, Any]] = []
    for axis in AXIS_ORDER:
        row = by_axis.get(axis)
        if row is None:
            continue
        code = str(row["class_code"])
        effect: dict[str, Any]
        if axis in {"ABC", "XYZ"}:
            effect = {}
        elif axis == "VED":
            effect = {}
        elif axis in {"FSN", "PLC"}:
            overlay_key = "fsn_order_action" if axis == "FSN" else "plc_order_action"
            action = _overlay_value(policy_overlays, overlay_key, code, ORDER_ACTIONS)
            effect = {"order_action": action}
        elif axis == "SDE":
            basis = _overlay_value(policy_overlays, "sde_lead_time_basis", code, LEAD_TIME_BASES)
            p50_days = _decimal(
                row["evidence"].get("p50_lead_time_days"),
                "SDE_LEAD_TIME_QUANTILES_REQUIRED",
            )
            p90_days = _decimal(
                row["evidence"].get("p90_lead_time_days"),
                "SDE_LEAD_TIME_QUANTILES_REQUIRED",
            )
            require(
                Decimal("0") <= p50_days <= p90_days,
                "SDE_LEAD_TIME_QUANTILES_REQUIRED",
            )
            days = p50_days if basis == "P50" else p90_days
            effect = {"lead_time_basis": basis, "lead_time_days": _decimal_text(days)}
        else:
            approval = _overlay_value(policy_overlays, "hml_approval_level", code, APPROVAL_LEVELS)
            effect = {"approval_level": approval}
        projection.append(
            {
                "axis": axis,
                "class_code": code,
                "source_contract_key": row["source_contract_key"],
                "policy_effect": effect,
            }
        )
    return projection


def _overlay_value(
    overlays: Mapping[str, Any], key: str, class_code: str, allowed: Sequence[str]
) -> str:
    mapping = overlays.get(key)
    require(type(mapping) is dict and class_code in mapping, "ITEM_POLICY_OVERLAY_MISSING")
    return choice(*allowed)(mapping[class_code])


def _projection_effect(axis: str, value: Any) -> dict[str, Any]:
    require(type(value) is dict, "ITEM_POLICY_AXIS_PROJECTION")
    if axis in {"ABC", "XYZ", "VED"}:
        require(not value, "ITEM_POLICY_AXIS_PROJECTION")
        return {}
    if axis in {"FSN", "PLC"}:
        return shape(value, {"order_action": choice(*ORDER_ACTIONS)})
    if axis == "SDE":
        return shape(
            value,
            {
                "lead_time_basis": choice(*LEAD_TIME_BASES),
                "lead_time_days": decimal_string,
            },
        )
    return shape(value, {"approval_level": choice(*APPROVAL_LEVELS)})


def _strictest_action(actions: Sequence[str]) -> str:
    priority = {"ALLOW": 0, "REVIEW": 1, "BLOCK": 2}
    return max(actions or ["ALLOW"], key=priority.__getitem__)


def _adjustment_reasons(
    projection: Sequence[Mapping[str, Any]], ved_reason: str | None
) -> list[str]:
    reasons: list[str] = []
    if ved_reason is not None:
        reasons.append(ved_reason)
    for row in projection:
        effect = row["policy_effect"]
        if row["axis"] in {"FSN", "PLC"}:
            reasons.append(f"{row['axis']}_ORDER_ACTION_{effect['order_action']}")
        elif row["axis"] == "SDE":
            reasons.append(f"SDE_PROTECTION_LEAD_TIME_{effect['lead_time_basis']}")
        elif row["axis"] == "HML":
            reasons.append(f"HML_APPROVAL_{effect['approval_level']}")
    return reasons


def _gate_reason_codes(
    *,
    unavailable_operational_axes: Sequence[tuple[str, str]],
    classification_status: str,
    strategy: str | None,
    order_action: str,
    approval_level: str | None,
) -> list[str]:
    reasons = [f"OPERATIONAL_{axis}_{status}" for axis, status in unavailable_operational_axes]
    if classification_status != "CLASSIFIED":
        reasons.append("BASE_CLASSIFICATION_UNCLASSIFIED")
    if strategy not in {None, "MATHEMATICAL"}:
        reasons.append(f"OPERATIONAL_STRATEGY_{strategy}_BLOCKED")
    if order_action != "ALLOW":
        reasons.append(f"ORDER_ACTION_{order_action}")
    if approval_level in {"STANDARD", "HIGH_VALUE"}:
        reasons.append(f"HML_APPROVAL_{approval_level}")
    return reasons


def _validate_semantics(policy: Mapping[str, Any]) -> None:
    classified = policy["policy_source"] != "UNCLASSIFIED"
    require(policy["evidence_storage_allowed"], "ITEM_POLICY_SEMANTICS_INVALID")
    if classified:
        require(
            policy["effective_target_service_level"] is not None
            and Decimal("0.5")
            <= Decimal(policy["effective_target_service_level"])
            <= Decimal("0.9999")
            and policy["effective_review_cycle_weeks"] is not None
            and 1 <= policy["effective_review_cycle_weeks"] <= 13
            and policy["effective_strategy"] is not None,
            "ITEM_POLICY_SEMANTICS_INVALID",
        )
    else:
        require(
            policy["effective_target_service_level"] is None
            and policy["effective_review_cycle_weeks"] is None
            and policy["effective_strategy"] is None
            and not policy["operational_io_eligible"],
            "ITEM_POLICY_SEMANTICS_INVALID",
        )
    projected = {row["axis"]: row["policy_effect"] for row in policy["effective_axis_projection"]}
    actions = [projected[axis]["order_action"] for axis in ("FSN", "PLC") if axis in projected]
    expected_action = _strictest_action(actions)
    if not policy["operational_io_eligible"]:
        expected_action = "BLOCK"
    require(policy["effective_order_action"] == expected_action, "ITEM_POLICY_SEMANTICS_INVALID")
    require(
        policy["recommendation_calculation_allowed"]
        == (policy["operational_io_eligible"] and expected_action != "BLOCK")
        and policy["no_order_gate"] == (expected_action == "BLOCK")
        and policy["automatic_publish_allowed"]
        == (policy["operational_io_eligible"] and expected_action == "ALLOW")
        and policy["automatic_order_allowed"]
        == (
            policy["automatic_publish_allowed"]
            and policy["effective_approval_level"] in {None, "AUTO"}
        ),
        "ITEM_POLICY_SEMANTICS_INVALID",
    )
    require(
        policy["order_execution_allowed"] == policy["automatic_order_allowed"],
        "ITEM_POLICY_SEMANTICS_INVALID",
    )
    sde = projected.get("SDE")
    require(
        (
            sde is None
            and policy["effective_protection_lead_time_basis"] is None
            and policy["effective_protection_lead_time_days"] is None
        )
        or (
            sde is not None
            and policy["effective_protection_lead_time_basis"] == sde["lead_time_basis"]
            and policy["effective_protection_lead_time_days"] == sde["lead_time_days"]
        ),
        "ITEM_POLICY_SEMANTICS_INVALID",
    )
    hml = projected.get("HML")
    require(
        policy["effective_approval_level"] == (None if hml is None else hml["approval_level"]),
        "ITEM_POLICY_SEMANTICS_INVALID",
    )
    expected_reasons = _adjustment_reasons(
        policy["effective_axis_projection"], policy["policy_adjustment_reason"]
    )
    require(
        policy["policy_adjustment_reasons"] == expected_reasons, "ITEM_POLICY_SEMANTICS_INVALID"
    )
    valid_source_gate_codes = {
        f"OPERATIONAL_{axis}_{status}"
        for axis in AXIS_ORDER
        for status in AXIS_STATUSES
        if status != "CLASSIFIED"
    }
    source_gates = [
        code for code in policy["policy_gate_reason_codes"] if code in valid_source_gate_codes
    ]
    parsed_source_gates: list[tuple[str, str]] = []
    for code in source_gates:
        matched = next(
            (
                (axis, status)
                for axis in AXIS_ORDER
                for status in AXIS_STATUSES
                if status != "CLASSIFIED" and code == f"OPERATIONAL_{axis}_{status}"
            ),
            None,
        )
        require(matched is not None, "ITEM_POLICY_SEMANTICS_INVALID")
        parsed_source_gates.append(matched)
    require(
        parsed_source_gates
        == sorted(parsed_source_gates, key=lambda value: AXIS_ORDER.index(value[0])),
        "ITEM_POLICY_SEMANTICS_INVALID",
    )
    expected_gates = list(source_gates)
    if not classified:
        expected_gates.append("BASE_CLASSIFICATION_UNCLASSIFIED")
    if policy["effective_strategy"] not in {None, "MATHEMATICAL"}:
        expected_gates.append(f"OPERATIONAL_STRATEGY_{policy['effective_strategy']}_BLOCKED")
    if expected_action != "ALLOW":
        expected_gates.append(f"ORDER_ACTION_{expected_action}")
    if policy["effective_approval_level"] in {"STANDARD", "HIGH_VALUE"}:
        expected_gates.append(f"HML_APPROVAL_{policy['effective_approval_level']}")
    require(
        policy["policy_gate_reason_codes"] == expected_gates
        and (
            policy["operational_io_eligible"]
            or bool(source_gates)
            or not classified
            or policy["effective_strategy"] not in {None, "MATHEMATICAL"}
        ),
        "ITEM_POLICY_SEMANTICS_INVALID",
    )


def _decimal(value: Any, error_code: str) -> Decimal:
    try:
        result = Decimal(str(value))
        require(result.is_finite(), error_code)
        return result
    except (InvalidOperation, ValueError, TypeError):
        raise InventoryInputError(error_code) from None


def _decimal_text(value: Decimal) -> str:
    return "0" if value == 0 else format(value.normalize(), "f")


def _policy_digest(*, item_id: str, policy: Mapping[str, Any]) -> str:
    effective_policy = {
        key: value
        for key, value in policy.items()
        if key not in {"classification_config_hash", "classification_config_binding_hash"}
    }
    return digest(
        {
            "contract_version": EFFECTIVE_POLICY_V2_CONTRACT_VERSION,
            "item_id": item_id,
            **effective_policy,
        }
    )


def _config_binding_digest(
    *,
    item_id: str,
    classification_config_hash: str,
    effective_policy_hash: str,
) -> str:
    return digest(
        {
            "contract_version": EFFECTIVE_POLICY_V2_CONTRACT_VERSION,
            "item_id": item_id,
            "classification_config_hash": classification_config_hash,
            "effective_policy_hash": effective_policy_hash,
        }
    )


__all__ = [
    "EFFECTIVE_POLICY_V2_CONTRACT_VERSION",
    "EFFECTIVE_POLICY_V2_FIELDS",
    "derive_effective_item_policy_v2",
    "effective_policy_content_hash",
    "validate_effective_item_policy_v2",
]

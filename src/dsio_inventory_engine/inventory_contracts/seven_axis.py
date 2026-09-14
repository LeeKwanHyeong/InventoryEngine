"""Deterministic classification contracts for the optional inventory axes."""

from __future__ import annotations

import math
from datetime import date
from decimal import Decimal
from typing import Any, Mapping, Sequence

from .values import InventoryInputError, require, yyyyww


AXIS_RESULT_CONTRACT_VERSION = "2.0.0"
AXIS_STATUSES = (
    "CLASSIFIED",
    "UNCLASSIFIED",
    "UNVERIFIED",
    "SYNTHETIC",
    "NOT_APPLICABLE",
)


def not_applicable(axis: str, source_contract_key: str) -> dict[str, Any]:
    return _result(
        axis=axis,
        status="NOT_APPLICABLE",
        class_code=None,
        application_mode="SHADOW",
        source_contract_key=source_contract_key,
        evidence={},
        reason_code="AXIS_DISABLED",
    )


def classify_fsn(
    *,
    source_row_count: int | None,
    invalid_row_count: int | None,
    positive_week_count: int,
    last_positive_demand_yyyyww: str | None,
    source_status: str,
    as_of_yyyyww: str,
    rules: Mapping[str, Any],
) -> dict[str, Any]:
    if not rules["enabled"]:
        return not_applicable("FSN", rules["source_contract_key"])
    if source_status == "SYNTHETIC":
        return _unverified("FSN", rules, "SYNTHETIC_SOURCE_NOT_OPERATIONAL", "SYNTHETIC")
    if source_status != "VERIFIED":
        return _unverified("FSN", rules, "MOVEMENT_EVIDENCE_INCOMPLETE")
    if (
        type(source_row_count) is not int
        or type(invalid_row_count) is not int
        or source_row_count <= 0
        or invalid_row_count < 0
        or invalid_row_count > source_row_count
    ):
        return _unverified("FSN", rules, "MOVEMENT_EVIDENCE_INCOMPLETE")
    if invalid_row_count > 0:
        return _unverified("FSN", rules, "MOVEMENT_EVIDENCE_INVALID")
    lookback = int(rules["lookback_weeks"])
    require(0 <= positive_week_count <= lookback, "FSN_SOURCE_INVALID")
    require(
        (positive_week_count == 0) == (last_positive_demand_yyyyww is None),
        "FSN_SOURCE_INVALID",
    )
    weeks_since_movement = (
        lookback
        if last_positive_demand_yyyyww is None
        else _week_distance(last_positive_demand_yyyyww, as_of_yyyyww)
    )
    require(0 <= weeks_since_movement <= lookback, "FSN_SOURCE_INVALID")
    active_week_ratio = Decimal(positive_week_count) / Decimal(lookback)
    if weeks_since_movement >= int(rules["non_moving_weeks"]):
        grade = "N"
    elif active_week_ratio >= Decimal(str(rules["fast_min_active_week_ratio"])):
        grade = "F"
    else:
        grade = "S"
    return _classified(
        "FSN",
        grade,
        rules,
        {
            "source_row_count": source_row_count,
            "invalid_row_count": invalid_row_count,
            "positive_week_count": positive_week_count,
            "active_week_ratio": _decimal_text(active_week_ratio),
            "last_positive_demand_yyyyww": last_positive_demand_yyyyww,
            "weeks_since_movement": weeks_since_movement,
        },
    )


def classify_sde(
    *,
    supplier_count: int | None,
    planning_lead_time_days: Decimal | None,
    on_time_delivery_rate: Decimal | None,
    receipt_sample_count: int,
    source_status: str,
    rules: Mapping[str, Any],
    p50_lead_time_days: Decimal | None = None,
    p90_lead_time_days: Decimal | None = None,
) -> dict[str, Any]:
    if not rules["enabled"]:
        return not_applicable("SDE", rules["source_contract_key"])
    if source_status == "SYNTHETIC":
        return _unverified("SDE", rules, "SYNTHETIC_SOURCE_NOT_OPERATIONAL", "SYNTHETIC")
    if (
        source_status != "VERIFIED"
        or supplier_count is None
        or supplier_count < 1
        or planning_lead_time_days is None
        or not planning_lead_time_days.is_finite()
        or planning_lead_time_days < 0
        or p50_lead_time_days is None
        or not p50_lead_time_days.is_finite()
        or p50_lead_time_days < 0
        or p90_lead_time_days is None
        or not p90_lead_time_days.is_finite()
        or p90_lead_time_days < p50_lead_time_days
        or on_time_delivery_rate is None
        or not on_time_delivery_rate.is_finite()
        or not Decimal("0") <= on_time_delivery_rate <= Decimal("1")
        or receipt_sample_count < int(rules["min_receipt_sample_count"])
    ):
        return _unverified("SDE", rules, "SUPPLIER_LEAD_TIME_EVIDENCE_INCOMPLETE")
    scarce = (
        supplier_count <= int(rules["scarce_max_supplier_count"])
        or planning_lead_time_days >= Decimal(str(rules["scarce_min_lead_time_days"]))
        or on_time_delivery_rate <= Decimal(str(rules["scarce_max_on_time_rate"]))
    )
    difficult = planning_lead_time_days >= Decimal(
        str(rules["difficult_min_lead_time_days"])
    ) or on_time_delivery_rate <= Decimal(str(rules["difficult_max_on_time_rate"]))
    return _classified(
        "SDE",
        "S" if scarce else "D" if difficult else "E",
        rules,
        {
            "supplier_count": supplier_count,
            "planning_lead_time_days": _decimal_text(planning_lead_time_days),
            "p50_lead_time_days": _decimal_text(p50_lead_time_days),
            "p90_lead_time_days": _decimal_text(p90_lead_time_days),
            "on_time_delivery_rate": _decimal_text(on_time_delivery_rate),
            "receipt_sample_count": receipt_sample_count,
        },
    )


def hml_thresholds(
    unit_costs: Sequence[Decimal], *, medium_percentile: Decimal, high_percentile: Decimal
) -> tuple[Decimal, Decimal] | None:
    require(
        Decimal("0") < medium_percentile < high_percentile < Decimal("1"),
        "HML_THRESHOLD_INVALID",
    )
    values = sorted(value for value in unit_costs if value.is_finite() and value > 0)
    if not values:
        return None

    def nearest_rank(percentile: Decimal) -> Decimal:
        rank = max(1, math.ceil(float(percentile * Decimal(len(values)))))
        return values[rank - 1]

    return nearest_rank(medium_percentile), nearest_rank(high_percentile)


def classify_hml(
    *,
    unit_cost: Decimal | None,
    unit_cost_currency: str | None,
    thresholds: tuple[Decimal, Decimal] | None,
    source_status: str,
    rules: Mapping[str, Any],
    unit_cost_as_of_yyyyww: str | None = None,
    as_of_yyyyww: str | None = None,
) -> dict[str, Any]:
    if not rules["enabled"]:
        return not_applicable("HML", rules["source_contract_key"])
    if source_status == "SYNTHETIC":
        return _unverified("HML", rules, "SYNTHETIC_SOURCE_NOT_OPERATIONAL", "SYNTHETIC")
    if unit_cost_as_of_yyyyww is None or as_of_yyyyww is None:
        return _unverified("HML", rules, "INVENTORY_UNIT_COST_AS_OF_MISSING")
    try:
        source_age_weeks = _week_distance(unit_cost_as_of_yyyyww, as_of_yyyyww)
    except (InventoryInputError, ValueError):
        return _unverified("HML", rules, "INVENTORY_UNIT_COST_AS_OF_INVALID")
    if source_age_weeks < 0:
        return _unverified("HML", rules, "INVENTORY_UNIT_COST_FROM_FUTURE")
    if source_age_weeks > int(rules["max_source_age_weeks"]):
        return _unverified("HML", rules, "INVENTORY_UNIT_COST_STALE")
    if (
        source_status != "VERIFIED"
        or unit_cost is None
        or not unit_cost.is_finite()
        or unit_cost <= 0
        or unit_cost_currency != rules["currency"]
        or thresholds is None
    ):
        return _unverified("HML", rules, "INVENTORY_UNIT_COST_EVIDENCE_INCOMPLETE")
    medium_min, high_min = thresholds
    grade = "H" if unit_cost >= high_min else "M" if unit_cost >= medium_min else "L"
    return _classified(
        "HML",
        grade,
        rules,
        {
            "unit_cost": _decimal_text(unit_cost),
            "currency": unit_cost_currency,
            "unit_cost_as_of_yyyyww": unit_cost_as_of_yyyyww,
            "source_age_weeks": source_age_weeks,
            "medium_min_unit_cost": _decimal_text(medium_min),
            "high_min_unit_cost": _decimal_text(high_min),
        },
    )


def classify_plc(
    *,
    introduced_yyyyww: str | None,
    production_end_yyyyww: str | None,
    service_end_yyyyww: str | None,
    lifecycle_status_cd: str | None,
    source_status: str,
    as_of_yyyyww: str,
    rules: Mapping[str, Any],
    source_profile_hash: str | None = None,
) -> dict[str, Any]:
    if not rules["enabled"]:
        return not_applicable("PLC", rules["source_contract_key"])
    if (
        source_status not in {"VERIFIED", "SYNTHETIC"}
        or introduced_yyyyww is None
        or service_end_yyyyww is None
        or lifecycle_status_cd is None
    ):
        return _unverified("PLC", rules, "LIFECYCLE_EVIDENCE_INCOMPLETE")
    if source_status == "SYNTHETIC" and not _sha256(source_profile_hash):
        return _unverified("PLC", rules, "SYNTHETIC_PROFILE_HASH_INVALID")
    try:
        introduced = _week_monday(introduced_yyyyww)
        as_of = _week_monday(as_of_yyyyww)
        service_end = _week_monday(service_end_yyyyww)
        production_end = (
            _week_monday(production_end_yyyyww) if production_end_yyyyww is not None else None
        )
    except (InventoryInputError, ValueError):
        return _unverified("PLC", rules, "LIFECYCLE_CHRONOLOGY_INVALID")
    if service_end < introduced or (
        production_end is not None and (production_end < introduced or production_end > service_end)
    ):
        return _unverified("PLC", rules, "LIFECYCLE_CHRONOLOGY_INVALID")
    status = lifecycle_status_cd.upper()
    if status not in {"ACTIVE", "SERVICE_ONLY", "DISCONTINUED"}:
        return _unverified("PLC", rules, "LIFECYCLE_STATUS_UNSUPPORTED")
    if status == "DISCONTINUED" or as_of >= service_end:
        grade = "DISCONTINUED"
    elif status == "SERVICE_ONLY" or (production_end is not None and as_of >= production_end):
        grade = "SERVICE_ONLY"
    elif as_of < introduced:
        grade = "PRE_LAUNCH"
    else:
        age_weeks = (as_of - introduced).days // 7
        weeks_to_production_end = (
            (production_end - as_of).days // 7 if production_end is not None else None
        )
        if age_weeks < int(rules["introduction_weeks"]):
            grade = "INTRODUCTION"
        elif age_weeks < int(rules["growth_weeks"]):
            grade = "GROWTH"
        elif weeks_to_production_end is not None and weeks_to_production_end <= int(
            rules["decline_horizon_weeks"]
        ):
            grade = "DECLINE"
        else:
            grade = "MATURE"
    evidence = {
        "introduced_yyyyww": introduced_yyyyww,
        "production_end_yyyyww": production_end_yyyyww,
        "service_end_yyyyww": service_end_yyyyww,
        "lifecycle_status_cd": lifecycle_status_cd,
    }
    if source_status == "SYNTHETIC":
        evidence["source_profile_hash"] = source_profile_hash
        return _result(
            axis="PLC",
            status="SYNTHETIC",
            class_code=grade,
            application_mode=rules["application_mode"],
            source_contract_key=rules["source_contract_key"],
            evidence=evidence,
            reason_code="SYNTHETIC_SOURCE_SHADOW_ONLY",
        )
    return _classified("PLC", grade, rules, evidence)


def display_segment_code(axis_results: Sequence[Mapping[str, Any]]) -> str:
    base_code = "".join(
        str(result["class_code"])
        for axis in ("ABC", "XYZ")
        for result in axis_results
        if result.get("axis") == axis and result.get("class_code") is not None
    )
    overlays = [
        str(result["class_code"])
        for result in axis_results
        if result.get("axis") not in {"ABC", "XYZ"} and result.get("class_code") is not None
    ]
    return "-".join(([base_code] if base_code else []) + overlays)


def _classified(
    axis: str, class_code: str, rules: Mapping[str, Any], evidence: Mapping[str, Any]
) -> dict[str, Any]:
    return _result(
        axis=axis,
        status="CLASSIFIED",
        class_code=class_code,
        application_mode=rules["application_mode"],
        source_contract_key=rules["source_contract_key"],
        evidence=evidence,
        reason_code=None,
    )


def _unverified(
    axis: str,
    rules: Mapping[str, Any],
    reason_code: str,
    status: str = "UNVERIFIED",
) -> dict[str, Any]:
    return _result(
        axis=axis,
        status=status,
        class_code=None,
        application_mode=rules["application_mode"],
        source_contract_key=rules["source_contract_key"],
        evidence={},
        reason_code=reason_code,
    )


def _result(
    *,
    axis: str,
    status: str,
    class_code: str | None,
    application_mode: str,
    source_contract_key: str,
    evidence: Mapping[str, Any],
    reason_code: str | None,
) -> dict[str, Any]:
    require(status in AXIS_STATUSES, "AXIS_STATUS_INVALID")
    require(application_mode in {"SHADOW", "OPERATIONAL"}, "AXIS_MODE_INVALID")
    return {
        "axis": axis,
        "status": status,
        "class_code": class_code,
        "application_mode": application_mode,
        "policy_effective": application_mode == "OPERATIONAL" and status == "CLASSIFIED",
        "source_contract_key": source_contract_key,
        "evidence": dict(evidence),
        "reason_code": reason_code,
    }


def _week_monday(value: str) -> date:
    normalized = yyyyww(value)
    return date.fromisocalendar(int(normalized[:4]), int(normalized[4:]), 1)


def _sha256(value: str | None) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _week_distance(start: str, end: str) -> int:
    return (_week_monday(end) - _week_monday(start)).days // 7


def _decimal_text(value: Decimal) -> str:
    return "0" if value == 0 else format(value.normalize(), "f")


__all__ = [
    "AXIS_RESULT_CONTRACT_VERSION",
    "AXIS_STATUSES",
    "classify_fsn",
    "classify_hml",
    "classify_plc",
    "classify_sde",
    "display_segment_code",
    "hml_thresholds",
    "not_applicable",
]

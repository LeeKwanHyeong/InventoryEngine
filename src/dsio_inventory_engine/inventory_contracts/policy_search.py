"""Closed development policy search contract, mirrored across producer and engine.

No database, model loader, optimizer, or operational approval is implied here.
All decimal inputs are strings; ordering and equivalent decimal spellings normalize.
"""

from decimal import Decimal, InvalidOperation
from itertools import product
import copy
import hashlib
import json
import re

SEGMENTS = ("AX", "AY", "AZ", "BX", "BY", "BZ", "CX", "CY", "CZ")
POLICY_KINDS = ("ROP_FIXED_Q", "PERIODIC_ORDER_UP_TO")
PROFILE_FIELDS = {
    "policy_kinds",
    "rmos_values",
    "emos_values",
    "service_level_values",
    "review_cycle_weeks",
}
COST_FIELDS = {
    "currency",
    "holding_cost_per_unit_week",
    "backorder_cost_per_unit_week",
    "fixed_order_cost",
    "variable_order_cost_per_unit",
    "source_type",
    "allocation_basis",
}


def _check(condition, code):
    if not condition:
        raise ValueError(code)


def _decimal(value, minimum="0", maximum="1000000000000", *, positive=False):
    _check(
        isinstance(value, str)
        and len(value) <= 40
        and re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,6})?", value),
        "POLICY_SEARCH_DECIMAL_INVALID",
    )
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError("POLICY_SEARCH_DECIMAL_INVALID") from None
    _check(
        number.is_finite()
        and Decimal(minimum) <= number <= Decimal(maximum)
        and (not positive or number > 0),
        "POLICY_SEARCH_DECIMAL_RANGE",
    )
    return (
        format(number, "f").rstrip("0").rstrip(".")
        if "." in format(number, "f")
        else format(number, "f")
    )


def _vector(value, normalize):
    _check(type(value) is list and 1 <= len(value) <= 16, "POLICY_SEARCH_VECTOR_INVALID")
    result = [normalize(item) for item in value]
    _check(len(set(result)) == len(result), "POLICY_SEARCH_DUPLICATE_VALUE")
    return sorted(result, key=lambda item: Decimal(str(item)))


def _weeks(value):
    _check(type(value) is int and 1 <= value <= 13, "POLICY_SEARCH_REVIEW_INVALID")
    return value


def _profile(value, extra_fields, limit):
    _check(
        type(value) is dict and set(value) == PROFILE_FIELDS | extra_fields,
        "POLICY_SEARCH_PROFILE_INVALID",
    )
    kinds = value["policy_kinds"]
    _check(
        type(kinds) is list
        and 1 <= len(kinds) <= 2
        and all(type(kind) is str and kind in POLICY_KINDS for kind in kinds)
        and len(set(kinds)) == len(kinds),
        "POLICY_SEARCH_KIND_INVALID",
    )
    result = {key: copy.deepcopy(value[key]) for key in extra_fields}
    result.update(
        policy_kinds=sorted(kinds),
        rmos_values=_vector(value["rmos_values"], lambda item: _decimal(item, maximum="12")),
        emos_values=_vector(
            value["emos_values"], lambda item: _decimal(item, maximum="12", positive=True)
        ),
        service_level_values=_vector(
            value["service_level_values"], lambda item: _decimal(item, "0.5", "0.9999")
        ),
        review_cycle_weeks=_vector(value["review_cycle_weeks"], _weeks),
    )
    count = 1
    for key in PROFILE_FIELDS:
        count *= len(result[key])
    _check(count <= limit, "POLICY_SEARCH_CANDIDATE_LIMIT")
    return result


def normalize_cost_assumptions(cost):
    """Shared cost parsing; no policy, trajectory or approval is changed by re-pricing."""
    if cost is None:
        return None
    _check(
        type(cost) is dict
        and set(cost) == COST_FIELDS
        and cost["source_type"] == "DEVELOPMENT_ASSUMPTION"
        and cost["allocation_basis"] == "PER_ITEM_ORDER",
        "POLICY_SEARCH_COST_INVALID",
    )
    _check(
        isinstance(cost["currency"], str) and re.fullmatch(r"[A-Z]{3}", cost["currency"]),
        "POLICY_SEARCH_CURRENCY_INVALID",
    )
    return {key: cost[key] for key in ("currency", "source_type", "allocation_basis")} | {
        key: _decimal(cost[key])
        for key in COST_FIELDS - {"currency", "source_type", "allocation_basis"}
    }


def normalize_policy_search(value):
    fields = {
        "contract_version",
        "enabled",
        "evaluation_mode",
        "month_basis",
        "lookback_weeks",
        "max_candidates_per_item",
        "minimum_fill_rate",
        "selection_rule",
        "group_profiles",
        "item_overrides",
        "cost_assumptions",
    }
    _check(type(value) is dict and set(value) == fields, "POLICY_SEARCH_CONFIG_INVALID")
    _check(
        value["contract_version"] == "1.0.0"
        and value["evaluation_mode"] == "DEVELOPMENT_SHADOW"
        and value["month_basis"] == "52_WEEKS_PER_YEAR"
        and value["selection_rule"] == "SERVICE_FEASIBLE_THEN_LOWEST_COST",
        "POLICY_SEARCH_CONTRACT_INVALID",
    )
    _check(
        type(value["enabled"]) is bool
        and type(value["lookback_weeks"]) is int
        and value["lookback_weeks"] in (13, 26),
        "POLICY_SEARCH_LOOKBACK_INVALID",
    )
    limit = value["max_candidates_per_item"]
    _check(type(limit) is int and 1 <= limit <= 256, "POLICY_SEARCH_CANDIDATE_LIMIT")
    groups, overrides = value["group_profiles"], value["item_overrides"]
    _check(
        type(groups) is list
        and 1 <= len(groups) <= 9
        and type(overrides) is list
        and len(overrides) <= 1000,
        "POLICY_SEARCH_PROFILE_LIMIT",
    )
    groups = [_profile(row, {"segment_key"}, limit) for row in groups]
    _check(
        all(row["segment_key"] in SEGMENTS for row in groups)
        and len({row["segment_key"] for row in groups}) == len(groups),
        "POLICY_SEARCH_GROUP_INVALID",
    )
    overrides = [_profile(row, {"item_id", "reason"}, limit) for row in overrides]
    for row in overrides:
        item = row["item_id"]
        _check(
            isinstance(item, str)
            and 0 < len(item) <= 160
            and item == item.strip()
            and "\x00" not in item,
            "POLICY_SEARCH_ITEM_INVALID",
        )
        _check(
            isinstance(row["reason"], str) and 0 < len(row["reason"].strip()) <= 500,
            "POLICY_SEARCH_OVERRIDE_REASON_REQUIRED",
        )
        row["reason"] = row["reason"].strip()
    _check(
        len({row["item_id"] for row in overrides}) == len(overrides),
        "POLICY_SEARCH_OVERRIDE_DUPLICATE",
    )
    return {
        **copy.deepcopy(value),
        "minimum_fill_rate": _decimal(value["minimum_fill_rate"], maximum="1"),
        "group_profiles": sorted(groups, key=lambda row: row["segment_key"]),
        "item_overrides": sorted(overrides, key=lambda row: row["item_id"]),
        "cost_assumptions": normalize_cost_assumptions(value["cost_assumptions"]),
    }


def policy_search_hash(value):
    normalized = normalize_policy_search(value)
    return hashlib.sha256(
        json.dumps(
            normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def candidate_policies(config, *, item_id, segment_key):
    config = normalize_policy_search(config)
    _check(config["enabled"], "POLICY_SEARCH_DISABLED")
    override = next((row for row in config["item_overrides"] if row["item_id"] == item_id), None)
    profile = override or next(
        (row for row in config["group_profiles"] if row["segment_key"] == segment_key), None
    )
    _check(segment_key in SEGMENTS, "POLICY_SEARCH_ITEM_UNCLASSIFIED")
    _check(profile is not None, "POLICY_SEARCH_GROUP_NOT_CONFIGURED")
    result = []
    for kind, rmos, emos, service, review in product(
        *(
            profile[key]
            for key in (
                "policy_kinds",
                "rmos_values",
                "emos_values",
                "service_level_values",
                "review_cycle_weeks",
            )
        )
    ):
        body = {
            "item_id": item_id,
            "segment_key": segment_key,
            "policy_kind": kind,
            "rmos": rmos,
            "emos": emos,
            "target_service_level": service,
            "review_cycle_weeks": review,
            "range_source": "ITEM_OVERRIDE" if override else "GROUP_PROFILE",
            "override_reason": override["reason"] if override else None,
        }
        body["candidate_id"] = (
            "PC-"
            + hashlib.sha256(
                json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
            ).hexdigest()
        )
        result.append(body)
    return sorted(result, key=lambda row: row["candidate_id"])

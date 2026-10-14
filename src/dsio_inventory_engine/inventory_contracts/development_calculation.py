"""Closed calculation-only wire contract, mirrored with InventoryEngine.

This namespace is never an operational Result Bundle, Claim or publication.
"""

import copy
import hashlib
import json
import re
import uuid
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

CONTRACT_ID = "inventory-development-calculation"
CONTRACT_KEY = "inventory.development_calculation_manifest"
VERSION = "1.0.0"
MAX_PART_BYTES = 16_000_000


def check(condition, code):
    if not condition:
        raise ValueError(code)


def content_hash(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _id(value):
    check(
        type(value) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value),
        "CALCULATION_IDENTIFIER_INVALID",
    )
    return value


def _hash(value):
    check(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value), "CALCULATION_HASH_INVALID")
    return value


def _item_id(value):
    check(
        type(value) is str
        and bool(value.strip())
        and value == value.strip()
        and len(value) <= 160
        and "\x00" not in value,
        "CALCULATION_ITEM_IDENTIFIER_INVALID",
    )
    return value


def _uuid(value):
    try:
        check(type(value) is str and str(uuid.UUID(value)) == value, "CALCULATION_UUID_INVALID")
    except (ValueError, TypeError, AttributeError):
        raise ValueError("CALCULATION_UUID_INVALID") from None
    return value


def _integer(value, maximum):
    check(type(value) is int and 1 <= value <= maximum, "CALCULATION_INTEGER_INVALID")
    return value


def _shape(value, keys):
    check(type(value) is dict and set(value) == set(keys), "CALCULATION_FIELDS_INVALID")


def root_reference(command):
    command = validate_command(command)
    return (
        f"artifact:inventory-development:{command['tenant_id']}:{command['project_id']}:"
        f"{command['calculation_id']}:{command['attempt_no']}"
    )


COMMAND_FIELDS = {
    "contract_id",
    "contract_version",
    "environment",
    "execution_purpose",
    "calculation_id",
    "attempt_no",
    "tenant_id",
    "project_id",
    "scope",
    "demand_run_id",
    "plan_yyyyww",
    "source_binding",
    "canonical_input_hash",
    "config_revision_id",
    "config_hash",
    "classification_snapshot_id",
    "classification_content_hash",
    "effective_policy_content_hash",
    "mathematical_implementation_hash",
    "psi_implementation_hash",
    "client_request_hash",
    "max_items_per_part",
    "automatic_publish_allowed",
    "automatic_order_allowed",
    "engine_run_claimed",
    "content_hash",
}


def seal_command(value):
    body = {k: v for k, v in value.items() if k != "content_hash"}
    return validate_command({**body, "content_hash": content_hash(body)})


def validate_command(value):
    _shape(value, COMMAND_FIELDS)
    check(
        value["contract_id"] == CONTRACT_ID
        and value["contract_version"] == VERSION
        and value["environment"] == "DEVELOPMENT"
        and value["execution_purpose"] == "CALCULATION_ONLY",
        "CALCULATION_CONTRACT_INVALID",
    )
    for key in ("automatic_publish_allowed", "automatic_order_allowed", "engine_run_claimed"):
        check(value[key] is False, "CALCULATION_OPERATIONAL_FLAG_FORBIDDEN")
    for key in ("tenant_id", "project_id"):
        _id(value[key])
    for key in (
        "calculation_id",
        "demand_run_id",
        "config_revision_id",
        "classification_snapshot_id",
    ):
        _uuid(value[key])
    _integer(value["attempt_no"], 1000)
    _integer(value["max_items_per_part"], 64)
    _shape(value["scope"], {"company_cd", "subs_cd", "site_cd"})
    for v in value["scope"].values():
        _id(v)
    check(
        type(value["plan_yyyyww"]) is str
        and re.fullmatch(r"[1-9][0-9]{3}(0[1-9]|[1-4][0-9]|5[0-3])", value["plan_yyyyww"]),
        "CALCULATION_WEEK_INVALID",
    )
    _shape(value["source_binding"], {"bundle_id", "content_hash"})
    _id(value["source_binding"]["bundle_id"])
    _hash(value["source_binding"]["content_hash"])
    for key in (
        "canonical_input_hash",
        "config_hash",
        "classification_content_hash",
        "effective_policy_content_hash",
        "mathematical_implementation_hash",
        "psi_implementation_hash",
        "client_request_hash",
        "content_hash",
    ):
        _hash(value[key])
    check(
        value["content_hash"]
        == content_hash({k: v for k, v in value.items() if k != "content_hash"}),
        "CALCULATION_COMMAND_HASH_MISMATCH",
    )
    return copy.deepcopy(value)


def validate_manifest(value, command=None):
    _shape(
        value,
        {
            "contract_id",
            "contract_version",
            "source_contract_key",
            "artifact_reference",
            "status",
            "command",
            "source_artifact",
            "parts",
            "item_count",
            "row_count",
            "blocked_item_count",
            "metrics",
            "automatic_publish_allowed",
            "automatic_order_allowed",
            "engine_run_claimed",
            "content_hash",
        },
    )
    bound = validate_command(value["command"])
    if command is not None:
        check(bound == validate_command(command), "CALCULATION_COMMAND_BINDING_MISMATCH")
    root = root_reference(bound)
    check(
        value["contract_id"] == "inventory-development-calculation-manifest"
        and value["contract_version"] == VERSION
        and value["source_contract_key"] == CONTRACT_KEY
        and value["artifact_reference"] == root + ":manifest"
        and value["status"] == "CALCULATED",
        "CALCULATION_MANIFEST_INVALID",
    )
    for key in ("automatic_publish_allowed", "automatic_order_allowed", "engine_run_claimed"):
        check(value[key] is False, "CALCULATION_OPERATIONAL_FLAG_FORBIDDEN")
    _shape(value["source_artifact"], {"reference", "content_hash"})
    check(value["source_artifact"]["reference"] == root + ":input", "CALCULATION_REFERENCE_INVALID")
    _hash(value["source_artifact"]["content_hash"])
    check(
        type(value["parts"]) is list and 0 < len(value["parts"]) <= 20000,
        "CALCULATION_PARTS_INVALID",
    )
    items = []
    rows = 0
    for index, part in enumerate(value["parts"]):
        _shape(part, {"reference", "content_hash", "index", "item_ids", "row_count"})
        check(
            part["index"] == index
            and type(part["index"]) is int
            and part["reference"] == root + f":part:{index:06d}",
            "CALCULATION_PART_ORDER_INVALID",
        )
        _hash(part["content_hash"])
        check(
            type(part["item_ids"]) is list
            and 0 < len(part["item_ids"]) <= bound["max_items_per_part"]
            and part["item_ids"] == sorted(set(part["item_ids"])),
            "CALCULATION_PART_ITEMS_INVALID",
        )
        items.extend(_item_id(v) for v in part["item_ids"])
        rows += _integer(part["row_count"], 10_000_000)
    check(
        items == sorted(set(items))
        and type(value["item_count"]) is int
        and value["item_count"] == len(items)
        and type(value["row_count"]) is int
        and value["row_count"] == rows
        and type(value["blocked_item_count"]) is int
        and 0 <= value["blocked_item_count"] <= len(items),
        "CALCULATION_COVERAGE_INVALID",
    )
    _shape(value["metrics"], {"baseline", "recommended", "cost_status", "cost_reason"})
    check(
        value["metrics"]["cost_status"] == "NOT_AVAILABLE"
        and value["metrics"]["cost_reason"] == "COST_PROFILE_NOT_BOUND",
        "CALCULATION_COST_INVALID",
    )
    for kind in ("baseline", "recommended"):
        metric = value["metrics"][kind]
        quantities = {
            "horizon_demand_qty",
            "on_time_fulfilled_qty",
            "ending_available_inventory_qty",
            "ending_on_hand_inventory_qty",
            "ending_backorder_qty",
            "forecast_shortage_qty",
        }
        _shape(metric, quantities | {"projected_service_level", "service_status"})
        for key in quantities:
            check(
                type(metric[key]) is str
                and re.fullmatch(r"[0-9]+([.][0-9]+)?", metric[key])
                and Decimal(metric[key]) <= Decimal("200000000000000000000"),
                "CALCULATION_METRIC_INVALID",
            )
        demand, fulfilled = (
            Decimal(metric["horizon_demand_qty"]),
            Decimal(metric["on_time_fulfilled_qty"]),
        )
        check(fulfilled <= demand, "CALCULATION_METRIC_INVALID")
        with localcontext() as ctx:
            ctx.prec = 40
            ctx.rounding = ROUND_HALF_EVEN
            expected = (
                format((fulfilled / demand).quantize(Decimal("0.000001")), "f") if demand else None
            )
        check(
            metric["projected_service_level"] == expected
            and metric["service_status"] == ("AVAILABLE" if demand else "NO_DEMAND_IN_HORIZON"),
            "CALCULATION_SERVICE_METRIC_INVALID",
        )
    check(
        value["content_hash"]
        == content_hash({k: v for k, v in value.items() if k != "content_hash"}),
        "CALCULATION_MANIFEST_HASH_MISMATCH",
    )
    return copy.deepcopy(value)

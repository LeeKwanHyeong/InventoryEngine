"""Closed, bounded pure-evaluation inputs. Hashes identify inputs, not Source authority.

The future Source intake must replay Catalog/Guard before projecting these records.
Hand-authored records are intentionally sufficient for testing the evaluator itself.
Nothing in this module grants operational eligibility, approval or publication.
"""

from datetime import date, timedelta
from decimal import Context, Decimal, DecimalException, ROUND_HALF_EVEN, localcontext
from functools import wraps
import re

from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    canonical_json,
    day,
    digest,
    hash_value,
    identifier,
    item_identifier,
    read_json,
    require,
)

NUMBERS = Context(prec=40, rounding=ROUND_HALF_EVEN)
PROTOCOL_HASH = "a3e35fcf23915e87823883ea8c1f09448b245ae7ea1fffa8c77482bab0433280"
PROFILES = (
    "FIXED_COMPATIBILITY",
    "PRICE_BASE",
    "PRICE_HOLDING_HIGH",
    "PRICE_ORDER_HIGH",
    "PRICE_NO_FORECAST_PENALTY",
)
SPLIT_COUNTS = {"NOMINAL": 1, "SELECTION": 8, "VALIDATION": 8, "HOLDOUT": 16, "STRESS": 4}
MIN_DEMAND = {"SELECTION": 6, "VALIDATION": 6, "HOLDOUT": 12}
SAFETY = {
    "automatic_publish_allowed": False,
    "automatic_order_allowed": False,
    "operational_policy_approved": False,
    "database_writes": False,
}
POLICY_FIELDS = (
    "candidate_id",
    "policy_kind",
    "rmos",
    "emos",
    "target_service_level",
    "review_cycle_weeks",
)
ROW_QUANTITIES = (
    "boh_qty",
    "reserved_qty",
    "on_hand_boh_qty",
    "confirmed_supplier_receipt_qty",
    "recommended_receipt_qty",
    "available_qty",
    "backorder_open_qty",
    "fulfilled_backorder_qty",
    "fulfilled_confirmed_customer_order_qty",
    "fulfilled_forecast_qty",
    "eoh_qty",
    "on_hand_eoh_qty",
    "backorder_close_qty",
    "forecast_shortage_qty",
    "confirmed_customer_order_qty",
    "net_forecast_qty",
)
ROW_FIELDS = ("seq", "yyyyww", "start_date", "end_date", *ROW_QUANTITIES)


def shape(value, fields):
    require(type(value) is dict and set(value) == set(fields), "ROBUSTNESS_CONTRACT_FIELDS")


def input_errors(function):
    @wraps(function)
    def invoke(*args, **kwargs):
        try:
            with localcontext(NUMBERS):
                return function(*args, **kwargs)
        except InventoryInputError:
            raise
        except (KeyError, TypeError, ValueError, DecimalException, OverflowError, RecursionError):
            raise InventoryInputError("ROBUSTNESS_INPUT_INVALID") from None

    return invoke


def number(value):
    require(
        type(value) is str
        and len(value) <= 96
        and re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value) is not None,
        "ROBUSTNESS_NUMBER_INVALID",
    )
    result = Decimal(value)
    # Derived costs can exceed the per-row quantity/price limits after accumulation.
    require(result.is_finite() and result <= Decimal("1e80"), "ROBUSTNESS_NUMBER_INVALID")
    return result


def quantity(value):
    result = number(value)
    require(
        result == result.to_integral_value() and result <= Decimal("1e12"),
        "ROBUSTNESS_EA_QUANTITY_INVALID",
    )
    return result


def clone(value):
    return read_json(canonical_json(value), max_bytes=128_000_000)


def sealed(contract, body):
    result = {
        "contract_id": contract,
        "contract_version": "1.0.0",
        "protocol_hash": PROTOCOL_HASH,
        **body,
        **SAFETY,
    }
    result["content_hash"] = digest(result)
    return result


def policy(value):
    shape(value, POLICY_FIELDS)
    identifier(value["candidate_id"])
    require(
        value["policy_kind"] in ("ROP_FIXED_Q", "PERIODIC_ORDER_UP_TO"), "ROBUSTNESS_POLICY_INVALID"
    )
    for key in ("rmos", "emos", "target_service_level"):
        number(value[key])
    require(
        Decimal(0) < number(value["target_service_level"]) < Decimal(1), "ROBUSTNESS_POLICY_INVALID"
    )
    require(
        type(value["review_cycle_weeks"]) is int and 1 <= value["review_cycle_weeks"] <= 52,
        "ROBUSTNESS_POLICY_INVALID",
    )
    return value


def semantic_key(value):
    return (
        ("ROP_FIXED_Q", "PERIODIC_ORDER_UP_TO").index(value["policy_kind"]),
        *(number(value[key]) for key in ("rmos", "emos", "target_service_level")),
        value["review_cycle_weeks"],
    )


def path(value):
    shape(value, ("path_id", "split", "seed", "path_content_hash"))
    identifier(value["path_id"])
    require(value["split"] in SPLIT_COUNTS, "ROBUSTNESS_SPLIT_INVALID")
    require(type(value["seed"]) is int and 0 < value["seed"] < 2**63, "ROBUSTNESS_SEED_INVALID")
    hash_value(value["path_content_hash"])
    return value


def price(value, w0):
    if value is None:
        return
    shape(value, ("unit_price", "currency", "price_as_of", "source_hash"))
    require(number(value["unit_price"]) <= Decimal("1e12"), "ROBUSTNESS_PRICE_RANGE")
    require(
        value["currency"] == "USD" and day(value["price_as_of"]) <= w0,
        "ROBUSTNESS_PRICE_BINDING_INVALID",
    )
    hash_value(value["source_hash"])


def binding(value):
    shape(
        value,
        (
            "company_cd",
            "subs_cd",
            "site_cd",
            "item_id",
            "uom",
            "w0",
            "immutable_input_hash",
            "candidate_mode",
            "catalog_hash",
            "unit_price",
        ),
    )
    require(
        (value["company_cd"], value["subs_cd"], value["site_cd"], value["uom"])
        == ("DSE", "C100", "V101", "EA"),
        "ROBUSTNESS_SCOPE_INVALID",
    )
    item_identifier(value["item_id"])
    require(date.fromisoformat(day(value["w0"])).weekday() == 0, "ROBUSTNESS_CALENDAR_INVALID")
    for key in ("immutable_input_hash", "catalog_hash"):
        hash_value(value[key])
    require(
        value["candidate_mode"] in ("ABC_XYZ_SHARED_BUDGET", "LEGACY_STYLE", "WIDE_REFERENCE"),
        "ROBUSTNESS_MODE_INVALID",
    )
    price(value["unit_price"], value["w0"])
    return value


def trace(value):
    value = clone(value)
    shape(
        value,
        (
            "binding",
            "path",
            "candidate",
            "source_result_hash",
            "status",
            "failure_reason",
            "gate",
            "rows",
            "orders",
            "initial_receipts",
        ),
    )
    binding(value["binding"])
    path(value["path"])
    policy(value["candidate"])
    hash_value(value["source_result_hash"])
    gate = value["gate"]
    shape(gate, ("effective_order_action", "recommendation_calculation_allowed"))
    require(
        gate["effective_order_action"] in ("ALLOW", "REVIEW", "BLOCK")
        and type(gate["recommendation_calculation_allowed"]) is bool,
        "ROBUSTNESS_GATE_INVALID",
    )
    require(value["status"] in ("SUCCEEDED", "FAILED"), "ROBUSTNESS_STATUS_INVALID")
    receipts = value["initial_receipts"]
    require(type(receipts) is list and len(receipts) <= 10000, "ROBUSTNESS_SUPPLY_INVALID")
    for receipt in receipts:
        shape(receipt, ("receipt_id", "receipt_date", "quantity"))
        identifier(receipt["receipt_id"])
        quantity(receipt["quantity"])
        require(day(receipt["receipt_date"]) >= value["binding"]["w0"], "ROBUSTNESS_SUPPLY_INVALID")
    require(
        len({r["receipt_id"] for r in receipts}) == len(receipts), "ROBUSTNESS_DUPLICATE_SUPPLY"
    )
    if value["status"] == "FAILED":
        identifier(value["failure_reason"])
        require(
            value["rows"] is None and value["orders"] is None, "ROBUSTNESS_FAILED_TRACE_PAYLOAD"
        )
        return value
    require(value["failure_reason"] is None, "ROBUSTNESS_SUCCESS_FAILURE_REASON")
    rows, orders = value["rows"], value["orders"]
    require(type(rows) is list and len(rows) == 65, "ROBUSTNESS_CONTINUOUS_65_WEEKS_REQUIRED")
    require(type(orders) is list and len(orders) <= 65, "ROBUSTNESS_ORDERS_INVALID")
    start = date.fromisoformat(value["binding"]["w0"])
    with localcontext(NUMBERS):
        for index, row in enumerate(rows):
            shape(row, ROW_FIELDS)
            expected = start + timedelta(weeks=index)
            require(
                type(row["seq"]) is int
                and row["seq"] == index
                and row["start_date"] == expected.isoformat()
                and row["end_date"] == (expected + timedelta(days=6)).isoformat()
                and row["yyyyww"] == expected.strftime("%G%V"),
                "ROBUSTNESS_CALENDAR_INVALID",
            )
            for key in ROW_QUANTITIES:
                quantity(row[key])
        for order in orders:
            shape(order, ("order_id", "order_date", "receipt_date", "quantity"))
            identifier(order["order_id"])
            require(quantity(order["quantity"]) > 0, "ROBUSTNESS_ORDERS_INVALID")
            require(
                order["order_date"] in {row["start_date"] for row in rows}
                and order["order_date"] <= day(order["receipt_date"]) <= rows[-1]["end_date"],
                "ROBUSTNESS_ORDER_DATES_INVALID",
            )
    require(
        len({r["order_id"] for r in orders}) == len(orders)
        and len({r["order_date"] for r in orders}) == len(orders),
        "ROBUSTNESS_DUPLICATE_ORDER",
    )
    require(
        not (
            gate["effective_order_action"] == "BLOCK"
            or not gate["recommendation_calculation_allowed"]
        )
        or not orders,
        "ROBUSTNESS_GATE_BYPASS",
    )
    return value

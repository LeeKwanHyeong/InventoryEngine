"""Recompute windows, five independent cost assumptions and terminal supply diagnostics."""

from decimal import Decimal, localcontext

from dsio_inventory_engine.inventory_contracts.values import (
    canonical_json,
    digest,
    hash_value,
    quantity_text,
    require,
)
from dsio_inventory_engine.simulate_inventory.application.step import InventoryState, advance_bucket
from .contracts import NUMBERS, PROFILES, input_errors, number, sealed, trace


def _validate_psi(value):
    rows = value["rows"]
    state = InventoryState(
        *(number(rows[0][key]) for key in ("boh_qty", "reserved_qty", "backorder_open_qty"))
    )
    for row in rows:

        def inbound(records, date_key):
            return sum(
                (
                    number(r["quantity"])
                    for r in records
                    if row["start_date"] <= r[date_key] <= row["end_date"]
                ),
                Decimal(0),
            )

        confirmed = inbound(value["initial_receipts"], "receipt_date")
        recommended = inbound(value["orders"], "receipt_date")
        state, expected = advance_bucket(
            state,
            confirmed_customer_order_qty=number(row["confirmed_customer_order_qty"]),
            net_forecast_qty=number(row["net_forecast_qty"]),
            confirmed_supplier_receipt_qty=confirmed,
            recommended_receipt_qty=recommended,
        )
        require(
            all(number(row[key]) == number(qty) for key, qty in expected.items()),
            "ROBUSTNESS_PSI_REPLAY_MISMATCH",
        )


def _rates(profile_id, unit_price):
    require(profile_id in PROFILES, "ROBUSTNESS_COST_PROFILE_INVALID")
    if profile_id == "FIXED_COMPATIBILITY":
        return tuple(map(Decimal, ("1", "10", "25", "2", "0")))
    if unit_price is None:
        return None
    c = number(unit_price["unit_price"])
    return (
        c * Decimal("0.40" if profile_id == "PRICE_HOLDING_HIGH" else "0.20") / 52,
        c,
        Decimal(100 if profile_id == "PRICE_ORDER_HIGH" else 25),
        c,
        Decimal(0) if profile_id == "PRICE_NO_FORECAST_PENALTY" else c,
    )


def _cost(metrics, profile_id, unit_price, weeks):
    rates = _rates(profile_id, unit_price)
    specification = {
        "profile_id": profile_id,
        "unit_price": unit_price,
        "precision": 40,
        "rounding": "HALF_EVEN",
        "week_basis": 52,
    }
    if rates is None:
        return {
            "status": "NOT_CALCULABLE",
            "reason_code": "MISSING_UNIT_PRICE",
            "cost_profile_hash": digest(specification),
            "components": None,
            "total_cost": None,
            "cost_per_week": None,
            "display_total_cost": None,
        }
    exposures = (
        metrics["on_hand_eoh_unit_weeks"],
        metrics["customer_backorder_unit_weeks"],
        str(metrics["order_count"]),
        metrics["ordered_qty"],
        metrics["forecast_shortage_qty"],
    )
    names = ("holding", "customer_backorder", "fixed_order", "variable_order", "forecast_shortage")
    components = {
        key: rate * number(qty) for key, rate, qty in zip(names, rates, exposures, strict=True)
    }
    total = sum(components.values(), Decimal(0))
    return {
        "status": "CALCULABLE",
        "reason_code": None,
        "cost_profile_hash": digest(specification),
        "components": {k: quantity_text(v) for k, v in components.items()},
        "total_cost": quantity_text(total),
        "cost_per_week": quantity_text(total / weeks),
        "display_total_cost": format(total.quantize(Decimal("0.01")), ".2f"),
    }


def _window(value, start, stop, profile_id):
    rows = value["rows"][start:stop]

    def total(key):
        return sum((number(row[key]) for row in rows), Decimal(0))

    demand = total("confirmed_customer_order_qty") + total("net_forecast_qty")
    fulfilled = total("fulfilled_confirmed_customer_order_qty") + total("fulfilled_forecast_qty")
    # Count by accepted decision date, NOT arrival date or the Source's 65-week total_cost.
    orders = [
        o
        for o in value["orders"]
        if rows[0]["start_date"] <= o["order_date"] <= rows[-1]["end_date"]
    ]
    eoh = total("on_hand_eoh_qty")
    price = value["binding"]["unit_price"]
    average = eoh / len(rows)
    gate = value["gate"]
    allowed = (
        gate["effective_order_action"] != "BLOCK" and gate["recommendation_calculation_allowed"]
    )
    fill = fulfilled / demand if demand else None
    metrics = {
        "start_index": start,
        "stop_index_exclusive": stop,
        "week_count": len(rows),
        "current_demand_qty": quantity_text(demand),
        "current_fulfilled_qty": quantity_text(fulfilled),
        "fill_rate": quantity_text(fill) if fill is not None else None,
        "service_status": "EVALUABLE" if demand else "NO_DEMAND_IN_HORIZON",
        "path_service_success": fill is not None
        and fill >= Decimal("0.95")
        and number(rows[-1]["backorder_close_qty"]) == 0
        and allowed,
        "calculation_gate_allowed": allowed,
        "source_review_required": gate["effective_order_action"] == "REVIEW",
        "on_hand_eoh_unit_weeks": quantity_text(eoh),
        "average_on_hand_eoh_qty": quantity_text(average),
        "average_on_hand_eoh_value": quantity_text(average * number(price["unit_price"]))
        if price is not None
        else None,
        "customer_backorder_unit_weeks": quantity_text(total("backorder_close_qty")),
        "ending_customer_backorder_qty": rows[-1]["backorder_close_qty"],
        "ending_on_hand_eoh_qty": rows[-1]["on_hand_eoh_qty"],
        "forecast_shortage_qty": quantity_text(total("forecast_shortage_qty")),
        "order_count": len(orders),
        "ordered_qty": quantity_text(sum((number(o["quantity"]) for o in orders), Decimal(0))),
    }
    metrics["cost"] = _cost(metrics, profile_id, price, len(rows))
    return metrics


def _terminal(value, index):
    boundary = value["rows"][index]["end_date"]
    known = sum(
        (number(r["quantity"]) for r in value["initial_receipts"] if r["receipt_date"] > boundary),
        Decimal(0),
    )
    recommended = sum(
        (
            number(r["quantity"])
            for r in value["orders"]
            if r["order_date"] <= boundary < r["receipt_date"]
        ),
        Decimal(0),
    )
    price = value["binding"]["unit_price"]
    return {
        "week_index": index,
        "on_hand_eoh_qty": value["rows"][index]["on_hand_eoh_qty"],
        "customer_backorder_qty": value["rows"][index]["backorder_close_qty"],
        "known_pipeline_qty": quantity_text(known),
        "recommended_pipeline_qty": quantity_text(recommended),
        "total_pipeline_qty": quantity_text(known + recommended),
        "pipeline_value": quantity_text((known + recommended) * number(price["unit_price"]))
        if price is not None
        else None,
    }


@input_errors
def score_trace(document, *, profile_id="PRICE_BASE"):
    """Arithmetic validation and scoring only; supplied Gate is NOT re-authorized here.

    Inputs are immutable by copy. Receipts and accepted orders must account for every
    row's inflow; roll-forward and customer priority are replayed by the common PSI step.
    A caller must separately verify Source, actual strategy and Guard authenticity.
    """
    value = trace(document)
    require(profile_id in PROFILES, "ROBUSTNESS_COST_PROFILE_INVALID")
    with localcontext(NUMBERS):
        body = {
            "input_trace_hash": digest(value),
            "binding": value["binding"],
            "path": value["path"],
            "candidate": value["candidate"],
            "source_result_hash": value["source_result_hash"],
            "profile_id": profile_id,
            "verification_profile": "PURE_PSI_ARITHMETIC_NOT_SOURCE_AUTHORITY",
            "status": value["status"],
            "failure_reason": value["failure_reason"],
            "score": None,
            "tail": None,
            "prefixes": None,
            "terminal": None,
        }
        if value["status"] == "SUCCEEDED":
            _validate_psi(value)
            body.update(
                score=_window(value, 0, 52, profile_id),
                tail=_window(value, 52, 65, profile_id),
                prefixes={str(end): _window(value, 0, end, profile_id) for end in (13, 26, 52)},
                terminal={str(end): _terminal(value, end) for end in (51, 64)},
            )
        return sealed("inventory-robustness-path-scorecard", body)


@input_errors
def verify_scorecard(result, document, *, expected_scorecard_hash):
    """Recalculate every window and cost, not merely check a caller's self-resealed hash."""
    hash_value(expected_scorecard_hash)
    require(
        result.get("content_hash") == expected_scorecard_hash, "ROBUSTNESS_SCORECARD_HASH_MISMATCH"
    )
    rebuilt = score_trace(document, profile_id=result["profile_id"])
    require(
        canonical_json(result) == canonical_json(rebuilt), "ROBUSTNESS_SCORECARD_REPLAY_MISMATCH"
    )

"""Shared, deterministic stock transition. Strategies never implement this arithmetic."""

from dataclasses import dataclass
from decimal import Decimal, localcontext

from dsio_inventory_engine.inventory_contracts.values import quantity_text, require


@dataclass(frozen=True)
class InventoryState:
    available_qty: Decimal
    reserved_qty: Decimal
    backorder_qty: Decimal


def advance_bucket(
    state: InventoryState,
    *,
    confirmed_customer_order_qty: Decimal,
    net_forecast_qty: Decimal,
    confirmed_supplier_receipt_qty: Decimal,
    recommended_receipt_qty: Decimal = Decimal(0),
) -> tuple[InventoryState, dict[str, str]]:
    """Receipts, old backlog, current orders, forecast; only actual orders carry over."""
    values = (
        state.available_qty,
        state.reserved_qty,
        state.backorder_qty,
        confirmed_customer_order_qty,
        net_forecast_qty,
        confirmed_supplier_receipt_qty,
        recommended_receipt_qty,
    )
    require(
        all(isinstance(v, Decimal) and v.is_finite() and v >= 0 for v in values),
        "INVALID_PSI_STEP_QUANTITY",
    )
    with localcontext() as ctx:
        ctx.prec = 40
        boh, reserved, backlog, confirmed, forecast, inbound, recommended = values
        available = boh + inbound + recommended
        fulfilled_bo = min(available, backlog)
        fulfilled_order = min(available - fulfilled_bo, confirmed)
        fulfilled_forecast = min(available - fulfilled_bo - fulfilled_order, forecast)
        eoh = available - fulfilled_bo - fulfilled_order - fulfilled_forecast
        close_bo = backlog + confirmed - fulfilled_bo - fulfilled_order
        shortage = forecast - fulfilled_forecast
        require(eoh >= 0 and close_bo >= 0 and shortage >= 0, "PSI_NEGATIVE_BALANCE")
        require(
            boh + inbound + recommended
            == eoh + fulfilled_bo + fulfilled_order + fulfilled_forecast,
            "PSI_STOCK_CONSERVATION",
        )
        require(
            backlog + confirmed == close_bo + fulfilled_bo + fulfilled_order,
            "PSI_ORDER_CONSERVATION",
        )
        quantities = {
            "boh_qty": boh,
            "reserved_qty": reserved,
            "on_hand_boh_qty": boh + reserved,
            "confirmed_supplier_receipt_qty": inbound,
            "recommended_receipt_qty": recommended,
            "available_qty": available,
            "backorder_open_qty": backlog,
            "fulfilled_backorder_qty": fulfilled_bo,
            "fulfilled_confirmed_customer_order_qty": fulfilled_order,
            "fulfilled_forecast_qty": fulfilled_forecast,
            "eoh_qty": eoh,
            "on_hand_eoh_qty": eoh + reserved,
            "backorder_close_qty": close_bo,
            "forecast_shortage_qty": shortage,
        }
        return InventoryState(eoh, reserved, close_bo), {
            key: quantity_text(value) for key, value in quantities.items()
        }

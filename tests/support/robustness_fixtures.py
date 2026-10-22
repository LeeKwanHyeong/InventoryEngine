"""Hand ledgers only: no stochastic generator, official Seed, strategy or Source approval.

Integer arithmetic deliberately independent of production advance_bucket. Dates and
orders describe tiny numerical examples, not a claim to satisfy a physical-LT Guard.
"""

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal

from dsio_inventory_engine.inventory_contracts.values import digest

W0 = date(2055, 1, 4)


def at(week):
    return (W0 + timedelta(weeks=week)).isoformat()


def candidate(key="POLICY-A", **changes):
    return {
        "candidate_id": key,
        "policy_kind": "ROP_FIXED_Q",
        "rmos": "0.5",
        "emos": "1",
        "target_service_level": "0.95",
        "review_cycle_weeks": 1,
        **changes,
    }


def binding():
    return {
        "company_cd": "DSE",
        "subs_cd": "C100",
        "site_cd": "V101",
        "item_id": "HAND-ITEM",
        "uom": "EA",
        "w0": at(0),
        "immutable_input_hash": digest("hand-common-input"),
        "candidate_mode": "LEGACY_STYLE",
        "catalog_hash": digest("hand-catalog"),
        "unit_price": {
            "unit_price": "250",
            "currency": "USD",
            "price_as_of": at(-1),
            "source_hash": digest("hand-price"),
        },
    }


def hand_trace(
    *,
    fill="0.99",
    split="NOMINAL",
    index=0,
    choice=None,
    boh=0,
    reserved=0,
    backlog=0,
    forecast=None,
    customer=None,
    orders=None,
    receipts=None,
    failed=False,
    gate="REVIEW",
):
    # Separate path identity from its realized content; natural equal paths are retained.
    seed_base = {
        "NOMINAL": 1000,
        "SELECTION": 2000,
        "VALIDATION": 3000,
        "HOLDOUT": 4000,
        "STRESS": 5000,
    }[split]
    if forecast is None:
        forecast = [1000000 if fill is not None else 0] + [0] * 64
    customer = customer or [0] * 65
    if orders is None:
        qty = int(Decimal(fill or "0") * forecast[0])
        orders = (
            [{"order_id": "O-0", "order_date": at(0), "receipt_date": at(0), "quantity": str(qty)}]
            if qty
            else []
        )
    receipts = receipts or []
    rows = []
    for i in range(65):
        inbound = sum(
            int(r["quantity"])
            for r in receipts
            if at(i) <= r["receipt_date"] <= (W0 + timedelta(weeks=i, days=6)).isoformat()
        )
        recommended = sum(
            int(o["quantity"])
            for o in orders
            if at(i) <= o["receipt_date"] <= (W0 + timedelta(weeks=i, days=6)).isoformat()
        )
        available = boh + inbound + recommended
        old = min(available, backlog)
        confirmed = min(available - old, customer[i])
        predicted = min(available - old - confirmed, forecast[i])
        end, close = (
            available - old - confirmed - predicted,
            backlog + customer[i] - old - confirmed,
        )
        quantities = dict(
            boh_qty=boh,
            reserved_qty=reserved,
            on_hand_boh_qty=boh + reserved,
            confirmed_supplier_receipt_qty=inbound,
            recommended_receipt_qty=recommended,
            available_qty=available,
            backorder_open_qty=backlog,
            fulfilled_backorder_qty=old,
            fulfilled_confirmed_customer_order_qty=confirmed,
            fulfilled_forecast_qty=predicted,
            eoh_qty=end,
            on_hand_eoh_qty=end + reserved,
            backorder_close_qty=close,
            forecast_shortage_qty=forecast[i] - predicted,
            confirmed_customer_order_qty=customer[i],
            net_forecast_qty=forecast[i],
        )
        rows.append(
            {
                "seq": i,
                "yyyyww": (W0 + timedelta(weeks=i)).strftime("%G%V"),
                "start_date": at(i),
                "end_date": (W0 + timedelta(weeks=i, days=6)).isoformat(),
                **{k: str(v) for k, v in quantities.items()},
            }
        )
        boh, backlog = end, close
    return {
        "binding": binding(),
        "path": {
            "path_id": f"HAND-{split}-{index}",
            "split": split,
            "seed": seed_base + index,
            "path_content_hash": digest({"forecast": forecast, "customer": customer}),
        },
        "candidate": deepcopy(choice or candidate()),
        "source_result_hash": digest(f"SOURCE-{split}-{index}"),
        "status": "FAILED" if failed else "SUCCEEDED",
        "failure_reason": "WORKER_FAILED" if failed else None,
        "gate": {"effective_order_action": gate, "recommendation_calculation_allowed": True},
        "rows": None if failed else rows,
        "orders": None if failed else orders,
        "initial_receipts": receipts,
    }


def bank(fills=None, *, split="SELECTION", choices=None, changes=None):
    sizes = {"NOMINAL": 1, "SELECTION": 8, "VALIDATION": 8, "HOLDOUT": 16, "STRESS": 4}
    fills = fills or ["0.99"] * sizes[split]
    choices = choices or [candidate()]
    traces = []
    for i, fill in enumerate(fills):
        for choice in choices:
            kwargs = changes(choice, i) if changes else {}
            traces.append(hand_trace(split=split, index=i, fill=fill, choice=choice, **kwargs))
    return {
        "binding": binding(),
        "expected_paths": [deepcopy(t["path"]) for t in traces if t["candidate"] == choices[0]],
        "candidates": choices,
        "traces": traces,
    }


def cost_trace():
    orders = [
        {"order_id": "CLEAR-OLD-BO", "order_date": at(1), "receipt_date": at(1), "quantity": "2"},
        {
            "order_id": "SCORE-ARRIVES-IN-TAIL",
            "order_date": at(51),
            "receipt_date": at(52),
            "quantity": "8",
        },
        {"order_id": "TAIL-ORDER", "order_date": at(52), "receipt_date": at(53), "quantity": "100"},
    ]
    receipts = [
        {"receipt_id": "KNOWN-TAIL", "receipt_date": at(60), "quantity": "3"},
        {"receipt_id": "KNOWN-AFTER-END", "receipt_date": at(70), "quantity": "7"},
    ]
    return hand_trace(
        forecast=[0, 0, 1] + [0] * 62,
        customer=[2] + [0] * 64,
        orders=orders,
        reserved=5,
        receipts=receipts,
    )

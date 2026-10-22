"""Independent deterministic Warm-up of supplied history, NOT an official path generator.

No production strategy, Guard, PSI step, random draw, DB or Source approval is imported.
The independent integer/Decimal ledger is checked against literal hand expectations.
"""

from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING

from dsio_inventory_engine.inventory_contracts.values import (
    canonical_json,
    day,
    digest,
    hash_value,
    identifier,
    item_identifier,
    quantity_text,
    require,
)
from .contracts import clone, input_errors, quantity, sealed, shape

GENERATOR_VERSION = "independent-robustness-warmup-v1"
Z95 = Decimal("1.644853626951")


def _ceil(value: Decimal) -> Decimal:
    return value.to_integral_value(rounding=ROUND_CEILING)


def _history_request(document: dict) -> dict:
    value = clone(document)
    shape(
        value,
        (
            "scope",
            "w0",
            "history_snapshot_id",
            "history_revision",
            "master_revision",
            "calendar_revision",
            "lead_time_days",
            "moq",
            "order_multiple",
            "known_future_customer_order_qty",
            "history",
        ),
    )
    scope = value["scope"]
    shape(scope, ("company_cd", "subs_cd", "site_cd", "item_id", "uom"))
    require(
        (scope["company_cd"], scope["subs_cd"], scope["site_cd"], scope["uom"])
        == ("DSE", "C100", "V101", "EA"),
        "ROBUSTNESS_SCOPE_INVALID",
    )
    item_identifier(scope["item_id"])
    w0 = date.fromisoformat(day(value["w0"]))
    require(w0.weekday() == 0, "WARMUP_CALENDAR_INVALID")
    for key in ("history_snapshot_id", "history_revision", "master_revision", "calendar_revision"):
        identifier(value[key])
    require(
        type(value["lead_time_days"]) is int
        and 0 <= value["lead_time_days"] <= 49
        and value["lead_time_days"] % 7 == 0,
        "WARMUP_LEAD_TIME_INVALID",
    )
    require(value["moq"] == value["order_multiple"] == "5", "WARMUP_FROZEN_LOT_MISMATCH")
    quantity(value["known_future_customer_order_qty"])
    rows = value["history"]
    require(type(rows) is list and len(rows) == 104, "WARMUP_COMPLETE_104_WEEKS_REQUIRED")
    for i, row in enumerate(rows):
        shape(row, ("start_date", "gross_demand_qty", "confirmed_customer_order_qty"))
        require(
            row["start_date"] == (w0 + timedelta(weeks=i - 104)).isoformat(),
            "WARMUP_HISTORY_GAP_OR_LOOKAHEAD",
        )
        require(
            quantity(row["gross_demand_qty"]) >= quantity(row["confirmed_customer_order_qty"]),
            "WARMUP_TOTAL_BELOW_KNOWN_ORDER",
        )
    return value


class DevelopmentSourceProducer:
    """Derive Source datasets from explicit pre-W0 values; never sample new demand."""

    @input_errors
    def produce(self, document: dict) -> dict:
        value = _history_request(document)
        w0, history = date.fromisoformat(value["w0"]), value["history"]
        # Indices 26..51 = W0-78..W0-53. No in-Warm-up demand enters reference stats.
        reference = [quantity(row["gross_demand_qty"]) for row in history[26:52]]
        mean = sum(reference, Decimal(0)) / 26
        variance = sum(((x - mean) ** 2 for x in reference), Decimal(0)) / 26
        sigma = variance.sqrt()
        lead_weeks = (value["lead_time_days"] + 6) // 7
        safety = _ceil(Z95 * sigma * Decimal(lead_weeks).sqrt())
        rop = _ceil(mean * lead_weeks + safety)
        raw_q = _ceil(mean * 52 / 12)
        lot = _ceil(max(raw_q, Decimal(5)) / 5) * 5 if mean else Decimal(0)
        available, backlog = _ceil(mean * (lead_weeks + 2)), Decimal(0)
        initial = quantity_text(available)
        policy = {
            "reference_mean": quantity_text(mean),
            "population_stddev": quantity_text(sigma),
            "reference_history_hash": digest(history[26:52]),
            "z95": str(Z95),
            "lead_weeks": lead_weeks,
            "safety_stock_qty": quantity_text(safety),
            "rop_qty": quantity_text(rop),
            "raw_monthly_q_qty": quantity_text(raw_q),
            "lot_qty": quantity_text(lot),
            "frozen_before_warmup": True,
        }
        ledger, orders, pending = [], [], []
        for index, demand in enumerate(history[52:]):
            begin = date.fromisoformat(demand["start_date"])
            position = (
                available + sum((quantity(o["quantity"]) for o in pending), Decimal(0)) - backlog
            )
            new_order = None
            if mean and position <= rop:
                due = begin + timedelta(days=value["lead_time_days"])
                new_order = {
                    "receipt_id": f"WARMUP-{index}",
                    "order_date": begin.isoformat(),
                    "due_date": due.isoformat(),
                    "receipt_date": (begin + timedelta(weeks=lead_weeks)).isoformat(),
                    "quantity": quantity_text(lot),
                }
                # No finite-horizon rejection: future arrivals remain supply commitments.
                pending.append(new_order)
                orders.append(new_order)
            arriving = [o for o in pending if o["receipt_date"] == begin.isoformat()]
            inbound = sum((quantity(o["quantity"]) for o in arriving), Decimal(0))
            gross, confirmed = (
                quantity(demand[k]) for k in ("gross_demand_qty", "confirmed_customer_order_qty")
            )
            consumed = min(gross, confirmed)
            net = gross - consumed
            stock = available + inbound
            old_filled = min(stock, backlog)
            customer_filled = min(stock - old_filled, confirmed)
            forecast_filled = min(stock - old_filled - customer_filled, net)
            end = stock - old_filled - customer_filled - forecast_filled
            close = backlog + confirmed - old_filled - customer_filled
            require(
                available + inbound == end + old_filled + customer_filled + forecast_filled
                and backlog + confirmed == close + old_filled + customer_filled,
                "WARMUP_CONSERVATION_FAILED",
            )
            numeric = {
                "boh_qty": available,
                "receipt_qty": inbound,
                "backorder_open_qty": backlog,
                "inventory_position_before_order": position,
                "gross_demand_qty": gross,
                "confirmed_customer_order_qty": confirmed,
                "forecast_consumed_qty": consumed,
                "net_forecast_qty": net,
                "fulfilled_backorder_qty": old_filled,
                "fulfilled_confirmed_customer_order_qty": customer_filled,
                "fulfilled_forecast_qty": forecast_filled,
                "eoh_qty": end,
                "backorder_close_qty": close,
                "forecast_shortage_qty": net - forecast_filled,
            }
            ledger.append(
                {
                    "week_index": index - 52,
                    "start_date": begin.isoformat(),
                    **{k: quantity_text(v) for k, v in numeric.items()},
                    "new_order_id": new_order["receipt_id"] if new_order else None,
                    "received_order_ids": sorted(o["receipt_id"] for o in arriving),
                }
            )
            pending = [o for o in pending if o not in arriving]
            available, backlog = end, close
        for qty in (available, backlog, rop, lot):
            quantity(quantity_text(qty))
        planning_mean = (
            sum((quantity(r["gross_demand_qty"]) for r in history[-26:]), Decimal(0)) / 26
        )
        forecast = _ceil(planning_mean)
        future = [
            {
                "yyyyww": (w0 + timedelta(weeks=i)).strftime("%G%V"),
                "start_date": (w0 + timedelta(weeks=i)).isoformat(),
                "gross_forecast_qty": quantity_text(forecast),
                "confirmed_customer_order_qty": value["known_future_customer_order_qty"],
            }
            for i in range(65)
        ]
        return sealed(
            "inventory-robustness-development-source",
            {
                "source_type": "SYNTHETIC_BOH",
                "generator_version": GENERATOR_VERSION,
                "source_request_hash": digest(value),
                "scope": value["scope"],
                "w0": value["w0"],
                "history_snapshot_id": value["history_snapshot_id"],
                "history_revision": value["history_revision"],
                "history_content_hash": digest(history),
                "uncensored_history": history,
                "master_revision": value["master_revision"],
                "calendar_revision": value["calendar_revision"],
                "physical_constraints": {
                    k: value[k] for k in ("lead_time_days", "moq", "order_multiple")
                },
                "reference_policy": policy,
                "warmup_initial_boh_qty": initial,
                "warmup_ledger": ledger,
                "warmup_ledger_hash": digest(ledger),
                "warmup_orders": orders,
                "w0_position": {
                    "position_date": value["w0"],
                    "on_hand_qty": quantity_text(available),
                    "reserved_qty": "0",
                    "available_qty": quantity_text(available),
                    "backorder_qty": quantity_text(backlog),
                },
                "w0_due_in": sorted(pending, key=lambda o: o["receipt_id"]),
                "planning_forecast": future,
                "planning_forecast_hash": digest(future),
                "w0_history_status": "SUFFICIENT_POSITIVE_DEMAND_HISTORY"
                if planning_mean
                else "ZERO_DEMAND_HISTORY",
                "official_paths_generated": False,
                "source_authority_verified": False,
            },
        )

    @input_errors
    def verify(self, result: dict, document: dict, *, expected_source_hash: str) -> None:
        hash_value(expected_source_hash)
        require(result.get("content_hash") == expected_source_hash, "WARMUP_SOURCE_HASH_MISMATCH")
        require(
            canonical_json(result) == canonical_json(self.produce(document)),
            "WARMUP_SOURCE_REPLAY_MISMATCH",
        )

"""Independent reference ledger. Do not import production PSI or policy calculators."""

from datetime import date, timedelta
from decimal import Context, Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, localcontext

from dsio_inventory_engine.inventory_contracts.training import SERVICE_Z, VERSION
from dsio_inventory_engine.inventory_contracts.values import digest, quantity_text as q, require
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, decimal_string

NUMBERS = Context(prec=40, rounding=ROUND_HALF_EVEN)


def ceil_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


def demand_series(data: dict, item: dict) -> list[Decimal | None]:
    rows = {
        r["yyyyww"]: Decimal(r["demand_qty"])
        for r in data["demand"]
        if r["item_id"] == item["item_id"]
    }
    return [
        None if i < item["active_from_index"] else rows.get(c["yyyyww"], Decimal(0))
        for i, c in enumerate(data["calendar"])
    ]


def observed_demand(series: list[Decimal | None], index: int) -> Decimal:
    value = series[index]
    if value is None:
        raise InventoryInputError("INACTIVE_DEMAND")
    return value


def reference_policy(series: list, index: int, item: dict, data: dict, scenario: dict) -> dict:
    """Rolling prior-only ddof=1 statistics; raw policies rounded independently."""
    count = index - item["active_from_index"]
    require(count >= 13, "INSUFFICIENT_HISTORY")
    lookback = 26 if count >= 26 and data["lookback_weeks"] == 26 else 13
    history = series[index - lookback : index]
    require(len(history) == lookback and None not in history, "INSUFFICIENT_HISTORY")
    with localcontext(NUMBERS):
        mean = sum(history, Decimal(0)) / lookback
        std = (sum((x - mean) ** 2 for x in history) / (lookback - 1)).sqrt()
        service = scenario["service_level_code"] or item["service_level_code"]
        lead = Decimal(item["lead_time_weeks"])
        safety = Decimal(SERVICE_Z[service]) * std * lead.sqrt()
        step = Decimal(item["quantity_step"])
        return {
            "history_start_index": index - lookback,
            "history_end_index_exclusive": index,
            "lookback_weeks": lookback,
            "lookback_fallback": lookback != data["lookback_weeks"],
            "mean_demand_qty": q(mean),
            "sample_stddev_qty": q(std),
            "service_level_code": service,
            "z": SERVICE_Z[service],
            "safety_stock_qty": q(ceil_step(safety, step)),
            "rop_qty": q(ceil_step(mean * lead + safety, step)),
            "target_inventory_qty": q(
                ceil_step(mean * (lead + data["replenishment_cycle_weeks"]) + safety, step)
            ),
            "policy_source": item["policy_source"],
            "policy_reference": item["policy_reference"],
        }


def order_delay(scenario: dict, order_id: str, planned_index: int) -> int:
    kind = scenario["delay_kind"]
    applies = (
        kind == "FIXED_DELAY"
        or kind == "SELECTED_ORDER_DELAY"
        and order_id in scenario["selected_order_ids"]
        or kind == "DISRUPTION_WINDOW"
        and scenario["disruption_from_index"] <= planned_index < scenario["disruption_to_index"]
    )
    return scenario["delay_weeks"] if applies else 0


def due_date(data: dict, index: int) -> str:
    # Calendar beyond the supplied horizon is an explicit synthetic weekly extension.
    if index < len(data["calendar"]):
        return data["calendar"][index]["start_date"]
    return (
        date.fromisoformat(data["calendar"][-1]["start_date"])
        + timedelta(weeks=index - len(data["calendar"]) + 1)
    ).isoformat()


def make_order(
    data: dict,
    item: dict,
    scenario: dict,
    index: int,
    qty: Decimal,
    prefix: str,
    timing: str,
    *,
    delay_start_index: int | None = None,
    schedule: dict | None = None,
) -> dict:
    order_id = f"{prefix}:{item['item_id']}:{index}"
    if len(order_id) > 128:
        order_id = f"{prefix}:{digest(item['item_id'])}:{index}"
    planned = index + item["lead_time_weeks"]
    if schedule is not None:
        order_id, planned = schedule["order_id"], schedule["planned_due_index"]
    delay = (
        order_delay(scenario, order_id, planned)
        if delay_start_index is None or planned >= delay_start_index
        else 0
    )
    return {
        "order_id": order_id,
        "item_id": item["item_id"],
        "uom": item["uom"],
        "site_cd": data["context"]["site_cd"],
        "order_index": index,
        "order_timing": timing,
        "quantity": decimal_string(q(qty)),
        "planned_due_index": planned,
        "actual_due_index": planned + delay,
        "due_date": due_date(data, planned) if schedule is None else schedule["due_date"],
        "actual_due_date": due_date(data, planned + delay),
        "delay_weeks": delay,
        "delay_scenario_code": scenario["scenario_id"],
        "supply_type": "SYNTHETIC_PURCHASE_ORDER",
    }


def reference_flow(boh: Decimal, backlog: Decimal, receipt: Decimal, demand: Decimal) -> dict:
    """Actual unconstrained demand only, not actual + forecast consumption."""
    available = boh + receipt
    old_filled = min(available, backlog)
    current_filled = min(available - old_filled, demand)
    fulfilled = old_filled + current_filled
    eoh, close = available - fulfilled, backlog + demand - fulfilled
    require(eoh >= 0 and close >= 0, "REFERENCE_CONSERVATION")
    return {
        "boh_qty": q(boh),
        "receipt_qty": q(receipt),
        "demand_qty": q(demand),
        "backorder_open_qty": q(backlog),
        "backorder_fulfilled_qty": q(old_filled),
        "current_demand_fulfilled_qty": q(current_filled),
        "fulfilled_qty": q(fulfilled),
        "backorder_close_qty": q(close),
        "eoh_qty": q(eoh),
    }


def constrained_order(raw: Decimal, occupied: Decimal, item: dict) -> tuple[Decimal, list[str]]:
    if raw <= 0:
        return Decimal(0), []
    lot, moq = Decimal(item["lot_multiple"]), Decimal(item["moq"])
    qty = ceil_step(max(raw, moq), lot)
    reasons = ["MOQ_OR_MULTIPLE_ROUND_UP"] if qty != raw else []
    cap = item["physical_capacity_qty"]
    if cap is not None:
        headroom = max(Decimal(0), Decimal(cap) - occupied)
        maximum = (headroom / lot).to_integral_value(rounding=ROUND_FLOOR) * lot
        if qty > maximum:
            qty = maximum if maximum >= moq else Decimal(0)
            reasons.append("CONSERVATIVE_CAPACITY_LIMIT")
    return qty, reasons


def generate_warmup(
    data: dict, origin: int, scenario: dict, *, delay_start_index: int | None = None
) -> dict:
    """Caller supplies a validated TrainingRequest; each split resets independently."""
    with localcontext(NUMBERS):
        return _warmup(data, origin, scenario, delay_start_index)


def _warmup(data: dict, origin: int, scenario: dict, delay_start_index: int | None) -> dict:
    start = origin - data["warmup_weeks"]
    require(start >= 13 and origin <= len(data["calendar"]), "INSUFFICIENT_HISTORY")
    ledger, policies, orders, positions = [], [], [], []
    for item in data["items"]:
        series = demand_series(data, item)
        policy = reference_policy(series, start, item, data, scenario)
        boh, backlog = Decimal(policy["target_inventory_qty"]), Decimal(0)
        cap = item["physical_capacity_qty"]
        require(cap is None or boh <= Decimal(cap), "WARMUP_SEED_EXCEEDS_CAPACITY")
        pending: list[dict] = []
        for index in range(start, origin):
            receipt = sum(
                (Decimal(o["quantity"]) for o in pending if o["actual_due_index"] == index),
                Decimal(0),
            )
            pending = [o for o in pending if o["actual_due_index"] > index]
            flow = reference_flow(boh, backlog, receipt, observed_demand(series, index))
            boh, backlog = Decimal(flow["eoh_qty"]), Decimal(flow["backorder_close_qty"])
            open_qty = sum((Decimal(o["quantity"]) for o in pending), Decimal(0))
            position = boh + open_qty - backlog
            policy = reference_policy(series, index, item, data, scenario)
            raw = (
                max(Decimal(0), Decimal(policy["target_inventory_qty"]) - position)
                if position <= Decimal(policy["rop_qty"])
                else Decimal(0)
            )
            qty, reasons = constrained_order(raw, boh + open_qty, item)
            if qty:
                order = make_order(
                    data,
                    item,
                    scenario,
                    index,
                    qty,
                    "WARM",
                    "BUCKET_END",
                    delay_start_index=delay_start_index,
                )
                pending.append(order)
                orders.append(order)
            key = {
                "item_id": item["item_id"],
                "uom": item["uom"],
                "week_index": index,
                "yyyyww": data["calendar"][index]["yyyyww"],
            }
            ledger.append(
                {
                    **key,
                    **flow,
                    "open_order_qty_before_order": q(open_qty),
                    "inventory_position_qty": q(position),
                    "raw_order_qty": q(raw),
                    "order_qty": q(qty),
                    "adjustment_reasons": reasons,
                }
            )
            policies.append({**key, **policy})
        position_row = {
            **data["context"],
            "simulation_run_id": data["simulation_run_id"],
            "scenario_id": scenario["scenario_id"],
            "generator_version": VERSION,
            "item_id": item["item_id"],
            "uom": item["uom"],
            "position_date": due_date(data, origin),
            "position_source_type": "SYNTHETIC_BOH",
            "on_hand_qty": decimal_string(q(boh)),
            "reserved_qty": "0",
            "available_qty": q(boh),
            "backorder_qty": decimal_string(q(backlog)),
            "prior_eoh_qty": q(boh),
            "prior_position_date": (
                date.fromisoformat(due_date(data, origin)) - timedelta(days=1)
            ).isoformat(),
            "demand_snapshot_id": data["demand_snapshot_id"],
            "policy_snapshot_id": data["policy_snapshot_id"],
            "legacy_target_inventory_qty": item["legacy_target_inventory_qty"],
            "synthetic_target_inventory_qty": policy["target_inventory_qty"],
        }
        position_row["content_hash"] = digest(position_row)
        positions.append(position_row)
    open_orders = [o for o in orders if o["actual_due_index"] >= origin]
    datasets = {
        "positions": positions,
        "open_orders": open_orders,
        "orders": orders,
        "ledger": ledger,
        "policies": policies,
    }
    return {
        **datasets,
        "origin_index": origin,
        "warmup_start_index": start,
        "manifest": {
            "generator_contract_id": "io-synthetic-boh-generator-v1",
            "generator_version": VERSION,
            "simulation_run_id": data["simulation_run_id"],
            "scenario_id": scenario["scenario_id"],
            "random_seed": None,
            "stddev_method": "SAMPLE_DDOF_1",
            "z_value_mapping_version": "normal-service-level-v1",
            "dataset_hashes": {k: digest(v) for k, v in datasets.items()},
            "dataset_row_counts": {k: len(v) for k, v in datasets.items()},
            "verification_status": "COMPUTED_LOCALLY_NOT_SEALED",
        },
    }

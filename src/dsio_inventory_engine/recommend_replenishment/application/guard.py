"""Source constraints dominate strategy proposals. No database or learned arithmetic."""

from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, localcontext

from dsio_inventory_engine.inventory_contracts.values import quantity_text, require


def policy_at(policies: list[dict], decision_date: str) -> dict:
    applicable = [p for p in policies if p["effective_from"] <= decision_date < p["effective_to"]]
    require(len(applicable) == 1, "MISSING_DECISION_POLICY")
    return applicable[0]


def map_arrival(
    decision_date: str, lead_time_days: int, calendar: list[dict]
) -> tuple[str, dict | None]:
    # Zero days is explicitly same-bucket at the decision boundary, never a midweek arrival.
    require(lead_time_days <= 3660, "LEAD_TIME_RANGE")
    due = (date.fromisoformat(decision_date) + timedelta(days=lead_time_days)).isoformat()
    return due, next((b for b in calendar if b["start_date"] >= due), None)


def capacity_headroom(observation: dict, arrival: dict) -> Decimal | None:
    """Conservative commitment reservation: do not pre-spend forecast-dependent departures.

    Check every remaining bucket's cap, including future effective cap reductions.
    This is deliberately stricter than projected PSI occupancy and labelled as such.
    """
    physical = Decimal(observation["state"]["on_hand_qty"])
    incoming: dict[str, Decimal] = {}
    future_weeks = {b["yyyyww"] for b in observation["calendar"]}
    for row in observation["pending_supply"]:
        week = row["receipt_bucket"]
        if week not in future_weeks:
            # Overdue or beyond-horizon commitments still consume capacity. This
            # reserves space without asserting a new actual arrival date.
            physical += Decimal(row["quantity"])
        else:
            incoming[week] = incoming.get(week, Decimal(0)) + Decimal(row["quantity"])
    room = None
    for bucket in observation["calendar"]:
        physical += incoming.get(bucket["yyyyww"], Decimal(0))
        if bucket["seq"] < arrival["seq"]:
            continue
        policy = policy_at(observation["policy_schedule"], bucket["start_date"])
        cap = policy["physical_max_capacity"]
        if cap is not None:
            headroom = max(Decimal(0), Decimal(cap) - physical)
            room = headroom if room is None else min(room, headroom)
    return room


def validate_action(observation: dict, proposal: dict) -> dict:
    """Return accepted/adjusted/rejected evidence; never silently clip to an invalid lot."""
    with localcontext() as ctx:
        ctx.prec = 40
        return _validate_action(observation, proposal)


def _validate_action(observation: dict, proposal: dict) -> dict:
    action = proposal["action_type"]
    requested = Decimal(proposal["quantity"])
    control, policy = observation["control"], observation["policy"]
    rule = observation["quantity_rule"]
    scale = rule.get("planning_scale", rule.get("scale"))
    physical_scale = rule.get("physical_scale", rule.get("scale"))
    for value in [
        requested,
        *[Decimal(v) for v in proposal["calculated_policy"].values() if v is not None],
    ]:
        require(value.as_tuple().exponent >= -scale, "PROPOSAL_UOM_PRECISION")
    position = Decimal(observation["state"]["inventory_position_qty"])
    raw = max(Decimal(0), requested - position) if action == "ORDER_UP_TO" else requested
    result = {
        "decision_id": observation["decision_id"],
        "item_id": observation["item_id"],
        "uom": observation["uom"],
        "decision_date": observation["decision_date"],
        "action_type": action,
        "requested_qty": quantity_text(requested),
        "inventory_position_qty": quantity_text(position),
        "raw_order_qty": quantity_text(raw),
        "lot_rounded_qty": "0",
        "accepted_order_qty": "0",
        "uncovered_order_qty": quantity_text(raw),
        "due_date": None,
        "receipt_date": None,
        "receipt_bucket": None,
        "capacity_headroom_qty": None,
        "status": "NO_ORDER",
        "reason_codes": [],
        "source_policy_id": policy["policy_id"],
        "source_policy": policy,
        "calculated_policy": proposal["calculated_policy"],
        "effective_target_inventory_qty": quantity_text(requested)
        if action == "ORDER_UP_TO"
        else None,
        "approval_reference": observation["approval_reference"],
        "capacity_mode": observation["capacity_mode"],
    }

    def reject(reason: str) -> dict:
        result.update(status="REJECTED", reason_codes=[reason], effective_target_inventory_qty=None)
        return result

    if action not in observation["allowed_action_types"]:
        return reject("ACTION_NOT_APPROVED")
    if action == "ORDER_UP_TO" and not (
        Decimal(control["min_target_qty"]) <= requested <= Decimal(control["max_target_qty"])
    ):
        return reject("TARGET_OUTSIDE_APPROVED_BOUNDS")
    if raw == 0:
        result["reason_codes"] = ["HOLD" if action == "HOLD" else "NO_NET_REQUIREMENT"]
        return result
    if observation["decision_date"] not in control["order_dates"]:
        return reject("ORDER_CALENDAR_CLOSED")
    due, arrival = map_arrival(
        observation["decision_date"], policy["lead_time_days"], observation["calendar"]
    )
    result["due_date"] = due
    if arrival is None:
        return reject("ARRIVAL_OUTSIDE_PLAN_HORIZON")
    result.update(receipt_date=arrival["start_date"], receipt_bucket=arrival["yyyyww"])
    multiple, moq = Decimal(policy["order_multiple"]), Decimal(policy["moq"])
    require(
        multiple > 0
        and moq >= 0
        and all(v.as_tuple().exponent >= -physical_scale for v in (multiple, moq)),
        "PHYSICAL_LOT_PRECISION",
    )
    rounded = (max(moq, raw) / multiple).to_integral_value(rounding=ROUND_CEILING) * multiple
    result["lot_rounded_qty"] = quantity_text(rounded)
    headroom = capacity_headroom(observation, arrival)
    result["capacity_headroom_qty"] = None if headroom is None else quantity_text(headroom)
    limit = Decimal(control["max_order_qty"])
    if headroom is not None:
        limit = min(limit, headroom)
    accepted = min(rounded, (limit / multiple).to_integral_value(rounding=ROUND_FLOOR) * multiple)
    if accepted <= 0 or accepted < moq:
        return reject("NO_FEASIBLE_ORDER_QUANTITY")
    require(accepted.as_tuple().exponent >= -physical_scale, "PHYSICAL_ORDER_PRECISION")
    reasons = []
    if rounded != raw:
        reasons.append("MOQ_OR_MULTIPLE_ROUND_UP")
    if accepted != rounded:
        reasons.append("APPROVED_LIMIT_OR_CAPACITY_REDUCTION")
    if due != arrival["start_date"]:
        reasons.append("ARRIVAL_ROUNDED_FORWARD")
    result.update(
        accepted_order_qty=quantity_text(accepted),
        uncovered_order_qty=quantity_text(max(Decimal(0), raw - accepted)),
        status="ADJUSTED" if reasons else "ACCEPTED",
        reason_codes=reasons or ["CONSTRAINTS_PASSED"],
    )
    return result

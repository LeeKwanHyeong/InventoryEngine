"""Chronological, actual-demand reference world, not the production planning PSI.

All strategy families can supply quantities to the same step interface. Future
actuals and realized receipt dates never appear in observation payloads.
"""

import copy
from datetime import date
from decimal import Decimal, localcontext

from dsio_inventory_engine.inventory_contracts.training import TrainingRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.values import (
    decimal_string,
    digest,
    identifier,
    quantity_text as q,
    require,
)
from .dataset import feature, splits
from .reference import (
    NUMBERS,
    constrained_order,
    demand_series,
    generate_warmup,
    make_order,
    observed_demand,
    reference_flow,
)


class ReferenceEpisode:
    """In-process research interface, not a sandbox for untrusted strategy code."""

    def __init__(
        self,
        request: TrainingRequest,
        deployment: DeploymentScope,
        split: str,
        scenario_id: str,
        *,
        delay_scope: str = "INCLUDING_WARMUP",
    ):
        data = request.to_dict()
        require(deployment.environment == "DEVELOPMENT", "SYNTHETIC_PRODUCTION_FORBIDDEN")
        require(
            deployment.company_cd == data["context"]["company_cd"], "DEPLOYMENT_COMPANY_MISMATCH"
        )
        selected = [(s, a, b) for s, a, b in splits(data) if s == split]
        require(len(selected) == 1, "UNKNOWN_SPLIT")
        scenarios = [s for s in data["scenarios"] if s["scenario_id"] == scenario_id]
        require(len(scenarios) == 1, "UNKNOWN_SCENARIO")
        self._data, self._scenario = data, scenarios[0]
        self._series = {item["item_id"]: demand_series(data, item) for item in data["items"]}
        self._split, self._start, self._end = selected[0]
        self._index = self._start
        require(delay_scope in ("INCLUDING_WARMUP", "EPISODE_RECEIPTS_ONLY"), "INVALID_DELAY_SCOPE")
        self._initial = generate_warmup(
            data,
            self._start,
            self._scenario,
            delay_start_index=self._start if delay_scope == "EPISODE_RECEIPTS_ONLY" else None,
        )
        self._pending = copy.deepcopy(self._initial["open_orders"])
        self._state = {
            p["item_id"]: (Decimal(p["available_qty"]), Decimal(p["backorder_qty"]))
            for p in self._initial["positions"]
        }
        self._ledger: list[dict] = []
        self._orders: list[dict] = []
        self._decisions: list[dict] = []

    @property
    def done(self) -> bool:
        return self._index >= self._end

    def observe(self) -> dict:
        require(not self.done, "EPISODE_FINISHED")
        result = []
        with localcontext(NUMBERS):
            for item in self._data["items"]:
                boh, backlog = self._state[item["item_id"]]
                # Keep overdue orders visible; do not reveal the synthetic actual due date.
                pending = [
                    {k: order[k] for k in ("order_id", "quantity", "planned_due_index", "due_date")}
                    | {"overdue": order["planned_due_index"] < self._index}
                    for order in self._pending
                    if order["item_id"] == item["item_id"]
                ]
                position = (
                    boh + sum((Decimal(o["quantity"]) for o in pending), Decimal(0)) - backlog
                )
                result.append(
                    {
                        "item_id": item["item_id"],
                        "uom": item["uom"],
                        "boh_qty": q(boh),
                        "backorder_qty": q(backlog),
                        "inventory_position_qty": q(position),
                        "pending_supply": pending,
                        "constraints": copy.deepcopy(item),
                        "effective_service_level_code": self._scenario["service_level_code"]
                        or item["service_level_code"],
                        "forecast": feature(
                            self._data, item, self._index, self._series[item["item_id"]]
                        ),
                    }
                )
        return {
            "contract_id": "io-reference-observation-v1",
            "split": self._split,
            "week_index": self._index,
            "decision_timing": "BUCKET_START_BEFORE_RECEIPTS",
            "cost_profile": copy.deepcopy(self._scenario["cost"]),
            "items": result,
        }

    def step(self, actions: dict[str, str]) -> dict:
        require(not self.done, "EPISODE_FINISHED")
        require(type(actions) is dict and set(actions) == set(self._state), "ACTION_UNIVERSE")
        parsed = {k: Decimal(decimal_string(v)) for k, v in actions.items()}
        with localcontext(NUMBERS):
            return self._step(parsed)

    def step_admitted(self, actions: dict[str, dict], *, observation_hash: str) -> dict:
        """Trusted local adapter transport AFTER production guard; no second policy guard.

        Only schedule/UOM/identity integrity is checked here. This is not an
        authorization boundary for untrusted callers. Preflight is atomic.
        """
        require(not self.done, "EPISODE_FINISHED")
        require(observation_hash == digest(self.observe()), "STALE_WORLD_OBSERVATION")
        require(type(actions) is dict and set(actions) == set(self._state), "ACTION_UNIVERSE")
        ids = {o["order_id"] for o in self._initial["orders"] + self._orders}
        parsed = {}
        for item in self._data["items"]:
            action = actions[item["item_id"]]
            require(
                type(action) is dict
                and set(action) == {"quantity", "order_id", "due_date", "planned_due_index"},
                "ADMITTED_ACTION_SHAPE",
            )
            qty = Decimal(decimal_string(action["quantity"]))
            require(qty % Decimal(item["quantity_step"]) == 0, "ADMITTED_ACTION_UOM")
            if qty == 0:
                require(
                    all(action[k] is None for k in ("order_id", "due_date", "planned_due_index")),
                    "ZERO_ACTION_SCHEDULE",
                )
            else:
                order_id = identifier(action["order_id"])
                require(order_id not in ids, "DUPLICATE_ORDER_ID")
                ids.add(order_id)
                index = action["planned_due_index"]
                require(
                    type(index) is int and self._index <= index < self._end,
                    "ADMITTED_ACTION_HORIZON",
                )
                due = action["due_date"]
                require(type(due) is str, "ADMITTED_ACTION_DATE")
                try:
                    valid_date = date.fromisoformat(due).isoformat() == due
                except ValueError:
                    valid_date = False
                require(valid_date, "ADMITTED_ACTION_DATE")
                calendar = self._data["calendar"]
                require(
                    calendar[self._index]["start_date"] <= due <= calendar[index]["start_date"]
                    and (index == self._index or calendar[index - 1]["start_date"] < due),
                    "ADMITTED_ACTION_DATE_MAPPING",
                )
            parsed[item["item_id"]] = qty
        with localcontext(NUMBERS):
            return self._step(parsed, actions)

    def _step(self, actions: dict[str, Decimal], schedules: dict | None = None) -> dict:
        observation_hash = digest(self.observe())
        week_rows = []
        costs = self._scenario["cost"]
        admitted = {}
        # Preflight every item before mutating the episode (including rounded overflow).
        for item in self._data["items"]:
            item_id = item["item_id"]
            boh, backlog = self._state[item_id]
            occupied = boh + sum(
                (Decimal(o["quantity"]) for o in self._pending if o["item_id"] == item_id),
                Decimal(0),
            )
            qty, reasons = (
                constrained_order(actions[item_id], occupied, item)
                if schedules is None
                else (actions[item_id], ["PRODUCTION_GUARD_ADMITTED"])
            )
            decimal_string(q(qty))
            admitted[item_id] = (qty, reasons)
        for item in self._data["items"]:
            item_id, index = item["item_id"], self._index
            boh, backlog = self._state[item_id]
            qty, reasons = admitted[item_id]
            decision = {
                "week_index": index,
                "item_id": item_id,
                "requested_order_qty": q(actions[item_id]),
                "accepted_order_qty": q(qty),
                "adjustment_reasons": reasons,
                "observation_hash": observation_hash,
            }
            self._decisions.append(decision)
            if qty:
                order = make_order(
                    self._data,
                    item,
                    self._scenario,
                    index,
                    qty,
                    "EVAL",
                    "BUCKET_START_BEFORE_RECEIPTS",
                    schedule=None if schedules is None else schedules[item_id],
                )
                self._orders.append(order)
                self._pending.append(order)
            due = [
                o
                for o in self._pending
                if o["item_id"] == item_id and o["actual_due_index"] == index
            ]
            receipt = sum((Decimal(o["quantity"]) for o in due), Decimal(0))
            self._pending = [o for o in self._pending if o not in due]
            flow = reference_flow(
                boh, backlog, receipt, observed_demand(self._series[item_id], index)
            )
            eoh, close = Decimal(flow["eoh_qty"]), Decimal(flow["backorder_close_qty"])
            self._state[item_id] = (eoh, close)
            components = {
                "holding_cost": eoh * Decimal(costs["holding_per_unit_week"]),
                "backlog_cost": close * Decimal(costs["backlog_per_unit_week"]),
                "order_fixed_cost": Decimal(costs["fixed_per_order"]) if qty else Decimal(0),
                "purchase_cost": qty * Decimal(costs["purchase_per_unit"]),
                "terminal_backlog_cost": close * Decimal(costs["terminal_backlog_per_unit"])
                if index == self._end - 1
                else Decimal(0),
            }
            row = {
                "item_id": item_id,
                "uom": item["uom"],
                "week_index": index,
                **flow,
                "order_qty": q(qty),
                **{k: q(v) for k, v in components.items()},
                "total_cost": q(sum(components.values(), Decimal(0))),
            }
            self._ledger.append(row)
            week_rows.append(row)
        self._index += 1
        return {
            "rows": copy.deepcopy(week_rows),
            "done": self.done,
            "reward": q(-sum((Decimal(r["total_cost"]) for r in week_rows), Decimal(0))),
        }

    def result(self) -> dict:
        require(self.done, "EPISODE_NOT_FINISHED")
        with localcontext(NUMBERS):
            metrics = []
            for item in self._data["items"]:
                rows = [r for r in self._ledger if r["item_id"] == item["item_id"]]
                demand = sum((Decimal(r["demand_qty"]) for r in rows), Decimal(0))
                fulfilled = sum(
                    (Decimal(r["current_demand_fulfilled_qty"]) for r in rows), Decimal(0)
                )
                metrics.append(
                    {
                        "item_id": item["item_id"],
                        "uom": item["uom"],
                        "demand_qty": q(demand),
                        "on_time_fulfilled_qty": q(fulfilled),
                        "on_time_fill_rate": q(fulfilled / demand) if demand else None,
                        "current_demand_stockout_weeks": sum(
                            Decimal(r["current_demand_fulfilled_qty"]) < Decimal(r["demand_qty"])
                            for r in rows
                        ),
                        "backlog_weeks": sum(Decimal(r["backorder_close_qty"]) > 0 for r in rows),
                        "terminal_on_hand_qty": rows[-1]["eoh_qty"],
                        "terminal_backorder_qty": rows[-1]["backorder_close_qty"],
                        "terminal_open_supply_qty": q(
                            sum(
                                (
                                    Decimal(o["quantity"])
                                    for o in self._pending
                                    if o["item_id"] == item["item_id"]
                                ),
                                Decimal(0),
                            )
                        ),
                        "total_cost": q(sum((Decimal(r["total_cost"]) for r in rows), Decimal(0))),
                    }
                )
            result = {
                "split": self._split,
                "scenario_id": self._scenario["scenario_id"],
                "initial": self._initial,
                "ledger": self._ledger,
                "orders": self._orders,
                "decisions": self._decisions,
                "terminal_open_orders": self._pending,
                "metrics": metrics,
                "currency": self._scenario["cost"]["currency"],
                "total_cost": q(sum((Decimal(r["total_cost"]) for r in metrics), Decimal(0))),
                "cost_convention": "EOH_AND_CLOSING_BO_PER_WEEK_PURCHASE_AT_ORDER_NO_SALVAGE",
                "cycle_service_level_estimated": False,
                "initial_pipeline_purchase_cost_included": False,
            }
            result["content_hash"] = digest(result)
            # Caller mutation cannot change episode state or subsequent result hashes.
            return copy.deepcopy(result)

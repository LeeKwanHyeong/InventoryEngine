"""One observation builder for planned PSI and actual-demand evaluation."""

from decimal import Decimal

from dsio_inventory_engine.inventory_contracts.replenishment import ReplenishmentObservation
from dsio_inventory_engine.inventory_contracts.values import canonical_json, digest, quantity_text


def build_observation(
    *,
    context: dict,
    input_hash: str,
    item: dict,
    bucket: dict,
    calendar: list[dict],
    future_demand: list[dict],
    policy: dict,
    policy_schedule: list[dict],
    control: dict,
    quantity_rule: dict,
    available_qty: Decimal,
    reserved_qty: Decimal,
    backorder_qty: Decimal,
    pending_supply: list[dict],
    execution: dict,
) -> ReplenishmentObservation:
    position = (
        available_qty
        + sum((Decimal(r["quantity"]) for r in pending_supply), Decimal(0))
        - backorder_qty
    )
    return ReplenishmentObservation(
        canonical_json(
            {
                "contract_id": "io-replenishment-observation-v1",
                "decision_id": "DEC-" + digest([input_hash, item["item_id"], bucket["yyyyww"]]),
                "input_content_hash": input_hash,
                "context": context,
                "item_id": item["item_id"],
                "uom": item["uom"],
                "decision_date": bucket["start_date"],
                "bucket": bucket,
                "calendar": calendar,
                "future_demand": future_demand,
                "policy": policy,
                "policy_schedule": policy_schedule,
                "control": control,
                "quantity_rule": quantity_rule,
                "state": {
                    "available_qty": quantity_text(available_qty),
                    "reserved_qty": quantity_text(reserved_qty),
                    "on_hand_qty": quantity_text(available_qty + reserved_qty),
                    "backorder_qty": quantity_text(backorder_qty),
                    "inventory_position_qty": quantity_text(position),
                },
                "pending_supply": sorted(pending_supply, key=lambda r: r["supply_id"]),
                "strategy": execution["strategy"],
                "approval_reference": execution["approval_reference"],
                "allowed_action_types": execution["allowed_action_types"],
                "capacity_mode": execution["capacity_mode"],
                **(
                    {"strategy_input_binding": execution["strategy_input_binding"]}
                    if "strategy_input_binding" in execution
                    else {}
                ),
            }
        )
    )

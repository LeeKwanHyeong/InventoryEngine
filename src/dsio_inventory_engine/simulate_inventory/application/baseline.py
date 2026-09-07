"""Baseline only: receipts, existing backlog, current orders, then net forecast."""

from decimal import Decimal, localcontext

from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.values import digest
from dsio_inventory_engine.prepare_inventory.application.inventory_input import (
    PrepareInventoryInputUseCase,
)
from .step import InventoryState, advance_bucket


class RunPsiSimulationUseCase:
    def __init__(self, deployment: DeploymentScope):
        self.prepare = PrepareInventoryInputUseCase(deployment)

    def execute(self, request: CanonicalInputRequest) -> dict:
        prepared = self.prepare.execute(request).to_dict()
        with localcontext() as ctx:
            ctx.prec = 40
            rows = _roll_forward(prepared)
        return {
            "status": "BASELINE_PSI_COMPUTED_LOCALLY",
            "psi_scenario_type": "BASELINE",
            "run_claimed": False,
            "database_writes": False,
            "artifact_sealed": False,
            "evidence_persisted": False,
            "prepared_input": prepared,
            "psi_rows": rows,
            "psi_content_hash": digest(rows),
        }


def _roll_forward(prepared: dict) -> list[dict]:
    demands = {(r["item_id"], r["yyyyww"]): r for r in prepared["demands"]}
    receipts: dict[tuple[str, str | None], Decimal] = {}
    for row in prepared["receipt_decisions"]:
        key = (row["item_id"], row["yyyyww"])
        receipts[key] = receipts.get(key, Decimal(0)) + Decimal(row["included_qty"])
    positions = {r["item_id"]: r for r in prepared["positions"]}
    result = []
    scope = {k: prepared["context"][k] for k in ("company_cd", "subs_cd", "site_cd")}
    for item in prepared["master"]:
        item_id = item["item_id"]
        position = positions[item_id]
        state = InventoryState(
            *(Decimal(position[k]) for k in ("available_qty", "reserved_qty", "backorder_qty"))
        )
        for bucket in prepared["calendar"]:
            demand = demands[(item_id, bucket["yyyyww"])]
            inbound = receipts.get((item_id, bucket["yyyyww"]), Decimal(0))
            confirmed, forecast = (
                Decimal(demand[k]) for k in ("confirmed_customer_order_qty", "net_forecast_qty")
            )
            state, quantities = advance_bucket(
                state,
                confirmed_customer_order_qty=confirmed,
                net_forecast_qty=forecast,
                confirmed_supplier_receipt_qty=inbound,
            )
            result.append(
                {
                    **scope,
                    **bucket,
                    **demand,
                    "psi_scenario_type": "BASELINE",
                    **quantities,
                }
            )
    return result

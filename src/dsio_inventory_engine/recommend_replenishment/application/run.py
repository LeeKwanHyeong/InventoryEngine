"""Strategy-neutral, local Recommended PSI with request-local supply ledgers."""

from decimal import Decimal, localcontext

from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest, SCOPE
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.replenishment import (
    RecommendationRequest,
    ReplenishmentObservation,
    ReplenishmentProposal,
    ReplenishmentStrategy,
    descriptor,
)
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    digest,
    quantity_text,
    require,
)
from dsio_inventory_engine.prepare_inventory.application.inventory_input import (
    PrepareInventoryInputUseCase,
)
from dsio_inventory_engine.simulate_inventory.application.step import InventoryState, advance_bucket
from .guard import policy_at, validate_action
from .observation import build_observation


class RunRecommendedPsiUseCase:
    """No training, model discovery, DB access, publication, or implicit fallback."""

    def __init__(self, deployment: DeploymentScope):
        self.deployment = deployment

    def execute(self, request: RecommendationRequest, strategy: ReplenishmentStrategy) -> dict:
        request = RecommendationRequest.from_dict(request.to_dict())
        require(self.deployment.environment == "DEVELOPMENT", "LOCAL_RECOMMENDATION_ONLY")
        data = request.to_dict()
        prepared = (
            PrepareInventoryInputUseCase(self.deployment)
            .execute(CanonicalInputRequest.from_dict(data["canonical_input"]))
            .to_dict()
        )
        execution = data["execution"]
        require(
            descriptor(strategy.descriptor) == execution["strategy"], "STRATEGY_BINDING_MISMATCH"
        )
        if "strategy_input_binding" in execution:
            require(
                getattr(strategy, "input_binding", None) == execution["strategy_input_binding"],
                "STRATEGY_INPUT_BINDING_MISMATCH",
            )
        with localcontext() as ctx:
            ctx.prec = 40
            validate_controls(prepared, execution)
            rows, decisions, orders = simulate(prepared, execution, request.input_hash, strategy)
        return {
            "contract_id": "io-replenishment-result-v1",
            "contract_version": execution["contract_version"],
            "status": "RECOMMENDED_PSI_COMPUTED_LOCALLY",
            "psi_scenario_type": "RECOMMENDED",
            "strategy": execution["strategy"],
            "execution": execution,
            "run_claimed": False,
            "database_writes": False,
            "artifact_sealed": False,
            "evidence_persisted": False,
            "approval_authenticated": False,
            "model_artifact_verified": False,
            "prepared_input": prepared,
            "input_content_hash": request.input_hash,
            "psi_rows": rows,
            "psi_content_hash": digest(rows),
            "decision_evidence": decisions,
            "decision_content_hash": digest(decisions),
            "recommended_orders": orders,
            "orders_content_hash": digest(orders),
        }


def validate_controls(prepared: dict, execution: dict) -> None:
    master = {r["item_id"]: r for r in prepared["master"]}
    controls = execution["item_controls"]
    require({r["item_id"] for r in controls} == set(master), "CONTROL_UNIVERSE_MISMATCH")
    rules = {r["uom"]: r for r in prepared["manifest"]["quantity_rules"]}
    dates = {b["start_date"] for b in prepared["calendar"]}
    count = len(prepared["calendar"])
    require(len(master) * count * (count + 1) // 2 <= 200_000, "RECOMMENDATION_OBSERVATION_LIMIT")
    seq = {b["yyyyww"]: b["seq"] + 1 for b in prepared["calendar"]}
    evidence_rows = (
        2 * len(master) * count * (count + 1)
        + sum(
            seq[r["yyyyww"]]
            for r in prepared["receipt_decisions"]
            if Decimal(r["included_qty"]) > 0
        )
        + count * len(prepared["policies"])
        + count * sum(len(r["order_dates"]) for r in controls)
    )
    require(evidence_rows <= 500_000, "RECOMMENDATION_EVIDENCE_LIMIT")
    for row in controls:
        require(row["uom"] == master[row["item_id"]]["uom"], "CONTROL_UOM_MISMATCH")
        require(set(row["order_dates"]) <= dates, "ORDER_DATE_NOT_BUCKET_START")
        for key in ("max_order_qty", "min_target_qty", "max_target_qty"):
            require(
                Decimal(row[key]).as_tuple().exponent
                >= -rules[row["uom"]].get("planning_scale", rules[row["uom"]].get("scale")),
                "CONTROL_UOM_PRECISION",
            )
    require(all(p["lead_time_days"] <= 3660 for p in prepared["policies"]), "LEAD_TIME_RANGE")


def simulate(
    prepared: dict, execution: dict, input_hash: str, strategy: ReplenishmentStrategy
) -> tuple[list, list, list]:
    rows: list[dict] = []
    decisions: list[dict] = []
    orders: list[dict] = []
    controls = {r["item_id"]: r for r in execution["item_controls"]}
    rules = {r["uom"]: r for r in prepared["manifest"]["quantity_rules"]}
    positions = {r["item_id"]: r for r in prepared["positions"]}
    # Build per-item indexes once; all mutable state remains local to this call.
    demands: dict[str, list] = {r["item_id"]: [] for r in prepared["master"]}
    policies: dict[str, list] = {item: [] for item in demands}
    supplies: dict[str, list] = {item: [] for item in demands}
    for row in prepared["demands"]:
        demands[row["item_id"]].append(row)
    for row in prepared["policies"]:
        policies[row["item_id"]].append(row)
    for row in prepared["receipt_decisions"]:
        if Decimal(row["included_qty"]) > 0:
            supplies[row["item_id"]].append(
                {
                    "supply_id": "CONFIRMED:" + row["receipt_id"],
                    "supply_kind": "CONFIRMED",
                    "due_date": row["due_date"],
                    "receipt_bucket": row["yyyyww"],
                    "quantity": row["included_qty"],
                }
            )
    for item in prepared["master"]:
        item_id = item["item_id"]
        state = InventoryState(
            *(
                Decimal(positions[item_id][k])
                for k in ("available_qty", "reserved_qty", "backorder_qty")
            )
        )
        pending = supplies[item_id]
        demand_by_week = {r["yyyyww"]: r for r in demands[item_id]}
        for index, bucket in enumerate(prepared["calendar"]):
            future = prepared["calendar"][index:]
            policy = policy_at(policies[item_id], bucket["start_date"])
            observation = build_observation(
                context=prepared["context"],
                input_hash=input_hash,
                item=item,
                bucket=bucket,
                calendar=future,
                future_demand=[demand_by_week[b["yyyyww"]] for b in future],
                policy=policy,
                policy_schedule=policies[item_id],
                control=controls[item_id],
                quantity_rule=rules[item["uom"]],
                available_qty=state.available_qty,
                reserved_qty=state.reserved_qty,
                backorder_qty=state.backorder_qty,
                pending_supply=pending,
                execution=execution,
            )
            proposal = invoke(strategy, observation, execution["strategy"])
            validation = validate_action(observation.to_dict(), proposal)
            if Decimal(validation["accepted_order_qty"]) > 0:
                order = {
                    **{k: prepared["context"][k] for k in SCOPE},
                    "item_id": item_id,
                    "uom": item["uom"],
                    "order_id": "REC-" + observation.to_dict()["decision_id"],
                    "decision_id": validation["decision_id"],
                    "order_date": bucket["start_date"],
                    "due_date": validation["due_date"],
                    "receipt_date": validation["receipt_date"],
                    "receipt_bucket": validation["receipt_bucket"],
                    "quantity": validation["accepted_order_qty"],
                    "source_policy_id": policy["policy_id"],
                    "status": "SIMULATED_PENDING",
                }
                orders.append(order)
                pending.append(
                    {
                        "supply_id": order["order_id"],
                        "supply_kind": "RECOMMENDED",
                        "due_date": order["due_date"],
                        "receipt_bucket": order["receipt_bucket"],
                        "quantity": order["quantity"],
                    }
                )
            current = [s for s in pending if s["receipt_bucket"] == bucket["yyyyww"]]
            incoming = {
                kind: sum(
                    (Decimal(s["quantity"]) for s in current if s["supply_kind"] == kind),
                    Decimal(0),
                )
                for kind in ("CONFIRMED", "RECOMMENDED")
            }
            demand = demand_by_week[bucket["yyyyww"]]
            state, quantities = advance_bucket(
                state,
                confirmed_customer_order_qty=Decimal(demand["confirmed_customer_order_qty"]),
                net_forecast_qty=Decimal(demand["net_forecast_qty"]),
                confirmed_supplier_receipt_qty=incoming["CONFIRMED"],
                recommended_receipt_qty=incoming["RECOMMENDED"],
            )
            cap = policy["physical_max_capacity"]
            peak = Decimal(quantities["available_qty"]) + state.reserved_qty
            rows.append(
                {
                    **{k: prepared["context"][k] for k in SCOPE},
                    **bucket,
                    **demand,
                    "psi_scenario_type": "RECOMMENDED",
                    "strategy_type": execution["strategy"]["strategy_type"],
                    **quantities,
                    "physical_capacity_excess_qty": quantity_text(
                        max(Decimal(0), peak - Decimal(cap))
                    )
                    if cap is not None
                    else "0",
                }
            )
            pending[:] = [s for s in pending if s["receipt_bucket"] != bucket["yyyyww"]]
            decisions.append(
                {
                    "observation": observation.to_dict(),
                    "observation_hash": observation.content_hash,
                    "proposal": proposal,
                    "validation": validation,
                }
            )
    # Every accepted order is within the horizon and has been simulated exactly once.
    for order in orders:
        order["status"] = "SIMULATED_RECEIVED"
    return rows, decisions, orders


def invoke(
    strategy: ReplenishmentStrategy, observation: ReplenishmentObservation, binding: dict
) -> dict:
    try:
        submitted = strategy.decide(observation)
    except Exception:
        raise InventoryInputError(
            "STRATEGY_EXECUTION_FAILED",
            [
                {
                    "decision_id": observation.to_dict()["decision_id"],
                    "strategy": binding,
                }
            ],
        ) from None
    require(isinstance(submitted, ReplenishmentProposal), "INVALID_STRATEGY_RESULT")
    proposal = ReplenishmentProposal.from_dict(submitted.to_dict()).to_dict()
    require(proposal["strategy"] == binding, "STRATEGY_BINDING_MISMATCH")
    require(
        proposal["decision_id"] == observation.to_dict()["decision_id"]
        and proposal["observation_hash"] == observation.content_hash,
        "STALE_STRATEGY_DECISION",
    )
    return proposal

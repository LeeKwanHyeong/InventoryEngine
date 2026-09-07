"""Closed-loop production policy/guard adapter to an independent actual-demand World."""

from decimal import Decimal, localcontext

from dsio_inventory_engine.inventory_contracts.evaluation import ProductionEvaluationRequest
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.training import TrainingRequest
from dsio_inventory_engine.inventory_contracts.values import digest, quantity_text, require
from dsio_inventory_engine.recommend_replenishment.application.guard import (
    policy_at,
    validate_action,
)
from dsio_inventory_engine.recommend_replenishment.application.mathematical import (
    prepare_mathematical_strategy,
)
from dsio_inventory_engine.recommend_replenishment.application.observation import build_observation
from dsio_inventory_engine.recommend_replenishment.application.run import invoke
from dsio_inventory_engine.training.evaluation import ReferenceEpisode
from dsio_inventory_engine.training.reference import NUMBERS
from .admission import episode_range, validate_binding


class EvaluateMathematicalStrategyUseCase:
    def __init__(self, deployment: DeploymentScope):
        self.deployment = deployment

    def execute(self, request: ProductionEvaluationRequest) -> dict:
        require(self.deployment.environment == "DEVELOPMENT", "LOCAL_EVALUATION_ONLY")
        request = ProductionEvaluationRequest.from_dict(request.to_dict())
        data = request.to_dict()
        training = TrainingRequest.from_dict(data["training_request"])
        world = ReferenceEpisode(
            training,
            self.deployment,
            data["split"],
            data["scenario_id"],
            delay_scope=data["delay_scope"],
        )
        recommendation, prepared, report, strategy = prepare_mathematical_strategy(
            MathematicalPolicyRequest.from_dict(data["mathematical_request"]), self.deployment
        )
        validate_binding(data, prepared, world.observe())
        with localcontext(NUMBERS):
            decisions = self._simulate(data, world, recommendation, prepared, strategy)
        result = {
            "contract_id": "io-production-evaluation-result-v1",
            "contract_version": "1.0.0",
            "evaluation_id": data["evaluation_id"],
            "evaluation_kind": "PRODUCTION_MATHEMATICAL",
            "status": "EVALUATED_LOCALLY",
            "production_strategy_executed": True,
            "strategy": strategy.descriptor,
            "psi_scenario_type": "RECOMMENDED",
            "world_semantics": "ACTUAL_UNCENSORED_DEMAND_ALL_UNMET_BACKORDERED",
            "forecast_mode": data["forecast_mode"],
            "delay_scope": data["delay_scope"],
            "policy_refresh": "FROZEN_PRE_EPISODE",
            "horizon_order_rule": "PRODUCTION_REJECT_OUTSIDE_HORIZON",
            "input_content_hash": request.input_hash,
            "recommendation_input_hash": recommendation.input_hash,
            "input_snapshot": data,
            "prepared_input": prepared,
            "mathematical_policy_report": report,
            "mathematical_policy_content_hash": digest(report),
            "decision_evidence": decisions,
            "decision_content_hash": digest(decisions),
            "world_result": world.result(),
            "database_writes": False,
            "evidence_persisted": False,
            "artifact_sealed": False,
            "run_claimed": False,
            "approval_authenticated": False,
            "model_trained": False,
            "performance_superiority_claimed": False,
        }
        result["content_hash"] = digest(result)
        return result

    @staticmethod
    def _simulate(
        data, world, recommendation, prepared, strategy, *, on_transition=None
    ) -> list[dict]:
        calendar = prepared["calendar"]
        origin, _ = episode_range(data["training_request"], data["split"])
        execution = recommendation.to_dict()["execution"]
        controls = {r["item_id"]: r for r in execution["item_controls"]}
        rules = {r["uom"]: r for r in prepared["manifest"]["quantity_rules"]}
        policies = {
            i["item_id"]: [p for p in prepared["policies"] if p["item_id"] == i["item_id"]]
            for i in prepared["master"]
        }
        demands = {(r["item_id"], r["yyyyww"]): r for r in prepared["demands"]}
        week_indices = {b["yyyyww"]: origin + index for index, b in enumerate(calendar)}
        decisions = []
        generated_ids: set[str] = set()
        while not world.done:
            safe = world.observe()
            index = safe["week_index"] - origin
            bucket, future = calendar[index], calendar[index:]
            states = {i["item_id"]: i for i in safe["items"]}
            actions, step_decisions = {}, []
            for item in prepared["master"]:
                key = item["item_id"]
                state = states[key]
                pending = []
                for supply in state["pending_supply"]:
                    due_index = supply["planned_due_index"] - origin
                    pending.append(
                        {
                            "supply_id": supply["order_id"],
                            "supply_kind": "RECOMMENDED"
                            if supply["order_id"] in generated_ids
                            else "CONFIRMED",
                            "due_date": supply["due_date"],
                            "receipt_bucket": calendar[due_index]["yyyyww"]
                            if 0 <= due_index < len(calendar)
                            else None,
                            "quantity": supply["quantity"],
                        }
                    )
                observation = build_observation(
                    context=prepared["context"],
                    input_hash=recommendation.input_hash,
                    item=item,
                    bucket=bucket,
                    calendar=future,
                    future_demand=[demands[key, b["yyyyww"]] for b in future],
                    policy=policy_at(policies[key], bucket["start_date"]),
                    policy_schedule=policies[key],
                    control=controls[key],
                    quantity_rule=rules[item["uom"]],
                    available_qty=Decimal(state["boh_qty"]),
                    reserved_qty=Decimal(0),
                    backorder_qty=Decimal(state["backorder_qty"]),
                    pending_supply=pending,
                    execution=execution,
                )
                proposal = invoke(strategy, observation, execution["strategy"])
                validation = validate_action(observation.to_dict(), proposal)
                positive = Decimal(validation["accepted_order_qty"]) > 0
                order_id = "REC-" + validation["decision_id"] if positive else None
                actions[key] = {
                    "quantity": validation["accepted_order_qty"],
                    "order_id": order_id,
                    "due_date": validation["due_date"] if positive else None,
                    "planned_due_index": week_indices[validation["receipt_bucket"]]
                    if positive
                    else None,
                }
                if order_id is not None:
                    generated_ids.add(order_id)
                step_decisions.append(
                    {
                        "world_week_index": safe["week_index"],
                        "world_observation_hash": digest(safe),
                        "observation": observation.to_dict(),
                        "observation_hash": observation.content_hash,
                        "proposal": proposal,
                        "validation": validation,
                        "world_action": actions[key],
                    }
                )
            transition = world.step_admitted(actions, observation_hash=digest(safe))
            if on_transition is not None:
                on_transition(transition)
            for row in transition["rows"]:
                # Training and Canonical normalize differently: match by identity, not order.
                matched = next(
                    d for d in step_decisions if d["validation"]["item_id"] == row["item_id"]
                )
                matched["world_ledger_row"] = row
                cap = matched["observation"]["policy"]["physical_max_capacity"]
                matched["physical_capacity_excess_qty"] = (
                    quantity_text(
                        max(
                            Decimal(0),
                            Decimal(row["boh_qty"]) + Decimal(row["receipt_qty"]) - Decimal(cap),
                        )
                    )
                    if cap is not None
                    else "0"
                )
                require(
                    row["order_qty"] == matched["validation"]["accepted_order_qty"],
                    "WORLD_ACTION_MISMATCH",
                )
            decisions.extend(step_decisions)
        return decisions

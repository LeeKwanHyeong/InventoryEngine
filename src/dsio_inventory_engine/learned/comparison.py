"""Paired evaluation: identical exogenous World, production admission and guard."""

from decimal import Decimal, localcontext

from dsio_inventory_engine.evaluate_replenishment.application import (
    EvaluateMathematicalStrategyUseCase,
)
from dsio_inventory_engine.evaluate_replenishment.demo import build_evaluation_request
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.model import ModelArtifact
from dsio_inventory_engine.inventory_contracts.replenishment import ReplenishmentProposal
from dsio_inventory_engine.inventory_contracts.values import digest, require
from dsio_inventory_engine.recommend_replenishment.application.mathematical import (
    prepare_mathematical_strategy,
)
from dsio_inventory_engine.training.evaluation import ReferenceEpisode
from dsio_inventory_engine.training.reference import NUMBERS
from .application import evaluate_learned


class HoldStrategy:
    def __init__(self, original):
        self.descriptor = original.descriptor
        self.input_binding = original.input_binding

    def decide(self, observation):
        obs = observation.to_dict()
        return ReplenishmentProposal.from_dict(
            {
                "contract_id": "io-replenishment-decision-v1",
                "decision_id": obs["decision_id"],
                "observation_hash": observation.content_hash,
                "strategy": self.descriptor,
                "action_type": "HOLD",
                "quantity": "0",
                "calculated_policy": {
                    "safety_stock_qty": None,
                    "rop_qty": None,
                    "target_inventory_qty": None,
                },
                "reason_codes": ["NO_NEW_SUPPLY_CONTROL"],
            }
        )


def evaluate_hold(request, deployment) -> dict:
    # First validate the same Canonical/history/World binding as mathematical evaluation.
    from dsio_inventory_engine.evaluate_replenishment.admission import validate_binding
    from dsio_inventory_engine.inventory_contracts.training import TrainingRequest

    data = request.to_dict()
    rec, prepared, _, strategy = prepare_mathematical_strategy(
        MathematicalPolicyRequest.from_dict(data["mathematical_request"]), deployment
    )
    world = ReferenceEpisode(
        TrainingRequest.from_dict(data["training_request"]),
        deployment,
        data["split"],
        data["scenario_id"],
        delay_scope=data["delay_scope"],
    )
    validate_binding(data, prepared, world.observe())
    with localcontext(NUMBERS):
        decisions = EvaluateMathematicalStrategyUseCase._simulate(
            data, world, rec, prepared, HoldStrategy(strategy)
        )
    return {"world_result": world.result(), "decision_evidence": decisions}


def summarize(result: dict) -> dict:
    world = result["world_result"]
    decisions = result["decision_evidence"]
    demand = sum(Decimal(r["demand_qty"]) for r in world["metrics"])
    fulfilled = sum(Decimal(r["on_time_fulfilled_qty"]) for r in world["metrics"])
    return {
        "initial_state_hash": digest(world["initial"]),
        "world_result_hash": world["content_hash"],
        "decision_evidence_hash": digest(decisions),
        "clipped_feature_decisions": sum(
            bool(r["clipped_feature_names"]) for r in result.get("learned_decision_evidence", [])
        ),
        "total_cost": world["total_cost"],
        "currency": world["currency"],
        "on_time_fill_rate": str(fulfilled / demand) if demand else None,
        "metrics": world["metrics"],
        "cost_convention": world["cost_convention"],
        "item_weeks": len(world["ledger"]),
        "order_count": len(world["orders"]),
        "terminal_open_supply_qty": str(
            sum(Decimal(r["terminal_open_supply_qty"]) for r in world["metrics"])
        ),
        "terminal_backorder_qty": str(
            sum(Decimal(r["terminal_backorder_qty"]) for r in world["metrics"])
        ),
        "terminal_on_hand_qty": str(
            sum(Decimal(r["terminal_on_hand_qty"]) for r in world["metrics"])
        ),
        "rejected_decisions": sum(r["validation"]["status"] == "REJECTED" for r in decisions),
        "physical_capacity_excess_qty": str(
            sum(Decimal(r["physical_capacity_excess_qty"]) for r in decisions)
        ),
    }


def compare_strategies(training, models: list[ModelArtifact], deployment, *, split="TEST") -> dict:
    require(split in ("VALIDATION", "TEST"), "HELD_OUT_COMPARISON_ONLY")
    require(
        {m.to_dict()["strategy_type"] for m in models} == {"PREDICTIVE_ML", "DEEP_RL"}
        and len(models) == 2,
        "COMPARISON_MODEL_FAMILIES",
    )
    rows = []
    for scenario in training.to_dict()["scenarios"]:
        request = build_evaluation_request(
            training, deployment, split=split, scenario_id=scenario["scenario_id"]
        )
        results = {
            "NO_NEW_SUPPLY_CONTROL": summarize(evaluate_hold(request, deployment)),
            "MATHEMATICAL": summarize(
                EvaluateMathematicalStrategyUseCase(deployment).execute(request)
            ),
        }
        for model in models:
            results[model.to_dict()["strategy_type"]] = summarize(
                evaluate_learned(request, model, model.reference, deployment)
            ) | {"model_reference": model.reference}
        require(
            len({r["initial_state_hash"] for r in results.values()}) == 1,
            "COMPARISON_INITIAL_STATE_MISMATCH",
        )
        rows.append(
            {
                "scenario_id": scenario["scenario_id"],
                "evaluation_input_hash": request.input_hash,
                "strategies": results,
            }
        )
    result = {
        "split": split,
        "comparisons": rows,
        "same_initial_state_verified": True,
        "forecast_mode": "SYNTHETIC_FROZEN_MEAN",
        "delay_scope": "EPISODE_RECEIPTS_ONLY",
        "terminal_rule": "FINITE_HORIZON_TERMINAL_BACKLOG_COST_NO_SALVAGE",
        "production_guard_shared": True,
        "performance_superiority_claimed": False,
        "summary_only": True,
        "evidence_persisted": False,
    }
    result["content_hash"] = digest(result)
    return result

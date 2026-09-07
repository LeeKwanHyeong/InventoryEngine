"""Learned decisions retain Source constraints and approved policy precedence."""

from decimal import Context, Decimal, ROUND_CEILING, localcontext
from typing import Callable

from dsio_inventory_engine.inventory_contracts.model import ModelArtifact
from dsio_inventory_engine.inventory_contracts.replenishment import (
    ReplenishmentObservation,
    ReplenishmentProposal,
)
from dsio_inventory_engine.inventory_contracts.values import require, quantity_text
from .features import FEATURE_NAMES, ML_INDICES, normalized, vector
from .predict import predict


class LearnedStrategy:
    def __init__(
        self,
        artifact: ModelArtifact,
        report: dict,
        input_hash: str,
        binding: dict,
        *,
        training_predictor: Callable | None = None,
    ):
        self.artifact = artifact
        self.input_hash = input_hash
        self._binding = dict(binding)
        self._policies = {r["decision_id"]: r for r in report["policy_evidence"]}
        self._stats = {r["item_id"]: r for r in report["history_statistics"]}
        self._training_predictor = training_predictor
        self.records: list[dict] = []

    @property
    def descriptor(self) -> dict:
        return self.artifact.descriptor

    @property
    def input_binding(self) -> dict:
        return dict(self._binding)

    def decide(self, observation: ReplenishmentObservation) -> ReplenishmentProposal:
        obs = observation.to_dict()
        require(
            obs["input_content_hash"] == self.input_hash
            and obs.get("strategy_input_binding") == self.input_binding,
            "LEARNED_OBSERVATION_BINDING",
        )
        require(obs["decision_id"] in self._policies, "MISSING_LEARNED_POLICY")
        policy = self._policies[obs["decision_id"]]
        stats = self._stats[obs["item_id"]]
        features, scale = vector(obs, policy, stats)
        normalizer = self.artifact.to_dict()["normalizer"]
        scaled = normalized(features, normalizer)
        consumed = (
            ML_INDICES
            if self.descriptor["strategy_type"] == "PREDICTIVE_ML"
            else list(range(len(FEATURE_NAMES)))
        )
        clipped = [
            name
            for index, (name, x, m, s) in enumerate(
                zip(FEATURE_NAMES, features, normalizer["mean"], normalizer["std"], strict=True)
            )
            if index in consumed and abs((x - float(m)) / float(s)) > 10
        ]
        value = (
            self._training_predictor(features)
            if self._training_predictor is not None
            else predict(self.artifact.to_dict(), features)
        )
        effective = policy["effective_policy"]
        calculated = dict(policy["python_calculated_policy"])
        hold = False
        with localcontext(Context(prec=40)):
            rule = obs["quantity_rule"]
            quantum = Decimal(1).scaleb(-rule.get("planning_scale", rule.get("scale")))
            if policy["effective_policy_source"] == "PYTHON_CALCULATED":
                if self.descriptor["strategy_type"] == "PREDICTIVE_ML":
                    safety = (Decimal(format(float(value), ".12f")) * Decimal(str(scale))).quantize(
                        quantum, rounding=ROUND_CEILING
                    )
                    mean = Decimal(stats["mean_weekly_demand"])
                    lead = Decimal(policy["calculation_trace"]["lead_time_weeks"])
                    cycle = Decimal(policy["calculation_trace"]["replenishment_cycle_weeks"])
                    rop = (mean * lead + safety).quantize(quantum, rounding=ROUND_CEILING)
                    target = (rop + mean * cycle).quantize(quantum, rounding=ROUND_CEILING)
                    calculated = {
                        "safety_stock_qty": quantity_text(safety),
                        "rop_qty": quantity_text(rop),
                        "target_inventory_qty": quantity_text(target),
                    }
                    effective = calculated
                else:
                    require(type(value) is int and 0 <= value < 4, "PPO_ACTION_RANGE")
                    hold = value == 0
                    factor = (Decimal(1), Decimal("0.75"), Decimal(1), Decimal("1.25"))[int(value)]
                    target = max(
                        Decimal(effective["rop_qty"]),
                        Decimal(effective["target_inventory_qty"]) * factor,
                    ).quantize(quantum, rounding=ROUND_CEILING)
                    calculated = {**calculated, "target_inventory_qty": quantity_text(target)}
                    effective = calculated
                require(
                    all(Decimal(v) <= Decimal("1e12") for v in calculated.values()),
                    "LEARNED_POLICY_RANGE",
                )
            position = Decimal(obs["state"]["inventory_position_qty"])
            order = (
                effective is not None
                and not hold
                and position <= Decimal(effective["rop_qty"])
                and position < Decimal(effective["target_inventory_qty"])
            )
        self.records.append(
            {
                "decision_id": obs["decision_id"],
                "item_id": obs["item_id"],
                "decision_date": obs["decision_date"],
                "feature_contract_id": "io-learned-observation-v1",
                "features": [format(x, ".17g") for x in features],
                "normalized_features": [format(x, ".17g") for x in scaled],
                "clipped_feature_names": clipped,
                "model_feature_indices": consumed,
                "prediction": str(value),
                "effective_policy": effective,
                "base_policy_source": policy["effective_policy_source"],
                "learned_policy_applied": policy["effective_policy_source"] == "PYTHON_CALCULATED",
                "model_reference": self.artifact.reference,
            }
        )
        return ReplenishmentProposal.from_dict(
            {
                "contract_id": "io-replenishment-decision-v1",
                "decision_id": obs["decision_id"],
                "observation_hash": observation.content_hash,
                "strategy": self.descriptor,
                "action_type": "ORDER_UP_TO" if order else "HOLD",
                "quantity": effective["target_inventory_qty"] if order else "0",
                "calculated_policy": calculated,
                "reason_codes": [
                    "LEARNED_POLICY_DECISION",
                    policy["effective_policy_source"],
                    "ROP_TRIGGERED" if order else "NO_ORDER",
                ],
            }
        )

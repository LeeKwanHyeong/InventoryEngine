"""Executable mathematical strategy; all orders still pass the shared guard and PSI."""

from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping

from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.mathematical import (
    MATH_DESCRIPTOR,
    MathematicalPolicyRequest,
    policy_input_binding,
)
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.replenishment import (
    RecommendationRequest,
    ReplenishmentObservation,
    ReplenishmentProposal,
)
from dsio_inventory_engine.inventory_contracts.runtime import InventoryRuntimeExecutionRequest
from dsio_inventory_engine.inventory_contracts.values import (
    canonical_json,
    digest,
    read_json,
    require,
)
from dsio_inventory_engine.prepare_inventory.application.inventory_input import (
    PrepareInventoryInputUseCase,
)
from .math_input import validate_math_input
from .math_policy import build_policy_report
from .run import (
    RunRecommendedPsiUseCase,
    apply_effective_policy_controls,
    validate_controls,
)


@dataclass(frozen=True)
class MathematicalStrategy:
    input_hash: str
    binding_json: str
    policy_rows: Mapping[str, str]

    @property
    def descriptor(self) -> dict:
        return dict(MATH_DESCRIPTOR)

    @property
    def input_binding(self) -> dict:
        return read_json(self.binding_json)

    def decide(self, observation: ReplenishmentObservation) -> ReplenishmentProposal:
        obs = observation.to_dict()
        require(
            obs["input_content_hash"] == self.input_hash
            and obs.get("strategy_input_binding") == self.input_binding,
            "MATH_OBSERVATION_BINDING_MISMATCH",
        )
        require(obs["decision_id"] in self.policy_rows, "MISSING_MATH_DECISION_POLICY")
        policy = read_json(self.policy_rows[obs["decision_id"]])
        effective = policy["effective_policy"]
        position = Decimal(obs["state"]["inventory_position_qty"])
        order = (
            effective is not None
            and position <= Decimal(effective["rop_qty"])
            and position < Decimal(effective["target_inventory_qty"])
        )
        reason = (
            "POLICY_EXCLUDED"
            if effective is None
            else "ROP_TRIGGERED"
            if order
            else "NO_POLICY_REPLENISHMENT"
        )
        return ReplenishmentProposal.from_dict(
            {
                "contract_id": "io-replenishment-decision-v1",
                "decision_id": obs["decision_id"],
                "observation_hash": observation.content_hash,
                "strategy": self.descriptor,
                "action_type": "ORDER_UP_TO" if order else "HOLD",
                "quantity": effective["target_inventory_qty"] if order else "0",
                "calculated_policy": policy["python_calculated_policy"],
                "reason_codes": [
                    reason,
                    policy["effective_policy_source"],
                    policy["history_status"],
                ],
            }
        )


class RunMathematicalReplenishmentUseCase:
    def __init__(self, deployment: DeploymentScope):
        self.deployment = deployment

    def execute(
        self,
        request: MathematicalPolicyRequest,
        *,
        runtime_request: InventoryRuntimeExecutionRequest | None = None,
    ) -> dict:
        recommendation, _, report, strategy = prepare_mathematical_strategy(
            request,
            self.deployment,
            runtime_request=runtime_request,
        )
        data = MathematicalPolicyRequest.from_dict(request.to_dict()).to_dict()
        result = RunRecommendedPsiUseCase(self.deployment).execute(
            recommendation,
            strategy,
            runtime_request=runtime_request,
        )
        excluded = sum(
            r["effective_policy_source"] == "EXCLUDED" for r in report["policy_evidence"]
        )
        result.update(
            mathematical_policy_input=data["policy_input"],
            mathematical_policy_report=report,
            mathematical_policy_content_hash=digest(report),
            excluded_policy_decisions=excluded,
            policy_coverage="COMPLETE"
            if excluded == 0
            else "NONE"
            if excluded == len(report["policy_evidence"])
            else "PARTIAL",
        )
        return result


def prepare_mathematical_strategy(
    request: MathematicalPolicyRequest,
    deployment: DeploymentScope,
    *,
    runtime_request: InventoryRuntimeExecutionRequest | None = None,
) -> tuple[RecommendationRequest, dict, dict, MathematicalStrategy]:
    """Single production admission/calculation path; no simulated future PSI needed."""
    request = MathematicalPolicyRequest.from_dict(request.to_dict())
    require(deployment.environment == "DEVELOPMENT", "LOCAL_RECOMMENDATION_ONLY")
    data = request.to_dict()
    recommendation = RecommendationRequest.from_dict(data["recommendation"])
    prepared = (
        PrepareInventoryInputUseCase(deployment)
        .execute(CanonicalInputRequest.from_dict(data["recommendation"]["canonical_input"]))
        .to_dict()
    )
    apply_effective_policy_controls(
        prepared,
        data["recommendation"]["execution"],
        runtime_request=runtime_request,
    )
    validate_controls(prepared, data["recommendation"]["execution"])
    validate_math_input(data["policy_input"], data["recommendation"], prepared)
    report = build_policy_report(data["policy_input"], prepared, recommendation.input_hash)
    strategy = MathematicalStrategy(
        recommendation.input_hash,
        canonical_json(policy_input_binding(data["policy_input"])),
        MappingProxyType({r["decision_id"]: canonical_json(r) for r in report["policy_evidence"]}),
    )
    return recommendation, prepared, report, strategy

"""Development-only learned inference/evaluation; training is never imported here."""

from dataclasses import dataclass
from decimal import localcontext

from dsio_inventory_engine.inventory_contracts.evaluation import ProductionEvaluationRequest
from dsio_inventory_engine.inventory_contracts.canonical import read_canonical_envelope
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.model import ModelArtifact
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.replenishment import (
    MODEL_FIELDS,
    RecommendationRequest,
)
from dsio_inventory_engine.inventory_contracts.training import TrainingRequest
from dsio_inventory_engine.inventory_contracts.values import (
    canonical_json,
    digest,
    require,
    shape,
)
from dsio_inventory_engine.recommend_replenishment.application.math_policy import (
    build_policy_report,
)
from dsio_inventory_engine.recommend_replenishment.application.mathematical import (
    prepare_mathematical_strategy,
)
from dsio_inventory_engine.recommend_replenishment.application.run import RunRecommendedPsiUseCase
from dsio_inventory_engine.evaluate_replenishment.admission import validate_binding
from dsio_inventory_engine.evaluate_replenishment.application import (
    EvaluateMathematicalStrategyUseCase,
)
from dsio_inventory_engine.training.evaluation import ReferenceEpisode
from dsio_inventory_engine.training.reference import NUMBERS
from .strategy import LearnedStrategy


@dataclass(frozen=True)
class LearnedInferenceRequest:
    document_json: str

    @classmethod
    def from_dict(cls, value: dict) -> "LearnedInferenceRequest":
        data = shape(
            value,
            {
                "mathematical_request": lambda v: MathematicalPolicyRequest.from_dict(v).to_dict(),
                "model": lambda v: ModelArtifact.from_dict(v).to_dict(),
                "model_reference": lambda v: shape(v, MODEL_FIELDS),
            },
        )
        return cls(canonical_json(data))

    def to_dict(self) -> dict:
        return read_canonical_envelope(
            self.document_json, ("mathematical_request", "recommendation", "canonical_input")
        )


def compile_strategy(
    anchor: MathematicalPolicyRequest,
    artifact: ModelArtifact,
    deployment: DeploymentScope,
    *,
    training_predictor=None,
) -> tuple:
    _, prepared, _, _ = prepare_mathematical_strategy(anchor, deployment)
    data = anchor.to_dict()
    require(
        all(
            data["policy_input"]["profile"][k] == v
            for k, v in artifact.to_dict()["policy_contract"].items()
        ),
        "MODEL_POLICY_CONTRACT_MISMATCH",
    )
    rec = data["recommendation"]
    rec["execution"]["strategy"] = artifact.descriptor
    recommendation = RecommendationRequest.from_dict(rec)
    report = build_policy_report(data["policy_input"], prepared, recommendation.input_hash)
    strategy = LearnedStrategy(
        artifact,
        report,
        recommendation.input_hash,
        rec["execution"]["strategy_input_binding"],
        training_predictor=training_predictor,
    )
    return recommendation, prepared, report, strategy


class RunLearnedPsiUseCase:
    def __init__(self, deployment: DeploymentScope):
        self.deployment = deployment

    def execute(self, request: LearnedInferenceRequest) -> dict:
        require(self.deployment.environment == "DEVELOPMENT", "LOCAL_LEARNED_INFERENCE_ONLY")
        data = LearnedInferenceRequest.from_dict(request.to_dict()).to_dict()
        artifact = ModelArtifact.from_dict(data["model"])
        artifact.validate_for(
            data["model_reference"],
            data["mathematical_request"]["recommendation"]["canonical_input"]["context"],
        )
        rec, prepared, report, strategy = compile_strategy(
            MathematicalPolicyRequest.from_dict(data["mathematical_request"]),
            artifact,
            self.deployment,
        )
        result = RunRecommendedPsiUseCase(self.deployment).execute(rec, strategy)
        result.update(
            model_artifact_hash_verified=True,
            model_artifact_verified=True,
            model_artifact=artifact.to_dict(),
            learned_decision_evidence=strategy.records,
            mathematical_policy_report=report,
            model_trained=False,
            operational_model_approval_verified=False,
        )
        result["content_hash"] = digest(result)
        return result


def evaluate_learned(
    request: ProductionEvaluationRequest,
    model: ModelArtifact,
    expected: dict,
    deployment: DeploymentScope,
) -> dict:
    require(deployment.environment == "DEVELOPMENT", "LOCAL_LEARNED_INFERENCE_ONLY")
    data = ProductionEvaluationRequest.from_dict(request.to_dict()).to_dict()
    model = ModelArtifact.from_dict(model.to_dict())
    context = data["mathematical_request"]["recommendation"]["canonical_input"]["context"]
    model.validate_for(expected, context)
    rec, prepared, report, strategy = compile_strategy(
        MathematicalPolicyRequest.from_dict(data["mathematical_request"]), model, deployment
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
            data, world, rec, prepared, strategy
        )
    result = {
        "contract_id": "io-learned-evaluation-result-v1",
        "contract_version": "1.0.0",
        "status": "EVALUATED_LOCALLY",
        "evaluation_kind": model.to_dict()["strategy_type"],
        "strategy": strategy.descriptor,
        "execution": rec.to_dict()["execution"],
        "psi_scenario_type": "RECOMMENDED",
        "input_content_hash": digest({"evaluation": data, "model_reference": expected}),
        "input_snapshot": data,
        "model_artifact": model.to_dict(),
        "model_artifact_hash_verified": True,
        "prepared_input": prepared,
        "mathematical_policy_report": report,
        "decision_evidence": decisions,
        "learned_decision_evidence": strategy.records,
        "world_result": world.result(),
        "model_trained": False,
        "database_writes": False,
        "artifact_sealed": False,
        "run_claimed": False,
        "evidence_persisted": False,
        "operational_model_approval_verified": False,
        "performance_superiority_claimed": False,
    }
    result["content_hash"] = digest(result)
    return result

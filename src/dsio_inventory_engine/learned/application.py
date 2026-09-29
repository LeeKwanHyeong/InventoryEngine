"""Development-only learned inference/evaluation; training is never imported here."""

from dataclasses import dataclass
from decimal import localcontext

from dsio_inventory_engine.inventory_contracts.evaluation import ProductionEvaluationRequest
from dsio_inventory_engine.inventory_contracts.canonical import (
    CanonicalInputRequest,
    read_canonical_envelope,
)
from dsio_inventory_engine.inventory_contracts.mathematical import (
    MATH_DESCRIPTOR,
    MathematicalPolicyRequest,
)
from dsio_inventory_engine.inventory_contracts.model import ModelArtifact
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.replenishment import (
    MODEL_FIELDS,
    MODEL_APPROVAL_FIELDS,
    RecommendationRequest,
    replenishment_config_content_hash,
)
from dsio_inventory_engine.inventory_contracts.runtime import InventoryRuntimeExecutionRequest
from dsio_inventory_engine.inventory_contracts.training import TrainingRequest
from dsio_inventory_engine.inventory_contracts.values import (
    canonical_json,
    digest,
    hash_value,
    identifier,
    optional,
    require,
    shape,
)
from dsio_inventory_engine.prepare_inventory.application.inventory_input import (
    PreparedInventoryInput,
    PrepareInventoryInputUseCase,
    validate_prepared_projection,
)
from dsio_inventory_engine.recommend_replenishment.application.math_input import (
    validate_math_input,
)
from dsio_inventory_engine.recommend_replenishment.application.math_policy import (
    build_policy_report,
)
from dsio_inventory_engine.recommend_replenishment.application.mathematical import (
    prepare_mathematical_strategy,
)
from dsio_inventory_engine.recommend_replenishment.application.run import RunRecommendedPsiUseCase
from dsio_inventory_engine.recommend_replenishment.application.run import (
    apply_effective_policy_controls,
    validate_controls,
)
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
        if type(value) is dict:
            value = dict(value)
            value.setdefault("model_approval", None)
        data = shape(
            value,
            {
                "mathematical_request": lambda v: MathematicalPolicyRequest.from_dict(v).to_dict(),
                "model": lambda v: ModelArtifact.from_dict(v).to_dict(),
                "model_reference": lambda v: shape(v, MODEL_FIELDS),
                "model_approval": optional(lambda v: shape(v, MODEL_APPROVAL_FIELDS)),
            },
        )
        execution = data["mathematical_request"]["recommendation"]["execution"]
        if execution["contract_version"] == "2.0.0":
            require(
                execution["execution_purpose"] == "SHADOW"
                and data["model_approval"] is not None
                and {
                    key: data["model_approval"][key]
                    for key in ("model_id", "version", "content_hash")
                }
                == data["model_reference"],
                "LEARNED_SHADOW_MODEL_APPROVAL_REQUIRED",
            )
        else:
            require(data["model_approval"] is None, "LEARNED_MODEL_APPROVAL_UNEXPECTED")
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
    model_approval=None,
    runtime_request: InventoryRuntimeExecutionRequest | None = None,
    prepared_input: PreparedInventoryInput | None = None,
    challenger_id: str | None = None,
    implementation_content_hash: str | None = None,
) -> tuple:
    anchor = MathematicalPolicyRequest.from_dict(anchor.to_dict())
    data = anchor.to_dict()
    require(
        all(
            data["policy_input"]["profile"][k] == v
            for k, v in artifact.to_dict()["policy_contract"].items()
        ),
        "MODEL_POLICY_CONTRACT_MISMATCH",
    )
    rec = data["recommendation"]
    if rec["execution"]["contract_version"] == "2.0.0":
        require(
            rec["execution"]["execution_purpose"] == "SHADOW" and model_approval is not None,
            "LEARNED_SHADOW_MODEL_APPROVAL_REQUIRED",
        )
        require(
            rec["execution"]["execution_mode"] != "PLATFORM_BOUND" or training_predictor is None,
            "PLATFORM_BOUND_TRAINING_PREDICTOR_FORBIDDEN",
        )
        _validate_learned_execution_plan_binding(
            execution=rec["execution"],
            artifact=artifact,
            model_approval=model_approval,
            runtime_request=runtime_request,
            challenger_id=challenger_id,
            implementation_content_hash=implementation_content_hash,
        )
        rec["execution"]["strategy"] = artifact.descriptor
        rec["execution"]["model_approval"] = model_approval
        recommendation = RecommendationRequest.from_dict(rec)
        canonical_input = CanonicalInputRequest.from_dict(rec["canonical_input"])
        prepared = (
            prepared_input or PrepareInventoryInputUseCase(deployment).execute(canonical_input)
        ).to_dict()
        require(
            prepared["manifest"]["input_content_hash"] == canonical_input.input_hash,
            "PREPARED_INPUT_BINDING_MISMATCH",
        )
        apply_effective_policy_controls(
            prepared,
            recommendation.to_dict()["execution"],
            runtime_request=runtime_request,
            canonical_input=canonical_input,
        )
        validate_controls(prepared, recommendation.to_dict()["execution"])
        validate_math_input(data["policy_input"], recommendation.to_dict(), prepared)
    else:
        require(model_approval is None, "LEARNED_MODEL_APPROVAL_UNEXPECTED")
        require(runtime_request is None, "RUNTIME_BINDING_UNEXPECTED_FOR_V1")
        require(
            challenger_id is None and implementation_content_hash is None,
            "LEARNED_RUNTIME_PLAN_BINDING_UNEXPECTED",
        )
        _, prepared, _, _ = prepare_mathematical_strategy(
            anchor,
            deployment,
            prepared_input=prepared_input,
        )
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

    def execute(
        self,
        request: LearnedInferenceRequest,
        *,
        runtime_request: InventoryRuntimeExecutionRequest | None = None,
        prepared_input: PreparedInventoryInput | None = None,
        challenger_id: str | None = None,
        implementation_content_hash: str | None = None,
    ) -> dict:
        require(self.deployment.environment == "DEVELOPMENT", "LOCAL_LEARNED_INFERENCE_ONLY")
        data = LearnedInferenceRequest.from_dict(request.to_dict()).to_dict()
        anchor = MathematicalPolicyRequest.from_dict(data["mathematical_request"])
        canonical_input = CanonicalInputRequest.from_dict(
            anchor.to_dict()["recommendation"]["canonical_input"]
        )
        artifact = ModelArtifact.from_dict(data["model"])
        artifact.validate_for(
            data["model_reference"],
            data["mathematical_request"]["recommendation"]["canonical_input"]["context"],
        )
        shared_prepared = prepared_input or PrepareInventoryInputUseCase(self.deployment).execute(
            canonical_input
        )
        execution = anchor.to_dict()["recommendation"]["execution"]
        if (
            execution["contract_version"] == "2.0.0"
            and execution["execution_mode"] == "PLATFORM_BOUND"
        ):
            shared_prepared = validate_prepared_projection(
                canonical_input,
                shared_prepared,
                self.deployment,
            )
        rec, prepared, report, strategy = compile_strategy(
            anchor,
            artifact,
            self.deployment,
            model_approval=data["model_approval"],
            runtime_request=runtime_request,
            prepared_input=shared_prepared,
            challenger_id=challenger_id,
            implementation_content_hash=implementation_content_hash,
        )
        result = RunRecommendedPsiUseCase(self.deployment).execute(
            rec,
            strategy,
            runtime_request=runtime_request,
            prepared_input=shared_prepared,
        )
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


def _validate_learned_execution_plan_binding(
    *,
    execution: dict,
    artifact: ModelArtifact,
    model_approval: dict,
    runtime_request: InventoryRuntimeExecutionRequest | None,
    challenger_id: str | None,
    implementation_content_hash: str | None,
) -> None:
    """Bind Platform inference to one exact math anchor and sealed PPO claim."""

    if execution["execution_mode"] == "LOCAL_SHADOW":
        require(runtime_request is None, "LOCAL_SHADOW_RUNTIME_BINDING_FORBIDDEN")
        require(
            challenger_id is None and implementation_content_hash is None,
            "LOCAL_SHADOW_CHALLENGER_BINDING_FORBIDDEN",
        )
        return

    require(runtime_request is not None, "RUNTIME_EXECUTION_BINDING_REQUIRED")
    require(
        challenger_id is not None and implementation_content_hash is not None,
        "PLATFORM_SHADOW_CHALLENGER_BINDING_REQUIRED",
    )
    runtime = InventoryRuntimeExecutionRequest.from_dict(runtime_request.to_dict())
    plan = runtime.strategy_execution_plan
    require(plan is not None, "RUNTIME_STRATEGY_EXECUTION_PLAN_REQUIRED")
    operational = plan["operational_strategy"]
    require(
        operational["strategy_type"] == MATH_DESCRIPTOR["strategy_type"]
        and operational["implementation_id"] == MATH_DESCRIPTOR["implementation_id"]
        and operational["version"] == MATH_DESCRIPTOR["version"],
        "PLATFORM_OPERATIONAL_MATHEMATICAL_STRATEGY_REQUIRED",
    )
    require(
        operational["strategy_input_binding"] == execution["strategy_input_binding"],
        "MATHEMATICAL_STRATEGY_INPUT_BINDING_MISMATCH",
    )
    operational_math_execution = {
        **execution,
        "execution_purpose": "OPERATIONAL",
        "strategy": MATH_DESCRIPTOR,
        "model_approval": None,
    }
    require(
        operational["replenishment_config_content_hash"]
        == replenishment_config_content_hash(operational_math_execution),
        "MATHEMATICAL_EXECUTION_BINDING_MISMATCH",
    )
    claimed_id = identifier(challenger_id)
    claimed_hash = hash_value(implementation_content_hash)
    matches = [
        binding
        for binding in plan["shadow_challenger_bindings"]
        if binding["challenger_id"] == claimed_id
    ]
    require(len(matches) == 1, "PLATFORM_SHADOW_CHALLENGER_NOT_CLAIMED")
    binding = matches[0]
    descriptor = artifact.descriptor
    approval = shape(dict(model_approval), MODEL_APPROVAL_FIELDS)
    require(
        descriptor["strategy_type"] == "DEEP_RL"
        and binding["strategy_type"] == "DEEP_RL"
        and binding["algorithm"] == "PPO"
        and binding["implementation_id"] == descriptor["implementation_id"]
        and binding["version"] == descriptor["version"]
        and binding["implementation_content_hash"] == claimed_hash
        and binding["model"] == artifact.reference
        and binding["approval_reference"] == approval["approval_reference"]
        and binding["model"]
        == {key: approval[key] for key in ("model_id", "version", "content_hash")},
        "PLATFORM_SHADOW_CHALLENGER_BINDING_MISMATCH",
    )


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

"""Execute one canonical inventory input across baseline and strategy children.

The orchestrator is intentionally storage-neutral.  It validates a Platform
claim, prepares the canonical input once, executes independent child copies,
and returns sealed in-memory artifacts plus the Result Bundle.  Durable writes
and Runtime lifecycle callbacks remain separate adapters.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, localcontext
from typing import Any, Mapping, Protocol

from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.mathematical import (
    MATH_DESCRIPTOR,
    MathematicalPolicyRequest,
)
from dsio_inventory_engine.inventory_contracts.model import ModelArtifact
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.replenishment import (
    RecommendationRequest,
    replenishment_config_content_hash,
)
from dsio_inventory_engine.inventory_contracts.result_bundle import (
    BASE_SCENARIO_CONTENT_HASH,
    RESULT_BUNDLE_CONTRACT_ID,
    RESULT_BUNDLE_CONTRACT_VERSION,
    RESULT_BUNDLE_SOURCE_CONTRACT_KEY,
    PSI_SIMULATOR_IMPLEMENTATION_ID,
    PSI_SIMULATOR_IMPLEMENTATION_VERSION,
    derive_cost_profile_content_hash,
    derive_result_bundle_id,
    derive_stress_scenario_content_hash,
    seal_inventory_result_bundle,
)
from dsio_inventory_engine.inventory_contracts.runtime import (
    InventoryRuntimeExecutionRequest,
    validate_canonical_runtime_binding,
    validate_effective_policy_runtime_binding,
    validate_result_bundle_runtime_binding,
)
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    canonical_json,
    day,
    decimal_string,
    digest,
    hash_value,
    identifier,
    read_json,
    require,
)
from dsio_inventory_engine.learned.application import compile_strategy
from dsio_inventory_engine.prepare_inventory.application.inventory_input import (
    PrepareInventoryInputUseCase,
    PreparedInventoryInput,
    validate_prepared_projection,
)
from dsio_inventory_engine.recommend_replenishment.application.mathematical import (
    RunMathematicalReplenishmentUseCase,
)
from dsio_inventory_engine.recommend_replenishment.application.run import (
    RunRecommendedPsiUseCase,
    apply_effective_policy_controls,
)
from dsio_inventory_engine.simulate_inventory.application.baseline import (
    RunPsiSimulationUseCase,
)

from .result_artifacts import (
    build_failed_psi_child,
    build_psi_child_artifact,
    compare_psi_children,
    compare_psi_children_detailed,
    seal_action_observation_source,
)
from .simulation_registry import validate_cost_profile_payload


@dataclass(frozen=True, slots=True)
class ResolvedPpoChallenger:
    """A trusted loader's exact PPO implementation/model resolution."""

    model: ModelArtifact
    implementation_content_hash: str
    approval_reference: str

    def __post_init__(self) -> None:
        require(isinstance(self.model, ModelArtifact), "PPO_MODEL_ARTIFACT_REQUIRED")
        hash_value(self.implementation_content_hash)
        identifier(self.approval_reference)


@dataclass(frozen=True, slots=True)
class ResolvedCostProfile:
    """A trusted Registry's exact approved cost payload resolution."""

    cost_profile_id: str
    profile_contract_key: str
    profile_contract_version: str
    profile_payload_json: str
    profile_payload_content_hash: str
    profile_content_hash: str
    source_type: str
    approval_reference: str

    def __post_init__(self) -> None:
        identifier(self.cost_profile_id)
        require(
            self.profile_contract_key == "inventory.cost_profile",
            "COST_PROFILE_CONTRACT_INVALID",
        )
        require(_is_contract_version(self.profile_contract_version), "INVALID_CONTRACT_VERSION")
        payload = validate_cost_profile_payload(read_json(self.profile_payload_json))
        require(
            self.profile_payload_json == canonical_json(payload),
            "COST_PROFILE_PAYLOAD_NOT_CANONICAL",
        )
        require(
            digest(payload) == hash_value(self.profile_payload_content_hash),
            "COST_PROFILE_PAYLOAD_HASH_MISMATCH",
        )
        require(
            hash_value(self.profile_content_hash)
            == derive_cost_profile_content_hash(
                cost_profile_id=self.cost_profile_id,
                profile_contract_key=self.profile_contract_key,
                profile_contract_version=self.profile_contract_version,
                profile_payload_content_hash=self.profile_payload_content_hash,
            ),
            "COST_PROFILE_CONTENT_HASH_MISMATCH",
        )
        require(
            self.source_type in {"DEVELOPMENT_SYNTHETIC", "AUTHORIZED_SOURCE"},
            "COST_PROFILE_SOURCE_INVALID",
        )
        identifier(self.approval_reference)

    @property
    def payload(self) -> dict[str, str]:
        return validate_cost_profile_payload(read_json(self.profile_payload_json))


class StressScenarioRunner(Protocol):
    """Derive a stress-world Prepared input without executing a strategy."""

    def transform(
        self,
        *,
        prepared_input: PreparedInventoryInput,
        scenario_payload: Mapping[str, Any],
        deterministic_seed: int,
    ) -> PreparedInventoryInput: ...


class StressScenarioRunnerFactory(Protocol):
    """Create a fresh runner instance for every Stress child execution."""

    def __call__(self) -> StressScenarioRunner: ...


@dataclass(frozen=True, slots=True)
class ResolvedStressScenario:
    """A trusted resolver's exact stress payload and runner implementation."""

    scenario_id: str
    scenario_contract_key: str
    scenario_contract_version: str
    scenario_payload_json: str
    scenario_payload_content_hash: str
    deterministic_seed: int
    scenario_content_hash: str
    runner_implementation_id: str
    runner_implementation_version: str
    runner_implementation_content_hash: str
    runner_factory: StressScenarioRunnerFactory

    def __post_init__(self) -> None:
        identifier(self.scenario_id)
        require(self.scenario_id != "BASE", "STRESS_SCENARIO_ID_RESERVED")
        identifier(self.scenario_contract_key)
        require(
            _is_contract_version(self.scenario_contract_version),
            "INVALID_CONTRACT_VERSION",
        )
        payload = read_json(self.scenario_payload_json)
        require(
            self.scenario_payload_json == canonical_json(payload),
            "STRESS_SCENARIO_PAYLOAD_NOT_CANONICAL",
        )
        require(
            digest(payload) == hash_value(self.scenario_payload_content_hash),
            "STRESS_SCENARIO_PAYLOAD_HASH_MISMATCH",
        )
        require(
            type(self.deterministic_seed) is int
            and 0 <= self.deterministic_seed <= 9_223_372_036_854_775_807,
            "INVALID_DETERMINISTIC_SEED",
        )
        require(
            hash_value(self.scenario_content_hash)
            == derive_stress_scenario_content_hash(
                scenario_id=self.scenario_id,
                scenario_contract_key=self.scenario_contract_key,
                scenario_contract_version=self.scenario_contract_version,
                scenario_payload_content_hash=self.scenario_payload_content_hash,
                deterministic_seed=self.deterministic_seed,
            ),
            "STRESS_SCENARIO_CONTENT_HASH_MISMATCH",
        )
        identifier(self.runner_implementation_id)
        identifier(self.runner_implementation_version)
        hash_value(self.runner_implementation_content_hash)
        require(callable(self.runner_factory), "STRESS_SCENARIO_RUNNER_FACTORY_REQUIRED")


@dataclass(frozen=True, slots=True)
class PsiRunCommand:
    """All already-resolved inputs required for one deterministic Attempt."""

    runtime_request: InventoryRuntimeExecutionRequest
    mathematical_request: MathematicalPolicyRequest
    mathematical_implementation_content_hash: str
    psi_simulator_implementation_content_hash: str
    ppo_challengers: Mapping[str, ResolvedPpoChallenger] = field(default_factory=dict)
    stress_scenarios: Mapping[str, ResolvedStressScenario] = field(default_factory=dict)
    cost_profile: ResolvedCostProfile | None = None


@dataclass(frozen=True, slots=True)
class PsiRunBundle:
    """In-memory result ready for a later Artifact Writer and Runtime Handler."""

    canonical_input: CanonicalInputRequest
    prepared_input: PreparedInventoryInput
    result_bundle: Mapping[str, Any]
    artifacts: Mapping[str, Mapping[str, Any]]
    child_metrics: Mapping[str, Mapping[str, Any]]
    detailed_comparisons: tuple[Mapping[str, Any], ...]
    execution_results: Mapping[str, Mapping[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_input": self.canonical_input.to_dict(),
            "prepared_input": self.prepared_input.to_dict(),
            "result_bundle": _clone(self.result_bundle),
            "artifacts": _clone(self.artifacts),
            "child_metrics": _clone(self.child_metrics),
            "detailed_comparisons": _clone(list(self.detailed_comparisons)),
            "execution_results": _clone(self.execution_results),
        }


class RunPsiBundleUseCase:
    """Build Baseline, Math, PPO/Stress evidence and one sealed Result Bundle."""

    def __init__(
        self,
        deployment: DeploymentScope,
        *,
        prepare_use_case: PrepareInventoryInputUseCase | None = None,
    ) -> None:
        self.deployment = deployment
        self.prepare = prepare_use_case or PrepareInventoryInputUseCase(deployment)

    def execute(self, command: PsiRunCommand) -> PsiRunBundle:
        runtime, mathematical, canonical, plan = self._admit(command)
        runtime_snapshot = _clone(runtime.to_dict())
        prepared_candidate = self.prepare.execute(canonical)
        prepared = validate_prepared_projection(
            canonical,
            prepared_candidate,
            self.deployment,
        )
        manifest = prepared.to_dict()["manifest"]
        require(
            manifest["input_content_hash"] == canonical.input_hash
            and manifest["engine_run_id"] == runtime.engine_run_id,
            "PREPARED_INPUT_BINDING_MISMATCH",
        )

        baseline_result = RunPsiSimulationUseCase(self.deployment).execute(
            canonical,
            prepared_input=prepared,
        )
        baseline = build_psi_child_artifact(
            engine_run_id=runtime.engine_run_id,
            attempt_no=runtime.value["attempt_no"],
            canonical_input_hash=canonical.input_hash,
            result_kind="BASELINE_PSI",
            execution_role="EVIDENCE_ONLY",
            strategy_type="NONE",
            scenario_id="BASE",
            scenario_content_hash=plan["base_scenario_content_hash"],
            psi_rows=baseline_result["psi_rows"],
        )

        mathematical_result = RunMathematicalReplenishmentUseCase(self.deployment).execute(
            mathematical,
            runtime_request=_runtime_copy(runtime_snapshot),
            prepared_input=prepared,
        )
        mathematical_recommendation = RecommendationRequest.from_dict(
            mathematical.to_dict()["recommendation"]
        )
        mathematical_execution = mathematical_recommendation.to_dict()["execution"]
        mathematical_admissions, mathematical_observation_source = _base_action_source(
            prepared=prepared,
            execution=mathematical_execution,
            observation_input_hash=mathematical_recommendation.input_hash,
            runtime=runtime,
            canonical=canonical,
        )
        mathematical_child = build_psi_child_artifact(
            engine_run_id=runtime.engine_run_id,
            attempt_no=runtime.value["attempt_no"],
            canonical_input_hash=canonical.input_hash,
            result_kind="RECOMMENDED_PSI",
            execution_role="OPERATIONAL",
            strategy_type="MATHEMATICAL",
            scenario_id="BASE",
            scenario_content_hash=plan["base_scenario_content_hash"],
            psi_rows=mathematical_result["psi_rows"],
            decision_evidence=mathematical_result["decision_evidence"],
            expected_strategy_descriptor=MATH_DESCRIPTOR,
            expected_execution_approval_reference=mathematical_execution["approval_reference"],
            expected_observation_input_hash=mathematical_recommendation.input_hash,
            expected_strategy_input_binding=mathematical_execution.get("strategy_input_binding"),
            expected_admissions_by_item=mathematical_admissions,
            expected_observation_source=mathematical_observation_source,
        )
        _require_same_psi_universe(baseline, mathematical_child)

        child_builds: list[dict[str, Any]] = [baseline, mathematical_child]
        execution_results: dict[str, Mapping[str, Any]] = {
            baseline["child_result"]["child_result_id"]: baseline_result,
            mathematical_child["child_result"]["child_result_id"]: mathematical_result,
        }

        for challenger_binding in plan["shadow_challenger_bindings"]:
            challenger = self._execute_ppo_child(
                runtime=_runtime_copy(runtime_snapshot),
                mathematical=mathematical,
                prepared=prepared,
                canonical=canonical,
                plan_binding=challenger_binding,
                resolved=command.ppo_challengers.get(challenger_binding["challenger_id"]),
            )
            challenger = _isolate_optional_child_comparison(
                baseline,
                challenger,
                failure_prefix="PPO",
            )
            if challenger["child_result"]["status"] == "SUCCEEDED":
                execution_results[challenger["child_result"]["child_result_id"]] = challenger.pop(
                    "_execution_result"
                )
            child_builds.append(challenger)

        for scenario_binding in plan["stress_scenario_bindings"]:
            stress = self._execute_stress_child(
                runtime=_runtime_copy(runtime_snapshot),
                mathematical=mathematical,
                prepared=prepared,
                canonical=canonical,
                plan_binding=scenario_binding,
                resolved=command.stress_scenarios.get(scenario_binding["scenario_id"]),
            )
            stress = _isolate_optional_child_comparison(
                baseline,
                stress,
                failure_prefix="STRESS",
            )
            if stress["child_result"]["status"] == "SUCCEEDED":
                execution_results[stress["child_result"]["child_result_id"]] = stress.pop(
                    "_execution_result"
                )
            child_builds.append(stress)

        cost_payload = None if command.cost_profile is None else command.cost_profile.payload
        comparisons = [
            compare_psi_children(baseline, item, cost_profile=cost_payload)
            for item in child_builds[1:]
        ]
        detailed = tuple(
            compare_psi_children_detailed(baseline, item, cost_profile=cost_payload)
            for item in child_builds[1:]
        )
        artifacts = _collect_artifacts(child_builds)
        metrics = {
            item["child_result"]["child_result_id"]: _clone(item["artifact"]["metrics"])
            for item in child_builds
            if item["child_result"]["status"] == "SUCCEEDED"
        }
        result_runtime = _runtime_copy(runtime_snapshot)
        claim = result_runtime.value["claim"]
        bundle = seal_inventory_result_bundle(
            {
                "contract_id": RESULT_BUNDLE_CONTRACT_ID,
                "contract_version": RESULT_BUNDLE_CONTRACT_VERSION,
                "source_contract_key": RESULT_BUNDLE_SOURCE_CONTRACT_KEY,
                "result_bundle_id": derive_result_bundle_id(
                    runtime.engine_run_id, runtime.value["attempt_no"]
                ),
                "engine_run_id": runtime.engine_run_id,
                "attempt_no": runtime.value["attempt_no"],
                "tenant_id": claim["tenant_id"],
                "project_id": claim["project_id"],
                "planning_cycle_id": claim["planning_cycle_id"],
                "planning_cycle_revision_id": claim["planning_cycle_revision_id"],
                "cycle_site_execution_id": claim["cycle_site_execution_id"],
                "plan_id": claim["plan_id"],
                "scope": claim["scope"],
                "canonical_input_hash": canonical.input_hash,
                "site_binding_hash": claim["site_binding_hash"],
                "strategy_execution_plan_id": plan["strategy_execution_plan_id"],
                "strategy_execution_plan_content_hash": plan["content_hash"],
                "classification_config_hash": plan["classification_config_hash"],
                "effective_policy_content_hash": plan["effective_policy_content_hash"],
                "cost_profile_content_hash": (
                    None
                    if command.cost_profile is None
                    else command.cost_profile.profile_content_hash
                ),
                "result_children": [item["child_result"] for item in child_builds],
                "effective_child_result_id": mathematical_child["child_result"]["child_result_id"],
                "comparisons": comparisons,
                "automatic_publish_allowed": bool(mathematical_result["automatic_publish_allowed"]),
                "automatic_order_allowed": bool(mathematical_result["automatic_order_allowed"]),
            },
            strategy_execution_plan=plan,
        )
        validate_result_bundle_runtime_binding(
            result_runtime,
            bundle,
            expected_canonical_input_hash=canonical.input_hash,
        )
        return PsiRunBundle(
            canonical_input=canonical,
            prepared_input=prepared,
            result_bundle=bundle,
            artifacts=artifacts,
            child_metrics=metrics,
            detailed_comparisons=detailed,
            execution_results=execution_results,
        )

    def _admit(
        self, command: PsiRunCommand
    ) -> tuple[
        InventoryRuntimeExecutionRequest,
        MathematicalPolicyRequest,
        CanonicalInputRequest,
        dict[str, Any],
    ]:
        require(isinstance(command, PsiRunCommand), "PSI_RUN_COMMAND_INVALID")
        runtime = InventoryRuntimeExecutionRequest.from_dict(command.runtime_request.to_dict())
        mathematical = MathematicalPolicyRequest.from_dict(command.mathematical_request.to_dict())
        recommendation_request = RecommendationRequest.from_dict(
            mathematical.to_dict()["recommendation"]
        )
        recommendation = recommendation_request.to_dict()
        execution = recommendation["execution"]
        require(
            execution["contract_version"] == "2.0.0"
            and execution["execution_mode"] == "PLATFORM_BOUND"
            and execution["execution_purpose"] == "OPERATIONAL"
            and execution["strategy"] == MATH_DESCRIPTOR,
            "PSI_ORCHESTRATOR_OPERATIONAL_REQUEST_REQUIRED",
        )
        canonical = CanonicalInputRequest.from_dict(recommendation["canonical_input"])
        validate_canonical_runtime_binding(runtime, canonical_input=canonical)
        validate_effective_policy_runtime_binding(runtime, execution["effective_policy_binding"])
        plan = _clone(runtime.strategy_execution_plan)
        require(plan is not None, "RUNTIME_STRATEGY_EXECUTION_PLAN_REQUIRED")
        operational = plan["operational_strategy"]
        require(
            operational["strategy_type"] == MATH_DESCRIPTOR["strategy_type"]
            and operational["implementation_id"] == MATH_DESCRIPTOR["implementation_id"]
            and operational["version"] == MATH_DESCRIPTOR["version"],
            "MATHEMATICAL_IMPLEMENTATION_BINDING_MISMATCH",
        )
        require(
            operational["implementation_content_hash"]
            == hash_value(command.mathematical_implementation_content_hash),
            "MATHEMATICAL_IMPLEMENTATION_HASH_MISMATCH",
        )
        simulator = plan["psi_simulator"]
        require(
            simulator["implementation_id"] == PSI_SIMULATOR_IMPLEMENTATION_ID
            and simulator["version"] == PSI_SIMULATOR_IMPLEMENTATION_VERSION,
            "PSI_SIMULATOR_IMPLEMENTATION_BINDING_MISMATCH",
        )
        require(
            simulator["implementation_content_hash"]
            == hash_value(command.psi_simulator_implementation_content_hash),
            "PSI_SIMULATOR_IMPLEMENTATION_HASH_MISMATCH",
        )
        require(
            plan["base_scenario_content_hash"] == BASE_SCENARIO_CONTENT_HASH,
            "BASE_SCENARIO_CONTENT_HASH_MISMATCH",
        )
        require(
            operational["strategy_input_binding"] == execution["strategy_input_binding"],
            "MATHEMATICAL_STRATEGY_INPUT_BINDING_MISMATCH",
        )
        require(
            operational["replenishment_config_content_hash"]
            == replenishment_config_content_hash(execution),
            "MATHEMATICAL_EXECUTION_BINDING_MISMATCH",
        )
        require(
            set(command.ppo_challengers)
            <= {row["challenger_id"] for row in plan["shadow_challenger_bindings"]},
            "UNCLAIMED_PPO_CHALLENGER",
        )
        require(
            set(command.stress_scenarios)
            <= {row["scenario_id"] for row in plan["stress_scenario_bindings"]},
            "UNCLAIMED_STRESS_SCENARIO",
        )
        cost_binding = plan["cost_profile_binding"]
        require(
            (cost_binding is None) == (command.cost_profile is None),
            "COST_PROFILE_RESOLUTION_REQUIRED",
        )
        if cost_binding is not None and command.cost_profile is not None:
            require(
                all(
                    getattr(command.cost_profile, key) == value
                    for key, value in cost_binding.items()
                ),
                "COST_PROFILE_BINDING_MISMATCH",
            )
        return runtime, mathematical, canonical, plan

    def _execute_ppo_child(
        self,
        *,
        runtime: InventoryRuntimeExecutionRequest,
        mathematical: MathematicalPolicyRequest,
        prepared: PreparedInventoryInput,
        canonical: CanonicalInputRequest,
        plan_binding: Mapping[str, Any],
        resolved: ResolvedPpoChallenger | None,
    ) -> dict[str, Any]:
        base = {
            "engine_run_id": runtime.engine_run_id,
            "attempt_no": runtime.value["attempt_no"],
            "canonical_input_hash": canonical.input_hash,
            "result_kind": "RECOMMENDED_PSI",
            "execution_role": "SHADOW",
            "strategy_type": "DEEP_RL",
            "scenario_id": "BASE",
            "scenario_content_hash": runtime.strategy_execution_plan["base_scenario_content_hash"],
            "challenger_id": plan_binding["challenger_id"],
            "model_content_hash": plan_binding["model"]["content_hash"],
        }
        try:
            require(resolved is not None, "PPO_MODEL_UNAVAILABLE")
            _validate_ppo_resolution(plan_binding, resolved)
            model = ModelArtifact.from_dict(resolved.model.to_dict())
            model.validate_for(plan_binding["model"], canonical.to_dict()["context"])
            approval = {
                "approval_reference": plan_binding["approval_reference"],
                "status": "APPROVED",
                **plan_binding["model"],
            }
            shadow_anchor = _shadow_anchor(mathematical)
            recommendation, _, report, strategy = compile_strategy(
                shadow_anchor,
                model,
                self.deployment,
                model_approval=approval,
                runtime_request=runtime,
                prepared_input=prepared,
                challenger_id=plan_binding["challenger_id"],
                implementation_content_hash=resolved.implementation_content_hash,
            )
            result = RunRecommendedPsiUseCase(self.deployment).execute(
                recommendation,
                strategy,
                runtime_request=runtime,
                prepared_input=prepared,
            )
            require(
                result.get("automatic_publish_allowed") is False
                and result.get("automatic_order_allowed") is False,
                "SHADOW_AUTOMATION_GATE_INVALID",
            )
            result.update(
                model_artifact_hash_verified=True,
                model_artifact_verified=True,
                model_artifact=model.to_dict(),
                learned_decision_evidence=strategy.records,
                mathematical_policy_report=report,
                model_trained=False,
                operational_model_approval_verified=False,
            )
            ppo_execution = recommendation.to_dict()["execution"]
            ppo_admissions, ppo_observation_source = _base_action_source(
                prepared=prepared,
                execution=ppo_execution,
                observation_input_hash=recommendation.input_hash,
                runtime=runtime,
                canonical=canonical,
            )
            built = build_psi_child_artifact(
                **base,
                psi_rows=result["psi_rows"],
                decision_evidence=result["decision_evidence"],
                expected_strategy_descriptor=model.descriptor,
                expected_execution_approval_reference=ppo_execution["approval_reference"],
                expected_observation_input_hash=recommendation.input_hash,
                expected_strategy_input_binding=ppo_execution.get("strategy_input_binding"),
                expected_admissions_by_item=ppo_admissions,
                expected_model_approval_reference=plan_binding["approval_reference"],
                expected_observation_source=ppo_observation_source,
            )
            built["_execution_result"] = result
            return built
        except Exception as exc:
            return build_failed_psi_child(
                **base,
                failure_reason_code=_optional_child_failure_code(exc, prefix="PPO"),
            )

    def _execute_stress_child(
        self,
        *,
        runtime: InventoryRuntimeExecutionRequest,
        mathematical: MathematicalPolicyRequest,
        prepared: PreparedInventoryInput,
        canonical: CanonicalInputRequest,
        plan_binding: Mapping[str, Any],
        resolved: ResolvedStressScenario | None,
    ) -> dict[str, Any]:
        base = {
            "engine_run_id": runtime.engine_run_id,
            "attempt_no": runtime.value["attempt_no"],
            "canonical_input_hash": canonical.input_hash,
            "result_kind": "STRESS_PSI",
            "execution_role": "EVIDENCE_ONLY",
            "strategy_type": "MATHEMATICAL",
            "scenario_id": plan_binding["scenario_id"],
            "scenario_content_hash": plan_binding["scenario_content_hash"],
        }
        if resolved is None:
            return build_failed_psi_child(
                **base,
                status="SKIPPED",
                failure_reason_code="STRESS_SCENARIO_UNAVAILABLE",
            )
        try:
            require(
                resolved.scenario_id == plan_binding["scenario_id"]
                and resolved.scenario_contract_key == plan_binding["scenario_contract_key"]
                and resolved.scenario_contract_version == plan_binding["scenario_contract_version"]
                and resolved.scenario_payload_content_hash
                == plan_binding["scenario_payload_content_hash"]
                and resolved.deterministic_seed == plan_binding["deterministic_seed"]
                and resolved.scenario_content_hash == plan_binding["scenario_content_hash"],
                "STRESS_SCENARIO_BINDING_MISMATCH",
            )
            payload = read_json(resolved.scenario_payload_json)
            require(
                digest(payload) == resolved.scenario_payload_content_hash
                and resolved.scenario_content_hash
                == derive_stress_scenario_content_hash(
                    scenario_id=plan_binding["scenario_id"],
                    scenario_contract_key=resolved.scenario_contract_key,
                    scenario_contract_version=resolved.scenario_contract_version,
                    scenario_payload_content_hash=resolved.scenario_payload_content_hash,
                    deterministic_seed=resolved.deterministic_seed,
                ),
                "STRESS_SCENARIO_PAYLOAD_BINDING_MISMATCH",
            )
            require(
                resolved.runner_implementation_id == plan_binding["runner_implementation_id"]
                and resolved.runner_implementation_version
                == plan_binding["runner_implementation_version"]
                and resolved.runner_implementation_content_hash
                == plan_binding["runner_implementation_content_hash"],
                "STRESS_RUNNER_IMPLEMENTATION_BINDING_MISMATCH",
            )
            runner = resolved.runner_factory()
            replay_runner = resolved.runner_factory()
            require(
                runner is not replay_runner,
                "STRESS_RUNNER_INSTANCE_NOT_ISOLATED",
            )
            require(
                callable(getattr(runner, "transform", None)),
                "STRESS_SCENARIO_RUNNER_REQUIRED",
            )
            require(
                callable(getattr(replay_runner, "transform", None)),
                "STRESS_SCENARIO_RUNNER_REQUIRED",
            )
            stressed_candidate = runner.transform(
                prepared_input=PreparedInventoryInput(prepared.document_json),
                scenario_payload=_clone(payload),
                deterministic_seed=resolved.deterministic_seed,
            )
            replay_candidate = replay_runner.transform(
                prepared_input=PreparedInventoryInput(prepared.document_json),
                scenario_payload=_clone(payload),
                deterministic_seed=resolved.deterministic_seed,
            )
            require(
                type(stressed_candidate) is PreparedInventoryInput
                and type(replay_candidate) is PreparedInventoryInput,
                "STRESS_SCENARIO_RESULT_INVALID",
            )
            stressed_document = stressed_candidate.to_dict()
            replay_document = replay_candidate.to_dict()
            require(
                stressed_document == replay_document,
                "STRESS_DETERMINISM_REPLAY_MISMATCH",
            )
            stressed_prepared = _validate_stress_prepared_projection(
                base=prepared,
                candidate=stressed_document,
            )
            result = RunMathematicalReplenishmentUseCase(self.deployment).execute(
                _local_shadow_anchor(mathematical),
                prepared_input=stressed_prepared,
            )
            for row in result["psi_rows"]:
                row["psi_scenario_type"] = "STRESS"
            stress_execution = result.get("execution")
            require(
                result.get("automatic_publish_allowed") is False
                and result.get("automatic_order_allowed") is False
                and isinstance(stress_execution, Mapping)
                and stress_execution.get("contract_version") == "2.0.0"
                and stress_execution.get("execution_purpose") == "SHADOW"
                and stress_execution.get("strategy") == MATH_DESCRIPTOR
                and stress_execution.get("model_approval") is None,
                "STRESS_AUTOMATION_GATE_INVALID",
            )
            normalized_stress_execution = {
                **_clone(stress_execution),
                "execution_mode": "PLATFORM_BOUND",
                "execution_purpose": "OPERATIONAL",
                "strategy": dict(MATH_DESCRIPTOR),
                "model_approval": None,
            }
            operational = runtime.strategy_execution_plan["operational_strategy"]
            require(
                stress_execution.get("strategy_input_binding")
                == operational["strategy_input_binding"]
                and replenishment_config_content_hash(normalized_stress_execution)
                == operational["replenishment_config_content_hash"],
                "STRESS_MATHEMATICAL_EXECUTION_BINDING_MISMATCH",
            )
            stress_recommendation = RecommendationRequest.from_dict(
                {
                    "canonical_input": canonical.to_dict(),
                    "execution": _clone(stress_execution),
                }
            )
            normalized_stress_execution = stress_recommendation.to_dict()["execution"]
            stress_admissions = {
                row["item_id"]: row for row in result.get("effective_policy_admission_evidence", [])
            }
            stress_observation_source = seal_action_observation_source(
                prepared_input=_clone(result.get("prepared_input")),
                execution=normalized_stress_execution,
                observation_input_hash=stress_recommendation.input_hash,
                admissions_by_item=stress_admissions,
            )
            psi_rows = result.get("psi_rows")
            decisions = result.get("decision_evidence")
            require(
                type(psi_rows) is list and type(decisions) is list, "STRESS_SCENARIO_RESULT_INVALID"
            )
            built = build_psi_child_artifact(
                **base,
                psi_rows=psi_rows,
                decision_evidence=decisions,
                expected_strategy_descriptor=MATH_DESCRIPTOR,
                expected_execution_approval_reference=normalized_stress_execution[
                    "approval_reference"
                ],
                expected_observation_input_hash=stress_recommendation.input_hash,
                expected_strategy_input_binding=normalized_stress_execution.get(
                    "strategy_input_binding"
                ),
                expected_admissions_by_item=stress_admissions,
                expected_observation_source=stress_observation_source,
            )
            built["_execution_result"] = _clone(result)
            return built
        except Exception as exc:
            return build_failed_psi_child(
                **base,
                failure_reason_code=_optional_child_failure_code(exc, prefix="STRESS"),
            )


def _shadow_anchor(request: MathematicalPolicyRequest) -> MathematicalPolicyRequest:
    data = request.to_dict()
    execution = data["recommendation"]["execution"]
    execution["execution_mode"] = "PLATFORM_BOUND"
    execution["execution_purpose"] = "SHADOW"
    execution["strategy"] = dict(MATH_DESCRIPTOR)
    execution["model_approval"] = None
    return MathematicalPolicyRequest.from_dict(data)


def _local_shadow_anchor(request: MathematicalPolicyRequest) -> MathematicalPolicyRequest:
    data = _shadow_anchor(request).to_dict()
    data["recommendation"]["execution"]["execution_mode"] = "LOCAL_SHADOW"
    return MathematicalPolicyRequest.from_dict(data)


def _validate_stress_prepared_projection(
    *,
    base: PreparedInventoryInput,
    candidate: Mapping[str, Any],
) -> PreparedInventoryInput:
    """Allow a stress runner to vary only future demand and committed receipts."""

    require(type(candidate) is dict, "STRESS_PREPARED_INPUT_INVALID")
    base_document = base.to_dict()
    require(set(candidate) == set(base_document), "STRESS_PREPARED_INPUT_INVALID")
    immutable_sections = set(base_document) - {"manifest", "demands", "receipt_decisions"}
    require(
        all(candidate[key] == base_document[key] for key in immutable_sections),
        "STRESS_PREPARED_IMMUTABLE_SECTION_MISMATCH",
    )

    base_manifest = base_document["manifest"]
    candidate_manifest = candidate.get("manifest")
    require(
        type(candidate_manifest) is dict and set(candidate_manifest) == set(base_manifest),
        "STRESS_PREPARED_MANIFEST_INVALID",
    )
    mutable_manifest_fields = {"prepared_content_hash", "receipt_decisions_hash"}
    require(
        all(
            candidate_manifest[key] == base_manifest[key]
            for key in set(base_manifest) - mutable_manifest_fields
        )
        and candidate_manifest["receipt_decisions_hash"] == digest(candidate["receipt_decisions"]),
        "STRESS_PREPARED_MANIFEST_BINDING_MISMATCH",
    )

    base_demands = base_document["demands"]
    stressed_demands = candidate.get("demands")
    require(
        type(stressed_demands) is list
        and len(stressed_demands) == len(base_demands)
        and all(
            type(stressed) is dict
            and set(stressed) == set(original)
            and _stress_demand_identity(stressed) == _stress_demand_identity(original)
            for stressed, original in zip(stressed_demands, base_demands, strict=True)
        ),
        "STRESS_DEMAND_UNIVERSE_MISMATCH",
    )
    base_receipts = base_document["receipt_decisions"]
    stressed_receipts = candidate.get("receipt_decisions")
    require(
        type(stressed_receipts) is list
        and len(stressed_receipts) == len(base_receipts)
        and all(
            type(stressed) is dict
            and set(stressed) == set(original)
            and _stress_receipt_identity(stressed) == _stress_receipt_identity(original)
            for stressed, original in zip(stressed_receipts, base_receipts, strict=True)
        ),
        "STRESS_RECEIPT_UNIVERSE_MISMATCH",
    )
    _validate_stress_demand_values(stressed_demands)
    _validate_stress_receipt_values(
        stressed_receipts,
        calendar=base_document["calendar"],
        plan_start_date=base_document["context"]["plan_start_date"],
    )

    # Reconstruct the exact base class after one validated read so a hostile
    # PreparedInput subclass cannot change content between validation and use.
    return PreparedInventoryInput(canonical_json(candidate))


def _stress_demand_identity(row: Mapping[str, Any]) -> dict[str, Any]:
    mutable = {
        "gross_forecast_qty",
        "forecast_consumed_qty",
        "net_forecast_qty",
        "confirmed_customer_order_qty",
    }
    require(type(row) is dict, "STRESS_DEMAND_ROW_INVALID")
    return {key: _clone(value) for key, value in row.items() if key not in mutable}


def _stress_receipt_identity(row: Mapping[str, Any]) -> dict[str, Any]:
    mutable = {"due_date", "due_qty", "yyyyww", "included_qty", "exclusion_reason"}
    require(type(row) is dict, "STRESS_RECEIPT_ROW_INVALID")
    return {key: _clone(value) for key, value in row.items() if key not in mutable}


def _validate_stress_demand_values(rows: list[dict[str, Any]]) -> None:
    """Keep demand shocks arithmetically valid after the permitted field changes."""

    with localcontext() as ctx:
        ctx.prec = 40
        for row in rows:
            net = Decimal(_canonical_stress_quantity(row["net_forecast_qty"]))
            _canonical_stress_quantity(row["confirmed_customer_order_qty"])
            gross_raw = row["gross_forecast_qty"]
            consumed_raw = row["forecast_consumed_qty"]
            require(
                (gross_raw is None) == (consumed_raw is None),
                "STRESS_DEMAND_NETTING_EVIDENCE_INCOMPLETE",
            )
            if gross_raw is None:
                continue
            gross = Decimal(_canonical_stress_quantity(gross_raw))
            consumed = Decimal(_canonical_stress_quantity(consumed_raw))
            require(
                gross >= consumed and gross - consumed == net,
                "STRESS_DEMAND_NETTING_MISMATCH",
            )


def _validate_stress_receipt_values(
    rows: list[dict[str, Any]],
    *,
    calendar: list[dict[str, Any]],
    plan_start_date: str,
) -> None:
    """Recompute receipt inclusion from immutable status and the sealed calendar."""

    bucket_by_date: dict[str, str] = {}
    for bucket in calendar:
        start = date.fromisoformat(bucket["start_date"])
        end = date.fromisoformat(bucket["end_date"])
        for offset in range((end - start).days + 1):
            bucket_by_date[(start + timedelta(days=offset)).isoformat()] = bucket["yyyyww"]

    excluded = {
        "PLANNED": "SUPPLY_NOT_COMMITTED",
        "ORDERED": "VENDOR_COMMITMENT_NOT_VERIFIED",
        "UNVERIFIED_DUE_IN": "VENDOR_COMMITMENT_NOT_VERIFIED",
        "RECEIVED": "ALREADY_INCLUDED_IN_BOH",
        "CANCELLED": "SUPPLY_CANCELLED",
        "LEGACY_ASSUMED_CONFIRMED": "LEGACY_ONLY_SUPPLY",
    }
    for row in rows:
        due_date = day(row["due_date"])
        require(due_date == row["due_date"], "STRESS_RECEIPT_DATE_INVALID")
        due_qty = _canonical_stress_quantity(row["due_qty"])
        included_qty = _canonical_stress_quantity(row["included_qty"])
        week = bucket_by_date.get(due_date)
        reason = excluded.get(row["status"])
        if reason is None and week is None:
            require(due_date >= plan_start_date, "STRESS_OVERDUE_CONFIRMED_RECEIPT")
            reason = "OUTSIDE_PLAN_HORIZON"
        expected_included = due_qty if reason is None else "0"
        require(
            row["yyyyww"] == week
            and row["exclusion_reason"] == reason
            and included_qty == expected_included,
            "STRESS_RECEIPT_DERIVATION_MISMATCH",
        )


def _canonical_stress_quantity(value: Any) -> str:
    normalized = decimal_string(value)
    require(normalized == value, "STRESS_QUANTITY_NOT_CANONICAL")
    return normalized


def _base_action_source(
    *,
    prepared: PreparedInventoryInput,
    execution: Mapping[str, Any],
    observation_input_hash: str,
    runtime: InventoryRuntimeExecutionRequest,
    canonical: CanonicalInputRequest,
) -> tuple[dict[str, dict[str, Any]] | None, dict[str, Any]]:
    projected = prepared.to_dict()
    admissions = apply_effective_policy_controls(
        projected,
        dict(execution),
        runtime_request=runtime if execution.get("execution_mode") == "PLATFORM_BOUND" else None,
        canonical_input=canonical if execution.get("execution_mode") == "PLATFORM_BOUND" else None,
    )
    normalized_admissions = admissions or None
    return normalized_admissions, seal_action_observation_source(
        prepared_input=projected,
        execution=execution,
        observation_input_hash=observation_input_hash,
        admissions_by_item=normalized_admissions,
    )


def _validate_ppo_resolution(
    plan_binding: Mapping[str, Any], resolved: ResolvedPpoChallenger
) -> None:
    model = ModelArtifact.from_dict(resolved.model.to_dict())
    descriptor = model.descriptor
    require(
        model.to_dict()["strategy_type"] == "DEEP_RL"
        and plan_binding["strategy_type"] == "DEEP_RL"
        and plan_binding["algorithm"] == "PPO",
        "PPO_STRATEGY_BINDING_MISMATCH",
    )
    require(
        descriptor["implementation_id"] == plan_binding["implementation_id"]
        and descriptor["version"] == plan_binding["version"]
        and resolved.implementation_content_hash == plan_binding["implementation_content_hash"],
        "PPO_IMPLEMENTATION_BINDING_MISMATCH",
    )
    require(model.reference == plan_binding["model"], "PPO_MODEL_BINDING_MISMATCH")
    require(
        resolved.approval_reference == plan_binding["approval_reference"],
        "PPO_APPROVAL_BINDING_MISMATCH",
    )


def _require_same_psi_universe(baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> None:
    def keys(value: Mapping[str, Any]) -> set[tuple[Any, ...]]:
        artifact = value.get("artifact")
        require(type(artifact) is dict, "PSI_CHILD_ARTIFACT_REQUIRED")
        return {
            (
                row["company_cd"],
                row["subs_cd"],
                row["site_cd"],
                row["item_id"],
                row["uom"],
                row["seq"],
                row["yyyyww"],
                row["start_date"],
                row["end_date"],
            )
            for row in artifact["psi_rows"]
        }

    require(keys(candidate) == keys(baseline), "PSI_CHILD_UNIVERSE_MISMATCH")


def _isolate_optional_child_comparison(
    baseline: Mapping[str, Any],
    candidate: dict[str, Any],
    *,
    failure_prefix: str,
) -> dict[str, Any]:
    if candidate["child_result"]["status"] != "SUCCEEDED":
        return candidate
    try:
        compare_psi_children(
            baseline,
            {key: value for key, value in candidate.items() if key != "_execution_result"},
        )
    except Exception as exc:
        child = candidate["child_result"]
        artifact = candidate["artifact"]
        return build_failed_psi_child(
            engine_run_id=artifact["engine_run_id"],
            attempt_no=artifact["attempt_no"],
            canonical_input_hash=artifact["canonical_input_hash"],
            result_kind=child["result_kind"],
            execution_role=child["execution_role"],
            strategy_type=child["strategy_type"],
            scenario_id=child["scenario_id"],
            scenario_content_hash=child["scenario_content_hash"],
            challenger_id=child["challenger_id"],
            model_content_hash=child["model_content_hash"],
            failure_reason_code=_optional_child_failure_code(
                exc,
                prefix=failure_prefix,
            ),
        )
    return candidate


def _collect_artifacts(builds: list[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for built in builds:
        if built["child_result"]["status"] != "SUCCEEDED":
            continue
        reference = built["artifact_reference"]
        require(reference not in result, "PSI_ARTIFACT_REFERENCE_DUPLICATE")
        result[reference] = _clone(built["artifact"])
        for action in built["action_artifacts"].values():
            action_reference = action["reference"]
            require(action_reference not in result, "PSI_ARTIFACT_REFERENCE_DUPLICATE")
            result[action_reference] = _clone(action["artifact"])
    return result


def _optional_child_failure_code(exc: Exception, *, prefix: str) -> str:
    if isinstance(exc, InventoryInputError):
        code = str(exc)
        if code == "PSI_COMPARISON_UNIVERSE_MISMATCH":
            return "PSI_CHILD_UNIVERSE_MISMATCH"
        if prefix == "STRESS" and code == "STRESS_SCENARIO_UNAVAILABLE":
            # This code is reserved for a resolver miss, which is represented as
            # SKIPPED before execution. A runner raising it is still an execution
            # failure and must not escape optional-child isolation.
            return "STRESS_EXECUTION_FAILED"
        try:
            return identifier(code)
        except InventoryInputError:
            pass
    return f"{prefix}_EXECUTION_FAILED"


def _is_contract_version(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    pieces = value.split(".")
    return len(pieces) == 3 and all(piece.isdigit() and piece for piece in pieces)


def _clone(value: Any) -> Any:
    return copy.deepcopy(value)


def _runtime_copy(value: Mapping[str, Any]) -> InventoryRuntimeExecutionRequest:
    return InventoryRuntimeExecutionRequest.from_dict(_clone(dict(value)))


__all__ = [
    "PsiRunBundle",
    "PsiRunCommand",
    "ResolvedCostProfile",
    "ResolvedPpoChallenger",
    "ResolvedStressScenario",
    "RunPsiBundleUseCase",
    "StressScenarioRunner",
    "StressScenarioRunnerFactory",
]

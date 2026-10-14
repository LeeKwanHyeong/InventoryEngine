"""Build and resolve a pre-Claim approved input Manifest without loading executable code."""

import asyncio
import copy
from dataclasses import dataclass
from typing import Mapping, Protocol

from dsio_inventory_engine.classify_inventory.application import config_hash, validate_config
from dsio_inventory_engine.infrastructure.policy_analysis_inputs import normalized_payload
from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.model import ModelArtifact
from dsio_inventory_engine.inventory_contracts.result_bundle import seal_strategy_execution_plan
from dsio_inventory_engine.inventory_contracts.runtime import (
    validate_canonical_runtime_binding,
    validate_effective_policy_runtime_binding,
)
from dsio_inventory_engine.inventory_contracts.runtime_inputs import (
    bind_runtime_input_manifest,
    content_hash,
    instantiate_mathematical_template,
    plan_resources_content_hash,
    resource,
    seal_mathematical_template,
    seal_runtime_input_manifest,
    validate_manifest_plan_resources,
)
from dsio_inventory_engine.inventory_contracts.run_cost import (
    validate_run_cost_claim,
    validate_run_cost_input,
)
from dsio_inventory_engine.inventory_contracts.replenishment import (
    replenishment_config_content_hash,
)
from dsio_inventory_engine.inventory_contracts.trade_cost_projection import (
    validate_trade_cost_projection_runtime_binding,
)
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    hash_value,
    require,
)
from .landed_cost_pipeline import ResolvedRunLandedCost
from .policy_analysis_producer import produce_policy_analysis_inputs
from .psi_orchestrator import PsiRunCommand, ResolvedPpoChallenger
from .simulation_registry import DevelopmentSimulationRegistry


class RuntimeInputManifestProvider(Protocol):
    async def resolve(self, request) -> dict: ...


def build_runtime_input_manifest(
    *,
    plan,
    mathematical_request,
    config_values,
    classification_snapshot,
    tenant_id,
    project_id,
    scope,
    snapshot_id,
    models=None,
    evaluation_requests=None,
    run_cost_input=None,
    simulation_registry=None,
):
    """Source caller supplies original documents; no DB projection reconstruction or latest lookup."""
    request = MathematicalPolicyRequest.from_dict(mathematical_request.to_dict())
    canonical = CanonicalInputRequest.from_dict(
        request.to_dict()["recommendation"]["canonical_input"]
    )
    context = canonical.to_dict()["context"]
    plan = seal_strategy_execution_plan({k: v for k, v in plan.items() if k != "content_hash"})
    execution = request.to_dict()["recommendation"]["execution"]
    require(
        execution["strategy_input_binding"]
        == plan["operational_strategy"]["strategy_input_binding"]
        and replenishment_config_content_hash(execution)
        == plan["operational_strategy"]["replenishment_config_content_hash"],
        "RUNTIME_MATHEMATICAL_TEMPLATE_BINDING_MISMATCH",
    )
    config = validate_config(config_values)
    spec = plan["policy_analysis_spec"]
    classification, _ = normalized_payload(
        "CLASSIFICATION", spec["classification_snapshot_id"], classification_snapshot
    )
    require(
        config_hash(config) == spec["config_hash"]
        and config["policy_search"] == spec["search_spec"]
        and classification["config_id"] == spec["config_id"]
        and classification["config_revision_id"] == spec["config_revision_id"]
        and classification["content_hash"] == spec["classification_snapshot_content_hash"]
        and classification["effective_policy_content_hash"]
        == spec["effective_policy_content_hash"],
        "RUNTIME_INPUT_SOURCE_CONFIG_MISMATCH",
    )
    require(
        classification["tenant_id"] == tenant_id
        and classification["project_id"] == project_id
        and all(classification["scope"][k] == v for k, v in scope.items())
        and all(context[k] == scope[k] for k in ("company_cd", "subs_cd", "site_cd")),
        "RUNTIME_INPUT_SOURCE_SCOPE_MISMATCH",
    )
    rows = [
        resource("CONFIG", spec["config_revision_id"], spec["config_hash"], config),
        resource(
            "CLASSIFICATION",
            spec["classification_snapshot_id"],
            classification["content_hash"],
            classification,
        ),
        resource(
            "MATHEMATICAL_TEMPLATE",
            request.to_dict()["policy_input"]["snapshot_id"],
            request.to_dict()["policy_input"]["content_hash"],
            seal_mathematical_template(request.to_dict()),
        ),
    ]
    registry = simulation_registry or DevelopmentSimulationRegistry()
    if plan["cost_profile_binding"] is not None:
        resolved = registry.resolved_cost_profile()
        require(
            registry.cost_profile_binding() == plan["cost_profile_binding"],
            "RUNTIME_COST_REGISTRY_MISMATCH",
        )
        rows.append(
            resource(
                "COST_PROFILE",
                resolved.cost_profile_id,
                resolved.profile_content_hash,
                resolved.payload,
                approval_reference=resolved.approval_reference,
            )
        )
    stress = registry.resolved_stress_scenarios()
    expected_stress = {r["scenario_id"]: r for r in registry.stress_scenario_bindings()}
    for binding in plan["stress_scenario_bindings"]:
        require(
            binding == expected_stress.get(binding["scenario_id"]),
            "RUNTIME_STRESS_REGISTRY_MISMATCH",
        )
        resolved = stress[binding["scenario_id"]]
        from dsio_inventory_engine.inventory_contracts.values import read_json

        rows.append(
            resource(
                "STRESS_SCENARIO",
                binding["scenario_id"],
                binding["scenario_content_hash"],
                read_json(resolved.scenario_payload_json),
                approval_reference=registry.approval_reference,
            )
        )
    models = dict(models or {})
    require(
        set(models) == {r["challenger_id"] for r in plan["shadow_challenger_bindings"]},
        "RUNTIME_MODEL_SOURCE_SET_MISMATCH",
    )
    for binding in plan["shadow_challenger_bindings"]:
        model = ModelArtifact.from_dict(models[binding["challenger_id"]].to_dict())
        model.validate_for(binding["model"], context)
        rows.append(
            resource(
                "PPO_MODEL",
                binding["challenger_id"],
                model.reference["content_hash"],
                model.to_dict(),
                approval_reference=binding["approval_reference"],
            )
        )
    evaluations = dict(evaluation_requests or {})
    evaluation_spec = spec["evaluation_spec"]
    cases = (
        []
        if evaluation_spec["mode"] == "DISABLED"
        else [c for c in evaluation_spec["cases"] if c["input_source"] != "CURRENT_RUN"]
    )
    require(
        set(evaluations) == {c["input_snapshot_id"] for c in cases},
        "RUNTIME_EVALUATION_SOURCE_SET_MISMATCH",
    )
    for case in cases:
        raw = MathematicalPolicyRequest.from_dict(evaluations[case["input_snapshot_id"]]).to_dict()
        require(
            content_hash(raw) == case["input_content_hash"],
            "RUNTIME_EVALUATION_SOURCE_HASH_MISMATCH",
        )
        rows.append(
            resource(
                "MATHEMATICAL_EVALUATION",
                case["input_snapshot_id"],
                case["input_content_hash"],
                raw,
            )
        )
    require(
        (plan.get("landed_cost_binding") is not None) == (run_cost_input is not None),
        "RUNTIME_COST_INPUT_REQUIRED",
    )
    if run_cost_input is not None:
        raw = validate_run_cost_input(run_cost_input)
        rows.append(resource("RUN_COST_INPUT", raw["snapshot_id"], raw["content_hash"], raw))
    body = {
        "contract_id": "inventory-runtime-input-manifest",
        "contract_version": "1.0.0",
        "environment_scope": "DEVELOPMENT",
        "snapshot_id": snapshot_id,
        "tenant_id": tenant_id,
        "project_id": project_id,
        "planning_cycle_id": context["planning_cycle_id"],
        "planning_cycle_revision_id": context["planning_cycle_revision_id"],
        "demand_run_id": context["demand_run_id"],
        "scope": dict(scope),
        "config_id": spec["config_id"],
        "config_revision_id": spec["config_revision_id"],
        "config_hash": spec["config_hash"],
        "canonical_input_hash": canonical.input_hash,
        "plan_resources_content_hash": plan_resources_content_hash(plan),
        "resources": rows,
    }
    manifest = validate_manifest_plan_resources(seal_runtime_input_manifest(body), plan)
    return manifest, seal_strategy_execution_plan(bind_runtime_input_manifest(plan, manifest))


def validate_manifest_request(request, value):
    manifest = validate_manifest_plan_resources(value, request.strategy_execution_plan)
    claim = request.value["claim"]
    binding = request.strategy_execution_plan["runtime_input_binding"]
    require(
        manifest["snapshot_id"] == binding["snapshot_id"]
        and manifest["content_hash"] == binding["content_hash"],
        "RUNTIME_INPUT_MANIFEST_BINDING_MISMATCH",
    )
    require(
        all(
            manifest[key] == claim[key]
            for key in (
                "tenant_id",
                "project_id",
                "planning_cycle_id",
                "planning_cycle_revision_id",
                "demand_run_id",
                "config_id",
                "config_revision_id",
                "config_hash",
                "scope",
            )
        ),
        "RUNTIME_INPUT_MANIFEST_SCOPE_MISMATCH",
    )
    return manifest


@dataclass(frozen=True)
class RegisteredRuntimeImplementations:
    mathematical_content_hash: str
    psi_simulator_content_hash: str
    ppo_implementations: Mapping[tuple[str, str], str]

    def __post_init__(self):
        hash_value(self.mathematical_content_hash)
        hash_value(self.psi_simulator_content_hash)
        for value in self.ppo_implementations.values():
            hash_value(value)
        # Freeze a defensive copy; requests never register their own implementation hashes.
        from types import MappingProxyType

        object.__setattr__(
            self, "ppo_implementations", MappingProxyType(dict(self.ppo_implementations))
        )


class ApprovedInputCommandResolver:
    def __init__(self, *, provider, implementations, deployment, simulation_registry=None):
        require(deployment.environment == "DEVELOPMENT", "RUNTIME_APPROVED_INPUT_DEVELOPMENT_ONLY")
        self.provider, self.implementations, self.deployment = provider, implementations, deployment
        self.registry = simulation_registry or DevelopmentSimulationRegistry()

    async def resolve(self, request):
        try:
            manifest = validate_manifest_request(request, await self.provider.resolve(request))
            return await asyncio.to_thread(self._resolve, request, manifest)
        except (ValueError, KeyError, TypeError) as exc:
            if isinstance(exc, InventoryInputError):
                raise
            raise InventoryInputError("RUNTIME_APPROVED_INPUT_INVALID") from exc

    def _resolve(self, request, manifest):
        plan = request.strategy_execution_plan
        registered = self.implementations
        require(
            plan["operational_strategy"]["implementation_content_hash"]
            == registered.mathematical_content_hash
            and plan["psi_simulator"]["implementation_content_hash"]
            == registered.psi_simulator_content_hash,
            "RUNTIME_IMPLEMENTATION_NOT_REGISTERED",
        )
        rows = {(r["kind"], r["snapshot_id"]): r for r in manifest["resources"]}

        def source(kind, sid):
            return copy.deepcopy(rows[(kind, sid)]["document"])

        binding = plan["operational_strategy"]["strategy_input_binding"]
        math = MathematicalPolicyRequest.from_dict(
            instantiate_mathematical_template(
                source("MATHEMATICAL_TEMPLATE", binding["snapshot_id"]), request.engine_run_id
            )
        )
        canonical = CanonicalInputRequest.from_dict(
            math.to_dict()["recommendation"]["canonical_input"]
        )
        require(
            canonical.input_hash == manifest["canonical_input_hash"]
            and math.to_dict()["recommendation"]["execution"]["strategy_input_binding"] == binding
            and replenishment_config_content_hash(math.to_dict()["recommendation"]["execution"])
            == plan["operational_strategy"]["replenishment_config_content_hash"],
            "RUNTIME_MATHEMATICAL_TEMPLATE_BINDING_MISMATCH",
        )
        validate_canonical_runtime_binding(request, canonical_input=canonical)
        validate_effective_policy_runtime_binding(
            request, math.to_dict()["recommendation"]["execution"]["effective_policy_binding"]
        )
        cost = None
        if plan["cost_profile_binding"] is not None:
            cost = self.registry.resolved_cost_profile()
            require(
                self.registry.cost_profile_binding() == plan["cost_profile_binding"]
                and cost.payload == source("COST_PROFILE", cost.cost_profile_id),
                "RUNTIME_COST_REGISTRY_MISMATCH",
            )
        stress = {}
        registry_stress = {r["scenario_id"]: r for r in self.registry.stress_scenario_bindings()}
        for binding in plan["stress_scenario_bindings"]:
            require(
                binding == registry_stress.get(binding["scenario_id"]),
                "RUNTIME_STRESS_REGISTRY_MISMATCH",
            )
            resolved = self.registry.resolved_stress_scenarios()[binding["scenario_id"]]
            from dsio_inventory_engine.inventory_contracts.values import read_json

            row = rows[("STRESS_SCENARIO", binding["scenario_id"])]
            require(
                row["document"] == read_json(resolved.scenario_payload_json)
                and row["approval_reference"] == self.registry.approval_reference,
                "RUNTIME_STRESS_PAYLOAD_MISMATCH",
            )
            stress[binding["scenario_id"]] = resolved
        ppo = {}
        for binding in plan["shadow_challenger_bindings"]:
            require(
                registered.ppo_implementations.get(
                    (binding["implementation_id"], binding["version"])
                )
                == binding["implementation_content_hash"],
                "RUNTIME_PPO_IMPLEMENTATION_NOT_REGISTERED",
            )
            model = ModelArtifact.from_dict(source("PPO_MODEL", binding["challenger_id"]))
            require(
                model.descriptor["implementation_id"] == binding["implementation_id"]
                and model.descriptor["version"] == binding["version"]
                and model.to_dict()["strategy_type"] == "DEEP_RL",
                "RUNTIME_PPO_DESCRIPTOR_MISMATCH",
            )
            model.validate_for(binding["model"], canonical.to_dict()["context"])
            ppo[binding["challenger_id"]] = ResolvedPpoChallenger(
                model, binding["implementation_content_hash"], binding["approval_reference"]
            )
        spec = plan["policy_analysis_spec"]
        config = source("CONFIG", spec["config_revision_id"])
        classification = source("CLASSIFICATION", spec["classification_snapshot_id"])
        normalized_payload("CLASSIFICATION", spec["classification_snapshot_id"], classification)
        require(
            config_hash(config) == spec["config_hash"]
            and config["policy_search"] == spec["search_spec"]
            and classification["content_hash"] == spec["classification_snapshot_content_hash"]
            and classification["effective_policy_content_hash"]
            == spec["effective_policy_content_hash"]
            and classification["tenant_id"] == request.tenant_id
            and classification["project_id"] == request.project_id
            and classification["scope"] == request.value["claim"]["scope"]
            and classification["config_id"] == spec["config_id"]
            and classification["config_revision_id"] == spec["config_revision_id"],
            "RUNTIME_CLASSIFICATION_SOURCE_MISMATCH",
        )
        inputs = None
        if spec["search_spec"]["enabled"]:
            evaluations = {}
            if spec["evaluation_spec"]["mode"] != "DISABLED":
                for case in spec["evaluation_spec"]["cases"]:
                    if case["input_source"] != "CURRENT_RUN":
                        evaluations[case["case_id"]] = source(
                            "MATHEMATICAL_EVALUATION", case["input_snapshot_id"]
                        )
            inputs = produce_policy_analysis_inputs(
                deployment=self.deployment,
                specification=spec,
                mathematical_request=math,
                config_values=config,
                classification_snapshot=classification,
                evaluation_requests=evaluations,
            )
        return PsiRunCommand(
            request,
            math,
            registered.mathematical_content_hash,
            registered.psi_simulator_content_hash,
            ppo,
            stress,
            cost,
            inputs,
        )


class ApprovedInputLandedCostResolver:
    def __init__(self, *, provider, projection):
        self.provider, self.projection = provider, projection

    async def resolve(self, request):
        manifest = validate_manifest_request(request, await self.provider.resolve(request))
        binding = request.strategy_execution_plan["landed_cost_binding"]
        row = next(
            r
            for r in manifest["resources"]
            if r["kind"] == "RUN_COST_INPUT" and r["snapshot_id"] == binding["input_snapshot_id"]
        )
        inputs = validate_run_cost_claim(request, row["document"])
        projection = await self.projection.resolve(request)
        validate_trade_cost_projection_runtime_binding(
            request, projection, environment_scope="DEVELOPMENT"
        )
        return ResolvedRunLandedCost(inputs, projection)

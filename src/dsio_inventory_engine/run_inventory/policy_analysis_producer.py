"""Produce search/evaluation evidence from exact, claimed development inputs."""

import asyncio
import copy
from dataclasses import replace
from typing import Protocol

from dsio_inventory_engine.classify_inventory.application import config_hash, validate_config
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.policy_analysis_spec import (
    validate_policy_analysis_spec,
)
from dsio_inventory_engine.inventory_contracts.policy_sensitivity import PolicySensitivityRequest
from dsio_inventory_engine.inventory_contracts.values import digest, require
from dsio_inventory_engine.inventory_evidence.policy_analysis import (
    PolicyAnalysisInputs,
    run_shadow_request,
)
from dsio_inventory_engine.recommend_replenishment.application.policy_search import (
    RunPolicySearchUseCase,
)
from dsio_inventory_engine.recommend_replenishment.application.policy_sensitivity import (
    RunPolicySensitivityUseCase,
)
from .psi_orchestrator import PsiRunCommand


class AnalysisInputReader(Protocol):
    async def read(
        self, *, kind: str, snapshot_id: str, content_hash: str, tenant_id: str, project_id: str
    ) -> dict: ...


def produce_policy_analysis_inputs(
    *,
    deployment,
    specification,
    mathematical_request,
    config_values,
    classification_snapshot,
    evaluation_requests=None,
):
    """No stored Search is accepted as authority; evaluation cases start from Math requests."""
    require(deployment.environment == "DEVELOPMENT", "POLICY_ANALYSIS_DEVELOPMENT_ONLY")
    spec = validate_policy_analysis_spec(specification)
    config = validate_config(config_values)
    snapshot = copy.deepcopy(classification_snapshot)
    require(
        config_hash(config) == spec["config_hash"]
        and config["policy_search"] == spec["search_spec"]
        and snapshot["classification_snapshot_id"] == spec["classification_snapshot_id"]
        and snapshot["content_hash"] == spec["classification_snapshot_content_hash"]
        and snapshot["effective_policy_content_hash"] == spec["effective_policy_content_hash"]
        and snapshot["config_id"] == spec["config_id"]
        and snapshot["config_revision_id"] == spec["config_revision_id"],
        "POLICY_ANALYSIS_SOURCE_BINDING_MISMATCH",
    )
    require(spec["search_spec"]["enabled"], "POLICY_ANALYSIS_SEARCH_DISABLED")
    searcher = RunPolicySearchUseCase(deployment)
    search = searcher.execute(
        run_shadow_request(mathematical_request),
        config_values=config,
        classification_snapshot=snapshot,
    )
    evaluation, sources = None, {}
    request_sources = copy.deepcopy(dict(evaluation_requests or {}))
    evaluation_spec = spec["evaluation_spec"]
    expected_cases = (
        {}
        if evaluation_spec["mode"] == "DISABLED"
        else {
            case["case_id"]: case
            for case in evaluation_spec["cases"]
            if case["input_source"] != "CURRENT_RUN"
        }
    )
    require(
        set(request_sources) == set(expected_cases), "POLICY_ANALYSIS_EVALUATION_INPUTS_MISMATCH"
    )
    if evaluation_spec["mode"] != "DISABLED":
        cases = []
        for case in evaluation_spec["cases"]:
            if case["input_source"] == "CURRENT_RUN":
                result = search
            else:
                raw = MathematicalPolicyRequest.from_dict(
                    request_sources[case["case_id"]]
                ).to_dict()
                require(
                    digest(raw) == case["input_content_hash"],
                    "POLICY_ANALYSIS_EVALUATION_INPUT_HASH_MISMATCH",
                )
                request_sources[case["case_id"]] = raw
                result = searcher.execute(
                    run_shadow_request(MathematicalPolicyRequest.from_dict(raw)),
                    config_values=config,
                    classification_snapshot=snapshot,
                )
            cases.append({key: case[key] for key in ("case_id", "trace_id", "split", "seed")})
            cases[-1]["search_result"] = result
            sources[result["content_hash"]] = result
        raw = {
            key: copy.deepcopy(evaluation_spec[key])
            for key in (
                "evaluation_id",
                "source_type",
                "reference_case_id",
                "reference_cost_profile_id",
                "cost_profiles",
                "terminal_pairs",
            )
        }
        raw.update(
            contract_id="inventory-policy-sensitivity-request",
            contract_version="1.0.0",
            cases=cases,
        )
        evaluation = RunPolicySensitivityUseCase(deployment).execute(
            PolicySensitivityRequest.from_dict(raw)
        )
    return PolicyAnalysisInputs(search, config, snapshot, evaluation, sources, request_sources)


class ExactMathematicalCommandResolver:
    """Minimal concrete offline resolver; unsupported PPO/Stress/Cost resources fail closed."""

    def __init__(
        self,
        reader: AnalysisInputReader,
        *,
        mathematical_implementation_content_hash,
        psi_simulator_implementation_content_hash,
    ):
        self.reader = reader
        self.mathematical_hash = mathematical_implementation_content_hash
        self.simulator_hash = psi_simulator_implementation_content_hash

    async def resolve(self, request):
        plan = request.strategy_execution_plan
        require(
            not plan["shadow_challenger_bindings"]
            and not plan["stress_scenario_bindings"]
            and plan["cost_profile_binding"] is None,
            "PSI_COMMAND_RESOURCE_RESOLVER_REQUIRED",
        )
        require(
            plan["operational_strategy"]["implementation_content_hash"] == self.mathematical_hash
            and plan["psi_simulator"]["implementation_content_hash"] == self.simulator_hash,
            "PSI_COMMAND_IMPLEMENTATION_MISMATCH",
        )
        binding = plan["operational_strategy"]["strategy_input_binding"]
        raw = await self.reader.read(
            kind="MATHEMATICAL_BASE",
            snapshot_id=binding["snapshot_id"],
            content_hash=binding["content_hash"],
            tenant_id=request.tenant_id,
            project_id=request.project_id,
        )
        mathematical = MathematicalPolicyRequest.from_dict(raw)
        require(
            mathematical.to_dict()["recommendation"]["execution"]["strategy_input_binding"]
            == binding,
            "POLICY_ANALYSIS_MATHEMATICAL_INPUT_MISMATCH",
        )
        return PsiRunCommand(
            runtime_request=request,
            mathematical_request=mathematical,
            mathematical_implementation_content_hash=self.mathematical_hash,
            psi_simulator_implementation_content_hash=self.simulator_hash,
        )


class PolicyAnalysisCommandResolver:
    """Wrap a trusted PSI resolver; no 'latest' query and no precomputed result injection."""

    def __init__(self, *, base_resolver, reader: AnalysisInputReader, deployment):
        self.base_resolver, self.reader, self.deployment = base_resolver, reader, deployment

    async def resolve(self, request):
        command = await self.base_resolver.resolve(request)
        require(
            isinstance(command, PsiRunCommand)
            and command.runtime_request.canonical_hash == request.canonical_hash,
            "RUNTIME_COMMAND_CLAIM_MISMATCH",
        )
        spec = request.strategy_execution_plan.get("policy_analysis_spec")
        if spec is None:
            return command
        require(
            command.policy_analysis_inputs is None, "POLICY_ANALYSIS_PRECOMPUTED_INPUT_FORBIDDEN"
        )
        require(self.deployment.environment == "DEVELOPMENT", "POLICY_ANALYSIS_DEVELOPMENT_ONLY")
        scope = dict(tenant_id=request.tenant_id, project_id=request.project_id)
        config = await self.reader.read(
            kind="CONFIG",
            snapshot_id=spec["config_revision_id"],
            content_hash=spec["config_hash"],
            **scope,
        )
        snapshot = await self.reader.read(
            kind="CLASSIFICATION",
            snapshot_id=spec["classification_snapshot_id"],
            content_hash=spec["classification_snapshot_content_hash"],
            **scope,
        )
        require(
            config_hash(config) == spec["config_hash"]
            and config["policy_search"] == spec["search_spec"]
            and snapshot["effective_policy_content_hash"] == spec["effective_policy_content_hash"]
            and snapshot["config_id"] == spec["config_id"]
            and snapshot["config_revision_id"] == spec["config_revision_id"],
            "POLICY_ANALYSIS_SOURCE_BINDING_MISMATCH",
        )
        require(
            snapshot["tenant_id"] == request.tenant_id
            and snapshot["project_id"] == request.project_id,
            "POLICY_ANALYSIS_SOURCE_SCOPE_MISMATCH",
        )
        if not spec["search_spec"]["enabled"]:
            return command
        cases = {}
        if spec["evaluation_spec"]["mode"] != "DISABLED":
            for case in spec["evaluation_spec"]["cases"]:
                if case["input_source"] != "CURRENT_RUN":
                    cases[case["case_id"]] = await self.reader.read(
                        kind="MATHEMATICAL_REQUEST",
                        snapshot_id=case["input_snapshot_id"],
                        content_hash=case["input_content_hash"],
                        **scope,
                    )
        inputs = await asyncio.to_thread(
            produce_policy_analysis_inputs,
            deployment=self.deployment,
            specification=spec,
            mathematical_request=command.mathematical_request,
            config_values=config,
            classification_snapshot=snapshot,
            evaluation_requests=cases,
        )
        return replace(command, policy_analysis_inputs=inputs)

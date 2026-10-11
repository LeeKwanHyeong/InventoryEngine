"""Run-bound, development-only companion artifacts; never replace an operational PSI."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Mapping
from dsio_inventory_engine.classify_inventory.application import validate_config

from dsio_inventory_engine.inventory_contracts.policy_sensitivity import PolicySensitivityRequest
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.recommend_replenishment.application.policy_search import (
    RunPolicySearchUseCase,
)
from dsio_inventory_engine.inventory_contracts.values import digest, require
from dsio_inventory_engine.recommend_replenishment.application.policy_selection import (
    RunPolicySelectionUseCase,
)
from dsio_inventory_engine.recommend_replenishment.application.policy_sensitivity import (
    RunPolicySensitivityUseCase,
)

from .contracts import ArtifactObject

CONTRACT_KEY = "inventory.policy_analysis"
CONTRACT_ID = "inventory-policy-analysis-manifest"


def run_shadow_request(request: MathematicalPolicyRequest) -> MathematicalPolicyRequest:
    """Keep immutable Run inputs and derive a calculation-only local companion."""
    raw = copy.deepcopy(request.to_dict())
    raw["recommendation"]["execution"].update(
        execution_mode="LOCAL_SHADOW", execution_purpose="SHADOW"
    )
    return MathematicalPolicyRequest.from_dict(raw)


@dataclass(frozen=True)
class PolicyAnalysisInputs:
    search_result: dict
    config_values: dict
    classification_snapshot: dict
    evaluation_result: dict | None = None
    evaluation_sources: Mapping[str, dict] = field(default_factory=dict)


@dataclass(frozen=True)
class RunPolicyAnalysisEvidence:
    manifest: dict
    artifacts: tuple[ArtifactObject, ...]


def build_policy_analysis(
    *,
    deployment,
    request,
    bundle: Mapping,
    inputs: PolicyAnalysisInputs,
    mathematical_request: MathematicalPolicyRequest,
):
    """Recompute selection and optional evaluation before atomic Run persistence."""
    require(isinstance(inputs, PolicyAnalysisInputs), "POLICY_ANALYSIS_INPUTS_INVALID")
    request = type(request).from_dict(copy.deepcopy(request.to_dict()))
    mathematical_request = MathematicalPolicyRequest.from_dict(mathematical_request.to_dict())
    dispatch = request.to_dict()
    raw = {**dispatch["claim"], **dispatch["claim"]["scope"], "attempt_no": dispatch["attempt_no"]}
    search = copy.deepcopy(inputs.search_result)
    config_values = validate_config(inputs.config_values)
    require(
        mathematical_request.to_dict()["policy_input"]["content_hash"]
        == request.strategy_execution_plan["operational_strategy"]["strategy_input_binding"][
            "content_hash"
        ],
        "POLICY_ANALYSIS_MATHEMATICAL_INPUT_MISMATCH",
    )
    expected_search = RunPolicySearchUseCase(deployment).execute(
        run_shadow_request(mathematical_request),
        config_values=config_values,
        classification_snapshot=inputs.classification_snapshot,
    )
    require(expected_search == search, "POLICY_ANALYSIS_SEARCH_REPLAY_MISMATCH")
    selector = RunPolicySelectionUseCase(deployment)
    selection = selector.execute(search)  # Also replays actual PSI and the common action Guard.
    class_binding = next(
        (v for v in raw["input_bindings"] if v["input_type"] == "INVENTORY_CLASSIFICATION"), None
    )
    require(class_binding is not None, "POLICY_ANALYSIS_CLASSIFICATION_REQUIRED")
    require(
        search["canonical_input_hash"] == bundle["canonical_input_hash"]
        and search["context"]["engine_run_id"] == request.engine_run_id
        and search["config_hash"] == raw["config_hash"]
        and inputs.classification_snapshot["effective_policy_content_hash"]
        == class_binding["source_content_hash"]
        and search["classification_snapshot_id"] == class_binding["source_snapshot_id"],
        "POLICY_ANALYSIS_RUN_INPUT_MISMATCH",
    )
    for key in ("company_cd", "subs_cd", "site_cd"):
        require(search["context"][key] == raw[key], "POLICY_ANALYSIS_SCOPE_MISMATCH")
    require(
        bundle["engine_run_id"] == request.engine_run_id
        and bundle["attempt_no"] == raw["attempt_no"]
        and bundle["strategy_execution_plan_content_hash"]
        == request.strategy_execution_plan["content_hash"],
        "POLICY_ANALYSIS_BUNDLE_MISMATCH",
    )
    root = f"artifact:inventory:{request.engine_run_id}:{raw['attempt_no']}:policy-analysis"
    source_body = {
        "contract_id": "inventory-policy-analysis-inputs",
        "contract_version": "1.0.0",
        "mathematical_request": mathematical_request.to_dict(),
        "config_values": config_values,
        "classification_snapshot": copy.deepcopy(inputs.classification_snapshot),
    }
    source_document = {**source_body, "content_hash": digest(source_body)}
    documents = [("ACTUAL_INPUT", source_document), ("SEARCH", search), ("SELECTION", selection)]
    evaluation = copy.deepcopy(inputs.evaluation_result)
    sources = copy.deepcopy(dict(inputs.evaluation_sources))
    if evaluation is None:
        require(not sources, "POLICY_ANALYSIS_UNUSED_EVALUATION_SOURCES")
    else:
        spec = copy.deepcopy(evaluation["evaluation_spec"])
        require(
            set(sources) == {c["search_content_hash"] for c in spec["cases"]},
            "POLICY_ANALYSIS_EVALUATION_SOURCES_MISMATCH",
        )
        reference = next(c for c in spec["cases"] if c["case_id"] == spec["reference_case_id"])
        require(
            reference["search_content_hash"] == search["content_hash"],
            "POLICY_ANALYSIS_REFERENCE_CASE_MISMATCH",
        )
        reference_cost = next(
            c for c in spec["cost_profiles"] if c["profile_id"] == spec["reference_cost_profile_id"]
        )
        require(
            reference_cost["cost_assumptions"] == search["search_config"]["cost_assumptions"],
            "POLICY_ANALYSIS_REFERENCE_COST_MISMATCH",
        )
        for case in spec["cases"]:
            source_hash = case.pop("search_content_hash")
            require(
                case.pop("search_result_reference") == "PSR-" + source_hash,
                "POLICY_ANALYSIS_SOURCE_REFERENCE_MISMATCH",
            )
            require(
                sources[source_hash]["content_hash"] == source_hash,
                "POLICY_ANALYSIS_SOURCE_HASH_MISMATCH",
            )
            case["search_result"] = sources[source_hash]
        expected = RunPolicySensitivityUseCase(deployment).execute(
            PolicySensitivityRequest.from_dict(spec)
        )
        require(expected == evaluation, "POLICY_ANALYSIS_EVALUATION_REPLAY_MISMATCH")
        documents.append(("EVALUATION", evaluation))
        documents.extend(
            ("EVALUATION_SOURCE", sources[h])
            for h in sorted(sources)
            if h != search["content_hash"]
        )
    children, artifacts = [], []
    for role, document in documents:
        reference = f"{root}:{role.lower()}:{document['content_hash']}"
        contract_key = {
            "SEARCH": "inventory.policy_search_result",
            "SELECTION": "inventory.policy_selection_result",
            "EVALUATION": "inventory.policy_sensitivity_result",
            "EVALUATION_SOURCE": "inventory.policy_search_result",
            "ACTUAL_INPUT": "inventory.policy_analysis_inputs",
        }[role]
        artifacts.append(
            ArtifactObject.from_document(
                reference=reference,
                contract_key=contract_key,
                document=document,
            )
        )
        children.append(
            {
                "role": role,
                "artifact_reference": reference,
                "contract_id": document["contract_id"],
                "contract_version": document["contract_version"],
                "content_hash": document["content_hash"],
            }
        )
    body = {
        "contract_id": CONTRACT_ID,
        "contract_version": "1.0.0",
        "source_contract_key": CONTRACT_KEY,
        "artifact_reference": root,
        "engine_run_id": request.engine_run_id,
        "attempt_no": raw["attempt_no"],
        **{
            k: raw[k]
            for k in ("tenant_id", "project_id", "company_cd", "subs_cd", "site_cd", "config_hash")
        },
        "site_binding_hash": raw["site_binding_hash"],
        "classification_snapshot_id": search["classification_snapshot_id"],
        "classification_content_hash": search["classification_content_hash"],
        "classification_effective_policy_hash": class_binding["source_content_hash"],
        "canonical_input_hash": search["canonical_input_hash"],
        "strategy_execution_plan_content_hash": request.strategy_execution_plan["content_hash"],
        "result_bundle_content_hash": bundle["content_hash"],
        "evaluation_status": "AVAILABLE" if evaluation else "NOT_BOUND",
        "automatic_publish_allowed": False,
        "automatic_order_allowed": False,
        "operational_policy_approved": False,
        "children": children,
    }
    manifest = {**body, "content_hash": digest(body)}
    artifacts.append(
        ArtifactObject.from_document(
            reference=root,
            contract_key=CONTRACT_KEY,
            document=manifest,
        )
    )
    return RunPolicyAnalysisEvidence(manifest, tuple(artifacts))


def verify_policy_analysis(*, deployment, request, bundle, repository, pointer):
    root = (
        f"artifact:inventory:{request.engine_run_id}:{request.value['attempt_no']}:policy-analysis"
    )
    require(pointer["policy_analysis_reference"] == root, "POLICY_ANALYSIS_POINTER_MISMATCH")
    manifest = repository.read_artifact(root).document()
    require(
        manifest.get("contract_id") == CONTRACT_ID and manifest.get("contract_version") == "1.0.0",
        "POLICY_ANALYSIS_CONTRACT_INVALID",
    )
    require(
        type(manifest.get("children")) is list and 3 <= len(manifest["children"]) <= 28,
        "POLICY_ANALYSIS_CHILDREN_INVALID",
    )
    for child in manifest["children"]:
        require(
            child["role"]
            in {"ACTUAL_INPUT", "SEARCH", "SELECTION", "EVALUATION", "EVALUATION_SOURCE"}
            and child["artifact_reference"]
            == f"{root}:{child['role'].lower()}:{child['content_hash']}",
            "POLICY_ANALYSIS_CHILD_REFERENCE_MISMATCH",
        )
    require(
        manifest["content_hash"] == pointer["policy_analysis_content_hash"],
        "POLICY_ANALYSIS_POINTER_MISMATCH",
    )
    docs = {
        child["artifact_reference"]: repository.read_artifact(
            child["artifact_reference"]
        ).document()
        for child in manifest["children"]
    }
    by_role = {
        role: [docs[c["artifact_reference"]] for c in manifest["children"] if c["role"] == role]
        for role in ("ACTUAL_INPUT", "SEARCH", "SELECTION", "EVALUATION", "EVALUATION_SOURCE")
    }
    require(
        len(by_role["ACTUAL_INPUT"]) == len(by_role["SEARCH"]) == len(by_role["SELECTION"]) == 1
        and len(by_role["EVALUATION"]) <= 1,
        "POLICY_ANALYSIS_CHILDREN_INVALID",
    )
    search = by_role["SEARCH"][0]
    source = by_role["ACTUAL_INPUT"][0]
    sources = {d["content_hash"]: d for d in [search, *by_role["EVALUATION_SOURCE"]]}
    expected = build_policy_analysis(
        deployment=deployment,
        request=request,
        bundle=bundle,
        mathematical_request=MathematicalPolicyRequest.from_dict(source["mathematical_request"]),
        inputs=PolicyAnalysisInputs(
            search,
            source["config_values"],
            source["classification_snapshot"],
            by_role["EVALUATION"][0] if by_role["EVALUATION"] else None,
            sources if by_role["EVALUATION"] else {},
        ),
    )
    require(expected.manifest == manifest, "POLICY_ANALYSIS_MANIFEST_REPLAY_MISMATCH")
    for artifact in expected.artifacts:
        require(
            repository.read_artifact(artifact.reference) == artifact,
            "POLICY_ANALYSIS_ARTIFACT_REPLAY_MISMATCH",
        )
    return expected

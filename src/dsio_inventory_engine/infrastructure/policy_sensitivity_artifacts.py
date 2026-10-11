"""Persist experiment separately; replay against all immutable simulation parents."""

import copy
from pathlib import Path

from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.policy_sensitivity import (
    PolicySensitivityRequest,
    verify_policy_sensitivity_result,
)
from dsio_inventory_engine.inventory_contracts.values import canonical_json, require
from dsio_inventory_engine.recommend_replenishment.application.policy_sensitivity import (
    RunPolicySensitivityUseCase,
)
from .policy_search_artifacts import _LocalPolicyArtifactStore, LocalPolicySearchArtifactStore


class LocalPolicySensitivityArtifactStore(_LocalPolicyArtifactStore):
    artifact_prefix = "PSENS-"

    def __init__(self, root: Path, deployment: DeploymentScope):
        super().__init__(root)
        self.deployment = deployment

    def verify(self, value: dict) -> dict:
        verify_policy_sensitivity_result(value)
        data = copy.deepcopy(value["evaluation_spec"])
        source_store = LocalPolicySearchArtifactStore(self.root)
        for case in data["cases"]:
            content_hash = case.pop("search_content_hash")
            require(
                case.pop("search_result_reference") == "PSR-" + content_hash,
                "POLICY_SENSITIVITY_SOURCE_REFERENCE_MISMATCH",
            )
            case["search_result"] = source_store.read(content_hash)
        expected = RunPolicySensitivityUseCase(self.deployment).execute(
            PolicySensitivityRequest.from_dict(data)
        )
        require(
            canonical_json(expected) == canonical_json(value), "POLICY_SENSITIVITY_REPLAY_MISMATCH"
        )
        return value

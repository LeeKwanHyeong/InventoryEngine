"""Append-only selection artifact; its search source must exist and replay exactly."""

from pathlib import Path

from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.policy_selection import (
    verify_policy_selection_result,
)
from dsio_inventory_engine.inventory_contracts.values import canonical_json, require
from dsio_inventory_engine.recommend_replenishment.application.policy_selection import (
    RunPolicySelectionUseCase,
)
from .policy_search_artifacts import _LocalPolicyArtifactStore, LocalPolicySearchArtifactStore


class LocalPolicySelectionArtifactStore(_LocalPolicyArtifactStore):
    artifact_prefix = "PSEL-"

    def __init__(self, root: Path, deployment: DeploymentScope):
        super().__init__(root)
        self.deployment = deployment

    def verify(self, value: dict) -> dict:
        verify_policy_selection_result(value)
        source = LocalPolicySearchArtifactStore(self.root).read(value["search_result_content_hash"])
        expected = RunPolicySelectionUseCase(self.deployment).execute(source)
        require(
            canonical_json(value) == canonical_json(expected), "POLICY_SELECTION_REPLAY_MISMATCH"
        )
        return value

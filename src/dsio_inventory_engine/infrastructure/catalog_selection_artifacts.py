"""Append-only local Catalog PSI/selection evidence; exact external inputs are required on recovery."""

import copy
from pathlib import Path

from dsio_inventory_engine.classify_inventory.candidate_catalog import OfflineCandidateCatalog
from dsio_inventory_engine.inventory_contracts.catalog_selection import (
    RESULT_PREFIX,
    SOURCE_PREFIX,
    verify_catalog_selection_result,
)
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.recommend_replenishment.application.catalog_selection import (
    RunCatalogPolicySelectionUseCase,
)
from .policy_search_artifacts import _LocalPolicyArtifactStore


class LocalCatalogPsiArtifactStore(_LocalPolicyArtifactStore):
    artifact_prefix = SOURCE_PREFIX

    def __init__(
        self,
        root: Path,
        selector: RunCatalogPolicySelectionUseCase,
        request: MathematicalPolicyRequest,
        *,
        catalog: OfflineCandidateCatalog,
        expected_catalog_hash: str,
        config_values: dict,
        classification_snapshot: dict,
    ):
        super().__init__(root)
        self.selector = selector
        self.request = request
        self._bindings = {
            "catalog": catalog,
            "expected_catalog_hash": expected_catalog_hash,
            "config_values": copy.deepcopy(config_values),
            "classification_snapshot": copy.deepcopy(classification_snapshot),
        }

    def verify(self, value: dict) -> dict:
        self.selector.verify_source(
            value, self.request, expected_source_hash=value.get("content_hash"), **self._bindings
        )
        return value


class LocalCatalogPolicySelectionArtifactStore(_LocalPolicyArtifactStore):
    artifact_prefix = RESULT_PREFIX

    def __init__(self, root: Path, source_store: LocalCatalogPsiArtifactStore):
        super().__init__(root)
        self.source_store = source_store

    def verify(self, value: dict) -> dict:
        verify_catalog_selection_result(value)
        source = self.source_store.read(value["source_result_content_hash"])
        self.source_store.selector.verify_result(
            value,
            source,
            self.source_store.request,
            expected_selection_hash=value["content_hash"],
            expected_source_hash=source["content_hash"],
            **self.source_store._bindings,
        )
        return value

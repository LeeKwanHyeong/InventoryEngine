"""Explicit Offline entrypoint; no new Runtime, source lookup, writes or strategy injection."""

from decimal import localcontext

from dsio_inventory_engine.classify_inventory.candidate_catalog import (
    CandidateCatalogProducer,
    OfflineCandidateCatalog,
    VerifiedCandidateCatalog,
)
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.values import digest, require
from dsio_inventory_engine.recommend_replenishment.application.policy_search import (
    NUMBERS,
    RunPolicySearchUseCase,
)


class RunCatalogPolicySearchUseCase:
    def __init__(
        self, deployment: DeploymentScope, producer: CandidateCatalogProducer, *, numerics=None
    ):
        self.deployment = deployment
        self.producer = producer
        self.numerics = numerics

    def execute(
        self,
        request: MathematicalPolicyRequest,
        *,
        catalog: OfflineCandidateCatalog,
        expected_catalog_hash: str,
        config_values: dict,
        classification_snapshot: dict,
    ) -> dict:
        require(self.deployment.environment == "DEVELOPMENT", "CATALOG_DEVELOPMENT_ONLY")
        verified = VerifiedCandidateCatalog.bind(
            catalog, self.producer, expected_catalog_hash=expected_catalog_hash
        )
        with localcontext(NUMBERS):
            return RunPolicySearchUseCase(self.deployment, numerics=self.numerics)._execute(
                request,
                config_values=config_values,
                classification_snapshot=classification_snapshot,
                candidate_catalog=verified,
            )

    def verify_result(
        self,
        result: dict,
        request: MathematicalPolicyRequest,
        *,
        expected_result_hash: str,
        catalog: OfflineCandidateCatalog,
        expected_catalog_hash: str,
        config_values: dict,
        classification_snapshot: dict,
    ) -> None:
        require(
            result.get("content_hash") == expected_result_hash
            and digest({k: v for k, v in result.items() if k != "content_hash"})
            == expected_result_hash,
            "CATALOG_PSI_HASH_MISMATCH",
        )
        rebuilt = self.execute(
            request,
            catalog=catalog,
            expected_catalog_hash=expected_catalog_hash,
            config_values=config_values,
            classification_snapshot=classification_snapshot,
        )
        require(rebuilt["content_hash"] == expected_result_hash, "CATALOG_PSI_REPLAY_MISMATCH")

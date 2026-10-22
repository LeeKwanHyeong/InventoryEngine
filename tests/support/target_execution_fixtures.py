"""Trusted local wiring for tests; no numerical expectations or approvals."""

from dsio_inventory_engine.inventory_contracts.calculation_boundary import (
    CalculationBoundaryBinding,
)
from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.replenishment import RecommendationRequest
from dsio_inventory_engine.prepare_inventory.application.inventory_input import (
    PrepareInventoryInputUseCase,
)
from tests.support.calculation_adapter_fixtures import DEPLOYMENT


def boundary(recommendation, build_hash="a" * 64):
    rec = RecommendationRequest.from_dict(
        recommendation.to_dict() if hasattr(recommendation, "to_dict") else recommendation
    )
    canonical = CanonicalInputRequest.from_dict(rec.to_dict()["canonical_input"])
    prepared = PrepareInventoryInputUseCase(DEPLOYMENT).execute(canonical)
    return CalculationBoundaryBinding.from_sources(
        canonical,
        prepared_content_hash=prepared.to_dict()["manifest"]["prepared_content_hash"],
        psi_simulator={
            "implementation_id": "inventory.psi.target_guarded.csharp",
            "version": "1.0.0",
            "implementation_content_hash": build_hash,
        },
        calculation_backend="CSHARP_PYTHONNET",
        semantics_profile="TARGET_GUARDED",
        psi_scenario_type="RECOMMENDED",
        execution=rec.to_dict()["execution"],
    )

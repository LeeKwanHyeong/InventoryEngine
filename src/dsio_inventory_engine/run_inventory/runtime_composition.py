"""Development composition root for approved Source → PSI/analysis/cost → durable evidence."""

from dsio_inventory_engine.inventory_contracts.values import require
from dsio_inventory_engine.inventory_evidence.application import PersistInventoryResultBundleUseCase
from .approved_inputs import ApprovedInputCommandResolver, ApprovedInputLandedCostResolver
from .psi_orchestrator import RunPsiBundleUseCase
from .result_pipeline import InventoryResultPipelineHandler
from .runtime_execution import InventoryRuntimeWorker


def build_approved_inventory_runtime_pipeline(
    *,
    deployment,
    input_provider,
    implementations,
    evidence_repository,
    cost_projection=None,
    simulation_registry=None,
):
    require(deployment.environment == "DEVELOPMENT", "RUNTIME_APPROVED_INPUT_DEVELOPMENT_ONLY")
    return InventoryResultPipelineHandler(
        command_resolver=ApprovedInputCommandResolver(
            provider=input_provider,
            implementations=implementations,
            deployment=deployment,
            simulation_registry=simulation_registry,
        ),
        orchestrator=RunPsiBundleUseCase(deployment),
        persistence=PersistInventoryResultBundleUseCase(deployment, evidence_repository),
        evidence_repository=evidence_repository,
        landed_cost_resolver=(
            None
            if cost_projection is None
            else ApprovedInputLandedCostResolver(
                provider=input_provider, projection=cost_projection
            )
        ),
    )


def build_approved_inventory_runtime_worker(*, platform, **kwargs):
    pipeline = build_approved_inventory_runtime_pipeline(**kwargs)
    return InventoryRuntimeWorker(
        handler=pipeline,
        platform=platform,
        publication_acknowledger=pipeline,
        run_result_verifier=pipeline,
    )

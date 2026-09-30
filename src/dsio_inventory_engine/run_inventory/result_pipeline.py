"""Offline-capable Runtime Handler for PSI execution and durable evidence."""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Protocol

from dsio_inventory_engine.inventory_contracts.runtime import InventoryRuntimeExecutionRequest
from dsio_inventory_engine.inventory_contracts.values import require
from dsio_inventory_engine.inventory_evidence.application import (
    PersistInventoryResultBundleUseCase,
    ReadAndVerifyInventoryResultBundleUseCase,
    VerifiedInventoryResultBundle,
)
from dsio_inventory_engine.inventory_evidence.contracts import InventoryEvidenceUnitOfWork

from .psi_orchestrator import PsiRunCommand, RunPsiBundleUseCase
from .landed_cost_pipeline import RunLandedCostResolver, build_run_cost_evidence
from .runtime_execution import (
    InventoryRuntimeResult,
    InventoryRuntimeStageEventReceipt,
)


class InventoryPsiRunCommandResolver(Protocol):
    """Resolve already approved implementations and inputs for one claimed Attempt."""

    async def resolve(self, request: InventoryRuntimeExecutionRequest) -> PsiRunCommand: ...


class InventoryResultPipelineHandler:
    """Claim-bound Prepare → PSI → Artifact → durable Outbox pipeline."""

    def __init__(
        self,
        *,
        command_resolver: InventoryPsiRunCommandResolver,
        orchestrator: RunPsiBundleUseCase,
        persistence: PersistInventoryResultBundleUseCase,
        evidence_repository: InventoryEvidenceUnitOfWork,
        landed_cost_resolver: RunLandedCostResolver | None = None,
    ) -> None:
        self.command_resolver = command_resolver
        self.orchestrator = orchestrator
        self.persistence = persistence
        self.evidence_repository = evidence_repository
        self.landed_cost_resolver = landed_cost_resolver
        self.verifier = ReadAndVerifyInventoryResultBundleUseCase(
            orchestrator.deployment,
            evidence_repository,
        )

    async def execute(
        self,
        request: InventoryRuntimeExecutionRequest,
        report: Callable[..., Awaitable[InventoryRuntimeStageEventReceipt]],
    ) -> InventoryRuntimeResult:
        existing_outbox = await asyncio.to_thread(
            self.evidence_repository.find_outbox,
            request.engine_run_id,
            request.value["attempt_no"],
        )
        await report(
            event_type="inventory.input",
            stage="input_resolution",
            progress_percent=10,
            message_code="INVENTORY_INPUT_RESOLUTION_STARTED",
            payload_redacted={
                "strategy_plan_bound": request.strategy_execution_plan is not None,
                "sealed_result_found": existing_outbox is not None,
            },
        )
        if existing_outbox is not None:
            payload = existing_outbox.message.payload()
            verified = await asyncio.to_thread(
                self.verifier.execute,
                payload["bundle_reference"],
                strategy_execution_plan=request.strategy_execution_plan,
                runtime_request=request,
            )
            await report(
                event_type="inventory.evidence",
                stage="artifact_recovery",
                progress_percent=90,
                message_code="INVENTORY_RESULT_ARTIFACTS_RECOVERED",
                payload_redacted={
                    "artifact_count": verified.artifact_count,
                    "exact_replay": True,
                    "outbox_status": existing_outbox.status,
                },
            )
            return _runtime_result(verified, existing_outbox)
        command = await self.command_resolver.resolve(request)
        require(isinstance(command, PsiRunCommand), "PSI_RUN_COMMAND_INVALID")
        resolved_runtime = command.runtime_request
        require(
            resolved_runtime.engine_run_id == request.engine_run_id
            and resolved_runtime.canonical_hash == request.canonical_hash,
            "RUNTIME_COMMAND_CLAIM_MISMATCH",
        )
        resolved_cost = None
        if request.strategy_execution_plan.get("landed_cost_binding") is not None:
            require(self.landed_cost_resolver is not None, "RUN_COST_RESOLVER_REQUIRED")
            resolved_cost = await self.landed_cost_resolver.resolve(request)
        await report(
            event_type="inventory.psi",
            stage="psi_execution",
            progress_percent=25,
            message_code="INVENTORY_PSI_EXECUTION_STARTED",
            payload_redacted={"attempt_no": request.value["attempt_no"]},
        )
        run = await asyncio.to_thread(self.orchestrator.execute, command)
        await report(
            event_type="inventory.psi",
            stage="psi_execution",
            progress_percent=75,
            message_code="INVENTORY_PSI_EXECUTION_COMPLETED",
            payload_redacted={
                "child_count": len(run.result_bundle["result_children"]),
                "effective_strategy": "MATHEMATICAL",
            },
        )
        cost_evidence = None
        if resolved_cost is not None:
            cost_evidence = await asyncio.to_thread(
                build_run_cost_evidence,
                request=request,
                run=run,
                resolved=resolved_cost,
                cost_profile=command.cost_profile,
            )
        persisted = await asyncio.to_thread(
            self.persistence.execute,
            run,
            strategy_execution_plan=request.strategy_execution_plan,
            runtime_request=request,
            run_cost_evidence=cost_evidence,
        )
        await report(
            event_type="inventory.evidence",
            stage="artifact_persistence",
            progress_percent=90,
            message_code="INVENTORY_RESULT_ARTIFACTS_VERIFIED",
            payload_redacted={
                "artifact_count": persisted.artifact_count,
                "exact_replay": persisted.replayed,
                "outbox_staged": persisted.outbox.status == "PENDING",
            },
        )
        return _runtime_result(
            VerifiedInventoryResultBundle(
                bundle_reference=persisted.bundle_reference,
                result_bundle=run.result_bundle,
                canonical_input=run.canonical_input,
                prepared_input=run.prepared_input,
                outbox=persisted.outbox,
                artifact_count=persisted.artifact_count,
                run_cost_evidence=cost_evidence,
            ),
            persisted.outbox,
        )

    async def acknowledge_publication(self, result: InventoryRuntimeResult) -> None:
        require(result.publication_outbox_id is not None, "RUNTIME_RESULT_OUTBOX_REQUIRED")
        await asyncio.to_thread(
            self.evidence_repository.mark_published,
            result.publication_outbox_id,
            expected_payload_content_hash=result.publication_outbox_content_hash,
        )

    async def verify_runtime_result(self, request, result: InventoryRuntimeResult) -> None:
        verified = await asyncio.to_thread(
            self.verifier.execute,
            f"artifact:inventory:{request.engine_run_id}:{request.value['attempt_no']}:bundle",
            strategy_execution_plan=request.strategy_execution_plan,
            runtime_request=request,
        )
        expected = _runtime_result(verified, verified.outbox)
        require(
            result.inventory_result_content_hash == expected.inventory_result_content_hash
            and result.run_result_manifest_reference == expected.run_result_manifest_reference
            and result.run_result_manifest_content_hash == expected.run_result_manifest_content_hash
            and result.run_result_manifest == expected.run_result_manifest,
            "RUNTIME_RUN_RESULT_MANIFEST_POINTER_MISMATCH",
        )


def _runtime_result(verified, outbox) -> InventoryRuntimeResult:
    bundle = verified.result_bundle
    manifest = None if verified.run_cost_evidence is None else verified.run_cost_evidence.manifest
    return InventoryRuntimeResult(
        inventory_result_snapshot_id=bundle["result_bundle_id"],
        inventory_result_content_hash=bundle["content_hash"],
        automatic_publish_allowed=bundle["automatic_publish_allowed"],
        automatic_order_allowed=bundle["automatic_order_allowed"],
        effective_policy_content_hash=bundle["effective_policy_content_hash"],
        inventory_result_contract_key=bundle["source_contract_key"],
        inventory_result_contract_version=bundle["contract_version"],
        canonical_input=verified.canonical_input,
        inventory_result_bundle=bundle,
        publication_outbox_id=outbox.message.outbox_id,
        publication_outbox_content_hash=outbox.message.payload_content_hash,
        run_result_manifest=manifest,
        run_result_manifest_reference=None if manifest is None else manifest["artifact_reference"],
        run_result_manifest_content_hash=None if manifest is None else manifest["content_hash"],
    )


__all__ = ["InventoryPsiRunCommandResolver", "InventoryResultPipelineHandler"]

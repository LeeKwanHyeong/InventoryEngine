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
    ) -> None:
        self.command_resolver = command_resolver
        self.orchestrator = orchestrator
        self.persistence = persistence
        self.evidence_repository = evidence_repository
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
        persisted = await asyncio.to_thread(
            self.persistence.execute,
            run,
            strategy_execution_plan=request.strategy_execution_plan,
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


def _runtime_result(verified, outbox) -> InventoryRuntimeResult:
    bundle = verified.result_bundle
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
    )


__all__ = ["InventoryPsiRunCommandResolver", "InventoryResultPipelineHandler"]

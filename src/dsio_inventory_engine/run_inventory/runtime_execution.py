"""Runtime admission and worker orchestration for one Platform-claimed Attempt."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Protocol

from dsio_inventory_engine.inventory_contracts.runtime import (
    InventoryRuntimeExecutionReceipt,
    InventoryRuntimeExecutionRequest,
)
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    hash_value,
    identifier,
    require,
)


@dataclass(frozen=True, slots=True)
class InventoryRuntimeResult:
    inventory_result_snapshot_id: str
    inventory_result_content_hash: str

    def __post_init__(self) -> None:
        identifier(self.inventory_result_snapshot_id)
        hash_value(self.inventory_result_content_hash)


@dataclass(frozen=True, slots=True)
class InventoryRuntimeStageEvent:
    event_type: str
    stage: str
    status: str
    progress_percent: int
    message_code: str
    payload_redacted: Mapping[str, str | int | bool | None]

    def __post_init__(self) -> None:
        require(
            re.fullmatch(r"[a-z][a-z0-9_.-]{0,127}", self.event_type) is not None,
            "RUNTIME_EVENT_TYPE_INVALID",
        )
        require(
            re.fullmatch(r"[a-z][a-z0-9_.-]{0,127}", self.stage) is not None,
            "RUNTIME_EVENT_STAGE_INVALID",
        )
        require(
            self.status in {"running", "succeeded", "failed"},
            "RUNTIME_EVENT_STATUS_INVALID",
        )
        require(
            type(self.progress_percent) is int
            and 0 <= self.progress_percent <= 100,
            "RUNTIME_EVENT_PROGRESS_INVALID",
        )
        require(
            re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", self.message_code) is not None,
            "RUNTIME_EVENT_MESSAGE_INVALID",
        )
        require(
            len(self.payload_redacted) <= 32
            and all(
                isinstance(key, str)
                and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) is not None
                and (
                    value is None
                    or type(value) in {str, int, bool}
                )
                for key, value in self.payload_redacted.items()
            ),
            "RUNTIME_EVENT_PAYLOAD_INVALID",
        )


@dataclass(frozen=True, slots=True)
class InventoryRuntimeStageEventReceipt:
    site_row_version: int
    run_status: str
    replayed: bool

    def __post_init__(self) -> None:
        require(
            type(self.site_row_version) is int and self.site_row_version > 0,
            "PLATFORM_EVENT_RECEIPT_INVALID",
        )
        require(
            self.run_status
            in {"created", "running", "succeeded", "failed", "canceled"},
            "PLATFORM_EVENT_RECEIPT_INVALID",
        )
        require(type(self.replayed) is bool, "PLATFORM_EVENT_RECEIPT_INVALID")


class InventoryRuntimeSubmissionPort(Protocol):
    async def submit(
        self,
        request: InventoryRuntimeExecutionRequest,
    ) -> Mapping[str, Any]: ...


class InventoryRuntimeSubmissionConflict(InventoryInputError):
    pass


class InventoryRuntimeSubmissionUnavailable(InventoryInputError):
    pass


class InventoryPlatformLifecyclePort(Protocol):
    async def append_event(
        self,
        request: InventoryRuntimeExecutionRequest,
        event: InventoryRuntimeStageEvent,
    ) -> InventoryRuntimeStageEventReceipt: ...

    async def publish(
        self,
        request: InventoryRuntimeExecutionRequest,
        *,
        expected_site_row_version: int,
        result: InventoryRuntimeResult,
    ) -> Mapping[str, Any]: ...


class InventoryRuntimeExecutionHandler(Protocol):
    async def execute(
        self,
        request: InventoryRuntimeExecutionRequest,
        report: Callable[..., Awaitable[InventoryRuntimeStageEventReceipt]],
    ) -> InventoryRuntimeResult: ...


class InventoryRuntimeExecutionUseCase:
    """Validate the closed request before submitting it to a durable worker port."""

    def __init__(self, submission: InventoryRuntimeSubmissionPort) -> None:
        self._submission = submission

    async def accept(self, document: str) -> InventoryRuntimeExecutionReceipt:
        request = InventoryRuntimeExecutionRequest.from_json(document)
        receipt = InventoryRuntimeExecutionReceipt.from_dict(
            await self._submission.submit(request)
        )
        require(
            receipt.engine_run_id == request.engine_run_id,
            "RUNTIME_SUBMISSION_ID_MISMATCH",
        )
        return receipt


class InventoryRuntimeWorker:
    """Run stages and return all lifecycle mutations through Platform callbacks."""

    def __init__(
        self,
        *,
        handler: InventoryRuntimeExecutionHandler,
        platform: InventoryPlatformLifecyclePort,
    ) -> None:
        self._handler = handler
        self._platform = platform

    async def execute(
        self,
        request: InventoryRuntimeExecutionRequest,
    ) -> Mapping[str, Any]:
        row_version = request.site_row_version

        async def report(
            *,
            event_type: str,
            stage: str,
            progress_percent: int,
            message_code: str,
            payload_redacted: Mapping[str, str | int | bool | None] | None = None,
        ) -> InventoryRuntimeStageEventReceipt:
            nonlocal row_version
            receipt = await self._platform.append_event(
                request,
                InventoryRuntimeStageEvent(
                    event_type=event_type,
                    stage=stage,
                    status="running",
                    progress_percent=progress_percent,
                    message_code=message_code,
                    payload_redacted=payload_redacted or {},
                ),
            )
            row_version = receipt.site_row_version
            return receipt

        await report(
            event_type="inventory.run",
            stage="admission",
            progress_percent=0,
            message_code="INVENTORY_RUNTIME_ACCEPTED",
            payload_redacted={"input_binding_count": len(request.value["claim"]["input_bindings"])},
        )
        try:
            result = await self._handler.execute(request, report)
        except Exception as exc:
            error_code = _stable_error_code(exc)
            receipt = await self._platform.append_event(
                request,
                InventoryRuntimeStageEvent(
                    event_type="inventory.run",
                    stage="execution",
                    status="failed",
                    progress_percent=100,
                    message_code=error_code,
                    payload_redacted={"result_published": False},
                ),
            )
            row_version = receipt.site_row_version
            raise

        terminal = await self._platform.append_event(
            request,
            InventoryRuntimeStageEvent(
                event_type="inventory.run",
                stage="publication",
                status="succeeded",
                progress_percent=100,
                message_code="INVENTORY_RESULT_SEALED",
                payload_redacted={
                    "inventory_result_snapshot_id": result.inventory_result_snapshot_id,
                    "inventory_result_content_hash": result.inventory_result_content_hash,
                },
            ),
        )
        row_version = terminal.site_row_version
        publication = await self._platform.publish(
            request,
            expected_site_row_version=row_version,
            result=result,
        )
        require(
            str(publication.get("engine_run_id")) == request.engine_run_id
            and str(publication.get("effective_run_id")) == request.engine_run_id
            and publication.get("site_status") == "succeeded",
            "PLATFORM_PUBLICATION_RECEIPT_INVALID",
        )
        return {
            "engine_run_id": request.engine_run_id,
            "status": "succeeded",
            "inventory_result_snapshot_id": result.inventory_result_snapshot_id,
            "inventory_result_content_hash": result.inventory_result_content_hash,
            "site_row_version": int(publication["site_row_version"]),
            "cycle_status": str(publication["cycle_status"]),
            "replayed": bool(publication["replayed"]),
        }


def _stable_error_code(exc: Exception) -> str:
    if isinstance(exc, InventoryInputError):
        code = str(exc)
        if re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", code) is not None:
            return code
    return "INVENTORY_RUNTIME_EXECUTION_FAILED"


__all__ = [
    "InventoryPlatformLifecyclePort",
    "InventoryRuntimeExecutionHandler",
    "InventoryRuntimeExecutionUseCase",
    "InventoryRuntimeResult",
    "InventoryRuntimeStageEvent",
    "InventoryRuntimeStageEventReceipt",
    "InventoryRuntimeSubmissionPort",
    "InventoryRuntimeSubmissionConflict",
    "InventoryRuntimeSubmissionUnavailable",
    "InventoryRuntimeWorker",
]

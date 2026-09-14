"""Runtime admission and worker orchestration for one Platform-claimed Attempt."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Protocol

from dsio_inventory_engine.inventory_contracts.canonical import (
    CanonicalInputRequest,
    snapshot_content,
)
from dsio_inventory_engine.inventory_contracts.runtime import (
    InventoryRuntimeExecutionReceipt,
    InventoryRuntimeExecutionRequest,
    validate_canonical_runtime_binding,
    validate_result_bundle_runtime_binding,
)
from dsio_inventory_engine.inventory_contracts.result_bundle import (
    RESULT_BUNDLE_CONTRACT_VERSION,
    RESULT_BUNDLE_SOURCE_CONTRACT_KEY,
)
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    digest,
    hash_value,
    identifier,
    require,
)


@dataclass(frozen=True, slots=True)
class InventoryRuntimeResult:
    inventory_result_snapshot_id: str
    inventory_result_content_hash: str
    automatic_publish_allowed: bool | None = None
    automatic_order_allowed: bool | None = None
    effective_policy_content_hash: str | None = None
    inventory_result_contract_key: str | None = None
    inventory_result_contract_version: str | None = None
    canonical_input: CanonicalInputRequest | None = None
    inventory_result_bundle: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        identifier(self.inventory_result_snapshot_id)
        hash_value(self.inventory_result_content_hash)
        require(
            (self.automatic_publish_allowed is None) == (self.automatic_order_allowed is None)
            and (
                self.automatic_publish_allowed is None
                or (
                    type(self.automatic_publish_allowed) is bool
                    and type(self.automatic_order_allowed) is bool
                )
            ),
            "RUNTIME_RESULT_POLICY_GATE_INVALID",
        )
        require(
            self.automatic_publish_allowed is None
            or self.automatic_publish_allowed
            or not self.automatic_order_allowed,
            "RUNTIME_RESULT_POLICY_GATE_INVALID",
        )
        if self.effective_policy_content_hash is not None:
            hash_value(self.effective_policy_content_hash)
        require(
            (self.inventory_result_contract_key is None)
            == (self.inventory_result_contract_version is None),
            "RUNTIME_RESULT_CONTRACT_BINDING_INCOMPLETE",
        )
        if self.inventory_result_contract_key is not None:
            require(
                self.inventory_result_contract_key == RESULT_BUNDLE_SOURCE_CONTRACT_KEY
                and self.inventory_result_contract_version == RESULT_BUNDLE_CONTRACT_VERSION,
                "RUNTIME_RESULT_CONTRACT_BINDING_INVALID",
            )
        require(
            (self.canonical_input is None) == (self.inventory_result_bundle is None),
            "RUNTIME_RESULT_BUNDLE_PAYLOAD_INCOMPLETE",
        )
        if self.canonical_input is not None:
            require(
                isinstance(self.canonical_input, CanonicalInputRequest),
                "RUNTIME_RESULT_CANONICAL_INPUT_INVALID",
            )
            require(
                isinstance(self.inventory_result_bundle, Mapping),
                "RUNTIME_RESULT_BUNDLE_PAYLOAD_INVALID",
            )


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
            type(self.progress_percent) is int and 0 <= self.progress_percent <= 100,
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
                and (value is None or type(value) in {str, int, bool})
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
            self.run_status in {"created", "running", "succeeded", "failed", "canceled"},
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
        receipt = InventoryRuntimeExecutionReceipt.from_dict(await self._submission.submit(request))
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
            automatic_publish_allowed, automatic_order_allowed = _validate_result_policy_binding(
                request, result
            )
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

        sealed_result_payload: dict[str, str | bool | None] = {
            "inventory_result_snapshot_id": result.inventory_result_snapshot_id,
            "inventory_result_content_hash": result.inventory_result_content_hash,
            "automatic_publish_allowed": automatic_publish_allowed,
            "automatic_order_allowed": automatic_order_allowed,
            "effective_policy_content_hash": result.effective_policy_content_hash,
        }
        if result.inventory_result_contract_key is not None:
            sealed_result_payload.update(
                inventory_result_contract_key=result.inventory_result_contract_key,
                inventory_result_contract_version=result.inventory_result_contract_version,
            )
        terminal = await self._platform.append_event(
            request,
            InventoryRuntimeStageEvent(
                event_type="inventory.run",
                stage="publication",
                status=("succeeded" if automatic_publish_allowed else "running"),
                progress_percent=100,
                message_code=(
                    "INVENTORY_RESULT_SEALED"
                    if automatic_publish_allowed
                    else "INVENTORY_RESULT_REVIEW_REQUIRED"
                ),
                payload_redacted=sealed_result_payload,
            ),
        )
        row_version = terminal.site_row_version
        if not automatic_publish_allowed:
            review_result = {
                "engine_run_id": request.engine_run_id,
                "status": "review_required",
                "publication_status": "withheld_for_review",
                "inventory_result_snapshot_id": result.inventory_result_snapshot_id,
                "inventory_result_content_hash": result.inventory_result_content_hash,
                "effective_policy_content_hash": result.effective_policy_content_hash,
                "automatic_publish_allowed": False,
                "automatic_order_allowed": False,
                "site_row_version": row_version,
            }
            if result.inventory_result_contract_key is not None:
                review_result.update(
                    inventory_result_contract_key=result.inventory_result_contract_key,
                    inventory_result_contract_version=result.inventory_result_contract_version,
                )
            return review_result
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
        published_result = {
            "engine_run_id": request.engine_run_id,
            "status": "succeeded",
            "inventory_result_snapshot_id": result.inventory_result_snapshot_id,
            "inventory_result_content_hash": result.inventory_result_content_hash,
            "site_row_version": int(publication["site_row_version"]),
            "cycle_status": str(publication["cycle_status"]),
            "replayed": bool(publication["replayed"]),
        }
        if result.inventory_result_contract_key is not None:
            published_result.update(
                inventory_result_contract_key=result.inventory_result_contract_key,
                inventory_result_contract_version=result.inventory_result_contract_version,
            )
        return published_result


def _stable_error_code(exc: Exception) -> str:
    if isinstance(exc, InventoryInputError):
        code = str(exc)
        if re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", code) is not None:
            return code
    return "INVENTORY_RUNTIME_EXECUTION_FAILED"


def _validate_result_canonical_input(
    request: InventoryRuntimeExecutionRequest,
    canonical_input: CanonicalInputRequest,
) -> CanonicalInputRequest:
    """Re-admit the Handler's actual input and independently derive its hash."""

    canonical = CanonicalInputRequest.from_dict(canonical_input.to_dict())
    data = canonical.to_dict()
    for kind, snapshot in data["snapshots"].items():
        require(snapshot["status"] == "SEALED", "UNSEALED_INPUT")
        require(
            snapshot["row_count"] == len(snapshot["rows"]),
            "SNAPSHOT_ROW_COUNT_MISMATCH",
        )
        require(
            snapshot["content_hash"] == digest(snapshot_content(kind, snapshot)),
            "SNAPSHOT_HASH_MISMATCH",
        )
        require(
            data["input_bindings"][kind]
            == {
                "snapshot_id": snapshot["snapshot_id"],
                "content_hash": snapshot["content_hash"],
            },
            "PINNED_INPUT_MISMATCH",
        )
    validate_canonical_runtime_binding(
        request,
        canonical_input=canonical,
    )
    return canonical


def _validate_result_policy_binding(
    request: InventoryRuntimeExecutionRequest,
    result: InventoryRuntimeResult,
) -> tuple[bool, bool]:
    classification = request.classification_binding
    if classification is None:
        require(
            result.effective_policy_content_hash is None
            and result.inventory_result_contract_key is None
            and result.inventory_result_contract_version is None
            and result.canonical_input is None
            and result.inventory_result_bundle is None
            and (
                (
                    result.automatic_publish_allowed is None
                    and result.automatic_order_allowed is None
                )
                or (
                    result.automatic_publish_allowed is True
                    and result.automatic_order_allowed is True
                )
            ),
            "RUNTIME_EFFECTIVE_POLICY_BINDING_UNEXPECTED",
        )
        return True, True
    require(
        result.effective_policy_content_hash == classification["source_content_hash"],
        "RUNTIME_EFFECTIVE_POLICY_RESULT_HASH_MISMATCH",
    )
    require(
        result.inventory_result_contract_key == RESULT_BUNDLE_SOURCE_CONTRACT_KEY
        and result.inventory_result_contract_version == RESULT_BUNDLE_CONTRACT_VERSION,
        "RUNTIME_RESULT_BUNDLE_CONTRACT_REQUIRED",
    )
    expected = (
        request.value["claim"]["expected_automatic_publish_allowed"],
        request.value["claim"]["expected_automatic_order_allowed"],
    )
    actual = (
        result.automatic_publish_allowed,
        result.automatic_order_allowed,
    )
    require(
        all(type(value) is bool for value in actual),
        "RUNTIME_EFFECTIVE_POLICY_RESULT_GATE_REQUIRED",
    )
    require(actual == expected, "RUNTIME_EFFECTIVE_POLICY_RESULT_GATE_MISMATCH")
    require(
        result.canonical_input is not None and result.inventory_result_bundle is not None,
        "RUNTIME_RESULT_BUNDLE_REQUIRED",
    )
    canonical = _validate_result_canonical_input(request, result.canonical_input)
    bundle = validate_result_bundle_runtime_binding(
        request,
        result.inventory_result_bundle,
        expected_canonical_input_hash=canonical.input_hash,
    )
    require(
        result.inventory_result_snapshot_id == bundle["result_bundle_id"]
        and result.inventory_result_content_hash == bundle["content_hash"],
        "RUNTIME_RESULT_BUNDLE_POINTER_MISMATCH",
    )
    return bool(actual[0]), bool(actual[1])


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

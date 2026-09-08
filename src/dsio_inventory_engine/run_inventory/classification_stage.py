"""Attach classification Snapshot publication to one claimed Inventory Run."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from dsio_inventory_engine.classify_inventory.application import (
    InventoryClassificationLifecycleUseCase,
    InventoryScope,
)
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    hash_value,
    identifier,
    require,
)


@dataclass(frozen=True, slots=True)
class InventoryRunContext:
    engine_run_id: str
    tenant_id: str
    project_id: str
    scope: InventoryScope

    def __post_init__(self) -> None:
        try:
            uuid.UUID(self.engine_run_id)
        except (TypeError, ValueError) as exc:
            raise InventoryInputError("INVENTORY_RUN_ID_INVALID") from exc
        identifier(self.tenant_id)
        identifier(self.project_id)
        require(
            self.project_id == self.scope.project_id,
            "INVENTORY_RUN_SCOPE_MISMATCH",
        )


@dataclass(frozen=True, slots=True)
class InventoryRunEvent:
    event_type: str
    stage: str
    status: str
    message_code: str
    progress_percent: int
    payload_redacted: Mapping[str, str | int | bool | None]


class InventoryRunEventRecorder(Protocol):
    async def append(
        self,
        context: InventoryRunContext,
        event: InventoryRunEvent,
    ) -> Mapping[str, Any]: ...


class InventoryClassificationStageUseCase:
    """Publish a classification Snapshot and append bounded stage evidence."""

    def __init__(
        self,
        lifecycle: InventoryClassificationLifecycleUseCase,
        event_recorder: InventoryRunEventRecorder,
    ) -> None:
        self.lifecycle = lifecycle
        self.event_recorder = event_recorder

    async def execute(
        self,
        context: InventoryRunContext,
        *,
        approved_by: str,
    ) -> dict[str, Any]:
        identifier(approved_by)
        await self.event_recorder.append(
            context,
            InventoryRunEvent(
                event_type="inventory.classification",
                stage="classification",
                status="running",
                message_code="INVENTORY_CLASSIFICATION_STARTED",
                progress_percent=0,
                payload_redacted={"scope_bound": True},
            ),
        )
        try:
            receipt = await self.lifecycle.execute(
                context.scope,
                approved_by=approved_by,
                publish=True,
            )
            _validate_publication_receipt(receipt)
        except Exception as exc:
            code = (
                str(exc)
                if isinstance(exc, InventoryInputError)
                else "INVENTORY_CLASSIFICATION_FAILED"
            )
            await self.event_recorder.append(
                context,
                InventoryRunEvent(
                    event_type="inventory.classification",
                    stage="classification",
                    status="failed",
                    message_code=code,
                    progress_percent=100,
                    payload_redacted={"snapshot_published": False},
                ),
            )
            raise

        await self.event_recorder.append(
            context,
            InventoryRunEvent(
                event_type="inventory.classification",
                stage="classification",
                status="running",
                message_code="INVENTORY_CLASSIFICATION_SNAPSHOT_PUBLISHED",
                progress_percent=100,
                payload_redacted={
                    "classification_snapshot_id": receipt["classification_snapshot_id"],
                    "snapshot_revision": receipt["snapshot_revision"],
                    "content_hash": receipt["content_hash"],
                    "item_result_contract_version": receipt[
                        "item_result_contract_version"
                    ],
                    "item_result_count": receipt["item_result_count"],
                    "eligible_sku_count": receipt["eligible_sku_count"],
                    "classified_sku_count": receipt["classified_sku_count"],
                    "unclassified_sku_count": receipt["unclassified_sku_count"],
                    "exact_replay": receipt["exact_replay"],
                },
            ),
        )
        return {
            **receipt,
            "engine_run_id": context.engine_run_id,
            "run_claimed": True,
            "stage_event_persisted": True,
        }


def _validate_publication_receipt(receipt: Mapping[str, Any]) -> None:
    require(
        receipt.get("publication_status") in {"published", "exact_replay"},
        "INVENTORY_CLASSIFICATION_SNAPSHOT_NOT_PUBLISHED",
    )
    require(bool(receipt.get("classification_snapshot_id")), "INVENTORY_SNAPSHOT_ID_MISSING")
    require(
        type(receipt.get("snapshot_revision")) is int and receipt["snapshot_revision"] > 0,
        "INVENTORY_SNAPSHOT_REVISION_MISSING",
    )
    hash_value(receipt.get("content_hash"))
    require(
        receipt.get("item_result_contract_version") == "1.0.0"
        and type(receipt.get("item_result_count")) is int
        and receipt["item_result_count"] == receipt.get("eligible_sku_count"),
        "INVENTORY_CLASSIFICATION_ITEM_RESULTS_INCOMPLETE",
    )
    for field in (
        "eligible_sku_count",
        "classified_sku_count",
        "unclassified_sku_count",
    ):
        require(
            type(receipt.get(field)) is int and receipt[field] >= 0,
            "INVENTORY_SNAPSHOT_COUNT_INVALID",
        )


__all__ = [
    "InventoryClassificationStageUseCase",
    "InventoryRunContext",
    "InventoryRunEvent",
    "InventoryRunEventRecorder",
]

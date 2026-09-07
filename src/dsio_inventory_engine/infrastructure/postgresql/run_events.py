"""PostgreSQL append-only Event adapter for a claimed Inventory Engine Run."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Mapping

from dsio_inventory_engine.inventory_contracts.values import require
from dsio_inventory_engine.run_inventory.classification_stage import (
    InventoryRunContext,
    InventoryRunEvent,
)


_LOCK_RUN_SQL = """
SELECT status_projection
FROM dsai.engine_runtime_runs
WHERE engine_run_id = $1::uuid
  AND tenant_id = $2
  AND project_id = $3
  AND engine_key = 'inventory'
FOR UPDATE
"""

_NEXT_EVENT_SEQUENCE_SQL = """
SELECT COALESCE(MAX(event_seq), 0) + 1
FROM dsai.engine_runtime_run_events
WHERE engine_run_id = $1::uuid
"""

_INSERT_EVENT_SQL = """
INSERT INTO dsai.engine_runtime_run_events (
    event_id, tenant_id, engine_run_id, event_seq, event_key_hash,
    event_type, stage, status, message_redacted, progress_percent,
    payload_redacted
) VALUES (
    $1::uuid, $2, $3::uuid, $4, $5,
    $6, $7, $8, $9, $10, $11::jsonb
)
ON CONFLICT (engine_run_id, event_key_hash) DO NOTHING
RETURNING event_id::text, event_seq
"""

_UPDATE_PROJECTION_SQL = """
UPDATE dsai.engine_runtime_runs
SET current_stage_projection = $4,
    updated_at = NOW()
WHERE engine_run_id = $1::uuid
  AND tenant_id = $2
  AND project_id = $3
  AND engine_key = 'inventory'
  AND status_projection = 'running'
"""


class PostgresInventoryRunEventRecorder:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    async def append(
        self,
        context: InventoryRunContext,
        event: InventoryRunEvent,
    ) -> Mapping[str, Any]:
        _validate_event(event)
        canonical = {
            "engine_run_id": context.engine_run_id,
            "event_type": event.event_type,
            "stage": event.stage,
            "status": event.status,
            "message_code": event.message_code,
            "progress_percent": event.progress_percent,
            "payload_redacted": dict(event.payload_redacted),
        }
        event_key_hash = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        async with self.connection.transaction():
            run = await self.connection.fetchrow(
                _LOCK_RUN_SQL,
                context.engine_run_id,
                context.tenant_id,
                context.project_id,
            )
            require(run is not None, "INVENTORY_RUN_NOT_FOUND")
            require(run["status_projection"] == "running", "INVENTORY_RUN_NOT_RUNNING")
            event_seq = await self.connection.fetchval(
                _NEXT_EVENT_SEQUENCE_SQL,
                context.engine_run_id,
            )
            inserted = await self.connection.fetchrow(
                _INSERT_EVENT_SQL,
                str(uuid.uuid4()),
                context.tenant_id,
                context.engine_run_id,
                event_seq,
                event_key_hash,
                event.event_type,
                event.stage,
                event.status,
                event.message_code,
                event.progress_percent,
                json.dumps(dict(event.payload_redacted), sort_keys=True, separators=(",", ":")),
            )
            if inserted is None:
                return {"publication_status": "exact_replay", "event_key_hash": event_key_hash}
            updated = await self.connection.execute(
                _UPDATE_PROJECTION_SQL,
                context.engine_run_id,
                context.tenant_id,
                context.project_id,
                event.stage,
            )
            require(updated == "UPDATE 1", "INVENTORY_RUN_PROJECTION_CONFLICT")
        return {
            "publication_status": "published",
            "event_id": inserted["event_id"],
            "event_seq": inserted["event_seq"],
            "event_key_hash": event_key_hash,
        }


def _validate_event(event: InventoryRunEvent) -> None:
    require(
        event.event_type == "inventory.classification" and event.stage == "classification",
        "INVENTORY_RUN_EVENT_IDENTITY_INVALID",
    )
    require(
        event.status in {"running", "succeeded", "failed"},
        "INVENTORY_RUN_EVENT_STATUS_INVALID",
    )
    require(0 <= event.progress_percent <= 100, "INVENTORY_RUN_EVENT_PROGRESS_INVALID")
    require(
        event.message_code.replace("_", "").isalnum()
        and event.message_code == event.message_code.upper(),
        "INVENTORY_RUN_EVENT_MESSAGE_INVALID",
    )
    allowed = {
        "scope_bound",
        "snapshot_published",
        "classification_snapshot_id",
        "snapshot_revision",
        "content_hash",
        "eligible_sku_count",
        "classified_sku_count",
        "unclassified_sku_count",
        "exact_replay",
    }
    require(set(event.payload_redacted) <= allowed, "INVENTORY_RUN_EVENT_PAYLOAD_INVALID")


__all__ = ["PostgresInventoryRunEventRecorder"]

"""Authenticated callbacks from InventoryEngine Runtime to Platform lifecycle APIs."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import urlparse

import httpx

from dsio_inventory_engine.inventory_contracts.runtime import (
    InventoryRuntimeExecutionRequest,
)
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    hash_value,
    require,
)
from dsio_inventory_engine.run_inventory.runtime_execution import (
    InventoryRuntimeResult,
    InventoryRuntimeStageEvent,
    InventoryRuntimeStageEventReceipt,
)


@dataclass(frozen=True, slots=True)
class PlatformLifecycleHttpSettings:
    base_url: str
    timeout_seconds: float = 10.0
    bearer_token: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "PlatformLifecycleHttpSettings":
        values = os.environ if environ is None else environ
        base_url = str(values.get("INVENTORY_PLATFORM_API_BASE_URL") or "").strip()
        require(bool(base_url), "PLATFORM_API_BASE_URL_REQUIRED")
        try:
            timeout = float(values.get("INVENTORY_PLATFORM_API_TIMEOUT_SECONDS") or "10")
        except (TypeError, ValueError):
            raise InventoryInputError("PLATFORM_API_TIMEOUT_INVALID") from None
        settings = cls(
            base_url=base_url.rstrip("/"),
            timeout_seconds=timeout,
            bearer_token=(
                str(values.get("INVENTORY_PLATFORM_API_BEARER_TOKEN") or "").strip() or None
            ),
        )
        settings.validate(environment=str(values.get("IO_ENVIRONMENT") or "development"))
        return settings

    def validate(self, *, environment: str) -> None:
        parsed = urlparse(self.base_url)
        require(
            parsed.scheme in {"http", "https"}
            and bool(parsed.netloc)
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment,
            "PLATFORM_API_BASE_URL_INVALID",
        )
        require(
            0 < self.timeout_seconds <= 120,
            "PLATFORM_API_TIMEOUT_INVALID",
        )
        if environment.strip().lower() in {"production", "prod"}:
            require(parsed.scheme == "https", "PLATFORM_API_HTTPS_REQUIRED")
            require(bool(self.bearer_token), "PLATFORM_API_AUTH_REQUIRED")


class PlatformInventoryLifecycleHttpClient:
    def __init__(
        self,
        settings: PlatformLifecycleHttpSettings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._client = client

    async def append_event(
        self,
        request: InventoryRuntimeExecutionRequest,
        event: InventoryRuntimeStageEvent,
    ) -> InventoryRuntimeStageEventReceipt:
        response = await self._request(
            "POST",
            f"/api/v1/engine-studio/inventory/runs/{request.engine_run_id}/events",
            json={
                "contract_id": "inventory-engine-run-stage-event-v1",
                "tenant_id": request.tenant_id,
                "project_id": request.project_id,
                "engine_run_id": request.engine_run_id,
                "event_type": event.event_type,
                "stage": event.stage,
                "status": event.status,
                "progress_percent": event.progress_percent,
                "message_code": event.message_code,
                "payload_redacted": dict(event.payload_redacted),
            },
        )
        payload = _response_payload(response, {200, 201}, "PLATFORM_EVENT_REJECTED")
        require(
            payload.get("contract_id") == "inventory-engine-run-stage-event-receipt-v1"
            and _uuid(payload.get("engine_run_id")) == request.engine_run_id
            and type(payload.get("event_seq")) is int
            and payload["event_seq"] > 0,
            "PLATFORM_EVENT_RECEIPT_INVALID",
        )
        return InventoryRuntimeStageEventReceipt(
            site_row_version=payload.get("site_row_version"),
            run_status=payload.get("run_status"),
            replayed=payload.get("replayed"),
        )

    async def publish(
        self,
        request: InventoryRuntimeExecutionRequest,
        *,
        expected_site_row_version: int,
        result: InventoryRuntimeResult,
    ) -> Mapping[str, Any]:
        result_payload: dict[str, Any] = {
            "contract_id": (
                "inventory-engine-run-publish-v2"
                if result.inventory_result_contract_key is not None
                else "inventory-engine-run-publish-v1"
            ),
            "tenant_id": request.tenant_id,
            "project_id": request.project_id,
            "engine_run_id": request.engine_run_id,
            "expected_site_row_version": expected_site_row_version,
            "inventory_result_snapshot_id": result.inventory_result_snapshot_id,
            "inventory_result_content_hash": result.inventory_result_content_hash,
        }
        if result.inventory_result_contract_key is not None:
            result_payload.update(
                inventory_result_contract_key=result.inventory_result_contract_key,
                inventory_result_contract_version=result.inventory_result_contract_version,
            )
        response = await self._request(
            "POST",
            f"/api/v1/engine-studio/inventory/runs/{request.engine_run_id}/publish",
            json=result_payload,
        )
        payload = _response_payload(response, {200}, "PLATFORM_PUBLICATION_REJECTED")
        require(
            payload.get("contract_id") == "inventory-engine-run-publish-receipt-v1"
            and _uuid(payload.get("engine_run_id")) == request.engine_run_id
            and _uuid(payload.get("effective_run_id")) == request.engine_run_id
            and payload.get("site_status") == "succeeded"
            and payload.get("cycle_status")
            in {"running", "partially_succeeded", "recovering", "succeeded", "failed"}
            and type(payload.get("replayed")) is bool
            and type(payload.get("site_row_version")) is int
            and payload["site_row_version"] > 0,
            "PLATFORM_PUBLICATION_RECEIPT_INVALID",
        )
        hash_value(result.inventory_result_content_hash)
        return payload

    async def _request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        headers = {"Accept": "application/json"}
        if self._settings.bearer_token:
            headers["Authorization"] = f"Bearer {self._settings.bearer_token}"
        try:
            if self._client is not None:
                return await self._client.request(
                    method,
                    path,
                    headers=headers,
                    timeout=self._settings.timeout_seconds,
                    **kwargs,
                )
            async with httpx.AsyncClient(base_url=self._settings.base_url) as client:
                return await client.request(
                    method,
                    path,
                    headers=headers,
                    timeout=self._settings.timeout_seconds,
                    **kwargs,
                )
        except httpx.HTTPError:
            raise InventoryInputError("PLATFORM_LIFECYCLE_UNAVAILABLE") from None


def _response_payload(
    response: httpx.Response,
    statuses: set[int],
    error_code: str,
) -> dict[str, Any]:
    require(response.status_code in statuses, error_code)
    try:
        payload = response.json()
    except (TypeError, ValueError):
        raise InventoryInputError(error_code) from None
    require(isinstance(payload, dict), error_code)
    return payload


def _uuid(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError):
        raise InventoryInputError("PLATFORM_RECEIPT_ID_INVALID") from None


__all__ = [
    "PlatformInventoryLifecycleHttpClient",
    "PlatformLifecycleHttpSettings",
]

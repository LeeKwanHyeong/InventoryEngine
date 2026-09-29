"""Authenticated sealed Trade Cost Projection reader for Inventory Runtime."""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from dsio_inventory_engine.infrastructure.http.platform_lifecycle import (
    PlatformLifecycleHttpSettings,
)
from dsio_inventory_engine.inventory_contracts.runtime import (
    InventoryRuntimeExecutionRequest,
)
from dsio_inventory_engine.inventory_contracts.trade_cost_projection import (
    TradeCostRevisionSetProjection,
    validate_trade_cost_projection_runtime_binding,
)
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    require,
)


class TradeCostProjectionResolver(Protocol):
    async def resolve(
        self,
        request: InventoryRuntimeExecutionRequest,
    ) -> TradeCostRevisionSetProjection: ...


class PlatformTradeCostProjectionHttpClient:
    def __init__(
        self,
        settings: PlatformLifecycleHttpSettings,
        *,
        environment_scope: str = "DEVELOPMENT",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        require(
            environment_scope in {"DEVELOPMENT", "PRODUCTION"},
            "TRADE_COST_ENVIRONMENT_INVALID",
        )
        self._settings = settings
        self._environment_scope = environment_scope
        self._client = client

    async def resolve(
        self,
        request: InventoryRuntimeExecutionRequest,
    ) -> TradeCostRevisionSetProjection:
        binding = request.trade_cost_binding
        context = request.canonical_context_binding
        require(binding is not None, "TRADE_COST_RUNTIME_BINDING_REQUIRED")
        require(context is not None, "TRADE_COST_RUNTIME_CONTEXT_REQUIRED")
        response = await self._request(
            "GET",
            "/api/v1/engine-studio/inventory/trade-cost/revision-sets/"
            f"{binding['source_snapshot_id']}/projection",
            params={
                "project_id": request.project_id,
                "expected_content_hash": binding["source_content_hash"],
                "valuation_date": context["master_as_of_date"],
            },
            headers={
                "X-Request-ID": request.value["claim"]["request_id"],
                "X-Correlation-ID": request.value["claim"]["correlation_id"],
            },
        )
        require(response.status_code == 200, "TRADE_COST_PROJECTION_REJECTED")
        try:
            payload = response.json()
        except (TypeError, ValueError):
            raise InventoryInputError("TRADE_COST_PROJECTION_REJECTED") from None
        require(isinstance(payload, dict), "TRADE_COST_PROJECTION_REJECTED")
        projection = TradeCostRevisionSetProjection.from_dict(payload)
        validate_trade_cost_projection_runtime_binding(
            request,
            projection,
            environment_scope=self._environment_scope,
        )
        return projection

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        headers = {"Accept": "application/json", **dict(kwargs.pop("headers", {}))}
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
            raise InventoryInputError("TRADE_COST_PROJECTION_UNAVAILABLE") from None


__all__ = [
    "PlatformTradeCostProjectionHttpClient",
    "TradeCostProjectionResolver",
]

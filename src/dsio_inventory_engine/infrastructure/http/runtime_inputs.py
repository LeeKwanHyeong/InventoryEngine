"""Authenticated exact approved-input resolution. Only the configured Platform URL is used."""

import httpx

from dsio_inventory_engine.inventory_contracts.runtime_inputs import MAX_BYTES
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, read_json, require
from dsio_inventory_engine.run_inventory.approved_inputs import validate_manifest_request


class PlatformRuntimeInputHttpClient:
    def __init__(self, settings, *, client=None):
        self.settings, self.client = settings, client

    async def resolve(self, request):
        headers = {
            "Accept": "application/json",
            "X-Request-ID": request.value["claim"]["request_id"],
            "X-Correlation-ID": request.value["claim"]["correlation_id"],
        }
        if self.settings.bearer_token:
            headers["Authorization"] = "Bearer " + self.settings.bearer_token
        path = (
            f"/api/v1/engine-studio/inventory/runs/{request.engine_run_id}/runtime-inputs/resolve"
        )
        try:
            if self.client is None:
                async with httpx.AsyncClient(base_url=self.settings.base_url) as client:
                    response = await client.post(
                        path,
                        json=request.to_dict(),
                        headers=headers,
                        timeout=self.settings.timeout_seconds,
                    )
            else:
                response = await self.client.post(
                    path,
                    json=request.to_dict(),
                    headers=headers,
                    timeout=self.settings.timeout_seconds,
                )
        except httpx.HTTPError:
            raise InventoryInputError("PLATFORM_RUNTIME_INPUT_UNAVAILABLE") from None
        require(response.status_code == 200, "PLATFORM_RUNTIME_INPUT_REJECTED")
        try:
            doc = read_json(response.text, max_bytes=MAX_BYTES)
            return validate_manifest_request(request, doc)
        except (ValueError, KeyError, TypeError):
            raise InventoryInputError("PLATFORM_RUNTIME_INPUT_INVALID") from None

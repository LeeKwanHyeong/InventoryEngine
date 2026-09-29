"""Platform Trade Cost projection HTTP binding tests."""

from __future__ import annotations

import unittest

import httpx

from dsio_inventory_engine.infrastructure.http.platform_lifecycle import (
    PlatformLifecycleHttpSettings,
)
from dsio_inventory_engine.infrastructure.http.trade_cost_projection import (
    PlatformTradeCostProjectionHttpClient,
)
from dsio_inventory_engine.inventory_contracts.runtime import (
    InventoryRuntimeExecutionRequest,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from tests.support.runtime_fixtures import (
    reseal_runtime_site_binding,
    runtime_dispatch_v2,
)
from tests.support.trade_cost_fixtures import (
    REVISION_SET_ID,
    sealed_trade_cost_projection,
)


def _runtime_request(projection: dict) -> InventoryRuntimeExecutionRequest:
    value = runtime_dispatch_v2(
        config_hash="3" * 64,
        effective_policy_content_hash="b" * 64,
    )
    value["claim"]["input_bindings"].append(
        {
            "input_type": "TRADE_COST_REVISION_SET",
            "source_contract_key": "inventory.trade_cost_revision_set",
            "source_snapshot_id": projection["revision_set"]["revision_set_id"],
            "source_content_hash": projection["revision_set_content_hash"],
            "source_contract_version": "1.0.0",
        }
    )
    return InventoryRuntimeExecutionRequest.from_dict(reseal_runtime_site_binding(value))


class TradeCostProjectionHttpTest(unittest.IsolatedAsyncioTestCase):
    async def test_reads_projection_bound_by_platform_claim(self) -> None:
        projection = sealed_trade_cost_projection()
        request = _runtime_request(projection)

        async def handler(http_request: httpx.Request) -> httpx.Response:
            self.assertEqual(
                f"/api/v1/engine-studio/inventory/trade-cost/revision-sets/"
                f"{REVISION_SET_ID}/projection",
                http_request.url.path,
            )
            self.assertEqual("project-a", http_request.url.params["project_id"])
            self.assertEqual(
                projection["revision_set_content_hash"],
                http_request.url.params["expected_content_hash"],
            )
            self.assertEqual(
                "2026-09-28",
                http_request.url.params["valuation_date"],
            )
            self.assertEqual("Bearer test-token", http_request.headers["Authorization"])
            self.assertEqual("request-1", http_request.headers["X-Request-ID"])
            return httpx.Response(200, json=projection)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://platform.example",
        ) as http_client:
            resolver = PlatformTradeCostProjectionHttpClient(
                PlatformLifecycleHttpSettings(
                    base_url="https://platform.example",
                    bearer_token="test-token",
                ),
                client=http_client,
            )
            resolved = await resolver.resolve(request)

        self.assertEqual(
            projection["projection_content_hash"],
            resolved.to_dict()["projection_content_hash"],
        )

    async def test_rejects_projection_outside_claim_scope(self) -> None:
        projection = sealed_trade_cost_projection(origin_site_cd="V101")
        request = _runtime_request(projection)

        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=projection)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://platform.example",
        ) as http_client:
            resolver = PlatformTradeCostProjectionHttpClient(
                PlatformLifecycleHttpSettings(base_url="https://platform.example"),
                client=http_client,
            )
            with self.assertRaisesRegex(
                InventoryInputError,
                "TRADE_COST_RUNTIME_SCOPE_MISMATCH",
            ):
                await resolver.resolve(request)

    async def test_rejects_platform_error_without_using_unsealed_data(self) -> None:
        projection = sealed_trade_cost_projection()
        request = _runtime_request(projection)

        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(409, json={"detail": "hash mismatch"})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://platform.example",
        ) as http_client:
            resolver = PlatformTradeCostProjectionHttpClient(
                PlatformLifecycleHttpSettings(base_url="https://platform.example"),
                client=http_client,
            )
            with self.assertRaisesRegex(
                InventoryInputError,
                "TRADE_COST_PROJECTION_REJECTED",
            ):
                await resolver.resolve(request)


if __name__ == "__main__":
    unittest.main()

"""Optional cross-repository Platform transport to Runtime API verification."""

import os
import sys
import unittest

import httpx

from dsio_inventory_engine.entrypoints.runtime_api import create_inventory_runtime_app
from dsio_inventory_engine.inventory_contracts.runtime import (
    InventoryRuntimeExecutionRequest,
)
from tests.support.runtime_fixtures import RUN_ID, runtime_dispatch, runtime_dispatch_v2


class Submission:
    def __init__(self):
        self.requests = []

    async def submit(self, request):
        self.requests.append(request)
        return {"engine_run_id": request.engine_run_id, "status": "running", "replayed": False}


@unittest.skipUnless(
    os.environ.get("DSAI_PLATFORM_ROOT"),
    "platform source not explicitly provided",
)
class PlatformRuntimeDispatchOfflineTests(unittest.IsolatedAsyncioTestCase):
    async def test_v1_and_v2_dispatch_hashes_match_inventory_consumer(self):
        platform_root = os.environ["DSAI_PLATFORM_ROOT"]
        if platform_root not in sys.path:
            sys.path.insert(0, platform_root)
        from backend.platform_api.inventory_engine_run_contract import (
            InventoryEngineExecutionDispatch,
        )

        documents = (
            runtime_dispatch(),
            runtime_dispatch_v2(
                config_hash="a" * 64,
                effective_policy_content_hash="b" * 64,
            ),
        )
        for document in documents:
            with self.subTest(binding_count=len(document["claim"]["input_bindings"])):
                platform = InventoryEngineExecutionDispatch.model_validate(document)
                inventory = InventoryRuntimeExecutionRequest.from_dict(document)
                self.assertEqual(platform.canonical_hash, inventory.canonical_hash)

    async def test_platform_transport_calls_inventory_runtime_contract(self):
        platform_root = os.environ["DSAI_PLATFORM_ROOT"]
        if platform_root not in sys.path:
            sys.path.insert(0, platform_root)
        from backend.platform_api.inventory_engine_run_contract import (
            InventoryEngineExecutionDispatch,
        )
        from backend.platform_api.services.inventory_engine_http_transport import (
            InventoryEngineHttpExecutionTransport,
            InventoryEngineHttpTransportSettings,
        )

        submission = Submission()
        app = create_inventory_runtime_app(
            submission,
            bearer_token="inventory-runtime-token",
        )
        async with httpx.AsyncClient(
            base_url="http://inventory-runtime.test",
            transport=httpx.ASGITransport(app=app),
        ) as client:
            transport = InventoryEngineHttpExecutionTransport(
                InventoryEngineHttpTransportSettings(
                    base_url="http://inventory-runtime.test",
                    bearer_token="inventory-runtime-token",
                ),
                client=client,
            )
            dispatch = InventoryEngineExecutionDispatch.model_validate(runtime_dispatch())
            receipt = await transport.dispatch(dispatch)

        self.assertEqual(str(receipt.engine_run_id), RUN_ID)
        self.assertEqual(receipt.status, "running")
        self.assertEqual(len(submission.requests), 1)
        self.assertEqual(
            submission.requests[0].canonical_hash,
            dispatch.canonical_hash,
        )


if __name__ == "__main__":
    unittest.main()

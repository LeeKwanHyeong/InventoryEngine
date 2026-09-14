"""Offline Platform callback flow from claimed Attempt to effective Run."""

import json
import unittest

import httpx

from dsio_inventory_engine.infrastructure.http.platform_lifecycle import (
    PlatformInventoryLifecycleHttpClient,
    PlatformLifecycleHttpSettings,
)
from dsio_inventory_engine.inventory_contracts.runtime import (
    InventoryRuntimeExecutionRequest,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from dsio_inventory_engine.run_inventory.runtime_execution import (
    InventoryRuntimeResult,
    InventoryRuntimeWorker,
)
from tests.support.runtime_fixtures import RUN_ID, runtime_dispatch, runtime_dispatch_v2


class Handler:
    async def execute(self, request, report):
        await report(
            event_type="inventory.input",
            stage="input_validation",
            progress_percent=20,
            message_code="INVENTORY_INPUTS_VERIFIED",
            payload_redacted={"scope_verified": True},
        )
        await report(
            event_type="inventory.psi",
            stage="psi",
            progress_percent=70,
            message_code="INVENTORY_PSI_COMPLETED",
            payload_redacted={"strategy": "MATHEMATICAL"},
        )
        return InventoryRuntimeResult(
            inventory_result_snapshot_id="IO-RESULT-1",
            inventory_result_content_hash="a" * 64,
        )


class ReviewHandler(Handler):
    async def execute(self, request, report):
        result = await super().execute(request, report)
        return InventoryRuntimeResult(
            inventory_result_snapshot_id=result.inventory_result_snapshot_id,
            inventory_result_content_hash=result.inventory_result_content_hash,
            automatic_publish_allowed=False,
            automatic_order_allowed=False,
            effective_policy_content_hash="b" * 64,
        )


class MismatchedPolicyHandler(Handler):
    async def execute(self, request, report):
        result = await super().execute(request, report)
        return InventoryRuntimeResult(
            inventory_result_snapshot_id=result.inventory_result_snapshot_id,
            inventory_result_content_hash=result.inventory_result_content_hash,
            effective_policy_content_hash="c" * 64,
        )


class MissingPolicyGateHandler(Handler):
    async def execute(self, request, report):
        result = await super().execute(request, report)
        return InventoryRuntimeResult(
            inventory_result_snapshot_id=result.inventory_result_snapshot_id,
            inventory_result_content_hash=result.inventory_result_content_hash,
            effective_policy_content_hash="b" * 64,
        )


class MismatchedPolicyGateHandler(Handler):
    async def execute(self, request, report):
        result = await super().execute(request, report)
        return InventoryRuntimeResult(
            inventory_result_snapshot_id=result.inventory_result_snapshot_id,
            inventory_result_content_hash=result.inventory_result_content_hash,
            automatic_publish_allowed=True,
            automatic_order_allowed=True,
            effective_policy_content_hash="b" * 64,
        )


class RuntimeLifecycleOfflineTests(unittest.IsolatedAsyncioTestCase):
    def test_production_callback_settings_require_https_and_token(self):
        with self.assertRaisesRegex(InventoryInputError, "PLATFORM_API_HTTPS_REQUIRED"):
            PlatformLifecycleHttpSettings.from_env(
                {
                    "IO_ENVIRONMENT": "PRODUCTION",
                    "INVENTORY_PLATFORM_API_BASE_URL": "http://platform.internal",
                }
            )
        with self.assertRaisesRegex(InventoryInputError, "PLATFORM_API_AUTH_REQUIRED"):
            PlatformLifecycleHttpSettings.from_env(
                {
                    "IO_ENVIRONMENT": "PRODUCTION",
                    "INVENTORY_PLATFORM_API_BASE_URL": "https://platform.internal",
                }
            )

    async def test_stage_events_and_publication_return_to_platform(self):
        requests: list[httpx.Request] = []
        site_row_version = 2

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal site_row_version
            requests.append(request)
            payload = json.loads(request.content)
            if request.url.path.endswith("/events"):
                site_row_version += 1
                return httpx.Response(
                    201,
                    json={
                        "contract_id": "inventory-engine-run-stage-event-receipt-v1",
                        "engine_run_id": RUN_ID,
                        "event_seq": len(requests),
                        "replayed": False,
                        "run_status": payload["status"],
                        "site_row_version": site_row_version,
                    },
                )
            self.assertEqual(payload["expected_site_row_version"], site_row_version)
            site_row_version += 1
            return httpx.Response(
                200,
                json={
                    "contract_id": "inventory-engine-run-publish-receipt-v1",
                    "engine_run_id": RUN_ID,
                    "cycle_site_execution_id": "PCR-202601-01:V100",
                    "effective_run_id": RUN_ID,
                    "site_status": "succeeded",
                    "cycle_status": "succeeded",
                    "replayed": False,
                    "site_row_version": site_row_version,
                },
            )

        async with httpx.AsyncClient(
            base_url="http://platform.test",
            transport=httpx.MockTransport(handler),
        ) as client:
            platform = PlatformInventoryLifecycleHttpClient(
                PlatformLifecycleHttpSettings(
                    base_url="http://platform.test",
                    bearer_token="platform-service-token",
                ),
                client=client,
            )
            result = await InventoryRuntimeWorker(
                handler=Handler(),
                platform=platform,
            ).execute(InventoryRuntimeExecutionRequest.from_dict(runtime_dispatch()))

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["site_row_version"], 7)
        event_requests = [item for item in requests if item.url.path.endswith("/events")]
        self.assertEqual(len(event_requests), 4)
        self.assertEqual(
            [json.loads(item.content)["status"] for item in event_requests],
            ["running", "running", "running", "succeeded"],
        )
        self.assertTrue(requests[-1].url.path.endswith("/publish"))
        self.assertTrue(
            all(
                item.headers["authorization"] == "Bearer platform-service-token"
                for item in requests
            )
        )
        self.assertNotIn(
            "platform-service-token",
            "".join(item.content.decode("utf-8") for item in requests),
        )

    async def test_review_result_is_sealed_as_evidence_without_automatic_publish(self):
        requests: list[httpx.Request] = []
        site_row_version = 2

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal site_row_version
            requests.append(request)
            payload = json.loads(request.content)
            self.assertTrue(request.url.path.endswith("/events"))
            site_row_version += 1
            return httpx.Response(
                201,
                json={
                    "contract_id": "inventory-engine-run-stage-event-receipt-v1",
                    "engine_run_id": RUN_ID,
                    "event_seq": len(requests),
                    "replayed": False,
                    "run_status": payload["status"],
                    "site_row_version": site_row_version,
                },
            )

        async with httpx.AsyncClient(
            base_url="http://platform.test",
            transport=httpx.MockTransport(handler),
        ) as client:
            platform = PlatformInventoryLifecycleHttpClient(
                PlatformLifecycleHttpSettings(
                    base_url="http://platform.test",
                    bearer_token="platform-service-token",
                ),
                client=client,
            )
            result = await InventoryRuntimeWorker(
                handler=ReviewHandler(),
                platform=platform,
            ).execute(
                InventoryRuntimeExecutionRequest.from_dict(
                    runtime_dispatch_v2(
                        config_hash="3" * 64,
                        effective_policy_content_hash="b" * 64,
                    )
                )
            )

        self.assertEqual(result["status"], "review_required")
        self.assertEqual(result["publication_status"], "withheld_for_review")
        self.assertFalse(result["automatic_publish_allowed"])
        self.assertEqual(len(requests), 4)
        self.assertTrue(all(item.url.path.endswith("/events") for item in requests))
        last_event = json.loads(requests[-1].content)
        self.assertEqual(last_event["status"], "running")
        self.assertEqual(last_event["message_code"], "INVENTORY_RESULT_REVIEW_REQUIRED")

    async def test_v2_result_hash_must_match_platform_claim_before_publication(self):
        requests: list[httpx.Request] = []
        site_row_version = 2

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal site_row_version
            requests.append(request)
            payload = json.loads(request.content)
            self.assertTrue(request.url.path.endswith("/events"))
            site_row_version += 1
            return httpx.Response(
                201,
                json={
                    "contract_id": "inventory-engine-run-stage-event-receipt-v1",
                    "engine_run_id": RUN_ID,
                    "event_seq": len(requests),
                    "replayed": False,
                    "run_status": payload["status"],
                    "site_row_version": site_row_version,
                },
            )

        async with httpx.AsyncClient(
            base_url="http://platform.test",
            transport=httpx.MockTransport(handler),
        ) as client:
            platform = PlatformInventoryLifecycleHttpClient(
                PlatformLifecycleHttpSettings(
                    base_url="http://platform.test",
                    bearer_token="platform-service-token",
                ),
                client=client,
            )
            with self.assertRaisesRegex(
                InventoryInputError,
                "RUNTIME_EFFECTIVE_POLICY_RESULT_HASH_MISMATCH",
            ):
                await InventoryRuntimeWorker(
                    handler=MismatchedPolicyHandler(),
                    platform=platform,
                ).execute(
                    InventoryRuntimeExecutionRequest.from_dict(
                        runtime_dispatch_v2(
                            config_hash="3" * 64,
                            effective_policy_content_hash="b" * 64,
                        )
                    )
                )

        self.assertEqual(len(requests), 4)
        self.assertTrue(all(item.url.path.endswith("/events") for item in requests))
        failure = json.loads(requests[-1].content)
        self.assertEqual(failure["status"], "failed")
        self.assertEqual(
            failure["message_code"],
            "RUNTIME_EFFECTIVE_POLICY_RESULT_HASH_MISMATCH",
        )

    async def test_v2_result_requires_explicit_policy_gates_and_exact_claim_match(self):
        for handler_instance, code in (
            (
                MissingPolicyGateHandler(),
                "RUNTIME_EFFECTIVE_POLICY_RESULT_GATE_REQUIRED",
            ),
            (
                MismatchedPolicyGateHandler(),
                "RUNTIME_EFFECTIVE_POLICY_RESULT_GATE_MISMATCH",
            ),
        ):
            with self.subTest(code=code):
                requests: list[httpx.Request] = []
                site_row_version = 2

                def handler(request: httpx.Request) -> httpx.Response:
                    nonlocal site_row_version
                    requests.append(request)
                    payload = json.loads(request.content)
                    self.assertTrue(request.url.path.endswith("/events"))
                    site_row_version += 1
                    return httpx.Response(
                        201,
                        json={
                            "contract_id": "inventory-engine-run-stage-event-receipt-v1",
                            "engine_run_id": RUN_ID,
                            "event_seq": len(requests),
                            "replayed": False,
                            "run_status": payload["status"],
                            "site_row_version": site_row_version,
                        },
                    )

                async with httpx.AsyncClient(
                    base_url="http://platform.test",
                    transport=httpx.MockTransport(handler),
                ) as client:
                    platform = PlatformInventoryLifecycleHttpClient(
                        PlatformLifecycleHttpSettings(
                            base_url="http://platform.test",
                            bearer_token="platform-service-token",
                        ),
                        client=client,
                    )
                    with self.assertRaisesRegex(InventoryInputError, code):
                        await InventoryRuntimeWorker(
                            handler=handler_instance,
                            platform=platform,
                        ).execute(
                            InventoryRuntimeExecutionRequest.from_dict(
                                runtime_dispatch_v2(
                                    config_hash="3" * 64,
                                    effective_policy_content_hash="b" * 64,
                                )
                            )
                        )

                self.assertEqual(len(requests), 4)
                failure = json.loads(requests[-1].content)
                self.assertEqual(failure["status"], "failed")
                self.assertEqual(failure["message_code"], code)


if __name__ == "__main__":
    unittest.main()

"""Offline Platform callback flow from claimed Attempt to effective Run."""

import copy
import json
import unittest

import httpx

from dsio_inventory_engine.infrastructure.http.platform_lifecycle import (
    PlatformInventoryLifecycleHttpClient,
    PlatformLifecycleHttpSettings,
)
from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.result_bundle import (
    derive_result_bundle_id,
    seal_inventory_result_bundle,
)
from dsio_inventory_engine.inventory_contracts.runtime import (
    InventoryRuntimeExecutionRequest,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from dsio_inventory_engine.run_inventory.runtime_execution import (
    InventoryRuntimeResult,
    InventoryRuntimeWorker,
)
from tests.support.fixtures import build_request, golden_case, reseal
from tests.support.runtime_fixtures import (
    CONFIG_REVISION_ID,
    DEMAND_RUN_ID,
    RUN_ID,
    bind_runtime_dispatch_to_canonical,
    runtime_dispatch,
    runtime_dispatch_v2,
)


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


def _canonical_runtime_input() -> CanonicalInputRequest:
    value = build_request(golden_case())
    value["context"].update(
        engine_run_id=RUN_ID,
        configuration_revision=CONFIG_REVISION_ID,
        demand_run_id=DEMAND_RUN_ID,
    )
    value["snapshots"]["forecast"]["metadata"]["demand_run_id"] = DEMAND_RUN_ID
    return CanonicalInputRequest.from_dict(reseal(value))


def _runtime_request_v2(
    *,
    config_hash: str = "3" * 64,
    effective_policy_content_hash: str = "b" * 64,
    expected_automatic_publish_allowed: bool = False,
    expected_automatic_order_allowed: bool = False,
) -> InventoryRuntimeExecutionRequest:
    canonical = _canonical_runtime_input()
    dispatch = runtime_dispatch_v2(
        config_hash=config_hash,
        effective_policy_content_hash=effective_policy_content_hash,
        expected_automatic_publish_allowed=expected_automatic_publish_allowed,
        expected_automatic_order_allowed=expected_automatic_order_allowed,
    )
    return InventoryRuntimeExecutionRequest.from_dict(
        bind_runtime_dispatch_to_canonical(dispatch, canonical.to_dict())
    )


def _actions(child_id: str) -> dict:
    return {
        "raw_action_reference": f"artifact:{child_id}:raw",
        "raw_action_content_hash": "1" * 64,
        "constrained_action_reference": f"artifact:{child_id}:constrained",
        "constrained_action_content_hash": "2" * 64,
        "adjustment_reasons_reference": f"artifact:{child_id}:adjustments",
        "adjustment_reasons_content_hash": "3" * 64,
    }


def _successful_child(
    child_id: str,
    *,
    result_kind: str,
    execution_role: str,
    strategy_type: str,
    scenario_id: str,
    scenario_content_hash: str,
    challenger_id: str | None = None,
    model_content_hash: str | None = None,
) -> dict:
    no_strategy = strategy_type == "NONE"
    return {
        "child_result_id": child_id,
        "result_kind": result_kind,
        "execution_role": execution_role,
        "strategy_type": strategy_type,
        "scenario_id": scenario_id,
        "scenario_content_hash": scenario_content_hash,
        "challenger_id": challenger_id,
        "model_content_hash": model_content_hash,
        "status": "SUCCEEDED",
        "artifact_reference": f"artifact:{child_id}",
        "artifact_contract_key": "inventory.psi_result",
        "artifact_contract_version": "1.0.0",
        "child_content_hash": "4" * 64,
        "row_count": 26,
        "failure_reason_code": None,
        "action_evidence": (
            {
                "raw_action_reference": None,
                "raw_action_content_hash": None,
                "constrained_action_reference": None,
                "constrained_action_content_hash": None,
                "adjustment_reasons_reference": None,
                "adjustment_reasons_content_hash": None,
            }
            if no_strategy
            else _actions(child_id)
        ),
    }


def _comparison(candidate_id: str) -> dict:
    return {
        "baseline_child_result_id": "BASELINE-1",
        "candidate_child_result_id": candidate_id,
        "cost_delta": {
            "status": "NOT_AVAILABLE",
            "value": None,
            "reason_code": "COST_PROFILE_NOT_BOUND",
        },
        "service_level_delta": {"status": "AVAILABLE", "value": "0.01", "reason_code": None},
        "backorder_qty_delta": {"status": "AVAILABLE", "value": "-1", "reason_code": None},
    }


def _sealed_runtime_bundle(
    request: InventoryRuntimeExecutionRequest,
    *,
    canonical_input_hash: str,
    automatic_publish_allowed: bool,
    automatic_order_allowed: bool,
) -> dict:
    plan = request.strategy_execution_plan
    assert plan is not None
    claim = request.value["claim"]
    children = [
        _successful_child(
            "BASELINE-1",
            result_kind="BASELINE_PSI",
            execution_role="EVIDENCE_ONLY",
            strategy_type="NONE",
            scenario_id="BASE",
            scenario_content_hash=plan["base_scenario_content_hash"],
        ),
        _successful_child(
            "MATH-1",
            result_kind="RECOMMENDED_PSI",
            execution_role="OPERATIONAL",
            strategy_type="MATHEMATICAL",
            scenario_id="BASE",
            scenario_content_hash=plan["base_scenario_content_hash"],
        ),
    ]
    for challenger in plan["shadow_challenger_bindings"]:
        children.append(
            _successful_child(
                f"PPO-{challenger['challenger_id']}",
                result_kind="RECOMMENDED_PSI",
                execution_role="SHADOW",
                strategy_type="DEEP_RL",
                scenario_id="BASE",
                scenario_content_hash=plan["base_scenario_content_hash"],
                challenger_id=challenger["challenger_id"],
                model_content_hash=challenger["model"]["content_hash"],
            )
        )
    for scenario in plan["stress_scenario_bindings"]:
        children.append(
            _successful_child(
                f"STRESS-{scenario['scenario_id']}",
                result_kind="STRESS_PSI",
                execution_role="EVIDENCE_ONLY",
                strategy_type="MATHEMATICAL",
                scenario_id=scenario["scenario_id"],
                scenario_content_hash=scenario["scenario_content_hash"],
            )
        )
    body = {
        "contract_id": "inventory-result-bundle-v1",
        "contract_version": "1.0.0",
        "source_contract_key": "inventory.result_bundle",
        "result_bundle_id": derive_result_bundle_id(
            request.engine_run_id,
            request.value["attempt_no"],
        ),
        "engine_run_id": request.engine_run_id,
        "attempt_no": request.value["attempt_no"],
        "tenant_id": claim["tenant_id"],
        "project_id": claim["project_id"],
        "planning_cycle_id": claim["planning_cycle_id"],
        "planning_cycle_revision_id": claim["planning_cycle_revision_id"],
        "cycle_site_execution_id": claim["cycle_site_execution_id"],
        "plan_id": claim["plan_id"],
        "scope": claim["scope"],
        "canonical_input_hash": canonical_input_hash,
        "site_binding_hash": claim["site_binding_hash"],
        "strategy_execution_plan_id": plan["strategy_execution_plan_id"],
        "strategy_execution_plan_content_hash": plan["content_hash"],
        "classification_config_hash": plan["classification_config_hash"],
        "effective_policy_content_hash": plan["effective_policy_content_hash"],
        "cost_profile_content_hash": None,
        "result_children": children,
        "effective_child_result_id": "MATH-1",
        "comparisons": [_comparison(child["child_result_id"]) for child in children[1:]],
        "automatic_publish_allowed": automatic_publish_allowed,
        "automatic_order_allowed": automatic_order_allowed,
    }
    return seal_inventory_result_bundle(body, strategy_execution_plan=plan)


async def _v2_result(
    request: InventoryRuntimeExecutionRequest,
    report,
    *,
    result_publish_allowed: bool | None = None,
    result_order_allowed: bool | None = None,
    effective_policy_content_hash: str = "b" * 64,
    include_contract: bool = True,
    canonical_input: CanonicalInputRequest | None = None,
    bundle_canonical_input_hash: str | None = None,
    pointer_id: str | None = None,
    pointer_hash: str | None = None,
) -> InventoryRuntimeResult:
    await Handler().execute(request, report)
    claim = request.value["claim"]
    canonical = canonical_input or _canonical_runtime_input()
    bundle = _sealed_runtime_bundle(
        request,
        canonical_input_hash=bundle_canonical_input_hash or canonical.input_hash,
        automatic_publish_allowed=bool(claim["expected_automatic_publish_allowed"]),
        automatic_order_allowed=bool(claim["expected_automatic_order_allowed"]),
    )
    return InventoryRuntimeResult(
        inventory_result_snapshot_id=pointer_id or bundle["result_bundle_id"],
        inventory_result_content_hash=pointer_hash or bundle["content_hash"],
        automatic_publish_allowed=result_publish_allowed,
        automatic_order_allowed=result_order_allowed,
        effective_policy_content_hash=effective_policy_content_hash,
        inventory_result_contract_key="inventory.result_bundle" if include_contract else None,
        inventory_result_contract_version="1.0.0" if include_contract else None,
        canonical_input=canonical if include_contract else None,
        inventory_result_bundle=bundle if include_contract else None,
    )


class ReviewHandler(Handler):
    async def execute(self, request, report):
        return await _v2_result(
            request,
            report,
            result_publish_allowed=False,
            result_order_allowed=False,
        )


class MismatchedPolicyHandler(Handler):
    async def execute(self, request, report):
        return await _v2_result(request, report, effective_policy_content_hash="c" * 64)


class MissingPolicyGateHandler(Handler):
    async def execute(self, request, report):
        return await _v2_result(request, report)


class MismatchedPolicyGateHandler(Handler):
    async def execute(self, request, report):
        return await _v2_result(
            request,
            report,
            result_publish_allowed=True,
            result_order_allowed=True,
        )


class MissingResultBundleContractHandler(Handler):
    async def execute(self, request, report):
        return await _v2_result(
            request,
            report,
            result_publish_allowed=False,
            result_order_allowed=False,
            include_contract=False,
        )


class PublishedV2Handler(Handler):
    async def execute(self, request, report):
        return await _v2_result(
            request,
            report,
            result_publish_allowed=True,
            result_order_allowed=True,
        )


class CanonicalInputTamperHandler(Handler):
    async def execute(self, request, report):
        tampered = _canonical_runtime_input().to_dict()
        tampered["snapshots"]["forecast"]["rows"][0]["forecast_qty"] = "999"
        return await _v2_result(
            request,
            report,
            result_publish_allowed=False,
            result_order_allowed=False,
            canonical_input=CanonicalInputRequest.from_dict(tampered),
        )


class CanonicalClaimTamperHandler(Handler):
    async def execute(self, request, report):
        changed = copy.deepcopy(_canonical_runtime_input().to_dict())
        changed["snapshots"]["forecast"]["rows"][0]["forecast_qty"] = "999"
        return await _v2_result(
            request,
            report,
            result_publish_allowed=False,
            result_order_allowed=False,
            canonical_input=CanonicalInputRequest.from_dict(reseal(changed)),
        )


class CanonicalContextResealTamperHandler(Handler):
    def __init__(self, field: str, replacement: object, *, quantity_rule: bool = False):
        self.field = field
        self.replacement = replacement
        self.quantity_rule = quantity_rule

    async def execute(self, request, report):
        changed = _canonical_runtime_input().to_dict()
        target = changed["quantity_rules"][0] if self.quantity_rule else changed["context"]
        target[self.field] = self.replacement
        return await _v2_result(
            request,
            report,
            result_publish_allowed=False,
            result_order_allowed=False,
            canonical_input=CanonicalInputRequest.from_dict(changed),
        )


class ZeroCanonicalHashBundleHandler(Handler):
    async def execute(self, request, report):
        return await _v2_result(
            request,
            report,
            result_publish_allowed=False,
            result_order_allowed=False,
            bundle_canonical_input_hash="0" * 64,
        )


class ResultPointerTamperHandler(Handler):
    async def execute(self, request, report):
        return await _v2_result(
            request,
            report,
            result_publish_allowed=False,
            result_order_allowed=False,
            pointer_hash="0" * 64,
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
            self.assertEqual(payload["contract_id"], "inventory-engine-run-publish-v1")
            self.assertNotIn("inventory_result_contract_key", payload)
            self.assertNotIn("inventory_result_contract_version", payload)
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
            ).execute(_runtime_request_v2())

        self.assertEqual(result["status"], "review_required")
        self.assertEqual(result["publication_status"], "withheld_for_review")
        self.assertFalse(result["automatic_publish_allowed"])
        self.assertEqual(result["inventory_result_contract_key"], "inventory.result_bundle")
        self.assertEqual(result["inventory_result_contract_version"], "1.0.0")
        self.assertEqual(len(requests), 4)
        self.assertTrue(all(item.url.path.endswith("/events") for item in requests))
        last_event = json.loads(requests[-1].content)
        self.assertEqual(last_event["status"], "running")
        self.assertEqual(last_event["message_code"], "INVENTORY_RESULT_REVIEW_REQUIRED")
        self.assertEqual(
            last_event["payload_redacted"]["inventory_result_contract_key"],
            "inventory.result_bundle",
        )

    async def test_v2_publication_identifies_result_id_and_hash_as_bundle(self):
        requests: list[httpx.Request] = []
        site_row_version = 2

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal site_row_version
            requests.append(request)
            payload = json.loads(request.content)
            site_row_version += 1
            if request.url.path.endswith("/events"):
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
            self.assertEqual(payload["inventory_result_contract_key"], "inventory.result_bundle")
            self.assertEqual(payload["inventory_result_contract_version"], "1.0.0")
            self.assertEqual(payload["contract_id"], "inventory-engine-run-publish-v2")
            self.assertEqual(
                payload["inventory_result_snapshot_id"],
                derive_result_bundle_id(RUN_ID, 1),
            )
            self.assertNotEqual(payload["inventory_result_content_hash"], "a" * 64)
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
                handler=PublishedV2Handler(),
                platform=platform,
            ).execute(
                _runtime_request_v2(
                    expected_automatic_publish_allowed=True,
                    expected_automatic_order_allowed=True,
                )
            )

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(
            result["inventory_result_snapshot_id"],
            derive_result_bundle_id(RUN_ID, 1),
        )
        self.assertEqual(result["inventory_result_contract_key"], "inventory.result_bundle")
        self.assertTrue(requests[-1].url.path.endswith("/publish"))

    async def test_publish_retry_replays_the_same_terminal_bundle(self):
        requests: list[httpx.Request] = []
        event_receipts: dict[str, tuple[int, int]] = {}
        publish_payloads: list[dict] = []
        site_row_version = 2

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal site_row_version
            requests.append(request)
            payload = json.loads(request.content)
            if request.url.path.endswith("/events"):
                event_key = json.dumps(payload, sort_keys=True, separators=(",", ":"))
                replayed = event_key in event_receipts
                if replayed:
                    event_seq, event_row_version = event_receipts[event_key]
                else:
                    site_row_version += 1
                    event_seq = len(event_receipts) + 1
                    event_row_version = site_row_version
                    event_receipts[event_key] = (event_seq, event_row_version)
                return httpx.Response(
                    201,
                    json={
                        "contract_id": "inventory-engine-run-stage-event-receipt-v1",
                        "engine_run_id": RUN_ID,
                        "event_seq": event_seq,
                        "replayed": replayed,
                        "run_status": payload["status"],
                        "site_row_version": event_row_version,
                    },
                )

            publish_payloads.append(payload)
            if len(publish_payloads) == 1:
                return httpx.Response(503, json={"detail": "temporary failure"})
            self.assertEqual(publish_payloads[1], publish_payloads[0])
            site_row_version += 1
            return httpx.Response(
                200,
                json={
                    "contract_id": "inventory-engine-run-publish-receipt-v1",
                    "engine_run_id": RUN_ID,
                    "cycle_site_execution_id": "PC-GOLDEN-R1:V101",
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
            worker = InventoryRuntimeWorker(
                handler=PublishedV2Handler(),
                platform=PlatformInventoryLifecycleHttpClient(
                    PlatformLifecycleHttpSettings(
                        base_url="http://platform.test",
                        bearer_token="platform-service-token",
                    ),
                    client=client,
                ),
            )
            request = _runtime_request_v2(
                expected_automatic_publish_allowed=True,
                expected_automatic_order_allowed=True,
            )
            with self.assertRaisesRegex(
                InventoryInputError,
                "PLATFORM_PUBLICATION_REJECTED",
            ):
                await worker.execute(request)
            result = await worker.execute(request)

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(len(publish_payloads), 2)
        self.assertEqual(len(event_receipts), 4)
        self.assertEqual(
            publish_payloads[0]["inventory_result_snapshot_id"],
            derive_result_bundle_id(RUN_ID, 1),
        )
        self.assertEqual(
            publish_payloads[0]["inventory_result_content_hash"],
            publish_payloads[1]["inventory_result_content_hash"],
        )

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
                ).execute(_runtime_request_v2())

        self.assertEqual(len(requests), 4)
        self.assertTrue(all(item.url.path.endswith("/events") for item in requests))
        failure = json.loads(requests[-1].content)
        self.assertEqual(failure["status"], "failed")
        self.assertEqual(
            failure["message_code"],
            "RUNTIME_EFFECTIVE_POLICY_RESULT_HASH_MISMATCH",
        )

    async def test_v2_rejects_canonical_or_bundle_tampering_before_terminal_event(
        self,
    ):
        for handler_instance, code in (
            (
                CanonicalInputTamperHandler(),
                "SNAPSHOT_HASH_MISMATCH",
            ),
            (
                CanonicalClaimTamperHandler(),
                "RUNTIME_CANONICAL_INPUT_BINDING_MISMATCH",
            ),
            (
                CanonicalContextResealTamperHandler("plan_type", "TGSM"),
                "RUNTIME_CANONICAL_CONTEXT_BINDING_MISMATCH",
            ),
            (
                CanonicalContextResealTamperHandler(
                    "inventory_cutoff_at",
                    "2026-09-27T14:00:00Z",
                ),
                "RUNTIME_CANONICAL_CONTEXT_BINDING_MISMATCH",
            ),
            (
                CanonicalContextResealTamperHandler(
                    "tolerance_qty",
                    "1",
                    quantity_rule=True,
                ),
                "RUNTIME_CANONICAL_CONTEXT_BINDING_MISMATCH",
            ),
            (
                CanonicalContextResealTamperHandler(
                    "scale",
                    1,
                    quantity_rule=True,
                ),
                "RUNTIME_CANONICAL_CONTEXT_BINDING_MISMATCH",
            ),
            (
                CanonicalContextResealTamperHandler(
                    "approval_reference",
                    "TAMPERED-APPROVAL",
                    quantity_rule=True,
                ),
                "RUNTIME_CANONICAL_CONTEXT_BINDING_MISMATCH",
            ),
            (
                ZeroCanonicalHashBundleHandler(),
                "RUNTIME_RESULT_BUNDLE_CANONICAL_INPUT_MISMATCH",
            ),
            (ResultPointerTamperHandler(), "RUNTIME_RESULT_BUNDLE_POINTER_MISMATCH"),
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
                        ).execute(_runtime_request_v2())

                self.assertEqual(len(requests), 4)
                failure = json.loads(requests[-1].content)
                self.assertEqual(failure["status"], "failed")
                self.assertEqual(failure["message_code"], code)

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
                        ).execute(_runtime_request_v2())

                self.assertEqual(len(requests), 4)
                failure = json.loads(requests[-1].content)
                self.assertEqual(failure["status"], "failed")
                self.assertEqual(failure["message_code"], code)

    async def test_v2_result_requires_result_bundle_contract_binding(self):
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
                "RUNTIME_RESULT_BUNDLE_CONTRACT_REQUIRED",
            ):
                await InventoryRuntimeWorker(
                    handler=MissingResultBundleContractHandler(),
                    platform=platform,
                ).execute(_runtime_request_v2())

        self.assertEqual(requests[-1].url.path.split("/")[-1], "events")
        failure = json.loads(requests[-1].content)
        self.assertEqual(failure["message_code"], "RUNTIME_RESULT_BUNDLE_CONTRACT_REQUIRED")


if __name__ == "__main__":
    unittest.main()

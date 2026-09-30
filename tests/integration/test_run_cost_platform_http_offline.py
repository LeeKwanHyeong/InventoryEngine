"""Cross-repository HTTP callbacks with real sealed evidence and in-memory Platform state."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi import FastAPI

from dsio_inventory_engine.infrastructure.http.platform_lifecycle import (
    PlatformInventoryLifecycleHttpClient,
    PlatformLifecycleHttpSettings,
)
from dsio_inventory_engine.infrastructure.sqlite import SqliteInventoryEvidenceUnitOfWork
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, require
from dsio_inventory_engine.inventory_evidence import PersistInventoryResultBundleUseCase
from dsio_inventory_engine.run_inventory.psi_orchestrator import RunPsiBundleUseCase
from dsio_inventory_engine.run_inventory.result_pipeline import InventoryResultPipelineHandler
from dsio_inventory_engine.run_inventory.runtime_execution import InventoryRuntimeWorker
from tests.integration.test_psi_orchestrator_offline import DEPLOYMENT
from tests.integration.test_result_pipeline_offline import _Resolver
from tests.support.run_cost_fixtures import CostResolver, bound_cost_command


@unittest.skipUnless(
    os.environ.get("DSAI_PLATFORM_ROOT"), "Platform source not explicitly provided"
)
class RunCostPlatformHttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_claim_hash_parent_callbacks_readback_and_review_cannot_publish(self):
        sys.path.insert(0, os.environ["DSAI_PLATFORM_ROOT"])
        sys.path.insert(0, str(Path(os.environ["DSAI_PLATFORM_ROOT"]) / "backend"))
        try:
            from backend.platform_api.dependencies import get_inventory_engine_run_service
        except ModuleNotFoundError as exc:
            if exc.name and exc.name.startswith(("backend", "platform_api")):
                raise
            self.skipTest(f"Full Platform API dependencies unavailable: {exc.name}")
        from backend.platform_api.inventory_engine_run import InventoryEngineRunInvariantError
        from backend.platform_api.inventory_engine_run_contract import (
            InventoryEngineExecutionDispatch,
        )
        from backend.platform_api.routers.inventory_engine_runtime import router
        from backend.platform_api.security import CurrentUser, get_current_user
        from backend.platform_api.services.inventory_engine_run_service import (
            InventoryEngineRunService,
        )

        for review in (False, True):
            with self.subTest(review=review), tempfile.TemporaryDirectory() as folder:
                command, cost = bound_cost_command(
                    action="REVIEW" if review else "ALLOW",
                    approval="HIGH_VALUE" if review else "AUTO",
                    include_stress=False,
                )
                request = command.runtime_request
                dispatch = InventoryEngineExecutionDispatch.model_validate(request.to_dict())
                self.assertEqual(dispatch.canonical_hash, request.canonical_hash)
                repository = SqliteInventoryEvidenceUnitOfWork(Path(folder) / "evidence.sqlite")
                pipeline = InventoryResultPipelineHandler(
                    command_resolver=_Resolver(command),
                    orchestrator=RunPsiBundleUseCase(DEPLOYMENT),
                    evidence_repository=repository,
                    persistence=PersistInventoryResultBundleUseCase(DEPLOYMENT, repository),
                    landed_cost_resolver=CostResolver(cost),
                )

                class Rbac:
                    async def require_permission(self, **scope):
                        require(
                            scope["tenant_id"] == request.tenant_id
                            and scope["scope_id"] == request.project_id,
                            "TEST_RBAC_SCOPE_MISMATCH",
                        )

                class Audit:
                    async def record_admin_event(self, **event):
                        return event

                class PlatformRepository:
                    def __init__(self):
                        self.parent = {}
                        self.events = []
                        self.publications = []
                        self.row_version = 2

                    async def append_stage_event(self, event):
                        self.events.append(event)
                        parent = {
                            key: value
                            for key, value in event.payload_redacted.items()
                            if key.startswith("run_result_manifest_")
                        }
                        if parent:
                            if self.parent and parent != self.parent:
                                raise InventoryEngineRunInvariantError(
                                    "inventory_parent_replay_conflict"
                                )
                            self.parent = parent
                        self.row_version += 1
                        return {
                            "engine_run_id": event.engine_run_id,
                            "event_seq": len(self.events),
                            "replayed": False,
                            "run_status": event.status,
                            "site_row_version": self.row_version,
                        }

                    async def publish_run(self, published):
                        if review:
                            raise InventoryEngineRunInvariantError(
                                "inventory_effective_run_review_required"
                            )
                        self.publications.append(published)
                        self.row_version += 1
                        return {
                            "engine_run_id": published.engine_run_id,
                            "cycle_site_execution_id": request.value["claim"][
                                "cycle_site_execution_id"
                            ],
                            "effective_run_id": published.engine_run_id,
                            "site_status": "succeeded",
                            "cycle_status": "succeeded",
                            "replayed": False,
                            "site_row_version": self.row_version,
                        }

                    async def read_result_reference(self, *, engine_run_id, tenant_id, project_id):
                        require(
                            str(engine_run_id) == request.engine_run_id
                            and tenant_id == request.tenant_id
                            and project_id == request.project_id,
                            "TEST_READ_SCOPE_MISMATCH",
                        )
                        return {
                            "engine_run_id": engine_run_id,
                            "attempt_no": request.value["attempt_no"],
                            "run_status": "running" if review else "succeeded",
                            "evidence_status": "pending" if review else "verified",
                            "run_result_manifest_required": True,
                            **self.parent,
                        }

                class StoredEvidenceVerifier:
                    async def verify(self, pointer):
                        verified = pipeline.verifier.execute(
                            f"artifact:inventory:{request.engine_run_id}:1:bundle",
                            strategy_execution_plan=request.strategy_execution_plan,
                            runtime_request=request,
                        )
                        parent = verified.run_cost_evidence.manifest
                        require(
                            pointer.get("run_result_manifest_reference")
                            == parent["artifact_reference"]
                            and pointer.get("run_result_manifest_content_hash")
                            == parent["content_hash"],
                            "TEST_PARENT_POINTER_MISMATCH",
                        )

                    async def verify_event(self, *, actor, event):
                        if event.payload_redacted.get("run_result_manifest_reference") is not None:
                            await self.verify(event.payload_redacted)

                    async def verify_publication(self, *, actor, request):
                        await self.verify(request.model_dump(mode="json"))

                state = PlatformRepository()
                service = InventoryEngineRunService(
                    repository=state,
                    rbac_enforcer=Rbac(),
                    audit_recorder=Audit(),
                    callback_verifier=StoredEvidenceVerifier(),
                )
                app = FastAPI()
                app.include_router(router)
                app.dependency_overrides[get_inventory_engine_run_service] = lambda: service
                app.dependency_overrides[get_current_user] = lambda: CurrentUser(
                    user_id="runtime", email="", tenant_id=request.tenant_id, roles=["ADMIN"]
                )
                async with httpx.AsyncClient(
                    base_url="http://platform.test", transport=httpx.ASGITransport(app=app)
                ) as client:
                    transport = PlatformInventoryLifecycleHttpClient(
                        PlatformLifecycleHttpSettings(base_url="http://platform.test"),
                        client=client,
                    )
                    result = await InventoryRuntimeWorker(
                        handler=pipeline,
                        platform=transport,
                        publication_acknowledger=pipeline,
                        run_result_verifier=pipeline,
                    ).execute(request)
                    self.assertEqual(result["status"], "review_required" if review else "succeeded")
                    self.assertEqual(len(state.publications), 0 if review else 1)
                    lookup = await client.get(
                        f"/api/v1/engine-studio/inventory/runs/{request.engine_run_id}/result-reference",
                        params={"project_id": request.project_id},
                    )
                    self.assertEqual(lookup.status_code, 200)
                    self.assertEqual(
                        lookup.json()["run_result_manifest_content_hash"],
                        result["run_result_manifest_content_hash"],
                    )
                    body = {
                        "contract_id": "inventory-engine-run-publish-v2",
                        "tenant_id": request.tenant_id,
                        "project_id": request.project_id,
                        "engine_run_id": request.engine_run_id,
                        "expected_site_row_version": state.row_version,
                        "inventory_result_snapshot_id": result["inventory_result_snapshot_id"],
                        "inventory_result_content_hash": result["inventory_result_content_hash"],
                        "inventory_result_contract_key": "inventory.result_bundle",
                        "inventory_result_contract_version": "1.0.0",
                        **state.parent,
                    }
                    if review:
                        forced = await client.post(
                            f"/api/v1/engine-studio/inventory/runs/{request.engine_run_id}/publish",
                            json=body,
                        )
                        self.assertEqual(forced.status_code, 422)
                        self.assertEqual(
                            forced.json()["detail"], "inventory_effective_run_review_required"
                        )
                    body["run_result_manifest_content_hash"] = "f" * 64
                    with self.assertRaisesRegex(
                        InventoryInputError, "TEST_PARENT_POINTER_MISMATCH"
                    ):
                        await StoredEvidenceVerifier().verify(body)

"""Platform RBAC, sealed Source and actual Engine API/SQLite, all offline."""
# ruff: noqa: E402
# Companion Platform imports intentionally follow checkout discovery.

import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dsio_inventory_engine.infrastructure.sqlite import SqliteInventoryEvidenceUnitOfWork
from dsio_inventory_engine.run_inventory.development_api import create_development_calculation_app
from dsio_inventory_engine.run_inventory.development_calculation import (
    DevelopmentCalculationExecutor,
)
from tests.integration.test_development_calculation_offline import inputs
from tests.unit.test_runtime_effective_policy_v2 import DEPLOYMENT

BACKEND = Path(__file__).resolve().parents[3] / "dsai-platform/backend"
if BACKEND.is_dir():
    sys.path.insert(0, str(BACKEND))
    sys.path.insert(0, str(BACKEND.parent))
pytest.importorskip(
    "platform_api", reason="companion Platform checkout needed for cross-API offline test"
)

from platform_api.dependencies import get_inventory_development_calculation_service
from platform_api.routers.inventory_engine_runtime import router
from platform_api.security import CurrentUser, get_current_user
from platform_api.services.inventory_sqlite_artifact_reader import (
    SqliteInventoryRuntimeArtifactReader,
)
from platform_api.services.inventory_development_calculation import (
    DevelopmentCalculationService,
    DevelopmentCalculationRequest,
    OptionalCalculationArtifactReader,
    PinnedDevelopmentCalculationProvider,
    HttpDevelopmentCalculationTransport,
)
from platform_api.rbac import PermissionDeniedError

TOKEN = "offline-test-token-not-a-runtime-secret"


class Guard:
    def __init__(self):
        self.calls, self.deny = [], False

    async def require_permission(self, **kwargs):
        self.calls.append(kwargs)
        if self.deny:
            raise PermissionDeniedError("denied")


@pytest.mark.asyncio
async def test_real_engine_http_with_platform_rbac_and_no_operational_claim(tmp_path):
    command, source = inputs(blocked=True)
    path = tmp_path / "evidence.sqlite3"
    repo = SqliteInventoryEvidenceUnitOfWork(path)
    executor = DevelopmentCalculationExecutor(
        deployment=DEPLOYMENT, repository=repo, source_provider=lambda c: source
    )
    engine = create_development_calculation_app(
        executor=executor, bearer_token=TOKEN, environment="development"
    )
    guard = Guard()
    actor = SimpleNamespace(user_id="developer", tenant_id=command["tenant_id"])
    body = DevelopmentCalculationRequest(
        project_id=command["project_id"],
        site_cd=command["scope"]["site_cd"],
        demand_run_id=command["demand_run_id"],
        idempotency_key="first",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=engine), base_url="http://offline"
    ) as client:
        service = DevelopmentCalculationService(
            environment="development",
            source_provider=PinnedDevelopmentCalculationProvider([command]),
            transport=HttpDevelopmentCalculationTransport(client=client, bearer_token=TOKEN),
            artifact_reader=OptionalCalculationArtifactReader(
                SqliteInventoryRuntimeArtifactReader(path)
            ),
            rbac_enforcer=guard,
        )
        assert (
            await client.post("/api/v1/development-calculations/execute", json=command)
        ).status_code == 401
        result = await service.execute(actor=actor, request=body)
        assert result["blocked_item_count"] == 2
        assert result["automatic_publish_allowed"] is False
        service.provider = None
        executor.source_provider = None
        assert await service.execute(actor=actor, request=body) == result
        assert (
            await service.read(
                actor=actor,
                project_id=body.project_id,
                calculation_id=result["command"]["calculation_id"],
            )
            == result
        )
        assert all(
            c["scope_type"] == "project" and c["scope_id"] == body.project_id for c in guard.calls
        )
        assert {c["permission_key"] for c in guard.calls} == {
            "engine_run.execute",
            "engine_report.read",
        }
        with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
            await service.execute(actor=actor, request=body.model_copy(update={"site_cd": "WRONG"}))
        guard.deny = True
        with pytest.raises(PermissionDeniedError):
            await service.execute(actor=actor, request=body)
        assert list(repo.list_pending()) == []


def test_development_api_never_activates_without_explicit_service():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="dev", tenant_id="tenant-a", email="", roles=["ADMIN"]
    )
    with TestClient(app) as client:
        reply = client.post(
            "/api/v1/engine-studio/inventory/development-calculations",
            json={
                "project_id": "project-a",
                "site_cd": "V101",
                "demand_run_id": "11111111-1111-4111-8111-111111111111",
                "idempotency_key": "test",
            },
        )
        assert reply.status_code == 503
        app.dependency_overrides[get_inventory_development_calculation_service] = lambda: None
        reply = client.post(
            "/api/v1/engine-studio/inventory/development-calculations",
            json={
                "project_id": "project-a",
                "site_cd": "V101",
                "demand_run_id": "11111111-1111-4111-8111-111111111111",
                "idempotency_key": "test",
                "config_hash": "a" * 64,
            },
        )
        assert reply.status_code == 422


def test_engine_ingress_is_authenticated_and_development_only(tmp_path):
    executor = DevelopmentCalculationExecutor(
        deployment=DEPLOYMENT,
        repository=SqliteInventoryEvidenceUnitOfWork(tmp_path / "evidence.sqlite3"),
    )
    with pytest.raises(ValueError):
        create_development_calculation_app(
            executor=executor, bearer_token=TOKEN, environment="production"
        )
    with pytest.raises(ValueError):
        create_development_calculation_app(
            executor=executor, bearer_token="", environment="development"
        )

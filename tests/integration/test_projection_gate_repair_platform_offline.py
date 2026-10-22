"""Real Engine repair via Platform service; no PostgreSQL/Source/PSI execution."""

import json
import os
from pathlib import Path
import sys
from unittest.mock import AsyncMock

import pytest

from dsio_inventory_engine.infrastructure.runtime_build import installed_runtime_build
from tests.integration.test_projection_gate_repair_offline import (
    frozen_legacy_world,
    no_calculation,
)
from tests.integration.test_psi_orchestrator_offline import DEPLOYMENT

pytestmark = pytest.mark.skipif(
    not os.environ.get("DSAI_PLATFORM_ROOT"), reason="Platform root not supplied"
)


@pytest.mark.asyncio
async def test_platform_prepares_reads_and_retries_derived_artifact_without_db(tmp_path):
    root = Path(os.environ["DSAI_PLATFORM_ROOT"])
    sys.path.insert(0, str(root / "backend"))
    from platform_api.inventory_engine_run_contract import InventoryEngineExecutionDispatch
    from platform_api.services.inventory_engine_run_service import InventoryEngineRunActor
    from platform_api.services.inventory_source_projection_execution import (
        InventorySourceProjectionExecutionService,
    )

    request, store, runtime, original, _ = await frozen_legacy_world(tmp_path)
    dispatch = InventoryEngineExecutionDispatch.model_validate(request.to_dict())

    class UnavailableSource:
        async def resolve(self, **kwargs):
            raise AssertionError("Source must not be queried")

    class Rbac:
        async def require_permission(self, **kwargs):
            assert kwargs["scope_id"] == dispatch.claim.project_id

    writer = AsyncMock()
    service = InventorySourceProjectionExecutionService(
        approved_input_export=UnavailableSource(),
        artifact_store=store,
        deployment=DEPLOYMENT,
        catalog=runtime.producer.projector.catalog,
        expected_build=original["document"]["installed_build"],
        rbac_enforcer=Rbac(),
        projection_service=writer,
        expected_projection_build=installed_runtime_build(),
    )
    actor = InventoryEngineRunActor(user_id="offline", tenant_id=dispatch.claim.tenant_id)
    args = dict(
        actor=actor,
        project_id=dispatch.claim.project_id,
        dispatch=dispatch,
        execution_reference=original["artifact_reference"],
        execution_hash=original["content_hash"],
        chunk_size=2,
    )
    before = store.read_artifact(original["artifact_reference"])
    with no_calculation():
        first = await service.prepare_gate_repair(**args)
        again = await service.prepare_gate_repair(**args)
        verified = await service.verify_gate_repair(
            actor=actor,
            project_id=dispatch.claim.project_id,
            dispatch=dispatch,
            repair_reference=first["artifact_reference"],
            repair_hash=first["content_hash"],
        )
    assert not first["replayed"] and again["replayed"]
    assert first["document"] == again["document"] == verified["document"]
    assert before == store.read_artifact(original["artifact_reference"])
    assert json.loads(before.document_bytes) == original["document"]
    writer.write.assert_not_called()

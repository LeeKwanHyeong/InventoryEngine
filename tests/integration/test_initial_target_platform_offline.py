"""Platform's first-call assembly with actual local sealed input adapters only."""

import os
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.support.initial_execution_fixtures import initial_world
from tests.unit.test_target_execution import ReferenceKernel

pytestmark = pytest.mark.skipif(
    not os.environ.get("DSAI_PLATFORM_ROOT"), reason="Platform root not supplied"
)


def platform_world(root):
    platform = Path(os.environ["DSAI_PLATFORM_ROOT"])
    sys.path.insert(0, str(platform))
    sys.path.insert(0, str(platform / "backend"))
    from platform_api.inventory_engine_run_contract import InventoryEngineExecutionDispatch
    from platform_api.services.inventory_engine_run_service import InventoryEngineRunActor
    from platform_api.services.inventory_target_result_execution import (
        InventoryInitialTargetExecutionService,
    )

    w = initial_world(root, ReferenceKernel())
    dispatch = InventoryEngineExecutionDispatch.model_validate(w.request.to_dict())
    actor = InventoryEngineRunActor(user_id="local-operator", tenant_id=dispatch.claim.tenant_id)

    class Rbac:
        def __init__(self):
            self.calls = []
            self.denied = False

        async def require_permission(self, **kw):
            self.calls.append(kw)
            if self.denied:
                raise PermissionError("denied")

    rbac = Rbac()
    return (
        w,
        InventoryInitialTargetExecutionService(runtime=w.runtime, rbac_enforcer=rbac),
        actor,
        dispatch,
        rbac,
    )


@pytest.mark.asyncio
async def test_platform_single_call_then_source_free_query_and_recovery(tmp_path):
    w, service, actor, dispatch, rbac = platform_world(tmp_path)
    auth = dict(actor=actor, project_id=dispatch.claim.project_id, dispatch=dispatch)
    assert w.store.find_outbox(w.request.engine_run_id, 1) is None
    result = await service.execute(**auth, target_input_reference=w.input_ref)
    assert result["result"]["order_plan"]["recommended_orders"]
    with patch.object(w.provider, "resolve", side_effect=AssertionError("Source reread")):
        assert await service.read(**auth, result_pointer=result["result_pointer"]) == result
        assert await service.recover(**auth, target_input_reference=w.input_ref) == result
    assert [c["permission_key"] for c in rbac.calls] == [
        "inventory_run.callback",
        "engine_report.read",
        "inventory_run.callback",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["tenant", "project", "permission", "reader", "hash"])
async def test_platform_denial_precedes_source_or_calculation(tmp_path, case):
    w, service, actor, dispatch, rbac = platform_world(tmp_path)
    project = dispatch.claim.project_id
    ref = dict(w.input_ref)
    if case == "tenant":
        actor = replace(actor, tenant_id="other")
    elif case == "project":
        project = "other"
    elif case == "permission":
        rbac.denied = True
    elif case == "reader":
        service._runtime = None
    else:
        ref["content_hash"] = "0" * 64
    from platform_api.services.inventory_dsim_projection import InventoryProjectionServiceError

    with patch.object(w.provider, "resolve", side_effect=AssertionError("unauthorized Source")):
        with pytest.raises((InventoryProjectionServiceError, PermissionError)):
            await service.execute(
                actor=actor, project_id=project, dispatch=dispatch, target_input_reference=ref
            )
    assert w.store.find_outbox(w.request.engine_run_id, 1) is None


async def installed_initial_roundtrip(root):
    from platform_api.services.inventory_target_result_execution import (
        build_inventory_local_initial_execution_service,
    )

    w, _, actor, dispatch, rbac = platform_world(root)
    kw = dict(
        environment="development",
        deployment=w.runtime.deployment,
        expected_build=w.build,
        artifact_store=w.store,
        target_storage=w.target,
        input_provider=w.provider,
        catalog=w.runtime_kwargs["catalog"],
        rbac_enforcer=rbac,
    )
    service = build_inventory_local_initial_execution_service(**kw)
    auth = dict(actor=actor, project_id=dispatch.claim.project_id, dispatch=dispatch)
    result = await service.execute(**auth, target_input_reference=w.input_ref)
    restarted = build_inventory_local_initial_execution_service(**(kw | {"input_provider": None}))
    assert await restarted.recover(**auth, target_input_reference=w.input_ref) == result
    assert await restarted.read(**auth, result_pointer=result["result_pointer"]) == result

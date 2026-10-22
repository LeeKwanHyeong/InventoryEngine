"""Optional sibling Platform service integration; no HTTP server or external DB."""

import copy
import os
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.support.target_runtime_fixtures import runtime_world
from tests.unit.test_target_execution import ReferenceKernel

pytestmark = pytest.mark.skipif(
    not os.environ.get("DSAI_PLATFORM_ROOT"), reason="Platform root not supplied"
)


def platform_world(tmp_path):
    root = Path(os.environ["DSAI_PLATFORM_ROOT"])
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "backend"))
    from platform_api.inventory_engine_run_contract import InventoryEngineExecutionDispatch
    from platform_api.services.inventory_engine_run_service import InventoryEngineRunActor
    from platform_api.services.inventory_target_result_execution import (
        InventoryTargetResultExecutionService,
    )

    w = runtime_world(tmp_path, ReferenceKernel())
    dispatch = InventoryEngineExecutionDispatch.model_validate(w.request.to_dict())
    actor = InventoryEngineRunActor(user_id="local-operator", tenant_id=dispatch.claim.tenant_id)

    class Rbac:
        def __init__(self):
            self.calls = []
            self.denied = False

        async def require_permission(self, **kwargs):
            self.calls.append(kwargs)
            if self.denied:
                raise PermissionError("offline permission denied")

    rbac = Rbac()
    service = InventoryTargetResultExecutionService(runtime=w.runtime, rbac_enforcer=rbac)
    return w, service, actor, dispatch, rbac


async def installed_local_roundtrip(root):
    """Called in a fresh interpreter whose package path is an installed wheel."""
    from platform_api.services.inventory_target_result_execution import (
        build_inventory_local_target_result_service,
    )

    w, _, actor, dispatch, rbac = platform_world(root)
    kw = dict(
        environment="development",
        deployment=w.runtime_kwargs["deployment"],
        expected_build=w.build,
        artifact_store=w.store,
        target_storage=w.target,
        catalog=w.catalog,
        rbac_enforcer=rbac,
    )
    auth = dict(actor=actor, project_id=dispatch.claim.project_id, dispatch=dispatch)
    service = build_inventory_local_target_result_service(**kw)
    result = await service.execute(**auth, sources=w.pointers)
    restarted = build_inventory_local_target_result_service(**kw)
    assert await restarted.read(**auth, result_pointer=result["result_pointer"]) == result
    assert await restarted.recover(**auth, sources=w.pointers) == result


@pytest.mark.asyncio
async def test_platform_local_call_read_recovery_and_permissions(tmp_path):
    w, service, actor, dispatch, rbac = platform_world(tmp_path)
    kw = dict(actor=actor, project_id=dispatch.claim.project_id, dispatch=dispatch)
    result = await service.execute(**kw, sources=w.pointers)
    assert await service.read(**kw, result_pointer=result["result_pointer"]) == result
    assert await service.recover(**kw, sources=w.pointers) == result
    assert [r["permission_key"] for r in rbac.calls] == [
        "inventory_run.callback",
        "engine_report.read",
        "inventory_run.callback",
    ]
    assert all(r["resource_id"] == str(dispatch.engine_run_id) for r in rbac.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["tenant", "project", "permission", "reader"])
async def test_platform_denial_precedes_reader_access(tmp_path, case):
    w, service, actor, dispatch, rbac = platform_world(tmp_path)
    project = dispatch.claim.project_id
    if case == "tenant":
        actor = replace(actor, tenant_id="other")
    elif case == "project":
        project = "other-project"
    elif case == "permission":
        rbac.denied = True
    else:
        service._runtime = None
    from platform_api.services.inventory_dsim_projection import InventoryProjectionServiceError

    with patch.object(w.runtime, "execute", side_effect=AssertionError("unauthorized reader")):
        with pytest.raises((InventoryProjectionServiceError, PermissionError)):
            await service.execute(
                actor=actor, project_id=project, dispatch=dispatch, sources=w.pointers
            )
    if case in {"tenant", "project"}:
        assert not rbac.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["execute", "read", "recover"])
async def test_platform_converts_invalid_pointer_and_missing_store_errors(tmp_path, operation):
    w, service, actor, dispatch, _ = platform_world(tmp_path)
    kw = {"result_pointer": {}} if operation == "read" else {"sources": {}}
    from platform_api.services.inventory_dsim_projection import InventoryProjectionServiceError

    with pytest.raises(InventoryProjectionServiceError, match="inventory_target_result_invalid"):
        await getattr(service, operation)(
            actor=actor, project_id=dispatch.claim.project_id, dispatch=dispatch, **kw
        )


def test_local_factory_passes_pinned_dependencies_and_has_no_write_service(tmp_path):
    w, _, _, _, rbac = platform_world(tmp_path)
    from platform_api.services.inventory_target_result_execution import (
        build_inventory_local_target_result_service,
    )

    kw = dict(
        environment="development",
        deployment=w.runtime_kwargs["deployment"],
        expected_build=w.build,
        artifact_store=w.store,
        target_storage=w.target,
        catalog=w.catalog,
        rbac_enforcer=rbac,
    )
    with patch(
        "platform_api.services.inventory_local_source_assembly.verify_installed_inventory_build",
        return_value=copy.deepcopy(w.build),
    ) as guard:
        service = build_inventory_local_target_result_service(**kw)
    guard.assert_called_once_with(w.build)
    assert service._runtime.target is w.target
    assert not hasattr(service, "write_verified")
    from platform_api.services.inventory_dsim_projection import InventoryProjectionServiceError

    with pytest.raises(InventoryProjectionServiceError, match="development_only"):
        build_inventory_local_target_result_service(**(kw | {"environment": "production"}))


@pytest.mark.asyncio
async def test_io_error_does_not_return_local_path(tmp_path):
    _, service, actor, dispatch, _ = platform_world(tmp_path)

    def unavailable(**kwargs):
        raise OSError("/private/path/internal-file")

    service._runtime = SimpleNamespace(read=unavailable)
    from platform_api.services.inventory_dsim_projection import InventoryProjectionServiceError

    with pytest.raises(
        InventoryProjectionServiceError, match="inventory_target_result_storage_unavailable"
    ) as error:
        await service.read(
            actor=actor, project_id=dispatch.claim.project_id, dispatch=dispatch, result_pointer={}
        )
    assert "/private/" not in str(error.value)

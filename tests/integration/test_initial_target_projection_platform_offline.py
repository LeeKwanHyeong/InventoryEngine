"""First calculation -> Platform Writer -> same-Run SQL read, with no real DB."""

import copy
import importlib.util
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.integration.test_initial_target_platform_offline import platform_world
from tests.support.initial_execution_fixtures import initial_world
from tests.unit.test_target_execution import ReferenceKernel

pytestmark = pytest.mark.skipif(
    not os.environ.get("DSAI_PLATFORM_ROOT"), reason="Platform root not supplied"
)

if os.environ.get("DSAI_PLATFORM_ROOT"):
    sys.path.insert(0, os.environ["DSAI_PLATFORM_ROOT"])
    sys.path.insert(0, str(Path(os.environ["DSAI_PLATFORM_ROOT"]) / "backend"))


def recording_pool(w):
    path = (
        Path(os.environ["DSAI_PLATFORM_ROOT"])
        / "tests/unit/platform/test_inventory_dsim_projection.py"
    )
    spec = importlib.util.spec_from_file_location("_initial_writer_pool", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Pool(module.RecordingPool):
        readonly = False
        lose_response = False

        @asynccontextmanager
        async def transaction(self, *, isolation, readonly=False):
            if readonly:
                assert isolation == "repeatable_read"
                previous = copy.deepcopy(self.rows)
                self.readonly = True
                try:
                    yield
                    assert self.rows == previous
                finally:
                    self.readonly = False
            else:
                async with super().transaction(isolation=isolation):
                    yield
                if self.lose_response and self.rows["dsim.tb_io_publication_receipt"]:
                    self.lose_response = False
                    raise OSError("offline commit response lost")

        async def execute(self, sql, *args):
            assert not self.readonly, "read acquired a write lock"
            return await super().execute(sql, *args)

        async def executemany(self, sql, values):
            assert not self.readonly, "read inserted rows"
            return await super().executemany(sql, values)

    return Pool(SimpleNamespace(request=w.request, catalog=w.runtime_kwargs["catalog"]))


def assemble(w, pool, actor, dispatch, rbac, *, factory=False, allow=True, recovery=False):
    from platform_api.services.inventory_dsim_projection import (
        build_inventory_dsim_projection_service,
    )
    from platform_api.services.inventory_target_result_execution import (
        InventoryInitialTargetExecutionService,
        build_inventory_local_initial_execution_service,
    )

    if factory:
        return build_inventory_local_initial_execution_service(
            environment="development",
            deployment=w.runtime.deployment,
            expected_build=w.build,
            artifact_store=w.store,
            target_storage=w.target,
            input_provider=None if recovery else w.provider,
            catalog=w.runtime_kwargs["catalog"],
            rbac_enforcer=rbac,
            projection_pool=pool,
            allow_projection_writes=allow,
        )
    writer = build_inventory_dsim_projection_service(
        pool=pool,
        catalog=w.runtime_kwargs["catalog"],
        artifact_store=w.store,
        deployment=w.runtime.deployment,
        rbac_enforcer=rbac,
        allow_draft_writes=allow,
        target_result_reader=w.runtime.reader.link,
    )
    return InventoryInitialTargetExecutionService(
        runtime=w.runtime, rbac_enforcer=rbac, projection_service=writer
    )


async def roundtrip(root, *, case="success", factory=False):
    from platform_api.services.inventory_dsim_projection import InventoryProjectionServiceError

    w, _, actor, dispatch, rbac = platform_world(root)
    pool = recording_pool(w)
    service = assemble(w, pool, actor, dispatch, rbac, factory=factory, allow=case != "disabled")
    auth = dict(actor=actor, project_id=dispatch.claim.project_id, dispatch=dispatch)
    assert w.store.find_outbox(w.request.engine_run_id, 1) is None
    assert not any(pool.rows.values())
    if case == "disabled":
        with pytest.raises(InventoryProjectionServiceError, match="draft_writes_disabled"):
            await service.execute(**auth, target_input_reference=w.input_ref)
        assert not pool.calls and not any(pool.rows.values())
        return
    if case in {"rollback", "response_lost"}:
        pool.fail_after = "dsim.tb_io_psi_bucket" if case == "rollback" else None
        pool.lose_response = case == "response_lost"
        with pytest.raises(InventoryProjectionServiceError, match="storage_unavailable"):
            await service.execute(**auth, target_input_reference=w.input_ref)
        assert bool(pool.rows["dsim.tb_io_publication_receipt"]) == (case == "response_lost")
        if case == "rollback":
            assert not any(pool.rows.values())
        pool.fail_after = None
        service = assemble(w, pool, actor, dispatch, rbac, factory=factory, recovery=True)
        with patch.object(w.provider, "resolve", side_effect=AssertionError("Source reread")):
            result = await service.recover(**auth, target_input_reference=w.input_ref)
    else:
        result = await service.execute(**auth, target_input_reference=w.input_ref)
    assert result["database_projection"]["replayed"] == (case == "response_lost")
    assert result["database_writes"] == (case != "response_lost")
    assert not result["automatic_publish_allowed"] and not result["automatic_order_allowed"]
    saved = copy.deepcopy(pool.rows)
    bundle = w.store.read_artifact(
        result["result_pointer"]["sources"]["bundle"]["artifact_reference"]
    )
    restart = assemble(w, pool, actor, dispatch, rbac, factory=factory, recovery=True)
    count = len(pool.calls)
    with patch.object(w.provider, "resolve", side_effect=AssertionError("Source reread")):
        with patch.object(
            w.store, "commit_artifacts", side_effect=AssertionError("read wrote Artifact")
        ):
            read = await restart.read(**auth, result_pointer=result["result_pointer"])
    assert pool.rows == saved
    assert all(
        sql.lstrip().startswith("SELECT") and "pg_advisory_xact_lock" not in sql
        for sql, _ in pool.calls[count:]
    )
    assert read["result_pointer"] == result["result_pointer"]
    assert read["bundle"] == result["bundle"] and read["result"] == result["result"]
    assert read["database_writes"] is False
    stored = read["database_projection"]
    assert stored["receipt"] == result["database_projection"]["receipt"]
    assert all(
        r["engine_run_id"] == w.request.engine_run_id
        for rows in stored["tables"].values()
        for r in rows
    )
    counts = {name.split(".")[-1]: len(rows) for name, rows in stored["tables"].items()}
    counts["tb_io_publication_receipt"] = 1
    assert counts == result["projection_row_counts"] == stored["receipt"]["table_row_counts"]
    assert any(
        r["simulation_id"] == "SELECTED_ORDER_PLAN"
        for r in stored["tables"]["dsim.tb_io_psi_bucket"]
    )
    replay = await restart.recover(**auth, target_input_reference=w.input_ref)
    assert replay["database_projection"]["replayed"] and replay["database_writes"] is False
    assert pool.rows == saved and w.store.read_artifact(bundle.reference) == bundle
    # A read-only service can query the same rows with the write switch off.
    readonly = assemble(w, pool, actor, dispatch, rbac, factory=factory, allow=False, recovery=True)

    async def read_permission_only(**kwargs):
        if kwargs["permission_key"] != "engine_report.read":
            raise PermissionError("callback permission not granted")

    with patch.object(rbac, "require_permission", read_permission_only):
        assert (await readonly.read(**auth, result_pointer=result["result_pointer"]))[
            "database_projection"
        ] == stored
        if factory:
            from platform_api.services.inventory_target_result_execution import (
                build_inventory_local_target_result_service,
            )

            reader = build_inventory_local_target_result_service(
                environment="development",
                deployment=w.runtime.deployment,
                expected_build=w.build,
                artifact_store=w.store,
                target_storage=w.target,
                catalog=w.runtime_kwargs["catalog"],
                rbac_enforcer=rbac,
                projection_pool=pool,
            )
            assert (await reader.read(**auth, result_pointer=result["result_pointer"]))[
                "database_projection"
            ] == stored


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["success", "rollback", "response_lost", "disabled"])
async def test_calculation_writer_and_same_run_read(tmp_path, case):
    await roundtrip(tmp_path, case=case)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["not_stored", "missing_row", "receipt_counts", "permission", "ledger"]
)
async def test_read_never_repairs_missing_or_invalid_rows(tmp_path, case):
    from platform_api.services.inventory_dsim_projection import InventoryProjectionServiceError

    w, _, actor, dispatch, rbac = platform_world(tmp_path)
    pool = recording_pool(w)
    service = assemble(w, pool, actor, dispatch, rbac)
    auth = dict(actor=actor, project_id=dispatch.claim.project_id, dispatch=dispatch)
    if case == "not_stored":
        result = await w.runtime.execute(request=w.request, target_input_reference=w.input_ref)
        error = "PROJECTION_NOT_STORED"
    else:
        result = await service.execute(**auth, target_input_reference=w.input_ref)
        if case == "missing_row":
            pool.rows["dsim.tb_io_psi_bucket"].pop()
            error = "PROJECTION_PERSISTED_ROWS_MISMATCH"
        elif case == "receipt_counts":
            row = pool.rows["dsim.tb_io_publication_receipt"][0]
            counts = json.loads(row["table_row_counts"])
            counts["tb_io_item"] += 1
            row["table_row_counts"] = json.dumps(counts)
            error = "PROJECTION_RECEIPT_BINDING_MISMATCH"
        elif case == "ledger":
            pool.authority["claim_hash"] = "0" * 64
            error = "PROJECTION_LEDGER_BINDING_MISMATCH"
        else:
            rbac.denied = True
            error = "denied"
    before, calls = copy.deepcopy(pool.rows), len(pool.calls)
    with patch.object(w.store, "commit_artifacts", side_effect=AssertionError("read wrote")):
        with pytest.raises((InventoryProjectionServiceError, PermissionError), match=error):
            await service.read(**auth, result_pointer=result["result_pointer"])
    assert pool.rows == before
    assert all(
        sql.lstrip().startswith("SELECT") and "pg_advisory_xact_lock" not in sql
        for sql, _ in pool.calls[calls:]
    )


@pytest.mark.asyncio
async def test_block_gate_never_calls_writer(tmp_path):
    from platform_api.services.inventory_dsim_projection import InventoryProjectionServiceError
    from platform_api.services.inventory_target_result_execution import (
        InventoryInitialTargetExecutionService,
    )

    w = initial_world(tmp_path, ReferenceKernel(), fsn_action="BLOCK")
    pool = recording_pool(w)

    class Rbac:
        async def require_permission(self, **kwargs):
            pass

    from platform_api.inventory_engine_run_contract import InventoryEngineExecutionDispatch
    from platform_api.services.inventory_engine_run_service import InventoryEngineRunActor

    dispatch = InventoryEngineExecutionDispatch.model_validate(w.request.to_dict())
    actor = InventoryEngineRunActor(user_id="offline", tenant_id=w.request.tenant_id)
    service = assemble(w, pool, actor, dispatch, Rbac())
    assert isinstance(service, InventoryInitialTargetExecutionService)
    with pytest.raises(
        InventoryProjectionServiceError, match="ITEM_POLICY_RECOMMENDATION_CALCULATION_BLOCKED"
    ):
        await service.execute(
            actor=actor,
            project_id=dispatch.claim.project_id,
            dispatch=dispatch,
            target_input_reference=w.input_ref,
        )
    assert not pool.calls and not any(pool.rows.values())


async def installed_initial_projection_roundtrip(root, *, case):
    await roundtrip(root, case=case, factory=True)

"""Platform approved input service → installed package Source assembly, offline only."""

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from dsio_inventory_engine.infrastructure.sqlite import SqliteInventoryEvidenceUnitOfWork
from tests.integration.test_source_projection_execution_offline import source_fixture, CATALOG
from tests.integration.test_approved_runtime_inputs_offline import report
from tests.integration.test_psi_orchestrator_offline import DEPLOYMENT

pytestmark = pytest.mark.skipif(
    not os.environ.get("DSAI_PLATFORM_ROOT"), reason="Platform root not supplied"
)


def world(tmp_path, *, analysis=False):
    root = Path(os.environ["DSAI_PLATFORM_ROOT"])
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "backend"))
    spec = importlib.util.spec_from_file_location(
        "_source_projection_platform_fixture",
        root / "tests/unit/platform/test_inventory_runtime_input_export.py",
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    request, manifest, build = source_fixture(analysis=analysis)
    data = helper.fixture()
    data["dispatch"], data["manifest"] = request.to_dict(), json.loads(json.dumps(manifest))
    claim = request.to_dict()["claim"]
    doc = data["input_set"]
    for k in (
        "tenant_id",
        "project_id",
        "demand_run_id",
        "planning_cycle_id",
        "planning_cycle_revision_id",
        "scope",
        "plan_id",
        "config_id",
        "config_revision_id",
        "config_hash",
        "strategy_execution_plan",
    ):
        doc[k] = claim[k]
    binding = request.classification_binding
    doc.update(
        classification_snapshot_id=binding["source_snapshot_id"],
        effective_policy_content_hash=binding["source_content_hash"],
        canonical_input_hash=manifest["canonical_input_hash"],
        network_binding=None,
        development_cost_input_body=None,
    )
    math = next(
        r["document"] for r in manifest["resources"] if r["kind"] == "MATHEMATICAL_TEMPLATE"
    )
    canonical = math["mathematical_request"]["recommendation"]["canonical_input"]
    doc["snapshot_bindings"] = {
        k: {
            "snapshot_id": canonical["snapshots"][k]["snapshot_id"],
            "content_hash": canonical["snapshots"][k]["content_hash"],
        }
        for k in doc["snapshot_bindings"]
    }
    source, actor, dispatch = helper.source_service(data)
    from platform_api.services.inventory_source_projection_execution import (
        InventorySourceProjectionExecutionService,
    )

    store = SqliteInventoryEvidenceUnitOfWork(tmp_path / "platform.sqlite")
    kwargs = dict(
        approved_input_export=source,
        artifact_store=store,
        deployment=DEPLOYMENT,
        catalog=json.loads(CATALOG.read_text()),
        expected_build=build,
        rbac_enforcer=source.rbac,
    )
    return InventorySourceProjectionExecutionService(**kwargs), source, actor, dispatch, kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize("analysis", [False, True])
async def test_actual_platform_source_service_and_recovery(tmp_path, analysis):
    service, source, actor, dispatch, kwargs = world(tmp_path, analysis=analysis)
    result = await service.execute(
        actor=actor, project_id=dispatch.claim.project_id, dispatch=dispatch, report=report
    )
    assert len(source.authority.calls) == 1 and len(source.catalog.calls) == 1
    from platform_api.services.inventory_source_projection_execution import (
        InventorySourceProjectionExecutionService,
    )

    class Unavailable:
        async def resolve(self, **kwargs):
            raise AssertionError("Source must not be reread after sealing")

    kwargs["approved_input_export"] = Unavailable()
    restarted = InventorySourceProjectionExecutionService(**kwargs)
    assert (
        await restarted.execute(
            actor=actor, project_id=dispatch.claim.project_id, dispatch=dispatch, report=report
        )
        == result
    )
    assert (
        await restarted.verify(
            actor=actor,
            project_id=dispatch.claim.project_id,
            dispatch=dispatch,
            execution_reference=result["artifact_reference"],
            execution_hash=result["content_hash"],
        )
        == result
    )
    assert len(source.authority.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["scope", "permission", "build", "claim"])
async def test_scope_permission_build_or_approval_mismatch_cannot_execute(tmp_path, case):
    service, source, actor, dispatch, _ = world(tmp_path)
    if case == "scope":
        actor = replace_actor(actor)
    elif case == "permission":

        async def deny(**kwargs):
            raise PermissionError("denied")

        service._rbac.require_permission = deny
    elif case == "build":
        service._build["content_hash"] = "0" * 64
    else:
        source.authority.row["claim_hash"] = "0" * 64
    from platform_api.services.inventory_engine_run_service import InventoryEngineRunServiceError

    with pytest.raises((InventoryEngineRunServiceError, PermissionError)):
        await service.execute(
            actor=actor, project_id=dispatch.claim.project_id, dispatch=dispatch, report=report
        )
    assert service._store.find_outbox(str(dispatch.engine_run_id), 1) is None
    if case != "claim":
        assert not source.authority.calls


def replace_actor(actor):
    from dataclasses import replace

    return replace(actor, tenant_id="other-tenant")


@pytest.mark.asyncio
async def test_writer_receives_only_verified_exact_pointer(tmp_path):
    service, _, actor, dispatch, _ = world(tmp_path)
    result = await service.execute(
        actor=actor, project_id=dispatch.claim.project_id, dispatch=dispatch, report=report
    )
    calls = []

    class OfflineWriter:
        async def write(self, **kwargs):
            calls.append(kwargs)
            return {"offline_only": True}

    service._projection_service = OfflineWriter()
    kwargs = dict(
        actor=actor,
        project_id=dispatch.claim.project_id,
        dispatch=dispatch,
        execution_reference=result["artifact_reference"],
        execution_hash=result["content_hash"],
    )
    assert await service.write_verified(**kwargs) == {"offline_only": True}
    p = result["document"]["projection"]
    assert calls[0]["projection_reference"] == p["artifact_reference"]
    assert calls[0]["projection_manifest_hash"] == p["content_hash"]
    kwargs["execution_hash"] = "0" * 64
    from platform_api.services.inventory_engine_run_service import InventoryEngineRunServiceError

    with pytest.raises(InventoryEngineRunServiceError):
        await service.write_verified(**kwargs)
    assert len(calls) == 1

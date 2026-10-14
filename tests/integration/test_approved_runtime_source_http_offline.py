"""Platform approved-source API → actual Runtime resolvers/calculation/artifact recovery."""

import importlib.util
import json
import os
import sys
from pathlib import Path

import httpx
import pytest

from dsio_inventory_engine.infrastructure.http.platform_lifecycle import (
    PlatformLifecycleHttpSettings,
)
from dsio_inventory_engine.infrastructure.http.runtime_inputs import PlatformRuntimeInputHttpClient
from dsio_inventory_engine.infrastructure.http.trade_cost_projection import (
    PlatformTradeCostProjectionHttpClient,
)
from dsio_inventory_engine.infrastructure.runtime_input_files import (
    LocalRuntimeInputManifestCatalog,
)
from dsio_inventory_engine.infrastructure.sqlite import SqliteInventoryEvidenceUnitOfWork
from dsio_inventory_engine.inventory_contracts.runtime import InventoryRuntimeExecutionRequest
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from dsio_inventory_engine.run_inventory.runtime_composition import (
    build_approved_inventory_runtime_pipeline,
)
from tests.integration.test_approved_runtime_inputs_offline import input_manifest, report
from tests.integration.test_psi_orchestrator_offline import DEPLOYMENT

pytestmark = pytest.mark.skipif(
    not os.environ.get("DSAI_PLATFORM_ROOT"), reason="Platform source not explicitly provided"
)


@pytest.mark.asyncio
async def test_actual_source_api_registered_cost_ppo_stress_and_recovery(tmp_path):
    root = Path(os.environ["DSAI_PLATFORM_ROOT"])
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "backend"))
    spec = importlib.util.spec_from_file_location(
        "_offline_runtime_input_export_tests",
        root / "tests/unit/platform/test_inventory_runtime_input_export.py",
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    data = json.loads((root / "tests/fixtures/inventory_approved_runtime_inputs.json").read_text())
    runtime = InventoryRuntimeExecutionRequest.from_dict(data["dispatch"])
    generated, manifest, implementations, cost = input_manifest(landed=True)
    assert generated == runtime and json.loads(json.dumps(manifest)) == data["manifest"]
    catalog = LocalRuntimeInputManifestCatalog(tmp_path)
    catalog.publish(manifest)
    from platform_api.services.inventory_runtime_input_export import LocalRuntimeInputManifestReader

    service, _, _ = helper.source_service(data)
    service.catalog = LocalRuntimeInputManifestReader(tmp_path)
    app = helper.source_app(service)
    projection_calls = []

    @app.get(
        "/api/v1/engine-studio/inventory/trade-cost/revision-sets/{revision_set_id}/projection"
    )
    async def projection(
        revision_set_id: str, project_id: str, expected_content_hash: str, valuation_date: str
    ):
        binding = runtime.trade_cost_binding
        assert (revision_set_id, project_id, expected_content_hash, valuation_date) == (
            binding["source_snapshot_id"],
            runtime.project_id,
            binding["source_content_hash"],
            runtime.canonical_context_binding["master_as_of_date"],
        )
        projection_calls.append(revision_set_id)
        return cost.projection.to_dict()

    async with httpx.AsyncClient(
        base_url="http://offline.platform", transport=httpx.ASGITransport(app=app)
    ) as client:
        settings = PlatformLifecycleHttpSettings(base_url="http://offline.platform")
        source = PlatformRuntimeInputHttpClient(settings, client=client)
        evidence = SqliteInventoryEvidenceUnitOfWork(tmp_path / "evidence.sqlite")
        pipeline = build_approved_inventory_runtime_pipeline(
            deployment=DEPLOYMENT,
            input_provider=source,
            implementations=implementations,
            evidence_repository=evidence,
            cost_projection=PlatformTradeCostProjectionHttpClient(settings, client=client),
        )
        result = await pipeline.execute(runtime, report)
        assert all(
            c["status"] == "SUCCEEDED" for c in result.inventory_result_bundle["result_children"]
        )
        assert result.run_result_manifest is not None
        assert len(service.authority.calls) == 2 and len(projection_calls) == 1
        outbox = evidence.find_outbox(runtime.engine_run_id, 1).message.payload()
        analysis = evidence.read_artifact(outbox["policy_analysis_reference"]).document()
        actual = next(c for c in analysis["children"] if c["role"] == "ACTUAL_INPUT")
        assert (
            evidence.read_artifact(actual["artifact_reference"]).document()[
                "policy_analysis_spec_content_hash"
            ]
            == runtime.strategy_execution_plan["policy_analysis_spec"]["content_hash"]
        )

        # Reject another attempt or revoked source approval; never search latest.
        service.authority.row["attempt_no"] = 99
        with pytest.raises(InventoryInputError, match="PLATFORM_RUNTIME_INPUT_REJECTED"):
            await source.resolve(runtime)
        reads = len(service.authority.calls)
        assert await pipeline.execute(runtime, report) == result
        assert len(service.authority.calls) == reads and len(projection_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ['{"a":1,"a":2}', '{"a":NaN}', "{}"])
async def test_http_rejects_duplicate_nonfinite_or_missing_manifest(body):
    runtime, _, _, _ = input_manifest()
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=body))
    async with httpx.AsyncClient(base_url="http://offline.platform", transport=transport) as client:
        source = PlatformRuntimeInputHttpClient(
            PlatformLifecycleHttpSettings("http://offline.platform"), client=client
        )
        with pytest.raises(InventoryInputError, match="PLATFORM_RUNTIME_INPUT_INVALID"):
            await source.resolve(runtime)

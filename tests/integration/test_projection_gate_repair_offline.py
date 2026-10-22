"""Repair sealed row permissions, without recalculating PSI or touching PostgreSQL."""

import copy
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from dsio_inventory_engine.infrastructure.runtime_build import installed_runtime_build
from dsio_inventory_engine.infrastructure.sqlite.inventory_projection import (
    SqliteOfflineProjectionUnitOfWork,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from dsio_inventory_engine.inventory_evidence.contracts import ArtifactObject
from dsio_inventory_engine.inventory_evidence.projection import (
    WriteDsimProjectionUseCase,
    binding_identity,
)
from dsio_inventory_engine.inventory_evidence.projection_gate_repair import (
    RepairDsimProjectionGatesUseCase,
)
from dsio_inventory_engine.inventory_evidence.projection_source import (
    DsimSourceProjector,
    apply_claim_action_gates,
)
from tests.integration.test_source_projection_execution_offline import assembly
from tests.integration.test_approved_runtime_inputs_offline import report


@pytest.mark.parametrize(
    "run_publish,run_order,item_publish,item_order,expected",
    [
        (False, False, True, True, (False, False)),
        (True, False, True, True, (True, False)),
        (True, True, True, True, (True, True)),
        (True, True, False, False, (False, False)),
        (True, True, True, False, (True, False)),
        (True, True, False, True, (False, False)),
        (None, None, True, True, (False, False)),
        (1, 1, True, True, (False, False)),
    ],
)
def test_run_and_item_gate_conjunction(run_publish, run_order, item_publish, item_order, expected):
    result = apply_claim_action_gates(
        {
            "expected_automatic_publish_allowed": run_publish,
            "expected_automatic_order_allowed": run_order,
        },
        publish_allowed=item_publish,
        order_allowed=item_order,
    )
    assert tuple(result.values()) == expected


async def frozen_legacy_world(tmp_path, *, non_gate_change=False):
    request, _, build, store, runtime = assembly(tmp_path)
    assert request.to_dict()["claim"]["expected_automatic_publish_allowed"] is False
    original_build = DsimSourceProjector.build

    def legacy_build(self, **kwargs):
        result = original_build(self, **kwargs)
        # Fault-injected historical package: accepted row flags contradict the
        # review-only Run. Do not alter the underlying classification or PSI.
        for row in result["tables"]["dsim.tb_io_replenishment_proposal"]:
            if row["disposition"] in {"ACCEPTED", "ADJUSTED"}:
                row["automatic_publish_allowed"] = True
                row["automatic_order_allowed"] = True
                row["row_content_hash"] = digest(
                    {k: v for k, v in row.items() if k != "row_content_hash"}
                )
        if non_gate_change:
            row = result["tables"]["dsim.tb_io_psi_bucket"][0]
            row["eoh_qty"] = "999"
            row["row_content_hash"] = digest(
                {k: v for k, v in row.items() if k != "row_content_hash"}
            )
        return result

    # Reproduce the exact former missing-conjunction behavior only in this fixture.
    with (
        patch(
            "dsio_inventory_engine.inventory_evidence.projection_source.apply_claim_action_gates",
            side_effect=lambda claim, **flags: {
                "automatic_publish_allowed": flags["publish_allowed"],
                "automatic_order_allowed": flags["order_allowed"],
            },
        ),
        patch.object(DsimSourceProjector, "build", legacy_build),
    ):
        result = await runtime.execute(request=request, report=report, chunk_size=2)
    repair = RepairDsimProjectionGatesUseCase(
        projector=runtime.producer.projector,
        store=store,
        expected_calculation_build=build,
        expected_projection_build=installed_runtime_build(),
    )
    return request, store, runtime, result, repair


def no_calculation():
    stack = ExitStack()
    for target in (
        "dsio_inventory_engine.run_inventory.psi_orchestrator.RunPsiBundleUseCase.execute",
        "dsio_inventory_engine.simulate_inventory.application.baseline.RunPsiSimulationUseCase.execute",
        "dsio_inventory_engine.simulate_inventory.application.baseline._roll_forward",
        "dsio_inventory_engine.recommend_replenishment.application.mathematical.RunMathematicalReplenishmentUseCase.execute",
        "dsio_inventory_engine.recommend_replenishment.application.run.RunRecommendedPsiUseCase.execute",
        "dsio_inventory_engine.recommend_replenishment.application.run.simulate",
    ):
        stack.enter_context(patch(target, side_effect=AssertionError("must not recalculate PSI")))
    return stack


@pytest.mark.asyncio
async def test_same_run_derived_pointer_preserves_psi_sources_and_replays_offline(tmp_path):
    request, store, runtime, source, repair = await frozen_legacy_world(tmp_path)
    args = dict(
        request=request,
        source_reference=source["artifact_reference"],
        source_hash=source["content_hash"],
    )
    old_pointer = source["document"]["projection"]
    old_manifest, old_tables = runtime.verifier._read_integrity(
        request=request,
        reference=old_pointer["artifact_reference"],
        expected_hash=old_pointer["content_hash"],
    )
    assert any(
        r["automatic_publish_allowed"] for r in old_tables["dsim.tb_io_replenishment_proposal"]
    )
    refs = [
        source["artifact_reference"],
        old_pointer["artifact_reference"],
        old_manifest["bundle_reference"],
    ]
    refs += [r["artifact_reference"] for r in old_manifest["source_artifacts"]]
    refs += [r["artifact_reference"] for t in old_manifest["tables"].values() for r in t["chunks"]]
    originals = {ref: store.read_artifact(ref) for ref in refs}
    with pytest.raises(InventoryInputError, match="PROJECTION_SOURCE_ROWS_MISMATCH"):
        runtime.verifier.execute(
            request=request,
            reference=old_pointer["artifact_reference"],
            expected_hash=old_pointer["content_hash"],
        )
    with no_calculation():
        result = repair.execute(**args, chunk_size=2)
        pointer = result["document"]["projection"]
        assert result["document"]["changed_proposal_rows"] > 0
        assert pointer["content_hash"] != old_pointer["content_hash"]
        assert result["document"]["binding"] == binding_identity(request)
        verified = repair.verifier.execute(
            request=request,
            reference=pointer["artifact_reference"],
            expected_hash=pointer["content_hash"],
        )
        assert verified.tables == repair._restricted(request, old_tables)[0]
        assert all(
            not r["automatic_publish_allowed"] and not r["automatic_order_allowed"]
            for r in verified.tables["dsim.tb_io_replenishment_proposal"]
        )
        assert repair.execute(**args, chunk_size=2) == {**result, "replayed": True}
        assert (
            repair.verify(
                request=request,
                reference=result["artifact_reference"],
                expected_hash=result["content_hash"],
            )["document"]
            == result["document"]
        )
        # SQLite is the existing Offline UoW emulator, not development PostgreSQL.
        uow = SqliteOfflineProjectionUnitOfWork(
            tmp_path / "rows.sqlite",
            catalog=runtime.producer.projector.catalog,
            authority=binding_identity(request),
        )
        writer = WriteDsimProjectionUseCase(verifier=repair.verifier, unit_of_work=uow)
        kwargs = dict(
            request=request,
            reference=pointer["artifact_reference"],
            expected_hash=pointer["content_hash"],
        )
        first = await writer.execute(**kwargs)
        retry = await writer.execute(**kwargs)
        assert not first["replayed"] and retry["replayed"]
        assert first["receipt"] == retry["receipt"]
        async with uow.transaction(request) as tx:
            assert await tx.read_tables() == verified.tables
    assert all(store.read_artifact(ref) == obj for ref, obj in originals.items())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["source_hash", "calculation_build", "projection_build", "projection_fields"]
)
async def test_wrong_source_or_either_build_cannot_prepare(tmp_path, case):
    request, store, _, source, repair = await frozen_legacy_world(tmp_path)
    expected = "PROJECTION_REPAIR_SOURCE_POINTER_MISMATCH"
    source_hash = source["content_hash"]
    if case == "source_hash":
        source_hash = "f" * 64
    elif case == "calculation_build":
        repair.calculation_build["content_hash"] = "f" * 64
        expected = "PROJECTION_REPAIR_SOURCE_BINDING_MISMATCH"
    elif case == "projection_build":
        repair.projection_build["content_hash"] = "f" * 64
        expected = "PROJECTION_REPAIR_INSTALLED_BUILD_MISMATCH"
    else:
        doc = copy.deepcopy(source["document"])
        doc["projection"]["unknown"] = True
        doc["content_hash"] = digest({k: v for k, v in doc.items() if k != "content_hash"})
        forged = ArtifactObject.from_document(
            reference=source["artifact_reference"],
            contract_key="inventory.dsim_source_execution",
            document=doc,
        )
        original_read = store.read_artifact
        store.read_artifact = lambda ref: forged if ref == forged.reference else original_read(ref)
        source_hash = forged.content_hash
        expected = "PROJECTION_REPAIR_ORIGINAL_POINTER_INVALID"
    with pytest.raises(InventoryInputError, match=expected):
        repair.execute(
            request=request, source_reference=source["artifact_reference"], source_hash=source_hash
        )


@pytest.mark.asyncio
async def test_non_gate_change_cannot_be_laundered_as_gate_repair(tmp_path):
    request, _, _, source, repair = await frozen_legacy_world(tmp_path, non_gate_change=True)
    with pytest.raises(InventoryInputError, match="PROJECTION_REPAIR_NON_GATE_CHANGE"):
        repair.execute(
            request=request,
            source_reference=source["artifact_reference"],
            source_hash=source["content_hash"],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["projection_build", "changed_proposal_rows", "profile"])
async def test_resealed_repair_metadata_cannot_override_authority(tmp_path, field):
    request, store, _, source, repair = await frozen_legacy_world(tmp_path)
    result = repair.execute(
        request=request,
        source_reference=source["artifact_reference"],
        source_hash=source["content_hash"],
    )
    doc = copy.deepcopy(result["document"])
    doc[field] = "forged"
    doc["content_hash"] = digest({k: v for k, v in doc.items() if k != "content_hash"})
    obj = ArtifactObject.from_document(
        reference=result["artifact_reference"].rsplit(":", 1)[0] + ":" + doc["content_hash"],
        contract_key="inventory.dsim_projection_gate_repair",
        document=doc,
    )
    store.commit_artifacts((obj,))
    with pytest.raises(InventoryInputError, match="PROJECTION_REPAIR_REPLAY_MISMATCH"):
        repair.verify(request=request, reference=obj.reference, expected_hash=obj.content_hash)


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", ["receipt", "partial_rows"])
async def test_repair_cannot_replace_previously_stored_run_rows(tmp_path, existing):
    request, _, runtime, source, repair = await frozen_legacy_world(tmp_path)
    result = repair.execute(
        request=request,
        source_reference=source["artifact_reference"],
        source_hash=source["content_hash"],
    )
    pointer = result["document"]["projection"]
    uow = SqliteOfflineProjectionUnitOfWork(
        tmp_path / "existing.sqlite",
        catalog=runtime.producer.projector.catalog,
        authority=binding_identity(request),
    )
    async with uow.transaction(request) as tx:
        if existing == "receipt":
            await tx.insert_receipt(
                {
                    "engine_run_id": request.engine_run_id,
                    "projection_content_hash": source["document"]["projection"][
                        "projection_content_hash"
                    ],
                    "bundle_content_hash": source["document"]["result_bundle_content_hash"],
                    "projection_contract_version": "1.0.0",
                }
            )
        else:
            verified = repair.verifier.execute(
                request=request,
                reference=pointer["artifact_reference"],
                expected_hash=pointer["content_hash"],
            )
            await tx.insert_rows(
                "dsim.tb_io_plan_context", verified.tables["dsim.tb_io_plan_context"]
            )
        before = (await tx.read_receipt(), await tx.read_tables())
    writer = WriteDsimProjectionUseCase(verifier=repair.verifier, unit_of_work=uow)
    code = (
        "PROJECTION_WRITE_ONCE_CONFLICT"
        if existing == "receipt"
        else "PROJECTION_PARTIAL_ROWS_WITHOUT_RECEIPT"
    )
    with pytest.raises(InventoryInputError, match=code):
        await writer.execute(
            request=request,
            reference=pointer["artifact_reference"],
            expected_hash=pointer["content_hash"],
        )
    async with uow.transaction(request) as tx:
        assert (await tx.read_receipt(), await tx.read_tables()) == before

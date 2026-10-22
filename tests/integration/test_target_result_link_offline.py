"""Actual contract assembly, temporary SQLite Artifact emulator, no development DB."""

from unittest.mock import patch
import copy

import pytest

from dsio_inventory_engine.infrastructure.sqlite import SqliteInventoryEvidenceUnitOfWork
from dsio_inventory_engine.infrastructure.sqlite.inventory_projection import (
    SqliteOfflineProjectionUnitOfWork,
)
from dsio_inventory_engine.infrastructure.target_pipeline_artifacts import (
    LocalTargetPipelineArtifactStore,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from dsio_inventory_engine.inventory_evidence.application import (
    ReadAndVerifyInventoryResultBundleUseCase,
)
from dsio_inventory_engine.inventory_evidence.projection import (
    PersistDsimProjectionUseCase,
    VerifyDsimProjectionUseCase,
    WriteDsimProjectionUseCase,
    binding_identity,
)
from dsio_inventory_engine.inventory_evidence.projection_source import (
    DsimSourceProjector,
    normalize_tables,
)
from dsio_inventory_engine.inventory_evidence.target_result_link import TargetResultLinkUseCase
from dsio_inventory_engine.recommend_replenishment.application.target_durable import (
    RunDurableTargetPolicyPipelineUseCase,
)

from tests.support.target_result_link_fixtures import link_world
from tests.unit.test_target_execution import ReferenceKernel
from tests.support.calculation_adapter_fixtures import DEPLOYMENT


def persist(w):
    return w.link.execute(
        request=w.request,
        bundle_reference=w.persisted.bundle_reference,
        expected_input_reference=w.target_repo.lookup(w.request.engine_run_id, "INPUT"),
    )


def fresh_link(w, root):
    store = SqliteInventoryEvidenceUnitOfWork(root / "artifacts.sqlite")
    target = RunDurableTargetPolicyPipelineUseCase(
        DEPLOYMENT, ReferenceKernel(), LocalTargetPipelineArtifactStore(w.target_root)
    )
    return TargetResultLinkUseCase(deployment=DEPLOYMENT, target_storage=target, repository=store)


def assert_internal_foreign_keys(catalog, tables):
    for spec in catalog["tables"]:
        if spec["name"] not in tables:
            continue
        for fk in spec["foreign_keys"]:
            if fk["table"] not in tables:
                continue
            keys = {tuple(row[k] for k in fk["target"]) for row in tables[fk["table"]]}
            for row in tables[spec["name"]]:
                values = tuple(row[k] for k in fk["columns"])
                if all(v is not None for v in values):
                    assert values in keys, (spec["name"], fk, values)


@pytest.mark.parametrize(
    "mode,count",
    [("ABC_V1", 2), ("ABC_XYZ_SHARED_BUDGET", 32), ("LEGACY_STYLE", 32), ("WIDE_REFERENCE", 88)],
)
def test_all_candidates_selection_plan_and_original_bundle_are_linked(tmp_path, mode, count):
    w = link_world(tmp_path, ReferenceKernel(), mode=mode)
    run_id = w.request.engine_run_id
    original = w.store.read_artifact(w.persisted.bundle_reference)
    outbox = w.store.find_outbox(run_id, w.request.value["attempt_no"])
    ptr = w.link.execute(
        request=w.request,
        bundle_reference=w.persisted.bundle_reference,
        expected_input_reference=w.target_repo.lookup(run_id, "INPUT"),
    )
    assert ptr["verification_status"] == "RECOMPUTED_VERIFIED"
    linked = w.link.read(
        request=w.request,
        bundle_reference=w.persisted.bundle_reference,
        expected_link_hash=ptr["content_hash"],
    )
    assert linked.result["search_result"]["content_hash"] == linked.source_artifacts[0].content_hash
    assert (
        linked.result["selection_result"]["content_hash"] == linked.source_artifacts[1].content_hash
    )
    projection = w.producer.execute(
        request=w.request, bundle_reference=w.persisted.bundle_reference, chunk_size=2
    )
    verified = w.verifier.execute(
        request=w.request,
        reference=projection["artifact_reference"],
        expected_hash=projection["content_hash"],
    )
    rows = verified.tables
    assert_internal_foreign_keys(w.catalog, rows)
    assert len(rows["dsim.tb_io_policy_candidate"]) == count
    assert (
        len(rows["dsim.tb_io_policy_selection"])
        == linked.artifact.document()["counts"]["selection_count"]
    )
    assert any(r["simulation_id"] == "SELECTED_ORDER_PLAN" for r in rows["dsim.tb_io_psi_bucket"])
    proposals = [
        r
        for r in rows["dsim.tb_io_replenishment_proposal"]
        if r["simulation_id"] == "SELECTED_ORDER_PLAN"
    ]
    assert len(proposals) == len(linked.result["order_plan"]["decision_evidence"])
    assert all(
        not r["automatic_publish_allowed"] and not r["automatic_order_allowed"] for r in proposals
    )
    assert w.store.read_artifact(w.persisted.bundle_reference) == original
    assert w.store.find_outbox(run_id, w.request.value["attempt_no"]) == outbox
    assert w.link.execute(
        request=w.request,
        bundle_reference=w.persisted.bundle_reference,
        expected_input_reference=w.target_repo.lookup(run_id, "INPUT"),
    )["replayed"]


def test_sql_catalog_fix_preserves_business_ids_rows_and_hashes_and_keeps_old_artifacts(tmp_path):
    w = link_world(tmp_path, ReferenceKernel())
    persist(w)
    original = w.store.read_artifact(w.persisted.bundle_reference)
    linked = w.store.read_artifact(w.link.reference(w.request))
    old_catalog = copy.deepcopy(w.catalog)
    psi = next(t for t in old_catalog["tables"] if t["name"] == "dsim.tb_io_psi_bucket")
    assert "SELECTED_ORDER_PLAN" in psi["checks"][0]
    psi["checks"][0] = (
        "(candidate_id IS NULL AND simulation_id = 'BASE') OR "
        "(candidate_id IS NOT NULL AND simulation_id = candidate_id)"
    )
    old_projector = DsimSourceProjector(
        catalog=old_catalog,
        bundle_verifier=ReadAndVerifyInventoryResultBundleUseCase(DEPLOYMENT, w.store),
        target_result_reader=w.link,
    )
    old_pointer = PersistDsimProjectionUseCase(projector=old_projector, store=w.store).execute(
        request=w.request, bundle_reference=w.persisted.bundle_reference
    )
    old_object = w.store.read_artifact(old_pointer["artifact_reference"])
    new_pointer = w.producer.execute(
        request=w.request, bundle_reference=w.persisted.bundle_reference
    )
    new_object = w.store.read_artifact(new_pointer["artifact_reference"])
    old = VerifyDsimProjectionUseCase(projector=old_projector, store=w.store).execute(
        request=w.request,
        reference=old_pointer["artifact_reference"],
        expected_hash=old_pointer["content_hash"],
    )
    new = w.verifier.execute(
        request=w.request,
        reference=new_pointer["artifact_reference"],
        expected_hash=new_pointer["content_hash"],
    )
    assert old.tables == new.tables
    assert old_pointer["projection_content_hash"] == new_pointer["projection_content_hash"]
    assert old_object.document()["binding"] == new_object.document()["binding"]
    assert old_object.document()["catalog_hash"] == digest(old_catalog)
    assert new_object.document()["catalog_hash"] == digest(w.catalog)
    assert old_pointer["content_hash"] != new_pointer["content_hash"]
    assert w.store.read_artifact(old_pointer["artifact_reference"]) == old_object
    assert w.store.read_artifact(w.persisted.bundle_reference) == original
    assert w.store.read_artifact(w.link.reference(w.request)) == linked
    with pytest.raises(InventoryInputError, match="PROJECTION_CONTRACT_MISMATCH"):
        w.verifier.execute(
            request=w.request,
            reference=old_pointer["artifact_reference"],
            expected_hash=old_pointer["content_hash"],
        )


@pytest.mark.parametrize(
    "phase", ["before_link_commit", "after_link_commit", "after_projection_commit"]
)
def test_restart_after_interrupted_link_or_projection_preserves_parent_and_recovers(
    tmp_path, phase
):
    w = link_world(tmp_path, ReferenceKernel())
    original = w.store.read_artifact(w.persisted.bundle_reference)
    commit = w.store.commit_artifacts

    def interrupted(artifacts):
        if phase == "before_link_commit":
            raise OSError("injected interrupted commit")
        commit(artifacts)
        raise OSError("injected interrupted commit")

    if phase != "after_projection_commit":
        with patch.object(w.store, "commit_artifacts", side_effect=interrupted):
            with pytest.raises(OSError):
                persist(w)
    else:
        persist(w)
        with patch.object(w.store, "commit_artifacts", side_effect=interrupted):
            with pytest.raises(OSError):
                w.producer.execute(request=w.request, bundle_reference=w.persisted.bundle_reference)
    link = fresh_link(w, tmp_path)
    ptr = link.recover(
        request=w.request,
        bundle_reference=w.persisted.bundle_reference,
        expected_input_reference=w.target_repo.lookup(w.request.engine_run_id, "INPUT"),
    )
    assert ptr["replayed"] == (phase != "before_link_commit")
    projector = DsimSourceProjector(
        catalog=w.catalog,
        bundle_verifier=ReadAndVerifyInventoryResultBundleUseCase(DEPLOYMENT, link.repository),
        target_result_reader=link,
    )
    producer = PersistDsimProjectionUseCase(projector=projector, store=link.repository)
    result = producer.execute(request=w.request, bundle_reference=w.persisted.bundle_reference)
    verified = VerifyDsimProjectionUseCase(projector=projector, store=link.repository).execute(
        request=w.request,
        reference=result["artifact_reference"],
        expected_hash=result["content_hash"],
    )
    assert verified.tables["dsim.tb_io_policy_selection"]
    assert link.repository.read_artifact(w.persisted.bundle_reference) == original


@pytest.mark.parametrize("action", ["REVIEW", "BLOCK"])
def test_fsn_gates_not_selected_items_and_failed_candidates_keep_original_reasons(tmp_path, action):
    if action == "BLOCK":
        with pytest.raises(
            InventoryInputError, match="ITEM_POLICY_RECOMMENDATION_CALCULATION_BLOCKED"
        ):
            link_world(tmp_path, ReferenceKernel(), mode="LEGACY_STYLE", fsn_action=action)
        assert not (tmp_path / "artifacts.sqlite").exists()
        return
    w = link_world(tmp_path, ReferenceKernel(), mode="LEGACY_STYLE", fsn_action=action)
    persist(w)
    source = w.projector.build(request=w.request, bundle_reference=w.persisted.bundle_reference)
    rows = source["tables"]
    linked = w.link.read(request=w.request, bundle_reference=w.persisted.bundle_reference)
    selected = [
        r
        for r in rows["dsim.tb_io_replenishment_proposal"]
        if r["simulation_id"] == "SELECTED_ORDER_PLAN"
    ]
    assert all(
        not r["automatic_publish_allowed"] and not r["automatic_order_allowed"] for r in selected
    )
    if action == "BLOCK":
        assert not selected
        assert linked.result["order_plan"]["not_selected_item_count"] == 1
        assert {r["status"] for r in rows["dsim.tb_io_policy_selection"]} == {"NOT_SELECTED"}
    else:
        assert selected and all(r["order_action"] == "REVIEW" for r in selected)
    assert_internal_foreign_keys(w.catalog, rows)


def test_all_failed_catalog_keeps_diagnostic_and_has_no_fake_evaluation(tmp_path):
    w = link_world(tmp_path, ReferenceKernel(), mode="LEGACY_STYLE", all_failed=True)
    persist(w)
    rows = w.projector.build(request=w.request, bundle_reference=w.persisted.bundle_reference)[
        "tables"
    ]
    assert len(rows["dsim.tb_io_policy_candidate"]) == 32
    assert not rows["dsim.tb_io_policy_evaluation"]
    assert (
        len([r for r in rows["dsim.tb_io_replenishment_diagnostic"] if r["severity"] == "WARNING"])
        >= 32
    )
    assert not [
        r
        for r in rows["dsim.tb_io_replenishment_proposal"]
        if r["simulation_id"] == "SELECTED_ORDER_PLAN"
    ]


@pytest.mark.parametrize("mode,count", [("ABC_V1", 2), ("LEGACY_STYLE", 34)])
def test_existing_analysis_is_deduplicated_or_preserved_separately(tmp_path, mode, count):
    w = link_world(tmp_path, ReferenceKernel(), mode=mode, with_analysis=True)
    persist(w)
    rows = w.projector.build(request=w.request, bundle_reference=w.persisted.bundle_reference)[
        "tables"
    ]
    assert len(rows["dsim.tb_io_policy_candidate"]) == count
    assert_internal_foreign_keys(w.catalog, rows)


def test_unwired_projector_is_unchanged_and_cannot_validate_enriched_projection(tmp_path):
    w = link_world(tmp_path, ReferenceKernel())
    base = DsimSourceProjector(
        catalog=w.catalog,
        bundle_verifier=ReadAndVerifyInventoryResultBundleUseCase(DEPLOYMENT, w.store),
    )
    before = base.build(request=w.request, bundle_reference=w.persisted.bundle_reference)
    persist(w)
    assert base.build(request=w.request, bundle_reference=w.persisted.bundle_reference) == before
    pointer = w.producer.execute(request=w.request, bundle_reference=w.persisted.bundle_reference)
    with pytest.raises(InventoryInputError, match="PROJECTION_SOURCE_ROWS_MISMATCH"):
        VerifyDsimProjectionUseCase(projector=base, store=w.store).execute(
            request=w.request,
            reference=pointer["artifact_reference"],
            expected_hash=pointer["content_hash"],
        )


@pytest.mark.asyncio
async def test_local_projection_transaction_rolls_back_then_retries_without_parent_mutation(
    tmp_path,
):
    w = link_world(tmp_path, ReferenceKernel())
    persist(w)
    pointer = w.producer.execute(request=w.request, bundle_reference=w.persisted.bundle_reference)
    uow = SqliteOfflineProjectionUnitOfWork(
        tmp_path / "rows.sqlite", catalog=w.catalog, authority=binding_identity(w.request)
    )
    writer = WriteDsimProjectionUseCase(verifier=w.verifier, unit_of_work=uow)

    class Interrupted:
        def transaction(self, request):
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def fail():
                async with uow.transaction(request) as tx:
                    original = tx.insert_rows

                    async def stop(table, rows):
                        await original(table, rows)
                        raise OSError("injected partial rows")

                    tx.insert_rows = stop
                    yield tx

            return fail()

    with pytest.raises(OSError):
        await WriteDsimProjectionUseCase(verifier=w.verifier, unit_of_work=Interrupted()).execute(
            request=w.request,
            reference=pointer["artifact_reference"],
            expected_hash=pointer["content_hash"],
        )
    async with uow.transaction(w.request) as tx:
        assert await tx.read_receipt() is None
        assert not any((await tx.read_tables()).values())
    first = await writer.execute(
        request=w.request,
        reference=pointer["artifact_reference"],
        expected_hash=pointer["content_hash"],
    )
    second = await writer.execute(
        request=w.request,
        reference=pointer["artifact_reference"],
        expected_hash=pointer["content_hash"],
    )
    assert not first["replayed"] and second["replayed"]
    assert first["receipt"] == second["receipt"]


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_candidate",
        "missing_plan_row",
        "quantity",
        "arrival",
        "permissions",
        "missing_link_source",
    ],
)
def test_fully_resealed_projection_still_requires_original_target_graph(tmp_path, mutation):
    w = link_world(tmp_path, ReferenceKernel())
    persist(w)
    source = copy.deepcopy(
        w.projector.build(request=w.request, bundle_reference=w.persisted.bundle_reference)
    )
    tables = source["tables"]
    if mutation == "missing_candidate":
        tables["dsim.tb_io_policy_candidate"].pop()
    elif mutation == "missing_plan_row":
        row = next(
            r
            for r in tables["dsim.tb_io_psi_bucket"]
            if r["simulation_id"] == "SELECTED_ORDER_PLAN"
        )
        tables["dsim.tb_io_psi_bucket"].remove(row)
    elif mutation == "missing_link_source":
        source["source_artifacts"] = [
            p
            for p in source["source_artifacts"]
            if p["artifact_reference"] != w.link.reference(w.request)
        ]
    else:
        row = next(
            r
            for r in tables["dsim.tb_io_replenishment_proposal"]
            if r["simulation_id"] == "SELECTED_ORDER_PLAN"
        )
        if mutation == "quantity":
            row["constrained_order_qty"] = "999"
        elif mutation == "arrival":
            row["arrival_date"] = "2026-10-18"
        else:
            row["automatic_publish_allowed"] = row["automatic_order_allowed"] = True
    # Recompute row checksums too: rejection must come from the real Source graph,
    # not a stale hash left behind by the adversarial test.
    source["tables"] = normalize_tables(w.catalog, tables)

    class Forged:
        catalog = w.catalog

        def build(self, **kwargs):
            return copy.deepcopy(source)

    forged = PersistDsimProjectionUseCase(projector=Forged(), store=w.store).execute(
        request=w.request, bundle_reference=w.persisted.bundle_reference, chunk_size=2
    )
    with pytest.raises(InventoryInputError, match="PROJECTION_SOURCE_(ROWS|BINDING)_MISMATCH"):
        w.verifier.execute(
            request=w.request,
            reference=forged["artifact_reference"],
            expected_hash=forged["content_hash"],
        )

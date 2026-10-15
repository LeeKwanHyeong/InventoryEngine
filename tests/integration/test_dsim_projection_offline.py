"""Real sealed Artifact -> DSIM package -> transactional Offline storage/recovery.

SQLite here is a durable protocol emulator, NOT a PostgreSQL migration canary.
All input, output and corruption tests stay in pytest temporary directories.
"""

from __future__ import annotations

import copy
import json
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from dsio_inventory_engine.infrastructure.sqlite import SqliteInventoryEvidenceUnitOfWork
from dsio_inventory_engine.infrastructure.sqlite.inventory_projection import (
    SqliteOfflineProjectionUnitOfWork,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from dsio_inventory_engine.inventory_evidence.application import (
    PersistInventoryResultBundleUseCase,
    ReadAndVerifyInventoryResultBundleUseCase,
)
from dsio_inventory_engine.inventory_evidence.policy_analysis import build_policy_analysis
from dsio_inventory_engine.inventory_evidence.projection import (
    PersistDsimProjectionUseCase,
    VerifyDsimProjectionUseCase,
    WriteDsimProjectionUseCase,
    binding_identity,
)
from dsio_inventory_engine.inventory_evidence.projection_source import DsimSourceProjector
from dsio_inventory_engine.run_inventory.policy_analysis_producer import (
    produce_policy_analysis_inputs,
)
from dsio_inventory_engine.run_inventory.psi_orchestrator import RunPsiBundleUseCase
from tests.integration.test_automatic_policy_analysis_offline import automatic_command
from tests.integration.test_psi_orchestrator_offline import DEPLOYMENT
from tests.unit.test_seven_axis_segmentation import inputs

CATALOG_PATH = (
    Path(__file__).resolve().parents[2] / "schemas/inventory-dsim-physical-contract-v1.json"
)


def projection_world(tmp_path, *, evaluation=True, chunk_size=2):
    # Existing historical fixtures intentionally use 202601. This new fixture
    # constructs a separate Plan-aligned 202640 classification, not a Gate bypass.
    with patch(
        "tests.support.policy_search_fixtures.inputs",
        return_value=replace(
            inputs(),
            actual_yyyyww="202639",
            decision_yyyyww="202640",
            actual_close_window_start_yyyyww="202540",
        ),
    ):
        command, config, snapshot = automatic_command(evaluation=evaluation)
    analysis_inputs = produce_policy_analysis_inputs(
        deployment=DEPLOYMENT,
        specification=command.runtime_request.strategy_execution_plan["policy_analysis_spec"],
        mathematical_request=command.mathematical_request,
        config_values=config,
        classification_snapshot=snapshot,
    )
    command = replace(command, policy_analysis_inputs=analysis_inputs)
    run = RunPsiBundleUseCase(DEPLOYMENT).execute(command)
    evidence = build_policy_analysis(
        deployment=DEPLOYMENT,
        request=command.runtime_request,
        bundle=run.result_bundle,
        inputs=analysis_inputs,
        mathematical_request=command.mathematical_request,
    )
    artifact_path = tmp_path / "artifacts.sqlite"
    store = SqliteInventoryEvidenceUnitOfWork(artifact_path)
    persisted = PersistInventoryResultBundleUseCase(DEPLOYMENT, store).execute(
        run,
        strategy_execution_plan=command.runtime_request.strategy_execution_plan,
        runtime_request=command.runtime_request,
        policy_analysis_evidence=evidence,
    )
    catalog = json.loads(CATALOG_PATH.read_text())
    projector = DsimSourceProjector(
        catalog=catalog,
        bundle_verifier=ReadAndVerifyInventoryResultBundleUseCase(DEPLOYMENT, store),
    )
    producer = PersistDsimProjectionUseCase(projector=projector, store=store)
    pointer = producer.execute(
        request=command.runtime_request,
        bundle_reference=persisted.bundle_reference,
        chunk_size=chunk_size,
    )
    verifier = VerifyDsimProjectionUseCase(projector=projector, store=store)
    uow = SqliteOfflineProjectionUnitOfWork(
        tmp_path / "rows.sqlite",
        catalog=catalog,
        authority=binding_identity(command.runtime_request),
    )
    return SimpleNamespace(
        command=command,
        request=command.runtime_request,
        run=run,
        catalog=catalog,
        artifact_path=artifact_path,
        store=store,
        persisted=persisted,
        projector=projector,
        producer=producer,
        pointer=pointer,
        verifier=verifier,
        uow=uow,
        writer=WriteDsimProjectionUseCase(verifier=verifier, unit_of_work=uow),
    )


async def write(world, writer=None, pointer=None):
    p = pointer or world.pointer
    return await (writer or world.writer).execute(
        request=world.request,
        reference=p["artifact_reference"],
        expected_hash=p["content_hash"],
    )


@pytest.mark.asyncio
async def test_actual_artifacts_bindings_twenty_tables_receipt_and_restart(tmp_path):
    w = projection_world(tmp_path)
    result = await write(w)
    assert not result["replayed"]
    async with w.uow.transaction(w.request) as tx:
        tables = await tx.read_tables()
        assert len(tables) == 20
        assert result["receipt"]["table_row_counts"]["tb_io_publication_receipt"] == 1
        assert tables["dsim.tb_io_policy_candidate"]
        assert tables["dsim.tb_io_policy_evaluation"]
        assert tables["dsim.tb_io_policy_selection"]
        assert {r["disposition"] for r in tables["dsim.tb_io_replenishment_proposal"]} >= {
            "NO_ORDER"
        }
        assert any(r["arrival_date"] is None for r in tables["dsim.tb_io_replenishment_proposal"])
        assert any(
            r["subject_item_id"] is None for r in tables["dsim.tb_io_inventory_reconciliation"]
        )
        assert any(r["sealed_at"] is None for r in tables["dsim.tb_io_snapshot_manifest"])
        for name, rows in tables.items():
            assert result["receipt"]["table_row_counts"][name.split(".")[1]] == len(rows)
    # New gateway/verifier/UoW instances recover solely from persisted source bytes.
    store = SqliteInventoryEvidenceUnitOfWork(w.artifact_path)
    projector = DsimSourceProjector(
        catalog=w.catalog,
        bundle_verifier=ReadAndVerifyInventoryResultBundleUseCase(DEPLOYMENT, store),
    )
    writer = WriteDsimProjectionUseCase(
        verifier=VerifyDsimProjectionUseCase(projector=projector, store=store),
        unit_of_work=SqliteOfflineProjectionUnitOfWork(
            tmp_path / "rows.sqlite",
            catalog=w.catalog,
            authority=binding_identity(w.request),
        ),
    )
    replay = await write(w, writer)
    assert replay["replayed"] and replay["receipt"] == result["receipt"]


@pytest.mark.asyncio
async def test_chunk_size_changes_do_not_change_logical_hash_or_rewrite_receipt(tmp_path):
    w = projection_world(tmp_path)
    first = await write(w)
    second = w.producer.execute(
        request=w.request,
        bundle_reference=w.persisted.bundle_reference,
        chunk_size=1,
    )
    assert second["content_hash"] != w.pointer["content_hash"]
    assert second["projection_content_hash"] == w.pointer["projection_content_hash"]
    result = await write(w, pointer=second)
    assert result["replayed"] and result["receipt"] == first["receipt"]


@pytest.mark.parametrize(
    "mutation", ["quantity", "missing_row", "duplicate_pk", "cross_run", "unknown_column"]
)
@pytest.mark.asyncio
async def test_resealed_rows_cannot_replace_authoritative_artifacts(tmp_path, mutation):
    w = projection_world(tmp_path)

    class Forged:
        catalog = w.catalog

        def build(self, **kwargs):
            value = copy.deepcopy(w.projector.build(**kwargs))
            table = value["tables"]["dsim.tb_io_psi_bucket"]
            if mutation == "quantity":
                table[0]["eoh_qty"] = "999"
                table[0]["row_content_hash"] = digest(
                    {k: v for k, v in table[0].items() if k != "row_content_hash"}
                )
            elif mutation == "missing_row":
                table.pop()
            elif mutation == "duplicate_pk":
                table.append(copy.deepcopy(table[0]))
            elif mutation == "cross_run":
                table[0]["engine_run_id"] = "10000000-0000-4000-8000-000000000002"
            else:
                table[0]["surprise"] = True
            return value

    pointer = PersistDsimProjectionUseCase(projector=Forged(), store=w.store).execute(
        request=w.request,
        bundle_reference=w.persisted.bundle_reference,
    )
    with pytest.raises(InventoryInputError, match="PROJECTION_"):
        await write(w, pointer=pointer)
    async with w.uow.transaction(w.request) as tx:
        assert await tx.read_receipt() is None
        assert not any((await tx.read_tables()).values())


@pytest.mark.asyncio
async def test_missing_chunk_blocks_without_partial_rows(tmp_path):
    w = projection_world(tmp_path)
    m = w.store.read_artifact(w.pointer["artifact_reference"]).document()
    chunk = next(v["chunks"][0] for v in m["tables"].values() if v["chunks"])
    with sqlite3.connect(w.artifact_path) as conn:
        conn.execute(
            "DELETE FROM inventory_result_artifact WHERE artifact_reference=?",
            (chunk["artifact_reference"],),
        )
    with pytest.raises(InventoryInputError, match="ARTIFACT_NOT_FOUND"):
        await write(w)
    async with w.uow.transaction(w.request) as tx:
        assert await tx.read_receipt() is None


@pytest.mark.asyncio
async def test_corrupt_source_bytes_are_not_hidden_by_valid_projection_hash(tmp_path):
    w = projection_world(tmp_path)
    with sqlite3.connect(w.artifact_path) as conn:
        conn.execute(
            "UPDATE inventory_result_artifact SET document_bytes=? WHERE artifact_reference=?",
            (b"{}", w.persisted.bundle_reference),
        )
    with pytest.raises(InventoryInputError, match="ARTIFACT_BYTE_HASH_MISMATCH"):
        await write(w)


@pytest.mark.asyncio
async def test_authority_mismatch_rejects_before_artifact_read(tmp_path):
    w = projection_world(tmp_path)
    wrong = binding_identity(w.request)
    wrong["input_bindings"][0]["source_content_hash"] = "0" * 64
    with sqlite3.connect(w.uow.path) as conn:
        conn.execute("UPDATE offline_projection_authority SET body=?", (json.dumps(wrong),))
    with patch.object(w.store, "read_artifact", side_effect=AssertionError("must not read")):
        with pytest.raises(InventoryInputError, match="PROJECTION_LEDGER_BINDING_MISMATCH"):
            await write(w)


@pytest.mark.asyncio
async def test_write_failure_rolls_back_and_retry_uses_same_sealed_input(tmp_path):
    w = projection_world(tmp_path)

    class FailUow:
        @asynccontextmanager
        async def transaction(self, request):
            async with w.uow.transaction(request) as tx:
                real = tx.insert_rows
                calls = 0

                async def fail(table, rows):
                    nonlocal calls
                    await real(table, rows)
                    calls += 1
                    if calls == 3:
                        raise OSError("offline injected write failure")

                tx.insert_rows = fail
                yield tx

    with pytest.raises(OSError, match="injected"):
        await write(w, WriteDsimProjectionUseCase(verifier=w.verifier, unit_of_work=FailUow()))
    async with w.uow.transaction(w.request) as tx:
        assert await tx.read_receipt() is None and not any((await tx.read_tables()).values())
    assert not (await write(w))["replayed"]


@pytest.mark.asyncio
async def test_lost_commit_ack_is_idempotently_recovered(tmp_path):
    w = projection_world(tmp_path)

    class LoseAck:
        @asynccontextmanager
        async def transaction(self, request):
            async with w.uow.transaction(request) as tx:
                yield tx
                written = await tx.read_receipt() is not None
            if written:
                raise ConnectionError("commit acknowledgement lost")

    with pytest.raises(ConnectionError):
        await write(w, WriteDsimProjectionUseCase(verifier=w.verifier, unit_of_work=LoseAck()))
    assert (await write(w))["replayed"]


@pytest.mark.asyncio
async def test_verified_receipt_does_not_hide_a_missing_persisted_row(tmp_path):
    w = projection_world(tmp_path)
    await write(w)
    with sqlite3.connect(w.uow.path) as conn:
        conn.execute(
            "DELETE FROM offline_projection_row WHERE rowid=(SELECT min(rowid) FROM offline_projection_row)"
        )
    with pytest.raises(InventoryInputError, match="PROJECTION_PERSISTED_ROWS_MISMATCH"):
        await write(w)


@pytest.mark.asyncio
async def test_reference_same_hash_is_append_only_idempotent(tmp_path):
    w = projection_world(tmp_path)
    second = w.producer.execute(
        request=w.request,
        bundle_reference=w.persisted.bundle_reference,
        chunk_size=2,
    )
    assert second["replayed"] and second["content_hash"] == w.pointer["content_hash"]


@pytest.mark.asyncio
async def test_review_and_shadow_evidence_never_mutate_publication_gate(tmp_path):
    w = projection_world(tmp_path)
    await write(w)
    async with w.uow.transaction(w.request) as tx:
        tables = await tx.read_tables()
    children = {r["child_result_id"]: r for r in tables["dsim.tb_io_result_child"]}
    for row in tables["dsim.tb_io_replenishment_proposal"]:
        if children[row["child_result_id"]]["execution_role"] != "OPERATIONAL":
            assert not row["automatic_publish_allowed"] and not row["automatic_order_allowed"]
        if row["order_action"] not in (None, "ALLOW") or row["approval_level"] not in (
            None,
            "AUTO",
        ):
            assert not row["automatic_publish_allowed"] and not row["automatic_order_allowed"]
    # Projection adds no runtime publication event, Run Ledger or new PSI Outbox.
    assert (
        len(w.store.list_pending())
        + (w.store.find_outbox(w.request.engine_run_id, 1).status == "WITHHELD_FOR_REVIEW")
        == 1
    )

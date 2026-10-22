"""Original Artifact, hash/count completeness, SQLite atomicity and recovery. No PostgreSQL."""

import asyncio
import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

import pytest

from dsio_inventory_engine.infrastructure.sqlite.inventory_evidence import (
    SqliteInventoryEvidenceUnitOfWork,
)
from dsio_inventory_engine.infrastructure.sqlite.segmentation_analysis import (
    SqliteOfflineSegmentationUnitOfWork,
)
from dsio_inventory_engine.inventory_contracts.values import digest, InventoryInputError
from dsio_inventory_engine.inventory_evidence.contracts import ArtifactObject
from dsio_inventory_engine.inventory_evidence.segmentation_analysis import (
    PersistSegmentationAnalysisUseCase,
    SegmentationAnalysisProjector,
    VerifySegmentationAnalysisUseCase,
    WriteSegmentationAnalysisUseCase,
    exact_number,
)
from tests.support.segmentation_storage_fixtures import storage_input


def world(tmp_path, **kwargs):
    classifier, result, snapshot, binding = storage_input(**kwargs)
    store = SqliteInventoryEvidenceUnitOfWork(tmp_path / "artifacts.sqlite")
    projector = SegmentationAnalysisProjector(classifier=classifier)
    persister = PersistSegmentationAnalysisUseCase(projector=projector, store=store)
    pointer = persister.execute(result=result, classification_snapshot=snapshot, binding=binding)
    verifier = VerifySegmentationAnalysisUseCase(projector=projector, store=store)
    authority = {
        k: binding[k]
        for k in (
            "tenant_id",
            "project_id",
            "scope",
            "classification_snapshot_id",
            "classification_content_hash",
            "config_hash",
        )
    }
    uow = SqliteOfflineSegmentationUnitOfWork(
        tmp_path / "projection.sqlite",
        authority=authority,
        item_ids=[r["item_id"] for r in snapshot["items"]],
    )
    return dict(
        classifier=classifier,
        result=result,
        snapshot=snapshot,
        binding=binding,
        store=store,
        projector=projector,
        persister=persister,
        pointer=pointer,
        verifier=verifier,
        uow=uow,
    )


def verify(w):
    return w["verifier"].execute(
        binding=w["binding"],
        reference=w["pointer"]["reference"],
        expected_hash=w["pointer"]["content_hash"],
    )


def write(w, uow=None):
    return asyncio.run(
        WriteSegmentationAnalysisUseCase(
            verifier=w["verifier"], unit_of_work=uow or w["uow"]
        ).execute(
            binding=w["binding"],
            reference=w["pointer"]["reference"],
            expected_hash=w["pointer"]["content_hash"],
        )
    )


@pytest.mark.parametrize("gate", ["BLOCK", "REVIEW"])
def test_original_classification_gates_hashes_and_source_remain_unchanged(tmp_path, gate):
    w = world(tmp_path, gate=gate)
    old = copy.deepcopy(w["snapshot"])
    assert {i["effective_order_action"] for i in old["items"]} == {gate}
    tables = verify(w)
    assert tables["header"]["item_count"] == len(tables["items"]) == 3
    assert tables["header"]["assigned_item_count"] == 3
    assert tables["header"]["development_only"] and not tables["header"]["operational_approved"]
    first, again = write(w), write(w)
    assert first["replayed"] is False and again["replayed"] is True
    assert not first["automatic_publish_allowed"] and not first["automatic_order_allowed"]
    assert w["snapshot"] == old
    source = w["store"].read_artifact(tables["header"]["source_artifact_reference"]).document()
    assert source["classification_snapshot"] == json.loads(json.dumps(old))
    assert w["store"].list_pending() == ()


def test_restart_recovery_uses_only_sealed_artifacts_not_source_lookup(tmp_path):
    w = world(tmp_path)
    first = write(w)
    w["store"] = SqliteInventoryEvidenceUnitOfWork(tmp_path / "artifacts.sqlite")
    w["verifier"] = VerifySegmentationAnalysisUseCase(projector=w["projector"], store=w["store"])
    w["result"] = w["snapshot"] = None
    second = write(w)
    assert (
        second["replayed"]
        and first["segmentation_analysis_id"] == second["segmentation_analysis_id"]
    )


class FaultUnitOfWork:
    def __init__(self, underlying, *, after_commit=False):
        self.underlying, self.after_commit = underlying, after_commit

    @asynccontextmanager
    async def transaction(self, binding):
        async with self.underlying.transaction(binding) as tx:
            original = tx.insert_rows

            async def fail(table, rows):
                await original(table, rows)
                if table.endswith("_item") and not self.after_commit:
                    raise OSError("offline failure after insert")

            tx.insert_rows = fail
            yield tx
        if self.after_commit:
            raise OSError("commit acknowledgement lost")


@pytest.mark.parametrize("after_commit", [False, True])
def test_partial_failure_or_lost_commit_acknowledgement_can_be_retried(tmp_path, after_commit):
    w = world(tmp_path)
    with pytest.raises(OSError):
        write(w, FaultUnitOfWork(w["uow"], after_commit=after_commit))
    verify(w)
    result = write(w)
    assert result["replayed"] is after_commit
    with sqlite3.connect(w["uow"].path) as conn:
        assert conn.execute("SELECT count(*) FROM segmentation_header").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM segmentation_item").fetchone()[0] == 3


@pytest.mark.parametrize("corruption", ["missing", "extra", "changed", "header", "authority"])
def test_persisted_missing_extra_tampered_or_wrong_authority_fails_closed(tmp_path, corruption):
    w = world(tmp_path)
    write(w)
    with sqlite3.connect(w["uow"].path) as conn:
        if corruption == "missing":
            conn.execute("DELETE FROM segmentation_item WHERE item_id='ITEM-00000'")
        elif corruption == "extra":
            conn.execute(
                "INSERT INTO segmentation_item SELECT analysis_id,'ORPHAN',body FROM segmentation_item LIMIT 1"
            )
        elif corruption in ("header", "changed"):
            table = "segmentation_header" if corruption == "header" else "segmentation_item"
            row = json.loads(conn.execute("SELECT body FROM " + table + " LIMIT 1").fetchone()[0])
            row["item_count" if corruption == "header" else "reference_rop_qty"] = 999
            conn.execute(
                "UPDATE "
                + table
                + " SET body=? WHERE rowid=(SELECT min(rowid) FROM "
                + table
                + ")",
                (json.dumps(row),),
            )
        else:
            raw = json.loads(conn.execute("SELECT body FROM segmentation_authority").fetchone()[0])
            raw["tenant_id"] = "other"
            conn.execute("UPDATE segmentation_authority SET body=?", (json.dumps(raw),))
    with pytest.raises(InventoryInputError, match="SEGMENTATION_"):
        write(w)


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant_id", "other"),
        ("universe_content_hash", "0" * 64),
        ("classification_content_hash", "0" * 64),
        ("analysis_content_hash", "0" * 64),
    ],
)
def test_separately_pinned_binding_cannot_be_replaced(tmp_path, field, value):
    w = world(tmp_path)
    w["binding"][field] = value
    with pytest.raises(InventoryInputError, match="SEGMENTATION_"):
        verify(w)


@pytest.mark.parametrize("corruption", ["quantity", "missing", "extra", "count", "key", "version"])
def test_rehashed_projection_is_replayed_against_original_source(tmp_path, corruption):
    w = world(tmp_path)
    raw = w["store"].read_artifact(w["pointer"]["reference"]).document()
    if corruption == "quantity":
        raw["tables"]["items"][0]["reference_rop_qty"] = "999"
    elif corruption == "missing":
        raw["tables"]["items"].pop()
    elif corruption == "extra":
        raw["tables"]["items"].append(copy.deepcopy(raw["tables"]["items"][0]))
    elif corruption == "count":
        raw["tables"]["header"]["item_count"] += 1
    elif corruption == "key":
        raw["extra"] = True
    else:
        raw["contract_version"] = "9.0.0"
    raw["content_hash"] = digest({k: v for k, v in raw.items() if k != "content_hash"})
    changed = ArtifactObject.from_document(
        reference="artifact:tampered", contract_key=w["pointer"]["contract_key"], document=raw
    )
    w["store"].commit_artifacts((changed,))
    with pytest.raises(InventoryInputError, match="SEGMENTATION_"):
        w["verifier"].execute(
            binding=w["binding"], reference=changed.reference, expected_hash=changed.content_hash
        )


def test_unclassified_features_preserve_reasons_and_do_not_create_fake_zeroes(tmp_path):
    w = world(tmp_path, feature_change=lambda f: f["sources"].update(unit_cost=None))
    tables = verify(w)
    assert tables["header"]["unclassified_item_count"] == 3
    for row in tables["items"]:
        assert row["assignment_status"] == "UNCLASSIFIED"
        assert row["reason_codes"] and row["unit_cost_usd"] is None
        assert row["reference_rop_qty"] is None and row["candidate_profile_id"] is None
    write(w)


def test_decimal_comparison_does_not_round_80_digit_exact_ratio():
    raw = "1234567890" * 8 + ".1200"
    assert exact_number(raw) == raw[:-2]
    assert exact_number("0.000") == "0"
    with pytest.raises(InventoryInputError):
        exact_number("NaN")


@pytest.mark.parametrize("corruption", ["analysis", "classification", "version", "extra_key"])
def test_rehashed_source_cannot_change_original_analysis_or_gates(tmp_path, corruption):
    w = world(tmp_path)
    manifest = w["store"].read_artifact(w["pointer"]["reference"]).document()
    source = w["store"].read_artifact(manifest["source"]["reference"]).document()
    if corruption == "analysis":
        raw = source["analysis"]
        raw["items"][0]["profile_id"] = "LARGE"
        raw["content_hash"] = digest({k: v for k, v in raw.items() if k != "content_hash"})
    elif corruption == "classification":
        source["classification_snapshot"]["items"][0]["effective_order_action"] = "ALLOW"
    elif corruption == "version":
        source["contract_version"] = "9.0.0"
    else:
        source["unexpected"] = True
    source["content_hash"] = digest({k: v for k, v in source.items() if k != "content_hash"})
    obj = ArtifactObject.from_document(
        reference="artifact:tampered-source",
        contract_key=manifest["source"]["contract_key"],
        document=source,
    )
    w["store"].commit_artifacts((obj,))
    manifest["source"] = {
        "reference": obj.reference,
        "content_hash": obj.content_hash,
        "byte_content_hash": obj.byte_content_hash,
        "contract_key": obj.contract_key,
    }
    manifest["content_hash"] = digest({k: v for k, v in manifest.items() if k != "content_hash"})
    changed = ArtifactObject.from_document(
        reference="artifact:tampered-manifest",
        contract_key=w["pointer"]["contract_key"],
        document=manifest,
    )
    w["store"].commit_artifacts((changed,))
    with pytest.raises(InventoryInputError):
        w["verifier"].execute(
            binding=w["binding"], reference=changed.reference, expected_hash=changed.content_hash
        )


def test_missing_source_artifact_does_not_fall_back_to_latest_or_projection(tmp_path):
    w = world(tmp_path)
    source = verify(w)["header"]["source_artifact_reference"]
    with sqlite3.connect(w["store"].database_path) as conn:
        conn.execute("DELETE FROM inventory_result_artifact WHERE artifact_reference=?", (source,))
    with pytest.raises(InventoryInputError):
        write(w)
    with sqlite3.connect(w["uow"].path) as conn:
        assert conn.execute("SELECT count(*) FROM segmentation_header").fetchone()[0] == 0


def test_concurrent_offline_replay_has_one_complete_aggregate(tmp_path):
    w = world(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: write(w), range(2)))
    assert sorted(r["replayed"] for r in results) == [False, True]
    assert len({r["segmentation_analysis_id"] for r in results}) == 1


def test_source_and_projection_artifact_commit_is_atomic(tmp_path, monkeypatch):
    w = world(tmp_path)
    empty_store = SqliteInventoryEvidenceUnitOfWork(tmp_path / "empty-artifacts.sqlite")
    original = empty_store._write_artifact

    def fail(connection, artifact):
        if artifact.contract_key.endswith("_projection"):
            raise OSError("offline Artifact failure")
        return original(connection, artifact)

    monkeypatch.setattr(empty_store, "_write_artifact", fail)
    with pytest.raises(OSError):
        PersistSegmentationAnalysisUseCase(projector=w["projector"], store=empty_store).execute(
            result=w["result"], classification_snapshot=w["snapshot"], binding=w["binding"]
        )
    with sqlite3.connect(empty_store.database_path) as conn:
        assert conn.execute("SELECT count(*) FROM inventory_result_artifact").fetchone()[0] == 0


@pytest.mark.parametrize("source", [False, True])
def test_byte_hash_corruption_is_detected_before_relational_writes(tmp_path, source):
    w = world(tmp_path)
    reference = w["pointer"]["reference"]
    if source:
        reference = verify(w)["header"]["source_artifact_reference"]
    with sqlite3.connect(w["store"].database_path) as conn:
        conn.execute(
            "UPDATE inventory_result_artifact SET byte_content_hash=? WHERE artifact_reference=?",
            ("0" * 64, reference),
        )
    with pytest.raises(InventoryInputError, match="HASH_MISMATCH"):
        write(w)

from hashlib import sha256
import gzip
import os
import time

import pytest

from dsio_inventory_engine.infrastructure.robustness_resource_files import encoded, write_control
from dsio_inventory_engine.infrastructure.robustness_resource_pool import (
    LocalRobustnessResourcePool,
)
from dsio_inventory_engine.infrastructure.robustness_resource_store import resource_store
from dsio_inventory_engine.infrastructure.robustness_artifacts import LocalRobustnessArtifactStore
from dsio_inventory_engine.infrastructure.robustness_compression import (
    CompressedRobustnessArtifactStore,
)
from dsio_inventory_engine.inventory_contracts.robustness_resources import ResourceLimits
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from tests.support.robustness_resource_fixtures import document


@pytest.fixture
def pool(tmp_path):
    root = tmp_path / "pool"
    root.mkdir()
    return LocalRobustnessResourcePool(root)


@pytest.mark.parametrize("version", ["1.0.0", "2.0.0", "3.0.0"])
def test_all_storage_formats_account_raw_bytes_and_preserve_idempotent_originals(pool, version):
    store = resource_store(pool, "HAND", version)
    value = document()
    ref = store.put(value)
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in store.root.iterdir()}
    usage = pool.inspect()
    assert usage["raw_bytes"] >= len(encoded(value))
    if version != "1.0.0":
        assert usage["raw_bytes"] > usage["physical_bytes"]
    assert store.put(value) == ref and store.read(ref) == value
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in store.root.iterdir()} == before
    assert pool.inspect() == usage
    assert not (pool.root / "pending.json").exists()


def test_compressed_size_cannot_bypass_shared_pool_budget_and_no_files_created_on_rejection(
    tmp_path,
):
    resources = LocalRobustnessResourcePool(tmp_path, ResourceLimits(18_000))
    first = resource_store(resources, "A", "2.0.0")
    second = resource_store(resources, "B", "2.0.0")
    first.put(document("A"))
    before = resources.inspect()
    assert before["physical_bytes"] < 2000 and before["raw_bytes"] > 10_000
    with pytest.raises(InventoryInputError, match="RAW_BUDGET_EXCEEDED"):
        second.put(document("B"))
    assert not list(second.root.iterdir())
    assert resources.inspect() == before
    # New instance/process cannot reset the persisted policy or stored usage.
    assert LocalRobustnessResourcePool(tmp_path, ResourceLimits(18_000)).inspect() == before
    with pytest.raises(InventoryInputError, match="POLICY_MISMATCH"):
        LocalRobustnessResourcePool(tmp_path)


@pytest.mark.parametrize("fault", ["symlink", "fifo", "unknown", "corrupt_gzip", "oversized_gzip"])
def test_unsafe_or_unaccountable_files_fail_closed_without_deletion(pool, tmp_path, fault):
    store = resource_store(pool, "HAND")
    file = store.root / ("object-" + "a" * 64 + ".json.gz")
    if fault == "symlink":
        outside = tmp_path / "outside"
        outside.write_bytes(b"safe")
        file.symlink_to(outside)
    elif fault == "fifo":
        os.mkfifo(file)
    elif fault == "unknown":
        file = store.root / "unrecognized.txt"
        file.write_bytes(b"keep")
    elif fault == "corrupt_gzip":
        file.write_bytes(gzip.compress(b"valid")[:-3])
    else:
        payload = gzip.compress(b"valid")
        file.write_bytes(payload[:-4] + (128_000_001).to_bytes(4, "little"))
    with pytest.raises(InventoryInputError):
        pool.inspect()
    assert file.exists() or file.is_symlink()


def test_only_retained_temporaries_expire_and_published_objects_never_do(pool):
    clock = [1_000_000_000_000]
    pool.clock = lambda: clock[0]
    store = resource_store(pool, "HAND", "1.0.0")
    value = document(size=10)
    ref = store.put(value)
    artifact = store.root / ("object-" + ref["content_hash"] + ".json")
    published_before = artifact.read_bytes()
    temporary = store.root / (".robustness-" + "b" * 32)
    temporary.write_bytes(b"partial")
    write_control(
        pool.root,
        "pending.json",
        {
            "store": store.root.name,
            "name": artifact.name,
            "raw_bytes": 1000,
            "stored_bytes": 7,
            "stored_sha256": sha256(b"partial").hexdigest(),
        },
    )
    usage = pool.inspect()
    assert usage["quarantined_files"] == 1 and not temporary.exists()
    assert not pool.purge_expired_temporaries()["removed_quarantine_files"]
    clock[0] += 86_400 * 1_000_000_000
    removed = pool.purge_expired_temporaries()["removed_quarantine_files"]
    assert len(removed) == 2 and all(name.startswith("q-") for name in removed)
    assert artifact.read_bytes() == published_before and store.read(ref) == value
    assert pool.inspect()["quarantined_files"] == 0


def test_unknown_old_temporary_is_conservatively_charged_and_not_guessed_from_compression(pool):
    store = resource_store(pool, "HAND")
    temporary = store.root / (".robustness-" + "c" * 32)
    temporary.write_bytes(b"truncated gzip")
    result = pool.inspect()
    assert result["raw_bytes"] >= 128_000_000
    assert result["quarantined_files"] == 1


def test_crash_after_link_retains_artifact_and_does_not_double_charge_same_inode(pool):
    store = resource_store(pool, "HAND", "1.0.0")
    ref = store.put(document())
    artifact = store.root / ("object-" + ref["content_hash"] + ".json")
    size = artifact.stat().st_size
    before = pool.inspect()
    temporary = store.root / (".robustness-" + "d" * 32)
    os.link(artifact, temporary)
    write_control(
        pool.root,
        "pending.json",
        {
            "store": store.root.name,
            "name": artifact.name,
            "raw_bytes": size,
            "stored_bytes": size,
            "stored_sha256": sha256(artifact.read_bytes()).hexdigest(),
        },
    )
    after = pool.inspect()
    assert after["quarantined_files"] == 1
    assert 0 < after["raw_bytes"] - before["raw_bytes"] < 1000  # Only quarantine metadata.
    assert store.read(ref) == document()


def test_directory_replacement_and_outside_stores_rejected(pool, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(InventoryInputError, match="OUTSIDE_POOL"):
        pool._require_store(outside)
    old = pool.root.with_name("preserved-old-root")
    pool.root.rename(old)
    pool.root.mkdir()
    with pytest.raises(InventoryInputError, match="ROOT_CHANGED"):
        pool.inspect()


def test_raw_size_cache_is_invalidated_on_physical_changes(pool):
    store = resource_store(pool, "HAND", "2.0.0")
    ref = store.put(document())
    before = pool.inspect()
    file = store.root / ("object-" + ref["content_hash"] + ".json.gz")
    file.write_bytes(gzip.compress(b"expanded" * 3000))
    assert pool.inspect()["raw_bytes"] > before["raw_bytes"]


def test_existing_nonempty_user_directory_is_not_adopted_or_cleaned(tmp_path):
    original = tmp_path / "keep.txt"
    original.write_bytes(b"do not modify")
    with pytest.raises(InventoryInputError, match="NOT_EMPTY"):
        LocalRobustnessResourcePool(tmp_path)
    assert original.read_bytes() == b"do not modify"
    assert {p.name for p in tmp_path.iterdir()} == {"keep.txt"}


@pytest.mark.parametrize(
    "legacy", [LocalRobustnessArtifactStore, CompressedRobustnessArtifactStore]
)
def test_resource_v3_reader_preserves_old_format_files_and_references(pool, legacy):
    root = pool.store_root("OLD")
    ref = legacy(root).put(document())
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.iterdir()}
    reader = resource_store(pool, "OLD")
    assert reader.read(ref) == document() and reader.put(document()) == ref
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.iterdir()} == before
    assert pool.inspect()["raw_bytes"] > 10_000


@pytest.mark.parametrize(
    "field,wrong",
    [
        ("raw_reserved_bytes", True),
        ("stored_bytes", False),
        ("retained_since_ns", -1),
        ("contract_version", "2.0.0"),
        ("unknown", 1),
    ],
)
def test_invalid_quarantine_metadata_blocks_purge_without_deleting_files(pool, field, wrong):
    store = resource_store(pool, "BAD")
    temporary = store.root / (".robustness-" + "e" * 32)
    temporary.write_bytes(b"incomplete")
    pool.inspect()
    metadata = next((pool.root / "quarantine").glob("q-*.json"))
    import json

    value = json.loads(metadata.read_text())
    value[field] = wrong
    metadata.write_bytes(encoded(value))
    before = {p.name: p.read_bytes() for p in metadata.parent.iterdir()}
    with pytest.raises(InventoryInputError):
        pool.purge_expired_temporaries()
    assert {p.name: p.read_bytes() for p in metadata.parent.iterdir()} == before


def test_startup_job_uses_same_budget_and_its_exact_hash_is_required(tmp_path):
    resources = LocalRobustnessResourcePool(tmp_path, ResourceLimits(18_000))
    value = {"arguments": "x" * 10_000}
    reference = resources.prepare_job(value, time.monotonic() + 10)
    assert resources.read_job(reference) == value
    assert resources.inspect()["raw_bytes"] >= len(encoded(value))
    with pytest.raises(InventoryInputError, match="JOB_HASH_MISMATCH"):
        resources.read_job({**reference, "sha256": "0" * 64})
    with pytest.raises(InventoryInputError, match="RAW_BUDGET_EXCEEDED"):
        resource_store(resources, "AFTER_JOB", "2.0.0").put(document())
    resources.retire_job(reference)
    assert not list((tmp_path / "jobs").iterdir())
    assert resources.inspect()["raw_bytes"] >= len(encoded(value))


def test_expired_startup_job_is_retained_in_quarantine_not_left_as_active_job(pool):
    reference = pool.prepare_job({"manual": "job"}, time.monotonic() + 0.05)
    time.sleep(0.06)
    assert pool.inspect()["quarantined_files"] == 1
    assert not (pool.root / "jobs" / reference["name"]).exists()


def test_startup_job_reserves_file_count_before_creating_any_transport_files(pool, monkeypatch):
    usage = pool.inspect()
    monkeypatch.setattr(
        "dsio_inventory_engine.infrastructure.robustness_resource_pool.MAX_RESOURCE_FILES",
        usage["files"] + 3,
    )
    before = {
        str(p.relative_to(pool.root)): p.read_bytes() for p in pool.root.rglob("*") if p.is_file()
    }
    with pytest.raises(InventoryInputError, match="FILE_COUNT_EXCEEDED"):
        pool.prepare_job({"manual": "job"}, time.monotonic() + 10)
    after = {
        str(p.relative_to(pool.root)): p.read_bytes() for p in pool.root.rglob("*") if p.is_file()
    }
    assert after == before

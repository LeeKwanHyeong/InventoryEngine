"""Real local files only; these documents test storage, not business authority."""

from concurrent.futures import ThreadPoolExecutor
import os
from unittest.mock import patch

import pytest

from dsio_inventory_engine.evaluate_replenishment.robustness.contracts import sealed
from dsio_inventory_engine.infrastructure.robustness_artifacts import (
    LocalRobustnessArtifactStore,
    artifact_reference,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, canonical_json


def document(value=1):
    return sealed("inventory-robustness-durable-header", {"hand_storage_fixture": value})


def object_path(root, ref):
    return root / ("object-" + ref["content_hash"] + ".json")


def test_round_trip_append_only_identity_and_no_temp_files(tmp_path):
    store = LocalRobustnessArtifactStore(tmp_path)
    value = document()
    ref = store.put(value)
    store.bind("HAND:ITEM-A", ref)
    before = {p.name: (p.stat().st_ino, p.stat().st_mtime_ns) for p in tmp_path.iterdir()}
    assert store.read(ref) == value
    assert store.put(value) == ref
    store.bind("HAND:ITEM-A", ref)
    assert store.lookup("HAND:ITEM-A") == ref
    assert LocalRobustnessArtifactStore(tmp_path).read(ref) == value
    assert before == {p.name: (p.stat().st_ino, p.stat().st_mtime_ns) for p in tmp_path.iterdir()}
    assert len(before) == 2
    assert object_path(tmp_path, ref).stat().st_mode & 0o777 == 0o600


def test_logical_reference_conflict_keeps_original(tmp_path):
    store = LocalRobustnessArtifactStore(tmp_path)
    first, second = store.put(document()), store.put(document(2))
    store.bind("HAND", first)
    with pytest.raises(InventoryInputError, match="DURABLE_ARTIFACT_CONFLICT"):
        store.bind("HAND", second)
    assert store.lookup("HAND") == first
    assert store.read(first) == document()


@pytest.mark.parametrize(
    "change",
    [
        {"byte_count": 0},
        {"byte_count": True},
        {"byte_count": 128_000_001},
        {"content_hash": "../../outside"},
        {"content_hash": "A" * 64},
        {"contract_id": "unknown"},
        {"extra": "field"},
    ],
)
def test_closed_reference_rejects_invalid_size_kind_hash_and_extra_fields(tmp_path, change):
    ref = LocalRobustnessArtifactStore(tmp_path).put(document())
    with pytest.raises(InventoryInputError):
        artifact_reference({**ref, **change})


@pytest.mark.parametrize("fault", ["hash", "bytes", "noncanonical", "duplicate", "missing"])
def test_corrupt_missing_and_noncanonical_objects_do_not_become_cache_misses(tmp_path, fault):
    store = LocalRobustnessArtifactStore(tmp_path)
    value = document()
    ref = store.put(value)
    path = object_path(tmp_path, ref)
    if fault == "hash":
        path.write_text(canonical_json({**value, "hand_storage_fixture": 2}) + "\n")
    elif fault == "bytes":
        ref = {**ref, "byte_count": ref["byte_count"] + 1}
    elif fault == "noncanonical":
        path.write_text(canonical_json(value) + " ")  # Same length and semantic hash.
    elif fault == "duplicate":
        payload = canonical_json(value)[:-1] + ',"hand_storage_fixture":1}\n'
        path.write_text(payload)
        ref = {**ref, "byte_count": len(payload.encode())}
    else:
        path.unlink()
    with pytest.raises(InventoryInputError):
        store.read(ref)
    if fault != "missing":
        with pytest.raises(InventoryInputError):
            store.bind("BAD", ref)
        assert store.lookup("BAD") is None


@pytest.mark.parametrize("kind", ["symlink", "directory", "fifo"])
def test_nonregular_objects_are_rejected_without_following_or_blocking(tmp_path, kind):
    store = LocalRobustnessArtifactStore(tmp_path)
    ref = store.put(document())
    path = object_path(tmp_path, ref)
    original = path.read_bytes()
    path.unlink()
    if kind == "symlink":
        target = tmp_path / "untouched"
        target.write_bytes(original)
        path.symlink_to(target)
    elif kind == "directory":
        path.mkdir()
    else:
        os.mkfifo(path)
    with pytest.raises(InventoryInputError, match="DURABLE_ARTIFACT_PATH_INVALID"):
        store.read(ref)
    with pytest.raises(InventoryInputError):
        store.put(document())
    if kind == "symlink":
        assert target.read_bytes() == original


def test_root_is_pinned_and_symlink_root_is_not_accepted(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    store = LocalRobustnessArtifactStore(root)
    root.rename(tmp_path / "old")
    root.mkdir()
    with pytest.raises(InventoryInputError, match="ROOT_CHANGED"):
        store.put(document())
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(InventoryInputError, match="ROOT_INVALID"):
        LocalRobustnessArtifactStore(link)


@pytest.mark.parametrize("key", ["", "x" * 257, 1, None])
def test_invalid_slot_keys_are_rejected(tmp_path, key):
    with pytest.raises(InventoryInputError, match="SLOT_KEY_INVALID"):
        LocalRobustnessArtifactStore(tmp_path).lookup(key)


def test_slot_symlink_and_self_resealed_wrong_key_are_rejected(tmp_path):
    store = LocalRobustnessArtifactStore(tmp_path)
    ref = store.put(document())
    slot = tmp_path / store._slot_name("HAND")
    slot.symlink_to(object_path(tmp_path, ref))
    with pytest.raises(InventoryInputError, match="PATH_INVALID"):
        store.lookup("HAND")
    slot.unlink()
    slot.write_text(
        canonical_json(
            sealed("inventory-robustness-durable-slot", {"slot_key": "OTHER", "reference": ref})
        )
        + "\n"
    )
    with pytest.raises(InventoryInputError, match="SLOT_BINDING_MISMATCH"):
        store.lookup("HAND")


def test_same_hash_concurrent_writers_are_idempotent(tmp_path):
    def write(_):
        store = LocalRobustnessArtifactStore(tmp_path)
        ref = store.put(document())
        store.bind("SHARED", ref)
        return ref

    with ThreadPoolExecutor(max_workers=4) as pool:
        references = list(pool.map(write, range(12)))
    assert all(ref == references[0] for ref in references)
    assert len(list(tmp_path.iterdir())) == 2


def test_different_hash_concurrent_pointer_has_one_winner(tmp_path):
    store = LocalRobustnessArtifactStore(tmp_path)
    refs = [store.put(document(i)) for i in range(4)]

    def bind(ref):
        try:
            LocalRobustnessArtifactStore(tmp_path).bind("SHARED", ref)
            return ref
        except InventoryInputError as error:
            assert str(error) == "DURABLE_ARTIFACT_CONFLICT"
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        winners = [r for r in pool.map(bind, refs) if r is not None]
    assert winners == [store.lookup("SHARED")]
    assert len(list(tmp_path.iterdir())) == 5


@pytest.mark.parametrize("fault", ["link", "fsync"])
def test_interrupted_file_publication_has_no_pointer_and_can_retry(tmp_path, fault):
    store = LocalRobustnessArtifactStore(tmp_path)
    with patch(
        "dsio_inventory_engine.infrastructure.robustness_artifacts.os." + fault,
        side_effect=OSError("HAND_INTERRUPTION"),
    ):
        with pytest.raises(OSError, match="HAND_INTERRUPTION"):
            store.put(document())
    assert store.lookup("HAND") is None
    assert not list(tmp_path.glob(".robustness-*"))
    ref = store.put(document())
    store.bind("HAND", ref)
    assert store.read(store.lookup("HAND")) == document()


def test_per_document_size_limit_does_not_weaken_existing_limit(tmp_path):
    store = LocalRobustnessArtifactStore(tmp_path)
    ref = store.put(document())
    with patch("dsio_inventory_engine.infrastructure.robustness_artifacts.MAX_BYTES", 100):
        with pytest.raises(InventoryInputError, match="SIZE_LIMIT"):
            store.read(ref)
        with pytest.raises(InventoryInputError, match="SIZE_LIMIT"):
            store.put(document())

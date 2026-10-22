from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import gzip
from hashlib import sha256
import json
from unittest.mock import patch

import pytest

from dsio_inventory_engine.evaluate_replenishment.robustness.contracts import sealed
from dsio_inventory_engine.infrastructure.robustness_artifacts import LocalRobustnessArtifactStore
from dsio_inventory_engine.infrastructure.robustness_compression import (
    CompressedRobustnessArtifactStore,
)
from dsio_inventory_engine.infrastructure.robustness_shared_artifacts import (
    SharedInputRobustnessArtifactStore,
)
from dsio_inventory_engine.infrastructure.robustness_shared_layout import (
    pack_document,
    restore_document,
)
from dsio_inventory_engine.inventory_contracts.robustness_shared_inputs import (
    MAX_REFERENCES,
    shared_document_reference,
)
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    canonical_json,
    digest,
)


def sample():
    calendar = [{"yyyyww": str(205500 + i), "description": "공유 Calendar" * 5} for i in range(16)]
    forecast = [
        {"yyyyww": str(205500 + i), "quantity": str(i), "item_id": "HAND"} for i in range(16)
    ]
    return sealed(
        "inventory-robustness-durable-header",
        {
            "parent_classification": {"snapshot_id": "HAND", "evidence": "분류" * 600},
            "calendar": calendar,
            "forecast": forecast,
            "observations": [
                {"calendar": deepcopy(calendar), "future_demand": deepcopy(forecast)}
                for _ in range(5)
            ],
            "literal_marker": {"path": ["calendar"], "reference": "this is original data"},
        },
    )


def layout(root, ref):
    return json.loads(
        gzip.decompress((root / ("object-" + ref["content_hash"] + ".shared.json.gz")).read_bytes())
    )


def identity(root):
    return {p.name: (p.stat().st_ino, p.stat().st_mtime_ns, p.read_bytes()) for p in root.iterdir()}


def packed_case():
    values = {}

    def persist(value, raw):
        h = digest(value)
        values[h] = deepcopy(value)
        return {
            "value_hash": h,
            "value_byte_count": len(raw),
            "byte_count": len(raw) + 100,
            "raw_sha256": "a" * 64,
            "stored_byte_count": 100,
            "stored_sha256": "b" * 64,
        }

    original = sample()
    return original, pack_document(original, persist), values


def test_exact_utf8_json_hash_and_shared_values_are_preserved(tmp_path):
    value, store = sample(), SharedInputRobustnessArtifactStore(tmp_path)
    ref = store.put(value)
    raw = (canonical_json(value) + "\n").encode()
    assert ref["storage_version"] == "3.0.0" and ref["content_hash"] == value["content_hash"]
    assert ref["byte_count"] == len(raw) and ref["raw_sha256"] == sha256(raw).hexdigest()
    assert len(list(tmp_path.glob("shared-*.gz"))) == 3
    packed = layout(tmp_path, ref)
    assert len(packed["shared_inputs"]) == 13
    assert packed["document"]["calendar"] is packed["document"]["parent_classification"] is None
    result = SharedInputRobustnessArtifactStore(tmp_path).read(ref)
    assert (canonical_json(result) + "\n").encode() == raw
    assert result["literal_marker"] == value["literal_marker"]
    result["observations"][0]["calendar"][0]["yyyyww"] = "CHANGED"
    assert result["calendar"][0]["yyyyww"] != "CHANGED"
    assert result["observations"][1]["calendar"][0]["yyyyww"] != "CHANGED"


def test_shared_value_is_stored_once_across_different_documents(tmp_path):
    store = SharedInputRobustnessArtifactStore(tmp_path)
    first = sample()
    ref = store.put(first)
    before = {p.name: p.read_bytes() for p in tmp_path.glob("shared-*.gz")}
    body = {k: v for k, v in first.items() if k != "content_hash"}
    body["another_result"] = "DIFFERENT"
    body["content_hash"] = digest(body)
    other = store.put(body)
    assert ref["content_hash"] != other["content_hash"] and store.read(other) == body
    assert before == {p.name: p.read_bytes() for p in tmp_path.glob("shared-*.gz")}


def test_idempotent_put_bind_and_compressor_upgrade_preserve_bytes(tmp_path):
    store = SharedInputRobustnessArtifactStore(tmp_path)
    ref = store.put(sample())
    store.bind("HAND", ref)
    before = identity(tmp_path)
    with patch(
        "dsio_inventory_engine.infrastructure.robustness_shared_artifacts.gzip.compress",
        side_effect=AssertionError("RECOMPRESS"),
    ):
        assert store.put(sample()) == ref
        store.bind("HAND", ref)
    assert store.lookup("HAND") == ref and identity(tmp_path) == before
    other = store.put(sealed("inventory-robustness-durable-header", {"other": True}))
    with pytest.raises(InventoryInputError, match="CONFLICT"):
        store.bind("HAND", other)


@pytest.mark.parametrize(
    "old_type", [LocalRobustnessArtifactStore, CompressedRobustnessArtifactStore]
)
def test_v1_v2_files_and_pointers_are_not_rewritten(tmp_path, old_type):
    old = old_type(tmp_path)
    ref = old.put(sample())
    old.bind("HAND", ref)
    before = identity(tmp_path)
    new = SharedInputRobustnessArtifactStore(tmp_path)
    assert new.read(ref) == sample() and new.put(sample()) == ref and new.lookup("HAND") == ref
    new.bind("HAND", ref)
    assert identity(tmp_path) == before and not list(tmp_path.glob("shared-*.gz"))


@pytest.mark.parametrize(
    "key,value",
    [
        ("storage_version", "3.1.0"),
        ("layout", "UNKNOWN"),
        ("encoding", "JSON"),
        ("compression_level", True),
        ("byte_count", True),
        ("byte_count", 128_000_001),
        ("manifest_byte_count", 128_000_001),
        ("stored_byte_count", 0),
        ("stored_sha256", "../x"),
    ],
)
def test_closed_v3_reference(tmp_path, key, value):
    ref = SharedInputRobustnessArtifactStore(tmp_path).put(sample())
    with pytest.raises(InventoryInputError):
        shared_document_reference({**ref, key: value})
    with pytest.raises(InventoryInputError):
        shared_document_reference({**ref, "extra": 1})


@pytest.mark.parametrize("fault", ["missing", "truncated", "changed", "directory", "symlink"])
def test_missing_or_corrupt_shared_file_fails_closed(tmp_path, fault):
    store = SharedInputRobustnessArtifactStore(tmp_path)
    ref = store.put(sample())
    target = next(tmp_path.glob("shared-*.gz"))
    if fault == "missing":
        target.unlink()
    elif fault == "truncated":
        target.write_bytes(target.read_bytes()[:-3])
    elif fault == "changed":
        data = bytearray(target.read_bytes())
        data[4] ^= 1
        target.write_bytes(data)
    elif fault == "directory":
        target.unlink()
        target.mkdir()
    else:
        target.unlink()
        target.symlink_to(tmp_path / "does-not-exist")
    with pytest.raises(InventoryInputError):
        store.read(ref)
    assert store.lookup("HAND") is None


@pytest.mark.parametrize(
    "fault", ["duplicate", "missing_path", "boolean_index", "non_null", "reordered", "expansion"]
)
def test_invalid_reference_plan_is_rejected(fault):
    original, packed, values = packed_case()
    expected = len((canonical_json(original) + "\n").encode())
    if fault == "duplicate":
        packed["shared_inputs"].append(deepcopy(packed["shared_inputs"][0]))
    elif fault == "missing_path":
        packed["shared_inputs"][0]["path"] = ["absent", "calendar"]
    elif fault == "boolean_index":
        packed["shared_inputs"][2]["path"] = ["observations", True, "calendar"]
    elif fault == "non_null":
        packed["document"]["calendar"] = "NOT_A_PLACEHOLDER"
    elif fault == "reordered":
        packed["shared_inputs"].reverse()
    else:
        packed["shared_inputs"][0]["reference"]["value_byte_count"] = 128_000_000

    def load(ref):
        assert fault == "reordered", "invalid plan must fail BEFORE loading"
        return values[ref["value_hash"]]

    with pytest.raises(InventoryInputError):
        restore_document(packed, load, expected_bytes=expected)


def test_missing_reference_and_excessive_fanout_are_not_successful():
    original, packed, _ = packed_case()
    expected = len((canonical_json(original) + "\n").encode())
    packed["shared_inputs"].pop()
    with pytest.raises(InventoryInputError, match="SIZE_MISMATCH"):
        restore_document(packed, lambda _: pytest.fail("must not load"), expected_bytes=expected)
    packed["shared_inputs"] *= MAX_REFERENCES
    with pytest.raises(InventoryInputError, match="COUNT_LIMIT"):
        restore_document(packed, lambda _: None, expected_bytes=expected)


def test_overlapping_paths_cannot_create_recursive_dependencies():
    original, packed, _ = packed_case()
    packed["shared_inputs"].append({**packed["shared_inputs"][0], "path": ["calendar", "calendar"]})
    with pytest.raises(InventoryInputError, match="PATH_INVALID"):
        restore_document(
            packed,
            lambda _: pytest.fail("must not load"),
            expected_bytes=len((canonical_json(original) + "\n").encode()),
        )


def test_small_values_and_literal_reference_lookalikes_stay_inline(tmp_path):
    v = sealed(
        "inventory-robustness-durable-header",
        {"calendar": [], "forecast": {"path": ["x"], "reference": "literal"}},
    )
    store = SharedInputRobustnessArtifactStore(tmp_path)
    ref = store.put(v)
    assert not list(tmp_path.glob("shared-*")) and store.read(ref) == v


def test_raw_limit_cannot_be_bypassed_by_sharing_or_compression(tmp_path):
    with patch(
        "dsio_inventory_engine.infrastructure.robustness_shared_artifacts.MAX_RAW_BYTES", 1000
    ):
        with pytest.raises(InventoryInputError, match="SIZE_LIMIT"):
            SharedInputRobustnessArtifactStore(tmp_path).put(sample())
    assert not list(tmp_path.iterdir())


def test_concurrent_same_content_writers_share_immutable_files(tmp_path):
    def write(_):
        s = SharedInputRobustnessArtifactStore(tmp_path)
        ref = s.put(sample())
        s.bind("HAND", ref)
        return ref

    with ThreadPoolExecutor(max_workers=4) as pool:
        refs = list(pool.map(write, range(8)))
    assert all(ref == refs[0] for ref in refs) and len(list(tmp_path.iterdir())) == 5


def test_v3_read_does_not_fall_back_to_an_old_format(tmp_path):
    store = SharedInputRobustnessArtifactStore(tmp_path)
    ref = store.put(sample())
    LocalRobustnessArtifactStore(tmp_path).put(sample())
    next(tmp_path.glob("*.shared.json.gz")).unlink()
    with pytest.raises(InventoryInputError, match="NOT_FOUND"):
        store.read(ref)


def test_resealed_layout_cannot_change_reconstructed_original_hash(tmp_path):
    store = SharedInputRobustnessArtifactStore(tmp_path)
    ref = store.put(sample())
    target = next(tmp_path.glob("*.shared.json.gz"))
    value = layout(tmp_path, ref)
    value["document"]["literal_marker"]["reference"] = "this is changed  data"
    raw = (canonical_json(value) + "\n").encode()
    stored = gzip.compress(raw, compresslevel=1, mtime=0)
    target.write_bytes(stored)
    bad = {
        **ref,
        "manifest_byte_count": len(raw),
        "stored_byte_count": len(stored),
        "stored_sha256": sha256(stored).hexdigest(),
    }
    with pytest.raises(InventoryInputError):
        store.read(bad)


@pytest.mark.parametrize(
    "field",
    [
        "value_hash",
        "value_byte_count",
        "byte_count",
        "raw_sha256",
        "stored_byte_count",
        "stored_sha256",
        "extra",
    ],
)
def test_resealed_shared_metadata_cannot_bypass_dependency_verification(tmp_path, field):
    store = SharedInputRobustnessArtifactStore(tmp_path)
    ref = store.put(sample())
    packed = layout(tmp_path, ref)
    item = packed["shared_inputs"][0]["reference"]
    if field in ("value_hash", "raw_sha256", "stored_sha256"):
        item[field] = "0" * 64
    elif field == "extra":
        item[field] = 1
    else:
        item[field] += 1
    raw = (canonical_json(packed) + "\n").encode()
    stored = gzip.compress(raw, compresslevel=1, mtime=0)
    (tmp_path / ("object-" + ref["content_hash"] + ".shared.json.gz")).write_bytes(stored)
    bad = {
        **ref,
        "manifest_byte_count": len(raw),
        "stored_byte_count": len(stored),
        "stored_sha256": sha256(stored).hexdigest(),
    }
    with pytest.raises(InventoryInputError):
        store.read(bad)


@pytest.mark.parametrize("size,expected_shared", [(511, 0), (512, 1)])
def test_minimum_reference_boundary_is_utf8_byte_count(tmp_path, size, expected_shared):
    value = sealed("inventory-robustness-durable-header", {"calendar": ["x" * (size - 4)]})
    store = SharedInputRobustnessArtifactStore(tmp_path)
    ref = store.put(value)
    assert len(list(tmp_path.glob("shared-*"))) == expected_shared and store.read(ref) == value


@pytest.mark.parametrize(
    "old_type", [LocalRobustnessArtifactStore, CompressedRobustnessArtifactStore]
)
def test_old_readers_do_not_claim_to_accept_v3(tmp_path, old_type):
    ref = SharedInputRobustnessArtifactStore(tmp_path).put(sample())
    with pytest.raises(InventoryInputError):
        old_type(tmp_path).read(ref)


@pytest.mark.parametrize("suffix", [".json", ".json.gz"])
def test_corrupt_existing_old_object_is_not_bypassed_by_put(tmp_path, suffix):
    store = SharedInputRobustnessArtifactStore(tmp_path)
    ref = store.put(sample())
    (tmp_path / ("object-" + ref["content_hash"] + suffix)).write_bytes(b"corrupt")
    with pytest.raises(InventoryInputError):
        store.put(sample())


def test_measurement_counts_unique_uncompressed_files_not_compressed_sizes(tmp_path):
    from scripts.verify_robustness_shared_storage import measure

    store = SharedInputRobustnessArtifactStore(tmp_path)
    ref = store.put(sample())
    store.bind("HAND", ref)
    result = measure(tmp_path)
    expected = sum(
        len(gzip.decompress(p.read_bytes())) if p.suffix == ".gz" else p.stat().st_size
        for p in tmp_path.iterdir()
    )
    assert result["unique_raw_storage_bytes"] == expected
    assert result["physical_bytes"] == sum(p.stat().st_size for p in tmp_path.iterdir())
    assert result["shared_file_count"] == 3 and result["file_count"] == 5
    assert result["unique_raw_storage_bytes"] > result["physical_bytes"]
    assert result["unique_raw_storage_bytes"] < ref["byte_count"]
    store.put(sample())
    store.bind("HAND", ref)
    assert measure(tmp_path) == result


@pytest.mark.parametrize("name", [".robustness-unfinished", "unknown.json"])
def test_measurement_never_silently_omits_unknown_or_temporary_files(tmp_path, name):
    from scripts.verify_robustness_shared_storage import measure

    SharedInputRobustnessArtifactStore(tmp_path).put(sample())
    (tmp_path / name).write_bytes(b"incomplete")
    with pytest.raises(InventoryInputError, match="UNEXPECTED_FILE"):
        measure(tmp_path)

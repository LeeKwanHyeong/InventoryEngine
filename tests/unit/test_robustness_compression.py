from concurrent.futures import ThreadPoolExecutor
import gzip
from hashlib import sha256
from unittest.mock import patch

import pytest

from dsio_inventory_engine.evaluate_replenishment.robustness.contracts import sealed
from dsio_inventory_engine.infrastructure.robustness_artifacts import LocalRobustnessArtifactStore
from dsio_inventory_engine.infrastructure.robustness_compression import (
    CompressedRobustnessArtifactStore,
    decode_gzip,
)
from dsio_inventory_engine.inventory_contracts.robustness_storage import (
    BENCHMARK_SPEC_HASH,
    MAX_RAW_BYTES,
    benchmark_spec,
    storage_reference,
    validate_benchmark_spec,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, canonical_json


def sample(value="repeat " * 3000):
    return sealed("inventory-robustness-durable-header", {"hand_storage_fixture": value})


def path_for(root, ref):
    return root / ("object-" + ref["content_hash"] + ".json.gz")


def test_codec_roundtrip_hashes_and_exact_raw_bytes(tmp_path):
    store = CompressedRobustnessArtifactStore(tmp_path)
    value = sample()
    raw = (canonical_json(value) + "\n").encode()
    ref = store.put(value)
    assert ref["storage_version"] == "2.0.0" and ref["content_hash"] == value["content_hash"]
    assert ref["byte_count"] == len(raw) and ref["raw_sha256"] == sha256(raw).hexdigest()
    stored = path_for(tmp_path, ref).read_bytes()
    assert len(stored) == ref["stored_byte_count"] < len(raw)
    assert sha256(stored).hexdigest() == ref["stored_sha256"]
    assert decode_gzip(stored, expected_bytes=len(raw)) == raw
    assert store.read(ref) == value
    assert CompressedRobustnessArtifactStore(tmp_path).read(ref) == value


def test_identical_write_and_pointer_preserve_files(tmp_path):
    store = CompressedRobustnessArtifactStore(tmp_path)
    ref = store.put(sample())
    store.bind("HAND", ref)
    before = {p.name: (p.stat().st_ino, p.stat().st_mtime_ns) for p in tmp_path.iterdir()}
    assert store.put(sample()) == ref
    store.bind("HAND", ref)
    assert store.lookup("HAND") == ref
    assert before == {p.name: (p.stat().st_ino, p.stat().st_mtime_ns) for p in tmp_path.iterdir()}
    other = store.put(sample("OTHER"))
    with pytest.raises(InventoryInputError, match="CONFLICT"):
        store.bind("HAND", other)


def test_legacy_object_and_pointer_are_read_without_conversion(tmp_path):
    old = LocalRobustnessArtifactStore(tmp_path)
    ref = old.put(sample())
    old.bind("HAND", ref)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    new = CompressedRobustnessArtifactStore(tmp_path)
    assert new.read(ref) == sample()
    assert new.lookup("HAND") == ref
    assert new.put(sample()) == ref
    new.bind("HAND", ref)
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}


def test_reader_cannot_silently_downgrade_a_compressed_reference(tmp_path):
    store = CompressedRobustnessArtifactStore(tmp_path)
    ref = store.put(sample())
    legacy_ref = {k: ref[k] for k in ("contract_id", "content_hash", "byte_count")}
    with pytest.raises(InventoryInputError, match="NOT_FOUND"):
        store.read(legacy_ref)
    with pytest.raises(InventoryInputError):
        LocalRobustnessArtifactStore(tmp_path).read(ref)


@pytest.mark.parametrize(
    "field,value",
    [
        ("storage_version", "9.0.0"),
        ("encoding", "ZSTD"),
        ("codec_version", "UNKNOWN"),
        ("compression_level", True),
        ("compression_level", 9),
        ("byte_count", True),
        ("byte_count", 128_000_001),
        ("stored_byte_count", 128_000_001),
        ("stored_byte_count", 0),
        ("raw_sha256", "../../escape"),
        ("stored_sha256", "X" * 64),
    ],
)
def test_closed_v2_reference(tmp_path, field, value):
    ref = CompressedRobustnessArtifactStore(tmp_path).put(sample())
    with pytest.raises(InventoryInputError):
        storage_reference({**ref, field: value})


@pytest.mark.parametrize(
    "fault", ["truncate", "crc", "physical_hash", "physical_size", "raw_hash", "raw_size"]
)
def test_corrupt_reference_or_compressed_bytes_fail_without_rewriting(tmp_path, fault):
    store = CompressedRobustnessArtifactStore(tmp_path)
    ref = store.put(sample())
    path = path_for(tmp_path, ref)
    if fault == "truncate":
        path.write_bytes(path.read_bytes()[:-4])
    elif fault == "crc":
        raw = bytearray(path.read_bytes())
        raw[-8] ^= 1
        path.write_bytes(raw)
        ref = {**ref, "stored_sha256": sha256(raw).hexdigest()}
    elif fault == "physical_hash":
        ref = {**ref, "stored_sha256": "0" * 64}
    elif fault == "physical_size":
        ref = {**ref, "stored_byte_count": ref["stored_byte_count"] + 1}
    elif fault == "raw_hash":
        ref = {**ref, "raw_sha256": "0" * 64}
    else:
        ref = {**ref, "byte_count": ref["byte_count"] - 1}
    before = path.read_bytes()
    with pytest.raises(InventoryInputError):
        store.read(ref)
    assert path.read_bytes() == before
    assert store.lookup("BAD") is None


@pytest.mark.parametrize("suffix", [b"trailing", gzip.compress(b"second"), b"\x00"])
def test_extra_gzip_members_or_trailing_bytes_are_not_accepted(suffix):
    raw = b"hand sample"
    with pytest.raises(InventoryInputError, match="FRAME_INVALID"):
        decode_gzip(gzip.compress(raw) + suffix, expected_bytes=len(raw))


def test_restore_size_is_bounded_before_json_parsing():
    payload = gzip.compress(b"x" * 1_000_000)
    with pytest.raises(InventoryInputError, match="DECOMPRESSION_LIMIT"):
        decode_gzip(payload, expected_bytes=100)
    with pytest.raises(InventoryInputError, match="SIZE_LIMIT"):
        decode_gzip(payload, expected_bytes=MAX_RAW_BYTES + 1)
    with pytest.raises(InventoryInputError, match="FRAME_INVALID"):
        decode_gzip(payload[:-3], expected_bytes=1_000_000)


def test_raw_limit_is_enforced_even_when_compressed_size_is_tiny(tmp_path):
    store = CompressedRobustnessArtifactStore(tmp_path)
    with patch("dsio_inventory_engine.infrastructure.robustness_compression.MAX_RAW_BYTES", 1000):
        with pytest.raises(InventoryInputError, match="SIZE_LIMIT"):
            store.put(sample())
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("kind", ["symlink", "directory"])
def test_nonregular_gzip_blob_is_rejected(tmp_path, kind):
    store = CompressedRobustnessArtifactStore(tmp_path)
    ref = store.put(sample())
    p = path_for(tmp_path, ref)
    p.unlink()
    if kind == "directory":
        p.mkdir()
    else:
        untouched = tmp_path / "original"
        untouched.write_bytes(b"do not follow")
        p.symlink_to(untouched)
    with pytest.raises(InventoryInputError, match="PATH_INVALID"):
        store.read(ref)
    with pytest.raises(InventoryInputError, match="PATH_INVALID"):
        store.put(sample())


def test_existing_corrupt_legacy_blob_does_not_fall_back_to_gzip(tmp_path):
    store = CompressedRobustnessArtifactStore(tmp_path)
    ref = store.put(sample())
    (tmp_path / ("object-" + ref["content_hash"] + ".json")).write_bytes(b"corrupt")
    with pytest.raises(InventoryInputError, match="CONFLICT"):
        store.put(sample())


def test_concurrent_same_hash_gzip_publication_is_idempotent(tmp_path):
    def write(_):
        store = CompressedRobustnessArtifactStore(tmp_path)
        ref = store.put(sample())
        store.bind("HAND", ref)
        return ref

    with ThreadPoolExecutor(max_workers=4) as pool:
        refs = list(pool.map(write, range(8)))
    assert all(ref == refs[0] for ref in refs)
    assert len(list(tmp_path.iterdir())) == 2


def test_changed_compressor_does_not_rewrite_valid_existing_frame(tmp_path):
    store = CompressedRobustnessArtifactStore(tmp_path)
    ref = store.put(sample())
    with patch(
        "dsio_inventory_engine.infrastructure.robustness_compression.gzip.compress",
        side_effect=AssertionError("MUST_REUSE_EXISTING_FRAME"),
    ):
        assert store.put(sample()) == ref


def test_fixed_benchmark_contract_and_limits_are_closed():
    spec = benchmark_spec()
    validate_benchmark_spec(spec, expected_hash=BENCHMARK_SPEC_HASH)
    assert spec["max_experiment_raw_bytes"] == 20_000_000_000
    assert spec["max_object_raw_bytes"] == spec["max_object_stored_bytes"] == 128_000_000
    for key, value in (
        ("workers", 2),
        ("weeks", 13),
        ("repetitions", 1),
        ("max_experiment_raw_bytes", 30_000_000_000),
    ):
        with pytest.raises(InventoryInputError, match="SPEC_MISMATCH"):
            validate_benchmark_spec({**spec, key: value}, expected_hash=BENCHMARK_SPEC_HASH)

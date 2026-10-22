import copy
import hashlib
import json
from pathlib import Path

import pytest

from scripts.benchmark_legacy_psi import (
    HEADER,
    NUMBERS,
    PROTOCOL,
    ROW,
    make_input,
    prepare,
    requests_from_input,
    serialize_results,
    store_new,
    summarize,
)
from scripts.run_legacy_psi_benchmark import report, verify_files
from dsio_inventory_engine.simulate_inventory.application.legacy_reference import (
    run_legacy_reference,
)


@pytest.fixture(scope="module")
def payload():
    protocol = json.loads(PROTOCOL.read_text())
    data = make_input(protocol)
    requests = requests_from_input(data, 1)
    rows = [run_legacy_reference(request) for request in requests]
    return serialize_results(rows, requests, "a" * 64, 1, 32, 52)


def test_protocol_and_input_are_frozen_deterministic_and_not_operational():
    protocol = json.loads(PROTOCOL.read_text())
    data = make_input(protocol)
    assert data == make_input(protocol)
    assert len(data["candidates"]) == 32
    assert len(data["calendar"]) == 52
    assert len(data["items"]) == 16
    assert protocol["status"] == "FROZEN_BEFORE_MEASUREMENT"
    assert protocol["dataset"]["source"].startswith("DEVELOPMENT")
    assert protocol["execution"]["measured_calls"] == 7
    assert protocol["execution"]["launches_per_language_workload"] == 3
    assert protocol["measurement"]["stop_on_any_mismatch"] is True
    assert protocol["measurement"]["compare_cost_or_selection"] is False
    assert HEADER.size == 56 and ROW.size == 132 and len(NUMBERS) == 14


def test_binary_contains_every_row_and_complete_input_binding(payload):
    header = HEADER.unpack_from(payload)
    assert header == (b"BPSI0001", bytes.fromhex("a" * 64), 1664, 1, 32, 52)
    assert len(payload) == HEADER.size + 1664 * ROW.size
    for index in range(1664):
        row = ROW.unpack_from(payload, HEADER.size + index * ROW.size)
        assert row[:3] == (0, index // 52, index % 52)
    assert payload == serialize_results(
        [
            run_legacy_reference(r)
            for r in requests_from_input(make_input(json.loads(PROTOCOL.read_text())), 1)
        ],
        requests_from_input(make_input(json.loads(PROTOCOL.read_text())), 1),
        "a" * 64,
        1,
        32,
        52,
    )


def test_exclusive_store_fsyncs_without_overwriting(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("os.fsync", lambda fd: calls.append(fd))
    target = tmp_path / "new.bin"
    store_new(target, b"original")
    assert len(calls) == 1
    with pytest.raises(FileExistsError):
        store_new(target, b"overwrite")
    assert target.read_bytes() == b"original"


def test_verify_every_payload_not_just_a_claimed_hash(tmp_path, payload):
    folder = tmp_path / "results/run"
    folder.mkdir(parents=True)
    (folder / "data.bin").write_bytes(payload)
    sample = {
        "file": "data.bin",
        "bytes": len(payload),
        "rows": 1664,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    record = {
        "output_directory": "run",
        "input_sha256": "a" * 64,
        "items": 1,
        "candidates": 32,
        "weeks": 52,
        "samples": [sample],
    }
    expected = {"name": "ONE_ITEM", "items": 1, "rows": 1664}
    result = verify_files(tmp_path, [record, record], input_hash="a" * 64, workload=expected)
    assert result["files_verified"] == 2
    changed = copy.deepcopy(record)
    changed["samples"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="HASH"):
        verify_files(tmp_path, [changed], input_hash="a" * 64, workload=expected)
    changed["samples"][0]["file"] = "../outside.bin"
    with pytest.raises(ValueError, match="PATH"):
        verify_files(tmp_path, [changed], input_hash="a" * 64, workload=expected)
    (folder / "data.bin").write_bytes(payload[:-1])
    with pytest.raises(ValueError, match="ROW_COUNT"):
        verify_files(tmp_path, [record], input_hash="a" * 64, workload=expected)
    (folder / "data.bin").write_bytes(b"cut")
    with pytest.raises(ValueError, match="TRUNCATED_HEADER"):
        verify_files(tmp_path, [record], input_hash="a" * 64, workload=expected)


def test_phase_membership_is_not_replaceable_by_duplicate_runs(tmp_path):
    protocol = json.loads(PROTOCOL.read_text())
    (tmp_path / "input.json").write_text("{}")
    record = {"workload": "ONE_ITEM", "language": "csharp", "tag": "launch0"}
    with pytest.raises(ValueError, match="MISSING_OR_DUPLICATE_RUN"):
        report(tmp_path, [record] * 8, protocol)


@pytest.mark.parametrize("values", [[], [-1], [float("nan")], [float("inf")]])
def test_invalid_samples_cannot_produce_statistics(values):
    with pytest.raises(ValueError):
        summarize(values)


def test_p95_and_median_are_defined_without_dropping_outliers():
    result = summarize(range(1, 22))
    assert result["count"] == 21 and result["median"] == 11 and result["p95"] == 20
    assert result["min"] == 1 and result["max"] == 21


def test_preparation_cannot_generate_inside_source_or_repository(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(ValueError):
        prepare(source, source / "stage")
    with pytest.raises(ValueError):
        prepare(source, Path(__file__).parent / "not-generated")
    assert not (source / "stage").exists()


@pytest.mark.parametrize("items", [0, 17])
def test_invalid_workload_is_not_silently_resized(items):
    with pytest.raises(ValueError):
        requests_from_input(make_input(json.loads(PROTOCOL.read_text())), items)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "negative_time", "wrong_total"])
def test_raw_result_membership_and_phase_totals_are_rechecked(tmp_path, mutation):
    architecture = PROTOCOL.parent.parent
    raw = json.loads(
        (architecture / "evidence/legacy-psi-benchmark-results-20261021.json").read_text()
    )
    (tmp_path / "input.json").write_bytes(
        (architecture / "data/legacy-psi-benchmark-input-20261021.json").read_bytes()
    )
    target = next(
        r for r in raw["records"] if r["workload"] == "ONE_ITEM" and r["tag"] == "launch0"
    )
    if mutation == "missing":
        target["samples"].pop()
    elif mutation == "duplicate":
        target["samples"][-1] = target["samples"][-2]
    elif mutation == "negative_time":
        target["samples"][0]["compute_seconds"] = -1
    else:
        target["samples"][0]["total_seconds"] += 1
    with pytest.raises(
        ValueError,
        match="BENCHMARK_(MISSING_OR_DUPLICATE_SAMPLE|INVALID_TIMING|PHASE_TOTAL_MISMATCH)",
    ):
        report(tmp_path, raw["records"], raw["protocol"])

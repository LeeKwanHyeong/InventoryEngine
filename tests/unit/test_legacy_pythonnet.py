"""Dependency-free guard/measurement tests; real CLR checks use the opt-in experiment."""

from dataclasses import fields
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.benchmark_legacy_psi import HEADER, ROW
from scripts.benchmark_legacy_pythonnet import expected_worker_exit
from scripts.run_legacy_pythonnet_experiment import PROTOCOL, docker_command, verify_and_summarize
from dsio_inventory_engine.infrastructure.legacy_pythonnet import (
    LegacyDotnetResult,
    LegacyPythonNetAdapter,
    MAX_INPUT_BYTES,
    MAX_OUTPUT_BYTES,
    verify_payload,
)


def payload(items, source_hash):
    rows = items * 32 * 52
    result = bytearray(HEADER.pack(b"BPSI0001", bytes.fromhex(source_hash), rows, items, 32, 52))
    for index in range(rows):
        result.extend(
            ROW.pack(index // (32 * 52), (index // 52) % 32, index % 52, 0, 0, *([0.0] * 14))
        )
    return bytes(result)


def test_import_does_not_load_pythonnet_or_clr():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import dsio_inventory_engine.infrastructure.legacy_pythonnet; assert 'clr' not in sys.modules; assert 'pythonnet' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("items", [0, -1, 17, True, 1.0])
def test_dimension_guard_precedes_managed_call(items):
    adapter = object.__new__(LegacyPythonNetAdapter)
    with pytest.raises(ValueError, match="ITEM_COUNT"):
        adapter.prepare(b"{}", items, expected_source_sha256=hashlib.sha256(b"{}").hexdigest())
    with pytest.raises(ValueError, match="ITEM_COUNT"):
        verify_payload(b"", "a" * 64, items)


@pytest.mark.parametrize("raw", [b"", b"a" * (MAX_INPUT_BYTES + 1), "{}"])
def test_request_size_type_guard_precedes_loading(raw):
    adapter = object.__new__(LegacyPythonNetAdapter)
    with pytest.raises(ValueError, match="INPUT_LIMIT"):
        adapter.prepare(raw, 1, expected_source_sha256="0" * 64)


def test_input_hash_guard_prevents_a_managed_call():
    adapter = object.__new__(LegacyPythonNetAdapter)
    with pytest.raises(ValueError, match="INPUT_HASH_MISMATCH"):
        adapter.run(b"{}", 1, expected_source_sha256="0" * 64)


def test_dll_tampering_and_config_are_rejected_before_dependency_import(tmp_path):
    dll = tmp_path / "kernel.dll"
    dll.write_bytes(b"trusted-code")
    with pytest.raises(ValueError, match="DLL_HASH_MISMATCH"):
        LegacyPythonNetAdapter(dll, expected_dll_sha256="0" * 64)
    with pytest.raises(ValueError, match="RUNTIME_CONFIG_MISSING"):
        LegacyPythonNetAdapter(
            dll, expected_dll_sha256=hashlib.sha256(dll.read_bytes()).hexdigest()
        )


def test_complete_binary_binding_and_bounds():
    raw = payload(1, "a" * 64)
    assert verify_payload(raw, "a" * 64, 1) == hashlib.sha256(raw).hexdigest()
    assert MAX_OUTPUT_BYTES == HEADER.size + 16 * 32 * 52 * ROW.size
    with pytest.raises(ValueError, match="PAYLOAD_SIZE"):
        verify_payload(raw[:-1], "a" * 64, 1)
    with pytest.raises(ValueError, match="PAYLOAD_BINDING"):
        verify_payload(raw, "b" * 64, 1)
    with pytest.raises(ValueError, match="SOURCE_HASH"):
        verify_payload(raw, "invalid", 1)


def test_oversize_managed_return_rejected_before_pointer_copy():
    adapter = object.__new__(LegacyPythonNetAdapter)
    oversized = type("Managed", (), {"Length": MAX_OUTPUT_BYTES + 1})()
    with pytest.raises(ValueError, match="OUTPUT_LIMIT"):
        adapter._to_python(oversized)


def test_diagnostic_protocol_pins_existing_input_and_preserves_operational_boundaries():
    protocol = json.loads(PROTOCOL.read_text())
    source = PROTOCOL.parent / "legacy-psi-benchmark-input-20261021.json"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == protocol["input_sha256"]
    assert protocol["scope"] == "OFFLINE_DIAGNOSTIC_ONLY_NO_RUNTIME_REGISTRATION"
    assert protocol["launches"] == 3 and protocol["measured_calls"] == 7
    assert protocol["measurement"]["native_loop_unchanged"] is True
    assert protocol["measurement"]["abort_on_mismatch"] is True
    assert "DB" in protocol["unchanged"] and "FSN" in protocol["unchanged"]
    assert {f.name for f in fields(LegacyDotnetResult)} >= {
        "input_copy_seconds",
        "prepare_seconds",
        "compute_seconds",
        "serialize_seconds",
        "managed_call_seconds",
        "output_copy_seconds",
        "verify_seconds",
        "total_seconds",
    }


def test_isolated_container_limits_and_no_network():
    args = docker_command(Path("/private/tmp/safe"), "sha256:" + "a" * 64, "test")
    assert args[args.index("--network") + 1] == "none"
    assert args[args.index("--cpus") + 1] == "1"
    assert "--read-only" in args
    assert "--privileged" not in args
    assert not any("docker.sock" in arg or ".env" in arg for arg in args)
    with pytest.raises(ValueError, match="PINNED_IMAGE"):
        docker_command(Path("/tmp"), "latest", "test")


@pytest.mark.parametrize("code", [-15, 143])
def test_coreclr_sigterm_exit_can_be_signal_or_managed_exit(code):
    assert expected_worker_exit("sigterm", code)
    assert not expected_worker_exit("sigterm", 0)
    assert expected_worker_exit("sigkill", -9)
    assert not expected_worker_exit("sigkill", 143)


@pytest.fixture
def fake_run(tmp_path, monkeypatch):
    raw = b"fixed"
    digest = hashlib.sha256(raw).hexdigest()
    protocol = json.loads(PROTOCOL.read_text())
    protocol["input_sha256"] = digest
    bodies = {items: payload(items, digest) for items in (1, 16)}
    for workload in protocol["workloads"]:
        workload["expected_payload_sha256"] = hashlib.sha256(bodies[workload["items"]]).hexdigest()
    original = Path.read_bytes

    def read(path):
        if path == tmp_path / "input.json":
            return raw
        if path.suffix == ".bin":
            return bodies[int(path.parent.name.split("-")[-2])]
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    records = []
    for workload in protocol["workloads"]:
        items = workload["items"]
        for mode in protocol["modes"]:
            for launch in range(3):
                body = bodies[items]
                samples = []
                for index in range(11):
                    samples.append(
                        dict(
                            phase="cold" if index == 0 else "warmup" if index <= 3 else "measured",
                            index=index,
                            file=f"{index}.bin",
                            bytes=len(body),
                            rows=items * 32 * 52,
                            sha256=hashlib.sha256(body).hexdigest(),
                            input_copy_seconds=0.1,
                            prepare_seconds=0.1,
                            managed_call_seconds=0.5,
                            compute_seconds=0.3,
                            serialize_seconds=0.2,
                            output_copy_seconds=0.1,
                            verify_seconds=0.2,
                            total_seconds=1.0,
                            store_seconds=0.1,
                            with_store_seconds=1.1,
                        )
                    )
                records.append(
                    dict(
                        mode=mode,
                        items=items,
                        launch=launch,
                        samples=samples,
                        directory=f"{mode}-{items}-{launch}",
                        peak_rss_bytes=1024,
                        input_sha256=digest,
                    )
                )
    return tmp_path, records, protocol


def test_report_checks_every_cold_warmup_and_measured_file(fake_run):
    result = verify_and_summarize(*fake_run)
    assert result["1"]["files_verified"] == 99
    assert result["16"]["files_verified"] == 99
    assert result["16"]["groups"]["pythonnet"]["timings"]["total_seconds"]["count"] == 21


@pytest.mark.parametrize("mutation", ["launch", "sample", "hash", "count", "path", "time", "total"])
def test_report_rejects_missing_tampered_or_invalid_measurements(fake_run, mutation):
    root, records, protocol = fake_run
    sample = records[0]["samples"][0]
    if mutation == "launch":
        records[-1] = records[0]
    elif mutation == "sample":
        records[0]["samples"].pop()
    elif mutation == "hash":
        sample["sha256"] = "0" * 64
    elif mutation == "count":
        sample["rows"] = 0
    elif mutation == "path":
        sample["file"] = "../other.bin"
    elif mutation == "time":
        sample["total_seconds"] = float("nan")
    else:
        sample["total_seconds"] += 1
    with pytest.raises(ValueError):
        verify_and_summarize(root, records, protocol)

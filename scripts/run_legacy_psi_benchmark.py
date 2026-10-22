"""Run only isolated benchmark containers and verify every complete PSI file."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
from time import perf_counter

from scripts.benchmark_legacy_psi import HEADER, ROW, summarize


def launch(root: Path, image: str, language: str, workload, tag: str, verify_only=False):
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise ValueError("PINNED_LOCAL_IMAGE_ID_REQUIRED")
    name = f"{language}-{workload['name']}-{tag}"
    container = "io-psi-" + root.name.lower() + "-" + name.lower()
    destination = root / "results" / name
    metric = root / "results" / f"{name}.json"
    if destination.exists() or metric.exists():
        raise ValueError("FRESH_BENCHMARK_RUN_REQUIRED")
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        container,
        "--platform",
        "linux/arm64",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--cpus",
        "1",
        "--memory",
        "1g",
        "--memory-swap",
        "1g",
        "--pids-limit",
        "256",
        "--tmpfs",
        "/tmp:rw,exec,nosuid,size=256m",
        "--mount",
        f"type=bind,source={root / 'stage'},target=/source,readonly",
        "--mount",
        f"type=bind,source={root / 'build'},target=/build,readonly",
        "--mount",
        f"type=bind,source={root / 'input.json'},target=/input.json,readonly",
        "--mount",
        f"type=bind,source={root / 'results'},target=/results",
        "--env",
        "DOTNET_CLI_HOME=/tmp/dotnet-cli",
        "--env",
        "DOTNET_CLI_TELEMETRY_OPTOUT=1",
        "--env",
        "DOTNET_TieredCompilation=0",
        "--env",
        "DOTNET_PROCESSOR_COUNT=1",
        "--env",
        "PYTHONHASHSEED=0",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        "--env",
        "PYTHONPATH=/source/src:/source",
        image,
    ]
    if language == "python":
        command += [
            "python3",
            "/source/worker.py",
            "worker",
            "--input",
            "/input.json",
            "--output",
            f"/results/{name}",
            "--items",
            str(workload["items"]),
        ]
    elif language == "csharp":
        command += [
            "dotnet",
            "/build/out/Benchmark.dll",
            "/input.json",
            f"/results/{name}",
            str(workload["items"]),
        ]
    else:
        raise ValueError("BENCHMARK_LANGUAGE")
    if verify_only:
        command.append("--verify-only")
    begin = perf_counter()
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=300)
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "stop", container], capture_output=True, check=False, timeout=15)
        raise
    value = json.loads(result.stdout)
    value.update(
        workload=workload["name"],
        tag=tag,
        container_wall_seconds=perf_counter() - begin,
        command=command,
        stderr=result.stderr,
        output_directory=name,
    )
    with metric.open("x") as target:
        target.write(json.dumps(value, indent=2) + "\n")
    return value


def verify_files(root: Path, records, *, input_hash, workload):
    reference = None
    files = 0
    for record in records:
        if Path(record["output_directory"]).name != record["output_directory"]:
            raise ValueError("BENCHMARK_ARTIFACT_PATH")
        if record["input_sha256"] != input_hash or record["items"] != workload["items"]:
            raise ValueError("BENCHMARK_INPUT_BINDING_MISMATCH")
        if record["candidates"] != 32 or record["weeks"] != 52:
            raise ValueError("BENCHMARK_GRAIN_MISMATCH")
        for sample in record["samples"]:
            if Path(sample["file"]).name != sample["file"]:
                raise ValueError("BENCHMARK_ARTIFACT_PATH")
            path = root / "results" / record["output_directory"] / sample["file"]
            if path.resolve().parent != (root / "results" / record["output_directory"]).resolve():
                raise ValueError("BENCHMARK_ARTIFACT_PATH")
            data = path.read_bytes()
            if len(data) < HEADER.size:
                raise ValueError("BENCHMARK_TRUNCATED_HEADER")
            magic, source, rows, items, candidates, weeks = HEADER.unpack_from(data)
            if (magic, source.hex(), rows, items, candidates, weeks) != (
                b"BPSI0001",
                input_hash,
                workload["rows"],
                workload["items"],
                32,
                52,
            ):
                raise ValueError("BENCHMARK_ARTIFACT_HEADER")
            if len(data) != HEADER.size + ROW.size * rows or len(data) != sample["bytes"]:
                raise ValueError("BENCHMARK_ARTIFACT_ROW_COUNT")
            if sample["rows"] != rows or hashlib.sha256(data).hexdigest() != sample["sha256"]:
                raise ValueError("BENCHMARK_ARTIFACT_HASH")
            if reference is None:
                reference = data
            elif data != reference:
                raise ValueError("CSHARP_PYTHON_PSI_NOT_BYTE_IDENTICAL")
            files += 1
    if reference is None:
        raise ValueError("BENCHMARK_EMPTY_OUTPUT")
    return {
        "files_verified": files,
        "rows_per_file": workload["rows"],
        "bytes_per_file": len(reference),
        "sha256": hashlib.sha256(reference).hexdigest(),
        "equality": "FULL_BINARY_PAYLOAD_EXACT_ALL_ROWS_ALL_SAMPLES",
    }


def report(root: Path, records, protocol):
    result = {}
    input_hash = hashlib.sha256((root / "input.json").read_bytes()).hexdigest()
    for workload in protocol["dataset"]["workloads"]:
        selected = [r for r in records if r["workload"] == workload["name"]]
        identities = {(r["language"], r["tag"]) for r in selected}
        expected_identities = {
            (language, tag)
            for language in ("csharp", "python")
            for tag in ("verify", "launch0", "launch1", "launch2")
        }
        if identities != expected_identities or len(selected) != len(identities):
            raise ValueError("BENCHMARK_MISSING_OR_DUPLICATE_RUN")
        for record in selected:
            expected_phases = (
                {"verification": 1}
                if record["tag"] == "verify"
                else {
                    "cold": protocol["execution"]["cold_calls"],
                    "warmup": protocol["execution"]["warmup_calls"],
                    "measured": protocol["execution"]["measured_calls"],
                }
            )
            actual_phases = {(s["phase"], s["index"]) for s in record["samples"]}
            wanted = {
                (phase, index) for phase, count in expected_phases.items() for index in range(count)
            }
            if actual_phases != wanted or len(record["samples"]) != len(wanted):
                raise ValueError("BENCHMARK_MISSING_OR_DUPLICATE_SAMPLE")
            for sample in record["samples"]:
                phases = [
                    sample[name]
                    for name in (
                        "compute_seconds",
                        "serialize_seconds",
                        "hash_seconds",
                        "store_seconds",
                    )
                ]
                if not all(math.isfinite(v) and v >= 0 for v in phases):
                    raise ValueError("BENCHMARK_INVALID_TIMING")
                if not math.isclose(sum(phases), sample["total_seconds"], abs_tol=1e-8):
                    raise ValueError("BENCHMARK_PHASE_TOTAL_MISMATCH")
        equality = verify_files(root, selected, input_hash=input_hash, workload=workload)
        groups = {}
        for language in ("csharp", "python"):
            runs = [r for r in selected if r["language"] == language and r["tag"] != "verify"]
            if len(runs) != protocol["execution"]["launches_per_language_workload"]:
                raise ValueError("BENCHMARK_MISSING_LAUNCH")
            measured = [s for r in runs for s in r["samples"] if s["phase"] == "measured"]
            expected = len(runs) * protocol["execution"]["measured_calls"]
            if len(measured) != expected:
                raise ValueError("BENCHMARK_MISSING_MEASURED_CALL")
            timings = {
                phase: summarize([s[phase] for s in measured])
                for phase in (
                    "compute_seconds",
                    "serialize_seconds",
                    "hash_seconds",
                    "store_seconds",
                    "total_seconds",
                )
            }
            groups[language] = {
                "runtime": runs[0]["runtime"],
                "timings": timings,
                "preparation_seconds": summarize([r["preparation_seconds"] for r in runs]),
                "cold_total_seconds": summarize(
                    [s["total_seconds"] for r in runs for s in r["samples"] if s["phase"] == "cold"]
                ),
                "peak_rss_bytes": summarize([r["peak_rss_bytes"] for r in runs]),
                "rows_per_compute_second": workload["rows"] / timings["compute_seconds"]["median"],
            }
        ratios = {}
        for phase in ("compute_seconds", "total_seconds"):
            cs = groups["csharp"]["timings"][phase]["median"]
            py = groups["python"]["timings"][phase]["median"]
            ratios[phase] = {
                "csharp_over_python_speed_ratio": cs / py,
                "python_over_csharp_time_ratio": py / cs,
                "python_time_reduction_percent": (cs - py) / cs * 100,
            }
        result[workload["name"]] = {"equality": equality, "groups": groups, "ratios": ratios}
    return result


def run(root: Path, image: str):
    data = json.loads((root / "input.json").read_text())
    protocol = data["protocol"]
    records = []
    for workload in protocol["dataset"]["workloads"]:
        checks = [
            launch(root, image, language, workload, "verify", True)
            for language in ("csharp", "python")
        ]
        verify_files(root, checks, input_hash=checks[0]["input_sha256"], workload=workload)
        records += checks
        print(f"Verified {workload['name']}: {workload['rows']} rows byte-identical", flush=True)
    for index, languages in enumerate(protocol["execution"]["launch_language_order"]):
        for workload in protocol["dataset"]["workloads"]:
            for language in languages:
                record = launch(root, image, language, workload, f"launch{index}")
                records.append(record)
                verify_files(
                    root,
                    [r for r in records if r["workload"] == workload["name"]],
                    input_hash=record["input_sha256"],
                    workload=workload,
                )
                print(f"Completed {language} {workload['name']} launch {index + 1}/3", flush=True)
    result = {
        "image_id": image,
        "protocol": protocol,
        "input_sha256": records[0]["input_sha256"],
        "records": records,
        "summary": report(root, records, protocol),
    }
    with (root / "benchmark-result.json").open("x") as target:
        target.write(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    run(args.root.resolve(), args.image)

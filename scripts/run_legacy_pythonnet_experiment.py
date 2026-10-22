"""Isolated, pinned image experiment: exact PSI output, timing and process cancellation."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
from time import perf_counter

from scripts.benchmark_legacy_psi import HEADER, ROW, ROOT, summarize

PROTOCOL = ROOT / "docs/architecture/data/legacy-pythonnet-experiment-protocol-v1.json"


def docker_command(root: Path, image: str, name: str) -> list[str]:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise ValueError("PINNED_IMAGE_ID_REQUIRED")
    return [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
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
        f"type=bind,source={root / 'build/out'},target=/build,readonly",
        "--mount",
        f"type=bind,source={root / 'input.json'},target=/input.json,readonly",
        "--mount",
        f"type=bind,source={root / 'results'},target=/results",
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


def call(root: Path, image: str, name: str, command: list[str]) -> dict:
    container = "io-pnet-" + root.parent.name.lower() + "-" + name
    args = docker_command(root, image, container) + command
    begin = perf_counter()
    try:
        output = subprocess.run(args, capture_output=True, text=True, check=True, timeout=180)
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"EXPERIMENT_WORKER_FAILED:{name}:{error.stderr[-4000:]}") from error
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", container], capture_output=True, timeout=15, check=False)
        raise
    value = json.loads(output.stdout)
    value.update(container_seconds=perf_counter() - begin, command=args, stderr=output.stderr)
    with (root / "results" / f"{name}.json").open("x") as target:
        json.dump(value, target, indent=2)
    return value


def verify_and_summarize(root: Path, records: list[dict], protocol: dict) -> dict:
    digest = hashlib.sha256((root / "input.json").read_bytes()).hexdigest()
    if digest != protocol["input_sha256"]:
        raise ValueError("EXPERIMENT_INPUT_CHANGED")
    expected = {
        (m, w["items"], launch)
        for m in protocol["modes"]
        for w in protocol["workloads"]
        for launch in range(protocol["launches"])
    }
    actual = {(r["mode"], r["items"], r["launch"]) for r in records}
    if actual != expected or len(actual) != len(records):
        raise ValueError("MISSING_OR_DUPLICATE_LAUNCH")
    output = {}
    for workload in protocol["workloads"]:
        items = workload["items"]
        selected = [r for r in records if r["items"] == items]
        reference = None
        files = 0
        for record in selected:
            if (
                record["input_sha256"] != digest
                or Path(record["directory"]).name != record["directory"]
            ):
                raise ValueError("INPUT_OR_DIRECTORY_MISMATCH")
            if [(s["phase"], s["index"]) for s in record["samples"]] != (
                [("cold", 0)]
                + [("warmup", i) for i in range(1, 4)]
                + [("measured", i) for i in range(4, 11)]
            ):
                raise ValueError("MISSING_OR_DUPLICATE_SAMPLE")
            for sample in record["samples"]:
                if Path(sample["file"]).name != sample["file"]:
                    raise ValueError("PAYLOAD_PATH")
                payload = (root / "results" / record["directory"] / sample["file"]).read_bytes()
                if len(payload) != HEADER.size + workload["rows"] * ROW.size:
                    raise ValueError("PAYLOAD_SIZE")
                if HEADER.unpack_from(payload) != (
                    b"BPSI0001",
                    bytes.fromhex(digest),
                    workload["rows"],
                    items,
                    32,
                    52,
                ):
                    raise ValueError("PAYLOAD_BINDING")
                if (
                    sample["sha256"] != hashlib.sha256(payload).hexdigest()
                    or sample["bytes"] != len(payload)
                    or sample["rows"] != workload["rows"]
                ):
                    raise ValueError("PAYLOAD_HASH_OR_COUNT")
                if reference is None:
                    reference = payload
                    if hashlib.sha256(payload).hexdigest() != workload["expected_payload_sha256"]:
                        raise ValueError("PREVIOUS_REFERENCE_OUTPUT_CHANGED")
                    for index, row in enumerate(ROW.iter_unpack(payload[HEADER.size :])):
                        if row[:3] != (index // (32 * 52), (index // 52) % 32, index % 52):
                            raise ValueError("PSI_ROW_COVERAGE")
                        if not all(math.isfinite(n) for n in row[5:]):
                            raise ValueError("NON_FINITE_PSI")
                elif payload != reference:
                    raise ValueError("PSI_BYTES_DIFFER")
                for key, value in sample.items():
                    if key.endswith("_seconds") and (not math.isfinite(value) or value < 0):
                        raise ValueError("INVALID_TIMING")
                phase_sum = sum(
                    sample[k]
                    for k in (
                        "input_copy_seconds",
                        "prepare_seconds",
                        "managed_call_seconds",
                        "output_copy_seconds",
                        "verify_seconds",
                    )
                )
                if not math.isclose(phase_sum, sample["total_seconds"], abs_tol=1e-7):
                    raise ValueError("TIMING_SUM")
                files += 1
        groups = {}
        for mode in protocol["modes"]:
            runs = [r for r in selected if r["mode"] == mode]
            measured = [s for r in runs for s in r["samples"] if s["phase"] == "measured"]
            groups[mode] = {
                "timings": {
                    key: summarize([s[key] for s in measured])
                    for key in measured[0]
                    if key.endswith("_seconds")
                },
                "peak_rss_bytes": summarize([r["peak_rss_bytes"] for r in runs]),
                "load_seconds": summarize([r.get("load_seconds", 0.0) for r in runs]),
                "cold_total_seconds": summarize([r["samples"][0]["total_seconds"] for r in runs]),
            }

        def total(mode):
            return groups[mode]["timings"]["total_seconds"]["median"]

        output[str(items)] = dict(
            files_verified=files,
            rows_per_file=workload["rows"],
            bytes_per_file=len(reference),
            payload_sha256=hashlib.sha256(reference).hexdigest(),
            groups=groups,
            python_reference_over_pythonnet_total_ratio=total("python_reference")
            / total("pythonnet"),
            pythonnet_over_direct_total_ratio=total("pythonnet") / total("csharp_direct"),
        )
    return output


def run(root: Path, image: str) -> dict:
    protocol = json.loads(PROTOCOL.read_text())
    if hashlib.sha256((root / "input.json").read_bytes()).hexdigest() != protocol["input_sha256"]:
        raise ValueError("EXPERIMENT_INPUT_CHANGED")
    records = []
    for workload in protocol["workloads"]:
        for launch in range(3):
            modes = protocol["modes"][launch:] + protocol["modes"][:launch]
            for mode in modes:
                name = f"{mode}-{workload['items']}-{launch}"
                if mode == "csharp_direct":
                    command = [
                        "dotnet",
                        "/build/PythonNet.Direct.dll",
                        "/input.json",
                        f"/results/{name}",
                        str(workload["items"]),
                    ]
                else:
                    command = [
                        "python3",
                        "/source/scripts/benchmark_legacy_pythonnet.py",
                        "worker",
                        "--input",
                        "/input.json",
                        "--output",
                        f"/results/{name}",
                        "--build",
                        "/build",
                        "--items",
                        str(workload["items"]),
                        "--mode",
                        mode,
                    ]
                value = call(root, image, name, command)
                value.update(launch=launch, directory=name)
                records.append(value)
                print(f"finished {name}", flush=True)
    summary = verify_and_summarize(root, records, protocol)
    probes = {}
    for kind in ("state", "cooperative", "supervise"):
        command = [
            "python3",
            "/source/scripts/benchmark_legacy_pythonnet.py",
            "supervise" if kind == "supervise" else "probe",
            "--input",
            "/input.json",
            "--build",
            "/build",
        ]
        if kind != "supervise":
            command += ["--kind", kind]
        probes[kind] = call(root, image, "probe-" + kind, command)
    expected = summary["16"]["payload_sha256"]
    if probes["cooperative"]["resumed_sha256"] != expected:
        raise ValueError("CANCEL_RECOVERY_DIFFERENT")
    if any(check["recovery"]["sha256"] != expected for check in probes["supervise"]["checks"]):
        raise ValueError("KILL_RECOVERY_DIFFERENT")
    result = dict(
        protocol=protocol,
        protocol_sha256=hashlib.sha256(PROTOCOL.read_bytes()).hexdigest(),
        image_id=image,
        summary=summary,
        probes=probes,
        records=records,
        dll_sha256=hashlib.sha256(
            (root / "build/out/Inventory.Legacy.Kernel.dll").read_bytes()
        ).hexdigest(),
    )
    with (root / "pythonnet-result.json").open("x") as target:
        json.dump(result, target, indent=2)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    value = run(args.root.resolve(), args.image)
    print(json.dumps({"summary": value["summary"], "probes": value["probes"]}, indent=2))

"""Frozen, serial C#/Python PSI benchmark tooling; no engine optimization.

prepare writes only a fresh private staging directory. worker writes only new
benchmark files. All current product calculation modules are copied unchanged.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import hashlib
import io
import json
import math
from pathlib import Path
import platform
import resource
import shutil
import statistics
import struct
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "docs/architecture/data/legacy-psi-benchmark-protocol-v1.json"
HEADER = struct.Struct("<8s32siiii")
ROW = struct.Struct("<iiiii14d")
NUMBERS = (
    "demand",
    "due_in",
    "boh",
    "eoh",
    "backorder",
    "backorder_close",
    "pending_po_close",
    "purchase_order",
    "demand_allocation",
    "stock_allocation",
    "weekly_allocation_rate",
    "exp_mos",
    "rop",
    "target",
)


def make_input(protocol):
    first = date.fromisoformat(protocol["dataset"]["start_date"])
    weeks = protocol["dataset"]["weeks"]
    calendar = [
        {
            "bucket_id": f"W{week:02d}",
            "start": (first + timedelta(weeks=week)).isoformat(),
            "end": (first + timedelta(weeks=week, days=6)).isoformat(),
        }
        for week in range(weeks)
    ]
    items = []
    for item in range(max(w["items"] for w in protocol["dataset"]["workloads"])):
        items.append(
            {
                "boh": 5.25 + item * 0.5,
                "backorder": (item % 3) * 0.25,
                "pending_po": 8.0,
                "internal_lead_time": protocol["dataset"]["lead_times_internal"][item % 4],
                "lot": {"minimum": 0.25, "maximum": 80.25, "step": 0.5},
                "demands": [
                    {
                        "when": bucket["start"],
                        "quantity": 0.0
                        if (week + item) % 9 == 0
                        else ((week * 7 + item * 3) % 23 + 1) * 0.25,
                    }
                    for week, bucket in enumerate(calendar)
                ],
                "receipts": [
                    {"when": calendar[week]["start"], "quantity": 2.0} for week in (0, 7, 21, 39)
                ],
            }
        )
    candidates = []
    for rop in (4.0, 8.0, 12.0, 20.0):
        for increment in (2.0, 4.0, 6.0, 8.0, 12.0, 16.0, 24.0, 32.0):
            index = len(candidates)
            candidates.append(
                {
                    "rop": rop,
                    "target": rop + increment,
                    "mos": 1.25,
                    "ma_demand": 2.5,
                    "stock_policy": protocol["dataset"]["policy_codes"][index % 4],
                }
            )
    return {"protocol": protocol, "calendar": calendar, "items": items, "candidates": candidates}


def prepare(source_root: Path, output: Path):
    from tests.reference.legacy_csharp.prepare_legacy_reference import stage

    output = output.resolve()
    if (
        output.exists()
        or output.is_relative_to(ROOT)
        or output.is_relative_to(source_root.resolve())
    ):
        raise ValueError("FRESH_EXTERNAL_BENCHMARK_DIRECTORY_REQUIRED")
    protocol = json.loads(PROTOCOL.read_text())
    output.mkdir()
    staged = output / "stage"
    manifest = stage(source_root, staged)
    base = ROOT / "tests/reference/legacy_csharp"
    for name in ("BenchmarkHarness.cs", "Benchmark.csproj", "Benchmark.Dockerfile"):
        shutil.copyfile(base / name, staged / name)
    shutil.copyfile(Path(__file__), staged / "worker.py")
    package = staged / "src/dsio_inventory_engine/simulate_inventory/application"
    package.mkdir(parents=True)
    for p in (package, package.parent, package.parent.parent):
        (p / "__init__.py").write_text("")
    original = ROOT / "src/dsio_inventory_engine/simulate_inventory/application"
    for name in ("legacy_reference.py", "legacy_numbers.py"):
        shutil.copyfile(original / name, package / name)
    payload = (json.dumps(make_input(protocol), indent=2) + "\n").encode()
    (output / "input.json").write_bytes(payload)
    (output / "build").mkdir()
    (output / "results").mkdir()
    result = {
        "input_sha256": hashlib.sha256(payload).hexdigest(),
        "protocol_sha256": hashlib.sha256(PROTOCOL.read_bytes()).hexdigest(),
        "source_manifest": manifest,
        "staged_files": [
            {
                "path": str(p.relative_to(staged)),
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            }
            for p in sorted(staged.rglob("*"))
            if p.is_file()
        ],
    }
    (output / "preparation.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def requests_from_input(data, item_count):
    from dsio_inventory_engine.simulate_inventory.application.legacy_reference import (
        LegacyBucket,
        LegacyDatedQuantity,
        LegacyLot,
        LegacyPolicy,
        LegacyPsiRequest,
    )

    if not 0 < item_count <= len(data["items"]):
        raise ValueError("BENCHMARK_ITEM_COUNT")
    buckets = tuple(
        LegacyBucket(b["bucket_id"], date.fromisoformat(b["start"]), date.fromisoformat(b["end"]))
        for b in data["calendar"]
    )
    requests = []
    for item in data["items"][:item_count]:
        demands = tuple(
            LegacyDatedQuantity(date.fromisoformat(r["when"]), r["quantity"])
            for r in item["demands"]
        )
        receipts = tuple(
            LegacyDatedQuantity(date.fromisoformat(r["when"]), r["quantity"])
            for r in item["receipts"]
        )
        for candidate in data["candidates"]:
            policies = (
                LegacyPolicy(
                    date(2026, 1, 1),
                    date(2027, 12, 31),
                    *(candidate[k] for k in ("rop", "target", "mos", "ma_demand")),
                ),
            )
            requests.append(
                LegacyPsiRequest(
                    buckets,
                    demands=demands,
                    receipts=receipts,
                    policies=policies,
                    boh=item["boh"],
                    backorder=item["backorder"],
                    pending_po=item["pending_po"],
                    internal_lead_time=item["internal_lead_time"],
                    stock_policy=candidate["stock_policy"],
                    lot=LegacyLot(*(item["lot"][k] for k in ("minimum", "maximum", "step"))),
                )
            )
    return requests


def serialize_results(results, requests, input_hash, items, candidates, weeks):
    out = io.BytesIO()
    out.write(
        HEADER.pack(
            b"BPSI0001",
            bytes.fromhex(input_hash),
            items * candidates * weeks,
            items,
            candidates,
            weeks,
        )
    )
    for index, (rows, request) in enumerate(zip(results, requests, strict=True)):
        days = int(request.internal_lead_time / 28800.0)
        for week, row in enumerate(rows):
            order = row.bucket.start.toordinal() if row.purchase_order > 0 else 0
            due = order + days if order else 0
            out.write(
                ROW.pack(
                    index // candidates,
                    index % candidates,
                    week,
                    order,
                    due,
                    *(getattr(row, name) for name in NUMBERS),
                )
            )
    return out.getvalue()


def store_new(path: Path, payload: bytes):
    import os

    with path.open("xb") as target:
        target.write(payload)
        target.flush()
        os.fsync(target.fileno())


def sample(requests, *, input_hash, items, candidates, weeks, path):
    from dsio_inventory_engine.simulate_inventory.application.legacy_reference import (
        run_legacy_reference,
    )

    begin = perf_counter()
    results = [run_legacy_reference(request) for request in requests]
    computed = perf_counter()
    payload = serialize_results(results, requests, input_hash, items, candidates, weeks)
    encoded = perf_counter()
    digest = hashlib.sha256(payload).hexdigest()
    hashed = perf_counter()
    store_new(path, payload)
    end = perf_counter()
    return {
        "compute_seconds": computed - begin,
        "serialize_seconds": encoded - computed,
        "hash_seconds": hashed - encoded,
        "store_seconds": end - hashed,
        "total_seconds": end - begin,
        "bytes": len(payload),
        "sha256": digest,
        "file": path.name,
        "rows": items * candidates * weeks,
    }


def worker(input_path: Path, output: Path, item_count: int, verify_only: bool = False):
    started = perf_counter()
    raw = input_path.read_bytes()
    input_hash = hashlib.sha256(raw).hexdigest()
    data = json.loads(raw)
    requests = requests_from_input(data, item_count)
    prepared = perf_counter()
    execution = data["protocol"]["execution"]
    candidates, weeks = len(data["candidates"]), len(data["calendar"])
    output.mkdir()
    samples = []
    phases = (
        (("verification", execution["verification_calls"]),)
        if verify_only
        else (
            ("cold", execution["cold_calls"]),
            ("warmup", execution["warmup_calls"]),
            ("measured", execution["measured_calls"]),
        )
    )
    for phase, count in phases:
        for index in range(count):
            samples.append(
                {
                    "phase": phase,
                    "index": index,
                    **sample(
                        requests,
                        input_hash=input_hash,
                        items=item_count,
                        candidates=candidates,
                        weeks=weeks,
                        path=output / f"{phase}-{index}.bin",
                    ),
                }
            )
    return {
        "language": "python",
        "runtime": platform.python_version(),
        "architecture": platform.machine(),
        "items": item_count,
        "candidates": candidates,
        "weeks": weeks,
        "input_sha256": input_hash,
        "preparation_seconds": prepared - started,
        "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
        "samples": samples,
    }


def summarize(values):
    values = sorted(values)
    if not values or not all(math.isfinite(v) and v >= 0 for v in values):
        raise ValueError("INVALID_BENCHMARK_SAMPLES")
    return {
        "count": len(values),
        "median": statistics.median(values),
        "p95": values[math.ceil(0.95 * len(values)) - 1],
        "min": values[0],
        "max": values[-1],
        "population_stdev": statistics.pstdev(values),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--source-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("worker")
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--items", type=int, required=True)
    p.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    result = (
        prepare(args.source_root, args.output)
        if args.command == "prepare"
        else worker(args.input, args.output, args.items, args.verify_only)
    )
    print(json.dumps(result, indent=2))

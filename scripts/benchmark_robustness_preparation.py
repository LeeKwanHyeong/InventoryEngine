"""Fresh-process paired preparation comparison, not an official policy experiment.

One literal 2055 path, all 32/32/88 candidates, three rotated repetitions.
Measures local calculation only: no storage/read speed or cold-cache claim.
The disabled variant still includes the identical input-hash hoisting change;
this isolates preparation-slot reuse rather than reconstructing an older build.
"""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import platform
import statistics
import subprocess
import sys
from time import perf_counter_ns

from scripts.benchmark_robustness_storage import source_manifest, peak_rss_bytes, write_new
from scripts.verify_robustness_shared_storage import EXPECTED_RESULTS
from dsio_inventory_engine.evaluate_replenishment.robustness.item_execution import (
    RunItemCatalogUseCase,
)
from dsio_inventory_engine.evaluate_replenishment.robustness.item_execution_steps import (
    prepare_context,
    execute_leaf,
    finish_result,
)
from dsio_inventory_engine.inventory_contracts.values import canonical_json, digest, require
from dsio_inventory_engine.inventory_contracts.robustness_storage import (
    MAX_RAW_BYTES,
    MAX_EXPERIMENT_RAW_BYTES,
    MAX_INVOCATION_SECONDS,
)

SPEC = {
    "contract_id": "inventory-robustness-preparation-comparison",
    "contract_version": "1.0.0",
    "manual_w0": "2055-01-04",
    "manual_path_id": "HAND-CATALOG-PATH",
    "manual_seed": 71,
    "weeks": 65,
    "modes": {"ABC_XYZ_SHARED_BUDGET": 32, "LEGACY_STYLE": 32, "WIDE_REFERENCE": 88},
    "variants": ["PREPARATION_EACH_CANDIDATE", "IMMUTABLE_REUSE"],
    "repetitions": 3,
    "workers": 1,
    "max_retained_bytes": MAX_RAW_BYTES,
    "max_raw_storage_bytes": MAX_EXPERIMENT_RAW_BYTES,
    "max_invocation_seconds": MAX_INVOCATION_SECONDS,
    "measures_io": False,
    "clears_os_cache": False,
    "official_paths_generated": False,
    "database_writes": False,
}


def sources():
    value = source_manifest()
    for name in ("benchmark_robustness_preparation.py", "verify_robustness_shared_storage.py"):
        path = Path(__file__).with_name(name)
        value["scripts/" + name] = sha256(path.read_bytes()).hexdigest()
    return value


def trial(mode, variant, repetition, source_hash):
    from tests.integration.test_robustness_durable_offline import prepared
    from tests.support.robustness_item_fixtures import DEV

    require(digest(sources()) == source_hash, "PREPARATION_COMPARISON_SOURCE_CHANGED")
    start = perf_counter_ns()
    case, assembly, path = prepared(mode=mode)
    runner = RunItemCatalogUseCase(DEV, case["producer"])
    ctx = prepare_context(
        runner.preparer,
        DEV,
        assembly,
        expected_assembly_hash=assembly["content_hash"],
        realization=path,
        expected_realization_hash=path.content_hash,
        reuse_preparation=variant == "IMMUTABLE_REUSE",
    )
    phases = {"CONTEXT_PREPARATION": perf_counter_ns() - start}
    preparation_ns = 0
    original = ctx.runner.preparation.prepare

    def measured(*args, **kwargs):
        nonlocal preparation_ns
        begin = perf_counter_ns()
        try:
            return original(*args, **kwargs)
        finally:
            preparation_ns += perf_counter_ns() - begin

    ctx.runner.preparation.prepare = measured
    begin = perf_counter_ns()
    leaves = [execute_leaf(ctx, i) for i in range(ctx.leaf_count)]
    phases["CANDIDATE_COMPUTE_INCLUDING_PREPARATION"] = perf_counter_ns() - begin
    begin = perf_counter_ns()
    result = finish_result(ctx, leaves)
    phases["ASSEMBLY_AND_EVALUATION"] = perf_counter_ns() - begin
    total = perf_counter_ns() - start
    count = SPEC["modes"][mode]
    require(
        result["content_hash"] == EXPECTED_RESULTS[mode], "PREPARATION_COMPARISON_RESULT_CHANGED"
    )
    children = [c for leaf in leaves for c in leaf["candidate_executions"]]
    require(
        len(children) == count
        and all(c["status"] == "SUCCEEDED" for c in children)
        and sum(len(c["causal_result"]["psi_rows"]) for c in children) == 65 * count,
        "PREPARATION_COMPARISON_COVERAGE_CHANGED",
    )
    slot = ctx.runner.preparation
    require(
        (slot.preparations, slot.hits)
        == ((1, count - 1) if variant == "IMMUTABLE_REUSE" else (count, 0)),
        "PREPARATION_COMPARISON_REUSE_NOT_EXECUTED",
    )
    require(total <= MAX_INVOCATION_SECONDS * 1_000_000_000, "PREPARATION_COMPARISON_TIMEOUT")
    require(digest(sources()) == source_hash, "PREPARATION_COMPARISON_SOURCE_CHANGED")
    return {
        "status": "VERIFIED",
        "spec_hash": digest(SPEC),
        "source_hash": source_hash,
        "mode": mode,
        "variant": variant,
        "repetition": repetition,
        "assembly_hash": assembly["content_hash"],
        "realization_hash": path.content_hash,
        "result_hash": result["content_hash"],
        "candidate_count": count,
        "psi_row_count": count * 65,
        "leaf_hashes": [leaf["content_hash"] for leaf in leaves],
        "phase_nanoseconds": phases,
        "preparation_subphase_nanoseconds": preparation_ns,
        "total_nanoseconds": total,
        "peak_rss_bytes": peak_rss_bytes(),
        "preparations": slot.preparations,
        "reuse_hits": slot.hits,
        "python": sys.version,
        "platform": platform.platform(),
        "automatic_publish_allowed": False,
        "automatic_order_allowed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--mode", choices=tuple(SPEC["modes"]))
    parser.add_argument("--variant", choices=SPEC["variants"])
    parser.add_argument("--repetition", type=int)
    parser.add_argument("--source-hash")
    args = parser.parse_args()
    root = args.output
    require(
        root.is_dir() and not root.is_symlink() and not list(root.iterdir()),
        "PREPARATION_ROOT_NOT_EMPTY",
    )
    if args.worker:
        require(args.repetition in (1, 2, 3), "PREPARATION_REPETITION_INVALID")
        value = trial(args.mode, args.variant, args.repetition, args.source_hash)
        write_new(root / "trial.json", value)
        return
    pinned = sources()
    source_hash = digest(pinned)
    write_new(
        root / "pinned-source.json",
        {"source": pinned, "source_hash": source_hash, "spec": SPEC, "spec_hash": digest(SPEC)},
    )
    reports = []
    for repetition in (1, 2, 3):
        for mode in SPEC["modes"]:
            variants = SPEC["variants"] if repetition != 2 else list(reversed(SPEC["variants"]))
            for variant in variants:
                folder = root / f"{repetition}-{mode}-{variant}"
                folder.mkdir(mode=0o700)
                subprocess.run(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--worker",
                        "--output",
                        str(folder),
                        "--mode",
                        mode,
                        "--variant",
                        variant,
                        "--repetition",
                        str(repetition),
                        "--source-hash",
                        source_hash,
                    ],
                    check=True,
                    timeout=MAX_INVOCATION_SECONDS,
                )
                reports.append(json.loads((folder / "trial.json").read_text()))
                print(f"VERIFIED {repetition} {mode} {variant}", flush=True)
    summaries = {}
    for mode in SPEC["modes"]:
        summaries[mode] = {}
        for variant in SPEC["variants"]:
            group = [r for r in reports if r["mode"] == mode and r["variant"] == variant]
            require(len(group) == 3, "PREPARATION_COMPARISON_INCOMPLETE")
            summaries[mode][variant] = {
                "median_phase_nanoseconds": {
                    p: statistics.median(r["phase_nanoseconds"][p] for r in group)
                    for p in group[0]["phase_nanoseconds"]
                },
                "median_total_nanoseconds": statistics.median(
                    r["total_nanoseconds"] for r in group
                ),
                "median_peak_rss_bytes": statistics.median(r["peak_rss_bytes"] for r in group),
                "min_peak_rss_bytes": min(r["peak_rss_bytes"] for r in group),
                "max_peak_rss_bytes": max(r["peak_rss_bytes"] for r in group),
                "median_preparation_subphase_nanoseconds": statistics.median(
                    r["preparation_subphase_nanoseconds"] for r in group
                ),
            }
        require(
            len(
                {
                    (
                        r["assembly_hash"],
                        r["realization_hash"],
                        r["result_hash"],
                        tuple(r["leaf_hashes"]),
                    )
                    for r in reports
                    if r["mode"] == mode
                }
            )
            == 1,
            "PREPARATION_COMPARISON_IDENTITY_CHANGED",
        )
    require(digest(sources()) == source_hash, "PREPARATION_COMPARISON_SOURCE_CHANGED")
    report = {
        "status": "COMPLETE",
        "spec_hash": digest(SPEC),
        "source_hash": source_hash,
        "trial_count": len(reports),
        "summaries": summaries,
    }
    files = [p for p in root.rglob("*.json") if p.is_file()]
    report_bytes = len((canonical_json(report) + "\n").encode())
    require(
        all(p.stat().st_size <= MAX_RAW_BYTES for p in files)
        and report_bytes <= MAX_RAW_BYTES
        and sum(p.stat().st_size for p in files) + report_bytes <= MAX_EXPERIMENT_RAW_BYTES,
        "PREPARATION_COMPARISON_RAW_BUDGET",
    )
    write_new(root / "complete.json", report)
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

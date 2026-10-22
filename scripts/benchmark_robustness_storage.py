"""Frozen 2055 manual storage comparison; no official paths, DB or model changes.

Run with the project's test-support PYTHONPATH. Every trial is a fresh process
and an empty private directory. OS filesystem cache is not cleared. Reports are
append-only; a failed trial cannot produce a successful aggregate report.
"""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import resource
import statistics
import subprocess
import sys
from time import perf_counter_ns
import zlib

from dsio_inventory_engine.evaluate_replenishment.robustness.durable import (
    RunDurableItemCatalogUseCase,
)
from dsio_inventory_engine.evaluate_replenishment.robustness.item_execution import (
    RunItemCatalogUseCase,
)
from dsio_inventory_engine.evaluate_replenishment.robustness.item_execution_steps import (
    execute_leaf,
    finish_result,
    prepare_context,
)
from dsio_inventory_engine.infrastructure.robustness_artifacts import LocalRobustnessArtifactStore
from dsio_inventory_engine.infrastructure.robustness_compression import (
    CompressedRobustnessArtifactStore,
)
from dsio_inventory_engine.inventory_contracts.robustness_storage import (
    BENCHMARK_SPEC_HASH,
    MAX_EXPERIMENT_RAW_BYTES,
    MAX_INVOCATION_SECONDS,
    MAX_RAW_BYTES,
    benchmark_spec,
    validate_benchmark_spec,
)
from dsio_inventory_engine.inventory_contracts.values import canonical_json, digest, require

ROOT = Path(__file__).resolve().parents[1]


def source_manifest() -> dict:
    paths = [
        p
        for folder in ("src", "tests", "schemas")
        for p in (ROOT / folder).rglob("*")
        if p.is_file() and p.suffix in (".py", ".json") and "__pycache__" not in p.parts
    ]
    paths.extend(
        [
            Path(__file__).resolve(),
            ROOT / "docs/architecture/data/catalog-robustness-protocol-v1.json",
            ROOT / "docs/architecture/data/robustness-storage-benchmark-v1.json",
        ]
    )
    return {
        str(p.relative_to(ROOT)): sha256(p.read_bytes()).hexdigest() for p in sorted(set(paths))
    }


def peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def write_new(path: Path, value: dict) -> None:
    data = (canonical_json(value) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


class MeteredRepository:
    def __init__(self, store):
        self.store, self.elapsed_ns, self.references = store, 0, {}

    def _call(self, method, *args):
        start = perf_counter_ns()
        try:
            return getattr(self.store, method)(*args)
        finally:
            self.elapsed_ns += perf_counter_ns() - start

    def put(self, value):
        ref = self._call("put", value)
        suffix = ".json.gz" if "storage_version" in ref else ".json"
        self.references["object-" + ref["content_hash"] + suffix] = ref
        return ref

    def read(self, ref):
        return self._call("read", ref)

    def bind(self, key, ref):
        return self._call("bind", key, ref)

    def lookup(self, key):
        return self._call("lookup", key)

    def sizes(self):
        physical, raw, artifacts = 0, 0, {}
        for path in self.store.root.iterdir():
            require(path.is_file() and not path.is_symlink(), "BENCHMARK_UNEXPECTED_FILE")
            count = path.stat().st_size
            physical += count
            if path.name in self.references:
                ref = self.references[path.name]
                raw += ref["byte_count"]
                artifacts[path.name] = ref
            else:
                require(
                    path.name.startswith("slot-") and path.suffix == ".json",
                    "BENCHMARK_UNEXPECTED_FILE",
                )
                raw += count
        return {"physical_bytes": physical, "raw_budget_bytes": raw, "objects": artifacts}


class TimedRunner(RunItemCatalogUseCase):
    def __init__(self, *args):
        super().__init__(*args)
        self.publication_replay_ns = 0

    def verify(self, *args, **kwargs):
        start = perf_counter_ns()
        try:
            return super().verify(*args, **kwargs)
        finally:
            self.publication_replay_ns += perf_counter_ns() - start


def trial(root: Path, mode: str, variant: str, repetition: int, expected_source_hash: str) -> dict:
    from tests.integration.test_robustness_durable_offline import prepared
    from tests.support.robustness_item_fixtures import DEV

    spec = benchmark_spec()
    validate_benchmark_spec(spec, expected_hash=BENCHMARK_SPEC_HASH)
    require(
        mode in spec["modes"] and variant in spec["variants"] and 1 <= repetition <= 3,
        "BENCHMARK_CASE_INVALID",
    )
    require(
        root.is_dir() and not root.is_symlink() and not list(root.iterdir()),
        "BENCHMARK_ROOT_NOT_EMPTY",
    )
    require(digest(source_manifest()) == expected_source_hash, "BENCHMARK_SOURCE_CHANGED")
    begin = perf_counter_ns()
    start = begin
    case, assembly, path = prepared(mode=mode)
    runner = TimedRunner(DEV, case["producer"])
    context = prepare_context(
        runner.preparer,
        runner.deployment,
        assembly,
        expected_assembly_hash=assembly["content_hash"],
        realization=path,
        expected_realization_hash=path.content_hash,
    )
    phases = {"PREPARATION": perf_counter_ns() - start}
    start = perf_counter_ns()
    result = finish_result(context, [execute_leaf(context, i) for i in range(context.leaf_count)])
    phases["COMPUTE"] = perf_counter_ns() - start
    count = spec["modes"][mode]
    require(len(result["path_scorecards"]) == count, "BENCHMARK_CANDIDATE_COUNT")
    store_type = (
        LocalRobustnessArtifactStore if variant == "JSON_V1" else CompressedRobustnessArtifactStore
    )
    repository = MeteredRepository(store_type(root))
    service = RunDurableItemCatalogUseCase(runner, repository)
    start = perf_counter_ns()
    receipt = service.write(
        "HAND-STORAGE-BENCHMARK",
        result,
        expected_result_hash=result["content_hash"],
        expected_assembly_hash=assembly["content_hash"],
        expected_realization_hash=path.content_hash,
    )
    write_ns = perf_counter_ns() - start
    phases["PUBLICATION_REPLAY"] = runner.publication_replay_ns
    phases["PERSIST_EXCLUDING_REPLAY"] = write_ns - runner.publication_replay_ns
    start = perf_counter_ns()
    manifest = repository.read(receipt["manifest_reference"])
    repository.read(receipt["input_reference"])
    header = repository.read(manifest["header_reference"])
    require(header["result_hash"] == result["content_hash"], "BENCHMARK_HEADER_MISMATCH")
    ids, rows, successful = [], 0, 0
    for entry, original in zip(manifest["leaves"], result["leaves"], strict=True):
        leaf = repository.read(entry["reference"])
        require(canonical_json(leaf) == canonical_json(original), "BENCHMARK_RAW_RESULT_MISMATCH")
        ids.extend(leaf["candidate_ids"])
        for child in leaf["candidate_executions"]:
            require(child["status"] == "SUCCEEDED", "BENCHMARK_UNEXPECTED_CANDIDATE_FAILURE")
            successful += 1
            rows += len(child["causal_result"]["psi_rows"])
    require(
        ids == [c["candidate_id"] for c in assembly["complete_candidates"]]
        and successful == count
        and rows == count * 65,
        "BENCHMARK_COVERAGE_MISMATCH",
    )
    phases["READ_INTEGRITY"] = perf_counter_ns() - start
    io_before = repository.elapsed_ns
    start = perf_counter_ns()
    verified = service.read(
        "HAND-STORAGE-BENCHMARK",
        expected_input_hash=receipt["input_reference"]["content_hash"],
        expected_result_hash=result["content_hash"],
    )
    read_with_replay_ns = perf_counter_ns() - start
    phases["REVALIDATE_EXCLUDING_STORAGE"] = read_with_replay_ns - (
        repository.elapsed_ns - io_before
    )
    require(verified["content_hash"] == result["content_hash"], "BENCHMARK_REPLAY_MISMATCH")
    require(digest(source_manifest()) == expected_source_hash, "BENCHMARK_SOURCE_CHANGED")
    usage = repository.sizes()
    require(usage["raw_budget_bytes"] <= MAX_EXPERIMENT_RAW_BYTES, "BENCHMARK_RAW_BUDGET_EXCEEDED")
    require(
        perf_counter_ns() - begin <= MAX_INVOCATION_SECONDS * 1_000_000_000, "BENCHMARK_TIME_LIMIT"
    )
    return {
        "spec_hash": BENCHMARK_SPEC_HASH,
        "source_hash": expected_source_hash,
        "mode": mode,
        "variant": variant,
        "repetition": repetition,
        "status": "VERIFIED",
        "assembly_hash": assembly["content_hash"],
        "realization_hash": path.content_hash,
        "parent_classification_hash": assembly["parent_classification"]["content_hash"],
        "complete_catalog_hash": assembly["candidate_catalog"]["content_hash"],
        "result_hash": result["content_hash"],
        "leaf_hashes": [leaf["content_hash"] for leaf in result["leaves"]],
        "candidate_count": count,
        "psi_row_count": rows,
        "phase_wall_ns": phases,
        "write_including_replay_ns": write_ns,
        "read_including_replay_ns": read_with_replay_ns,
        "peak_process_rss_bytes": peak_rss_bytes(),
        **usage,
        "runtime": {
            "python": platform.python_version(),
            "os": platform.system(),
            "machine": platform.machine(),
            "zlib": zlib.ZLIB_RUNTIME_VERSION,
        },
        "automatic_publish_allowed": False,
        "automatic_order_allowed": False,
        "database_writes": False,
        "official_paths_generated": False,
    }


def summarize(records: list[dict], *, allow_smoke: bool = False) -> dict:
    spec = benchmark_spec()
    expected_repetitions = 1 if allow_smoke else spec["repetitions"]
    require(
        len(records) == len(spec["modes"]) * 2 * expected_repetitions, "BENCHMARK_CASES_INCOMPLETE"
    )
    observed = {(r["mode"], r["variant"], r["repetition"]) for r in records}
    expected = {
        (m, v, n)
        for m in spec["modes"]
        for v in spec["variants"]
        for n in range(1, expected_repetitions + 1)
    }
    require(observed == expected, "BENCHMARK_CASES_INCOMPLETE")
    source_hashes = {r["source_hash"] for r in records}
    require(len(source_hashes) == 1, "BENCHMARK_SOURCE_CHANGED")
    summaries = []
    for mode, count in spec["modes"].items():
        group = [r for r in records if r["mode"] == mode]
        for key in (
            "assembly_hash",
            "realization_hash",
            "parent_classification_hash",
            "complete_catalog_hash",
            "result_hash",
            "leaf_hashes",
        ):
            require(
                len({canonical_json(r[key]) for r in group}) == 1, "BENCHMARK_EQUIVALENCE_MISMATCH"
            )
        require(
            all(
                r["status"] == "VERIFIED"
                and type(r["candidate_count"]) is int
                and r["candidate_count"] == count
                and type(r["repetition"]) is int
                and 1 <= r["repetition"] <= expected_repetitions
                and type(r["psi_row_count"]) is int
                and r["psi_row_count"] == count * 65
                and r["spec_hash"] == BENCHMARK_SPEC_HASH
                and r["database_writes"]
                is r["automatic_publish_allowed"]
                is r["automatic_order_allowed"]
                is False
                and r["official_paths_generated"] is False
                and type(r["raw_budget_bytes"]) is int
                and 0 < r["raw_budget_bytes"] <= MAX_EXPERIMENT_RAW_BYTES
                and type(r["physical_bytes"]) is int
                and r["physical_bytes"] > 0
                and type(r["peak_process_rss_bytes"]) is int
                and r["peak_process_rss_bytes"] > 0
                and set(r["phase_wall_ns"]) == set(spec["phases"])
                and all(type(n) is int and n >= 0 for n in r["phase_wall_ns"].values())
                for r in group
            ),
            "BENCHMARK_CASE_INVALID",
        )
        for variant in spec["variants"]:
            rows = [r for r in group if r["variant"] == variant]
            require(
                len({(r["physical_bytes"], r["raw_budget_bytes"]) for r in rows}) == 1,
                "BENCHMARK_SIZE_CHANGED",
            )
            summaries.append(
                {
                    "mode": mode,
                    "variant": variant,
                    "repetitions": len(rows),
                    "phase_wall_ns_median": {
                        p: statistics.median(r["phase_wall_ns"][p] for r in rows)
                        for p in spec["phases"]
                    },
                    "physical_bytes": rows[0]["physical_bytes"],
                    "raw_budget_bytes": rows[0]["raw_budget_bytes"],
                    "peak_process_rss_bytes_max": max(r["peak_process_rss_bytes"] for r in rows),
                }
            )
    require(
        sum(r["raw_budget_bytes"] for r in records) <= MAX_EXPERIMENT_RAW_BYTES,
        "BENCHMARK_RAW_BUDGET_EXCEEDED",
    )
    return {
        "status": "SMOKE_ONLY" if allow_smoke else "COMPLETE_COMPARISON",
        "spec_hash": BENCHMARK_SPEC_HASH,
        "case_count": len(records),
        "mathematical_strategy_speedup_claim": False,
        "summaries": summaries,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--mode", choices=tuple(benchmark_spec()["modes"]))
    parser.add_argument("--variant", choices=tuple(benchmark_spec()["variants"]))
    parser.add_argument("--repetition", type=int, default=1)
    parser.add_argument("--source-hash")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if args.worker:
        result = trial(args.output, args.mode, args.variant, args.repetition, args.source_hash)
        print(canonical_json(result), flush=True)
        return
    root = args.output
    require(
        root.is_dir() and not root.is_symlink() and not list(root.iterdir()),
        "BENCHMARK_ROOT_NOT_EMPTY",
    )
    source = source_manifest()
    source_hash = digest(source)
    write_new(
        root / "pinned-benchmark.json",
        {
            "spec": benchmark_spec(),
            "spec_hash": BENCHMARK_SPEC_HASH,
            "source": source,
            "source_hash": source_hash,
        },
    )
    records = []
    for repetition in range(1, 2 if args.smoke else 4):
        variants = (
            benchmark_spec()["variants"]
            if repetition % 2
            else list(reversed(benchmark_spec()["variants"]))
        )
        for mode, count in benchmark_spec()["modes"].items():
            for variant in variants:
                # Conservative maximum per physical object/slot before starting a trial.
                maximum = (2 * ((count + 15) // 16) + 5) * MAX_RAW_BYTES
                used = sum(r["raw_budget_bytes"] for r in records) + sum(
                    p.stat().st_size for p in root.glob("*.json")
                )
                require(used + maximum <= MAX_EXPERIMENT_RAW_BYTES, "BENCHMARK_RAW_BUDGET_EXCEEDED")
                case_root = root / f"{mode}-{variant}-{repetition}"
                case_root.mkdir(mode=0o700)
                print(
                    canonical_json(
                        {
                            "phase": "TRIAL",
                            "mode": mode,
                            "variant": variant,
                            "repetition": repetition,
                        }
                    ),
                    file=sys.stderr,
                    flush=True,
                )
                process = subprocess.run(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--worker",
                        "--output",
                        str(case_root),
                        "--mode",
                        mode,
                        "--variant",
                        variant,
                        "--repetition",
                        str(repetition),
                        "--source-hash",
                        source_hash,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=MAX_INVOCATION_SECONDS,
                    check=True,
                )
                record = json.loads(process.stdout)
                write_new(root / f"trial-{len(records) + 1:02d}.json", record)
                records.append(record)
    require(source_manifest() == source, "BENCHMARK_SOURCE_CHANGED")
    summary = summarize(records, allow_smoke=args.smoke)
    summary["raw_budget_bytes_all_trials"] = sum(r["raw_budget_bytes"] for r in records)
    summary["physical_bytes_all_trials"] = sum(r["physical_bytes"] for r in records)
    summary["root_metadata_bytes_before_complete"] = sum(
        p.stat().st_size for p in root.glob("*.json")
    )
    complete_bytes = len((canonical_json(summary) + "\n").encode())
    require(
        summary["raw_budget_bytes_all_trials"]
        + summary["root_metadata_bytes_before_complete"]
        + complete_bytes
        <= MAX_EXPERIMENT_RAW_BYTES,
        "BENCHMARK_RAW_BUDGET_EXCEEDED",
    )
    write_new(root / "complete-benchmark.json", summary)
    print(canonical_json(summary), flush=True)


if __name__ == "__main__":
    main()

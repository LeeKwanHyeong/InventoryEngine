"""Manual 2055 storage equivalence/size verification, not a timing benchmark.

Uses full actual Catalog/Guard/PSI replay. No official generator, DB or model.
Raw storage accounts for every physical uncompressed file exactly once, including
shared wrappers/manifests/slots; expanded logical document bytes are separate.
"""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re

from dsio_inventory_engine.infrastructure.robustness_artifacts import LocalRobustnessArtifactStore
from dsio_inventory_engine.infrastructure.robustness_compression import (
    CompressedRobustnessArtifactStore,
    decode_gzip,
)
from dsio_inventory_engine.infrastructure.robustness_shared_artifacts import (
    SharedInputRobustnessArtifactStore,
)
from dsio_inventory_engine.inventory_contracts.robustness_storage import (
    MAX_RAW_BYTES,
    MAX_EXPERIMENT_RAW_BYTES,
)
from dsio_inventory_engine.inventory_contracts.values import (
    canonical_json,
    digest,
    read_json,
    require,
)

EXPECTED_RESULTS = {
    "ABC_XYZ_SHARED_BUDGET": "a04d8830ff370b3717dff144042abfcc9a4d6d882a4b10d60f415de851277ee7",
    "LEGACY_STYLE": "fbd77610f2019fb44b68dbf12a3667dbe6f31238e2d428909355bc7e7f1c67c6",
    "WIDE_REFERENCE": "0c2a92d7fdd6d835a665d3c2bf1a4865b0fe797e0851e0a9268750fbfa31759b",
}


def measure(root: Path) -> dict:
    """Diagnostic only, after authoritative Repository/PSI verification; no admission."""
    store = LocalRobustnessArtifactStore(root)
    raw_total = physical_total = shared_count = file_count = 0
    files = {}
    for path in sorted(root.iterdir()):
        require(
            re.fullmatch(
                r"(?:object-[a-f0-9]{64}(?:\.shared)?\.json(?:\.gz)?|shared-[a-f0-9]{64}\.json\.gz|slot-[a-f0-9]{64}\.json)",
                path.name,
            )
            is not None,
            "SHARED_MEASUREMENT_UNEXPECTED_FILE",
        )
        with store._directory() as directory:
            stored = store._read_at(directory, path.name)
        if path.suffix == ".gz":
            require(len(stored) >= 18, "SHARED_MEASUREMENT_FRAME_INVALID")
            raw = decode_gzip(stored, expected_bytes=int.from_bytes(stored[-4:], "little"))
        else:
            raw = stored
        value = read_json(raw.decode(), max_bytes=MAX_RAW_BYTES)
        require((canonical_json(value) + "\n").encode() == raw, "SHARED_MEASUREMENT_NONCANONICAL")
        physical_total += len(stored)
        raw_total += len(raw)
        file_count += 1
        shared_count += path.name.startswith("shared-")
        files[path.name] = {
            "raw_bytes": len(raw),
            "stored_bytes": len(stored),
            "stored_sha256": sha256(stored).hexdigest(),
        }
    return {
        "unique_raw_storage_bytes": raw_total,
        "physical_bytes": physical_total,
        "shared_file_count": shared_count,
        "file_count": file_count,
        "files": files,
    }


def verify_case(root: Path, mode: str) -> dict:
    from tests.integration.test_robustness_durable_offline import (
        prepared,
        service,
        run,
        file_identity,
    )

    case, assembly, path = prepared(mode=mode)
    old_root, new_root = root / "GZIP_V2", root / "SHARED_V3"
    old_root.mkdir(mode=0o700)
    new_root.mkdir(mode=0o700)
    new = service(new_root, case, SharedInputRobustnessArtifactStore)
    receipt = run(new, assembly, path)
    result = new.read(
        "HAND",
        expected_input_hash=receipt["input_reference"]["content_hash"],
        expected_result_hash=EXPECTED_RESULTS[mode],
    )
    before = file_identity(new_root)
    require(
        service(new_root, case, SharedInputRobustnessArtifactStore).recover(
            "HAND", expected_input_hash=receipt["input_reference"]["content_hash"]
        )
        == receipt,
        "SHARED_RECOVERY_RECEIPT_MISMATCH",
    )
    require(file_identity(new_root) == before, "SHARED_RECOVERY_REWROTE_FILES")
    old = service(old_root, case, CompressedRobustnessArtifactStore)
    old_receipt = old.write(
        "HAND",
        result,
        expected_result_hash=result["content_hash"],
        expected_assembly_hash=assembly["content_hash"],
        expected_realization_hash=path.content_hash,
    )
    manifests = [
        s.repository.read(r["manifest_reference"]) for s, r in ((old, old_receipt), (new, receipt))
    ]
    refs = []
    for r, m in zip((old_receipt, receipt), manifests, strict=True):
        refs.append(
            [r["input_reference"], m["header_reference"]]
            + [entry["reference"] for entry in m["leaves"]]
        )
    logical_bytes = []
    for a, b in zip(*refs, strict=True):
        v1, v3 = old.repository.read(a), new.repository.read(b)
        require(canonical_json(v1) == canonical_json(v3), "SHARED_ORIGINAL_BYTES_MISMATCH")
        require(
            a["content_hash"] == b["content_hash"] and a["byte_count"] == b["byte_count"],
            "SHARED_ORIGINAL_REFERENCE_MISMATCH",
        )
        logical_bytes.append(a["byte_count"])
    manifest = manifests[1]
    count = 88 if mode == "WIDE_REFERENCE" else 32
    require(
        manifest["candidate_count"] == manifest["successful_count"] == count
        and manifest["failed_count"] == 0
        and manifest["psi_row_count"] == count * 65,
        "SHARED_CATALOG_COVERAGE_MISMATCH",
    )
    for leaf in result["leaves"]:
        for child in leaf["candidate_executions"]:
            psi = child["causal_result"]
            require(
                psi["automatic_publish_allowed"] is psi["automatic_order_allowed"] is False
                and psi["effective_policy_admission"]["effective_order_action"] == "REVIEW",
                "SHARED_GATE_CHANGED",
            )
    usage = {"GZIP_V2": measure(old_root), "SHARED_V3": measure(new_root)}
    require(
        usage["SHARED_V3"]["unique_raw_storage_bytes"]
        < usage["GZIP_V2"]["unique_raw_storage_bytes"],
        "SHARED_RAW_STORAGE_NOT_REDUCED",
    )
    require(
        sum(v["unique_raw_storage_bytes"] for v in usage.values()) <= MAX_EXPERIMENT_RAW_BYTES,
        "SHARED_VERIFICATION_RAW_BUDGET",
    )
    return {
        "mode": mode,
        "status": "VERIFIED",
        "assembly_hash": assembly["content_hash"],
        "realization_hash": path.content_hash,
        "result_hash": result["content_hash"],
        "candidate_count": count,
        "psi_row_count": count * 65,
        "expanded_original_input_header_leaf_bytes": sum(logical_bytes),
        "usage": usage,
        "receipts": {"GZIP_V2": old_receipt, "SHARED_V3": receipt},
        "performance_speed_claim": False,
        "official_paths_generated": False,
        "database_writes": False,
        "automatic_publish_allowed": False,
        "automatic_order_allowed": False,
    }


def main() -> None:
    from scripts.benchmark_robustness_storage import source_manifest, write_new

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.output
    require(
        root.is_dir() and not root.is_symlink() and not list(root.iterdir()),
        "SHARED_VERIFICATION_ROOT_NOT_EMPTY",
    )
    source = source_manifest()
    source["scripts/verify_robustness_shared_storage.py"] = sha256(
        Path(__file__).read_bytes()
    ).hexdigest()
    source_hash = digest(source)
    write_new(
        root / "pinned-source.json",
        {
            "source": source,
            "source_hash": source_hash,
            "scope": "2055_MANUAL_SIZING_ONLY_NOT_A_SPEED_BENCHMARK_OR_OFFICIAL_STUDY",
        },
    )
    reports = []
    for mode in EXPECTED_RESULTS:
        folder = root / mode
        folder.mkdir(mode=0o700)
        report = verify_case(folder, mode)
        report["source_hash"] = source_hash
        write_new(root / (mode + ".json"), report)
        reports.append(report)
        print(
            json.dumps({k: v for k, v in report.items() if k not in ("usage", "receipts")}),
            flush=True,
        )
    raw = sum(x["unique_raw_storage_bytes"] for r in reports for x in r["usage"].values())
    final_source = source_manifest()
    final_source["scripts/verify_robustness_shared_storage.py"] = sha256(
        Path(__file__).read_bytes()
    ).hexdigest()
    require(final_source == source, "SHARED_VERIFICATION_SOURCE_CHANGED")
    summary = {
        "status": "VERIFIED",
        "modes": list(EXPECTED_RESULTS),
        "unique_raw_storage_bytes": raw,
        "source_hash": source_hash,
        "performance_speed_claim": False,
    }
    require(
        raw
        + sum(p.stat().st_size for p in root.glob("*.json"))
        + len((canonical_json(summary) + "\n").encode())
        <= MAX_EXPERIMENT_RAW_BYTES,
        "SHARED_VERIFICATION_RAW_BUDGET",
    )
    write_new(root / "complete.json", summary)


if __name__ == "__main__":
    main()

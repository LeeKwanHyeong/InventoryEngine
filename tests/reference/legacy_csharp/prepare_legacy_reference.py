"""Stage pinned original C# fragments; never translate inventory calculations.

Only creates a fresh directory outside the repository. Original sources,
product code, manual expectations and connection files are never changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
EXTRA_HASHES = {
    "Entities/OptimizerEntity.cs": "fe38db6bb96879db69018e9624e78be6ce946161865cba44f3d093064cbd2939",
    "Entities/InboundEntity.cs": "f1eefe3ede84dd22ac257b13a2807ee6d6430420458a6c26c1da66fa5b4a2166",
}
FRAGMENTS = {
    "pipeline": ("RootIO/Program.cs", 802, 955),
    "advance": ("RootIO/Program.cs", 809, 847),
    "weekly_rate": ("RootIO/Program.cs", 945, 949),
    "base_month": ("RootIO/Program.cs", 255, 258),
    "bucket": ("Entities/PlanEntity.cs", 134, 141),
    "plan_bucket": ("Entities/PlanEntity.cs", 205, 226),
    "lot": ("Entities/PlanEntity.cs", 260, 297),
    "buffer_fields": ("DataMart/BufferPSIObject.cs", 19, 28),
    "set_bucket": ("DataMart/BufferPSIObject.cs", 32, 106),
    "set_due_in": ("DataMart/BufferPSIObject.cs", 110, 120),
    "time_uom": ("Entities/Definition.cs", 32, 32),
    "lead_time": ("Entities/Definition.cs", 189, 192),
    "eligibility": ("DataMart/OptimizerProcess.cs", 531, 636),
    "condition": ("Entities/OptimizerEntity.cs", 9, 18),
    "fixed_condition": ("Entities/OptimizerEntity.cs", 20, 33),
    "scenario": ("Entities/OptimizerEntity.cs", 84, 150),
    "buffer_dto": ("Entities/InboundEntity.cs", 87, 117),
    "stock_dto": ("Entities/InboundEntity.cs", 201, 218),
    "safety_stock_dto": ("Entities/InboundEntity.cs", 219, 233),
    "sales_order_dto": ("Entities/InboundEntity.cs", 247, 272),
    "calendar_dto": ("Entities/InboundEntity.cs", 301, 308),
}


def stage(source_root: Path, output: Path) -> dict:
    source_root, output = source_root.resolve(), output.resolve()
    if output.exists() or output.is_relative_to(ROOT) or output.is_relative_to(source_root):
        raise ValueError("A fresh staging directory outside the repository is required")
    register = json.loads(
        (ROOT / "docs/architecture/evidence/legacy-csharp-reconciliation-20261020.json").read_text()
    )
    expected = dict(EXTRA_HASHES)
    for entry in register["sources"]:
        parts = entry["path"].split("/RootIO/", 1)
        if len(parts) == 2:
            expected[parts[1]] = entry["sha256"]
    sources, records = {}, []
    for relative in sorted({v[0] for v in FRAGMENTS.values()}):
        raw = (source_root / relative).read_bytes()
        fingerprint = hashlib.sha256(raw).hexdigest()
        if fingerprint != expected[relative]:
            raise ValueError(f"Legacy source fingerprint mismatch: {relative}")
        sources[relative] = raw.decode("utf-8-sig").splitlines()
        records.append({"path": relative, "sha256": fingerprint})
    fragments = {
        name: "\n".join(sources[relative][start - 1 : end]) + "\n"
        for name, (relative, start, end) in FRAGMENTS.items()
    }
    output.mkdir()
    for name in ("Reference.csproj", "NuGet.Config", "ReferenceHarness.cs"):
        shutil.copyfile(Path(__file__).parent / name, output / name)
    shutil.copyfile(
        ROOT / "tests/fixtures/golden_legacy_python_semantics.json", output / "golden.json"
    )
    entity_names = (
        "bucket",
        "plan_bucket",
        "lot",
        "condition",
        "fixed_condition",
        "scenario",
        "buffer_dto",
        "stock_dto",
        "safety_stock_dto",
        "sales_order_dto",
        "calendar_dto",
    )
    entities = "namespace Entities {\n" + "".join(fragments[n] for n in entity_names)
    entities += (
        "public static class Definition {\n" + fragments["time_uom"] + fragments["lead_time"]
    )
    entities += "public static SilentLog log = new(); }\n"
    entities += "public class SilentLog { public void Debug(string text) { } }\n}\n"
    adapter = "using Entities;\nnamespace DataMart {\n"
    adapter += "public class HorizonCalendar { public List<BucketSchema> Buckets { get; set; } = new(); }\n"
    adapter += "public class BufferPSIObject {\n" + fragments["buffer_fields"]
    adapter += fragments["set_bucket"] + fragments["set_due_in"] + "}\n}\n"
    kernel = "using Entities;\nusing DataMart;\npublic static class NativeKernel {\n"
    kernel += "public static void Run(BufferPSIObject bpo) {\n" + fragments["pipeline"] + "}\n"
    kernel += "public static void Advance(PlanBucket m, double beforeEOH, double beforeAccBO, double beforeAccPO) {\n"
    kernel += fragments["advance"] + fragments["weekly_rate"] + "}\n"
    kernel += (
        "public static void SetBaseMonth(BucketSchema b, CALENDAR c) {\n"
        + fragments["base_month"]
        + "}\n}\n"
    )
    selector = "using Entities;\npublic class NativeSelector {\n"
    selector += "private FixedCondition _fixedCondition; private OptimizerCondition _condition;\n"
    selector += 'public NativeSelector(double minimum) { _condition = new() { ObjectiveFunction = "OBJ01" }; '
    selector += '_fixedCondition = new() { Const01_MinmumFillRateFlag = "Y", Const01_MinmumFillRate = minimum }; }\n'
    selector += fragments["eligibility"] + "}\n"
    for name, content in {
        "ExtractedEntities.cs": entities,
        "ExtractedBuffer.cs": adapter,
        "ExtractedKernel.cs": kernel,
        "ExtractedEligibility.cs": selector,
    }.items():
        (output / name).write_text(content)
    manifest = {
        "source_files": records,
        "fragments": [
            {
                "name": name,
                "source": v[0],
                "start_line": v[1],
                "end_line": v[2],
                "normalized_fragment_sha256": hashlib.sha256(fragments[name].encode()).hexdigest(),
            }
            for name, v in FRAGMENTS.items()
        ],
        "adaptations": [
            "Normalize line endings only; extracted calculation tokens are unchanged",
            "Wrap Program calculation blocks; no Main, DB pre/postprocess, source setup or single-buffer debug filter",
            "Use original DTOs; list-only HorizonCalendar and silent debug log are non-calculation scaffolding",
            "Allocation block for transition tests; full bucket loop for order/trigger tests",
            "Set eligibility condition fields directly; do not execute full optimizer workflow",
        ],
        "golden_sha256": hashlib.sha256((output / "golden.json").read_bytes()).hexdigest(),
    }
    manifest["staged_files"] = [
        {"path": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
        for p in sorted(output.iterdir())
        if p.is_file()
    ]
    (output / "extraction-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = stage(args.source_root, args.output)
    print(
        json.dumps(
            {
                "staged": str(args.output),
                "source_files": len(manifest["source_files"]),
                "fragments": len(manifest["fragments"]),
            }
        )
    )

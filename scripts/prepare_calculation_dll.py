"""Stage a DB-free library against fingerprint-verified original C# fragments.

Build output must be outside the repository. Reuses the existing extraction
manifest and never modifies or vendors the supplied Legacy source tree.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil

from tests.reference.legacy_csharp.prepare_legacy_reference import ROOT, stage


def prepare(source_root: Path, destination: Path) -> dict:
    destination = destination.resolve()
    if destination.exists() or destination.is_relative_to(ROOT):
        raise ValueError("FRESH_EXTERNAL_BUILD_DIRECTORY_REQUIRED")
    manifest = stage(source_root, destination)
    for name in (
        "Inventory.Calculation.csproj",
        "Wire.cs",
        "LegacyBatch.cs",
        "TargetTransition.cs",
    ):
        shutil.copyfile(ROOT / "native/Inventory.Calculation" / name, destination / name)
    manifest["calculation_facade_files"] = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(destination.iterdir())
        if p.name
        in {"Inventory.Calculation.csproj", "Wire.cs", "LegacyBatch.cs", "TargetTransition.cs"}
    }
    (destination / "calculation-source-manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.source_root, args.destination)
    print(
        json.dumps(
            {
                "source_files": len(result["source_files"]),
                "facade_files": len(result["calculation_facade_files"]),
            }
        )
    )

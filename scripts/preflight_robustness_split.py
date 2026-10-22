"""Offline resource preflight only. Does not generate or execute official paths.

Use one explicit empty private pool for this hand calibration, not one per mode.
The printed population counts identify metadata, never permission to run a study.
"""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import platform

from dsio_inventory_engine.evaluate_replenishment.robustness.resource_execution import (
    ControlledCatalogExecutor,
)
from dsio_inventory_engine.evaluate_replenishment.robustness.split_calibration import (
    MeasureHandCatalogResources,
)
from dsio_inventory_engine.evaluate_replenishment.robustness.split_preflight import (
    MODES,
    build_split_work_plan,
    project_split_resources,
)
from dsio_inventory_engine.infrastructure.robustness_resource_pool import (
    LocalRobustnessResourcePool,
)
from dsio_inventory_engine.inventory_contracts.values import digest, require

ROOT = Path(__file__).resolve().parents[1]


def source_fingerprint():
    files = []
    for directory in ("src", "tests", "scripts"):
        files.extend((ROOT / directory).rglob("*.py"))
    files.extend((ROOT / "tests/fixtures").glob("*.json"))
    files.extend((ROOT / "docs/architecture/data").glob("*.json"))
    return digest(
        {str(p.relative_to(ROOT)): sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
    )


def calibrate(root, *, repetitions=2):
    from scripts.verify_robustness_shared_storage import EXPECTED_RESULTS
    from tests.integration.test_robustness_durable_offline import prepared
    from tests.support.robustness_resource_fixtures import catalog_factory

    require(type(repetitions) is int and 1 <= repetitions <= 3, "PREFLIGHT_REPETITIONS_INVALID")
    require(
        root.is_dir() and not root.is_symlink() and not list(root.iterdir()),
        "PREFLIGHT_EXPLICIT_EMPTY_ROOT_REQUIRED",
    )
    fingerprint = source_fingerprint()
    protocol = json.loads(
        (ROOT / "docs/architecture/data/catalog-robustness-protocol-v1.json").read_text()
    )
    plan = build_split_work_plan(protocol)
    pool = LocalRobustnessResourcePool(root)
    service = MeasureHandCatalogResources(ControlledCatalogExecutor(pool, catalog_factory))
    samples = []
    for repetition in range(repetitions):
        modes = list(MODES)
        for mode in modes[repetition:] + modes[:repetition]:
            require(source_fingerprint() == fingerprint, "PREFLIGHT_IMPLEMENTATION_CHANGED")
            _, assembly, path = prepared(mode=mode)
            samples.append(
                service.execute(
                    f"HAND-PREFLIGHT-{mode}-{repetition + 1}",
                    assembly,
                    path.to_dict(),
                    EXPECTED_RESULTS[mode],
                    seconds=180,
                )
            )
    projection = project_split_resources(protocol, plan, samples)
    require(source_fingerprint() == fingerprint, "PREFLIGHT_IMPLEMENTATION_CHANGED")
    return {
        "source_fingerprint": fingerprint,
        "runtime": {
            "python": platform.python_version(),
            "os": platform.system(),
            "machine": platform.machine(),
        },
        "work_plan_hash": plan["content_hash"],
        "work_population_counts": plan["counts"],
        "samples": samples,
        "projection": projection,
        "pool_usage": pool.inspect(),
        "retained_manual_artifacts": True,
        "official_paths_generated": False,
        "database_writes": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--repetitions", type=int, choices=(1, 2, 3), default=2)
    options = parser.parse_args()
    result = calibrate(options.root, repetitions=options.repetitions)
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 2 if result["projection"]["status"].startswith("BLOCKED") else 0


if __name__ == "__main__":
    raise SystemExit(main())

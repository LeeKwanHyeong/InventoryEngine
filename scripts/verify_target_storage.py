"""Actual DLL storage/restart canary; isolated local output only, no DB/network.

Child workers intentionally receive SIGKILL after a durable record is published.
Parent recovery uses saved input only, not the fixture constructor or Source DB.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

from dsio_inventory_engine.infrastructure.calculation_pythonnet import CalculationPythonNetKernel
from dsio_inventory_engine.infrastructure.target_pipeline_artifacts import (
    LocalTargetPipelineArtifactStore,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from dsio_inventory_engine.recommend_replenishment.application.target_durable import (
    RunDurableTargetPolicyPipelineUseCase,
)
from scripts.verify_target_execution import MeasuredPort
from tests.support.calculation_adapter_fixtures import DEPLOYMENT
from tests.support.selected_order_plan_fixtures import execution_case
from tests.support.target_execution_fixtures import boundary


def runtime(build, root):
    dll = build / "Inventory.Calculation.dll"
    build_hash = hashlib.sha256(dll.read_bytes()).hexdigest()
    kernel = MeasuredPort(CalculationPythonNetKernel(dll, expected_dll_sha256=build_hash))
    repo = LocalTargetPipelineArtifactStore(root)
    return RunDurableTargetPolicyPipelineUseCase(DEPLOYMENT, kernel, repo), repo


def summary(view):
    return {
        "verification_status": view["verification_status"],
        "counts": view["counts"],
        "input_reference": view["input_reference"],
        "calculation_binding": view["result"]["calculation_binding"],
        "automatic_publish_allowed": view["automatic_publish_allowed"],
        "automatic_order_allowed": view["automatic_order_allowed"],
        "database_writes": view["database_writes"],
    }


def child(args):
    durable, repo = runtime(args.build, args.root)
    if args.run_id:
        print(json.dumps(summary(durable.recover(args.run_id)), sort_keys=True))
        return
    old, case = execution_case(mode=args.mode)
    inputs = {
        k: v
        for k, v in case.items()
        if k in {"config_values", "classification_snapshot", "catalog", "expected_catalog_hash"}
    }
    binding = boundary(case["request"].to_dict()["recommendation"], durable.kernel.dll_sha256)
    if args.kill_at:
        publish, bind = repo.put, repo.bind

        def stop_after_part(kind, value):
            ref = publish(kind, value)
            if kind == args.kill_at and kind != "INPUT":
                os.kill(os.getpid(), signal.SIGKILL)
            return ref

        def stop_after_binding(run_id, phase, reference):
            bind(run_id, phase, reference)
            if phase == args.kill_at:
                os.kill(os.getpid(), signal.SIGKILL)

        repo.put, repo.bind = stop_after_part, stop_after_binding
    view = durable.execute(case["request"], binding, producer=old.producer, **inputs)
    assert view["result"]["search_result"] == case["source"]
    assert view["result"]["selection_result"] == case["selection_result"]
    assert view["result"]["order_plan"] == old.execute(**case)
    print(json.dumps(summary(view), sort_keys=True))


def command(args, root, *extra):
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "--build",
        str(args.build),
        "--root",
        str(root),
        "--worker",
        *extra,
    ]


def verify(args):
    checks = []
    for mode, count in (
        ("ABC_XYZ_SHARED_BUDGET", 32),
        ("LEGACY_STYLE", 32),
        ("WIDE_REFERENCE", 88),
    ):
        root = args.root / mode
        root.mkdir()
        first = subprocess.run(
            command(args, root, "--mode", mode),
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
        expected = json.loads(first.stdout)
        durable, _ = runtime(args.build, root)
        run_id = expected["calculation_binding"]["engine_run_id"]
        quick = durable.read_integrity(run_id, expected_input_reference=expected["input_reference"])
        assert quick["verification_status"] == "INTEGRITY_VERIFIED"
        assert durable.kernel.calls == 0
        again = subprocess.run(
            command(args, root, "--run-id", run_id),
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
        assert json.loads(again.stdout) == expected
        assert expected["counts"]["candidate_count"] == count
        assert not expected["automatic_order_allowed"] and not expected["automatic_publish_allowed"]
        checks.append({"mode": mode, "fresh_process_verified": True, **expected})

    # Death after input, a result leaf, manifest and completed pointer respectively.
    for phase in ("INPUT", "PART", "MANIFEST", "COMPLETE"):
        root = args.root / ("interrupted-" + phase)
        root.mkdir()
        killed = subprocess.run(
            command(args, root, "--mode", "LEGACY_STYLE", "--kill-at", phase),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert killed.returncode == -signal.SIGKILL, killed.stderr
        durable, repo = runtime(args.build, root)
        # Existing fixture Run identity, not a new recovery ID or a new Source.
        _, case = execution_case(mode="LEGACY_STYLE")
        run_id = case["request"].to_dict()["recommendation"]["canonical_input"]["context"][
            "engine_run_id"
        ]
        saved = {p.name: p.read_bytes() for p in root.iterdir() if p.name.startswith("target-")}
        if phase != "COMPLETE":
            assert repo.lookup(run_id, "COMPLETE") is None
            try:
                durable.read_integrity(run_id)
            except InventoryInputError as error:
                assert str(error) == "TARGET_STORAGE_INCOMPLETE"
            else:
                raise AssertionError("incomplete run admitted")
        restored = subprocess.run(
            command(args, root, "--run-id", run_id),
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
        actual = json.loads(restored.stdout)
        assert actual["counts"]["candidate_count"] == 32
        assert actual["verification_status"] == "RECOMPUTED_VERIFIED"
        assert all((root / name).read_bytes() == value for name, value in saved.items())
        assert not actual["automatic_order_allowed"] and not actual["database_writes"]
        checks.append(
            {
                "interruption": phase,
                "exit_code": killed.returncode,
                "fresh_process_recovered": True,
                "published_bytes_preserved": True,
                **actual,
            }
        )
    return {
        "checks": checks,
        "check_count": len(checks),
        "network_used": False,
        "database_writes": False,
        "runtime_registered": False,
        "scope": "actual DLL local storage/replay; not a performance or production test",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--mode", default="LEGACY_STYLE")
    parser.add_argument("--kill-at")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    if args.worker:
        child(args)
    else:
        report = verify(args)
        (args.root / "verification-report.json").write_text(
            json.dumps(report, sort_keys=True) + "\n"
        )
        print(
            json.dumps(
                {
                    "check_count": report["check_count"],
                    "cases": [
                        {
                            **{
                                k: row[k] for k in ("mode", "interruption", "exit_code") if k in row
                            },
                            "candidates": row["counts"]["candidate_count"],
                            "verification_status": row["verification_status"],
                        }
                        for row in report["checks"]
                    ],
                    "database_writes": False,
                    "network_used": False,
                },
                sort_keys=True,
            )
        )

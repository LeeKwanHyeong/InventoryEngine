"""Offline actual DLL local Runtime canary with saved-only process recovery."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

from dsio_inventory_engine.infrastructure.calculation_pythonnet import CalculationPythonNetKernel
from dsio_inventory_engine.infrastructure.sqlite import SqliteInventoryEvidenceUnitOfWork
from dsio_inventory_engine.infrastructure.target_pipeline_artifacts import (
    LocalTargetPipelineArtifactStore,
)
from dsio_inventory_engine.inventory_contracts.runtime import InventoryRuntimeExecutionRequest
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from dsio_inventory_engine.recommend_replenishment.application.target_durable import (
    RunDurableTargetPolicyPipelineUseCase,
)
from dsio_inventory_engine.run_inventory.target_result_execution import (
    InventoryTargetResultExecution,
)
from tests.support.calculation_adapter_fixtures import DEPLOYMENT
from tests.support.target_runtime_fixtures import runtime_world


def numerical_kernel(build):
    dll = build / "Inventory.Calculation.dll"
    return CalculationPythonNetKernel(
        dll, expected_dll_sha256=hashlib.sha256(dll.read_bytes()).hexdigest()
    )


def summary(result):
    return {
        "result_pointer": result["result_pointer"],
        "counts": result["target_result_link"]["counts"],
        "installed_build": result["installed_build"],
        "verification_status": result["verification_status"],
        "automatic_publish_allowed": result["automatic_publish_allowed"],
        "automatic_order_allowed": result["automatic_order_allowed"],
    }


def worker(args):
    w = runtime_world(args.root, numerical_kernel(args.build), mode=args.mode)
    meta = {
        "request": w.request.to_dict(),
        "sources": w.pointers,
        "catalog": w.catalog,
        "installed_build": w.build,
    }
    (args.root / "resume.json").write_text(json.dumps(meta, sort_keys=True))
    return restore(args)


def restore(args):
    meta = json.loads((args.root / "resume.json").read_text())
    request = InventoryRuntimeExecutionRequest.from_dict(meta["request"])
    store = SqliteInventoryEvidenceUnitOfWork(args.root / "artifacts.sqlite")
    original = store.read_artifact(meta["sources"]["bundle"]["artifact_reference"])
    outbox = store.find_outbox(request.engine_run_id, request.value["attempt_no"])
    target = RunDurableTargetPolicyPipelineUseCase(
        DEPLOYMENT,
        numerical_kernel(args.build),
        LocalTargetPipelineArtifactStore(args.root / "target"),
    )
    runtime = InventoryTargetResultExecution(
        deployment=DEPLOYMENT,
        target_storage=target,
        evidence_repository=store,
        catalog=meta["catalog"],
        expected_build=meta["installed_build"],
    )
    if args.kill_at:
        commit = store.commit_artifacts

        def interrupted(artifacts):
            value = commit(artifacts)
            key = (
                "inventory.target_result_link"
                if args.kill_at == "LINK"
                else "inventory.dsim_projection"
            )
            if any(a.contract_key == key for a in artifacts):
                os.kill(os.getpid(), signal.SIGKILL)
            return value

        store.commit_artifacts = interrupted
    if args.read_only:
        pointer = json.loads((args.root / "result-pointer.json").read_text())

        def no_write(*args, **kwargs):
            raise AssertionError("read must not write")

        store.commit_artifacts = no_write
        result = runtime.read(request=request, result_pointer=pointer)
    else:
        result = runtime.recover(request=request, sources=meta["sources"])
        (args.root / "result-pointer.json").write_text(
            json.dumps(result["result_pointer"], sort_keys=True)
        )
    pointer = json.loads(json.dumps(result["result_pointer"]))
    pointer["projection"]["content_hash"] = "0" * 64
    try:
        runtime.read(request=request, result_pointer=pointer)
    except InventoryInputError:
        pass
    else:
        raise AssertionError("wrong pointer accepted")
    assert store.read_artifact(original.reference) == original
    assert store.find_outbox(request.engine_run_id, request.value["attempt_no"]) == outbox
    return summary(result)


def verify(args):
    checks = []
    for mode, expected_count, kill in (
        ("ABC_XYZ_SHARED_BUDGET", 32, None),
        ("LEGACY_STYLE", 32, None),
        ("WIDE_REFERENCE", 88, None),
        ("ABC_V1", 2, "LINK"),
        ("ABC_V1", 2, "PROJECTION"),
    ):
        root = args.root / (kill or mode)
        root.mkdir()
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--build",
            str(args.build),
            "--root",
            str(root),
        ]
        initial = subprocess.run(
            command + ["--worker", "--mode", mode] + (["--kill-at", kill] if kill else []),
            capture_output=True,
            text=True,
            timeout=240,
        )
        if kill:
            assert initial.returncode == -signal.SIGKILL, initial.stderr
        else:
            assert initial.returncode == 0, initial.stderr
        restored = subprocess.run(
            command + ["--restore"], capture_output=True, text=True, check=True, timeout=240
        )
        verified = subprocess.run(
            command + ["--read-only"], capture_output=True, text=True, check=True, timeout=240
        )
        result = json.loads(restored.stdout)
        assert json.loads(verified.stdout) == result
        if not kill:
            assert json.loads(initial.stdout) == result
        assert result["counts"]["candidate_count"] == expected_count
        assert not result["automatic_publish_allowed"] and not result["automatic_order_allowed"]
        checks.append(
            {"mode": mode, "interruption": kill, "fresh_process_verified": True, **result}
        )
    output = {"actual_csharp": True, "database_writes": False, "checks": checks}
    (args.root / "verification-report.json").write_text(
        json.dumps(output, sort_keys=True, indent=2)
    )
    return {"verified_cases": len(checks), "actual_csharp": True, "database_writes": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--mode", default="ABC_V1")
    parser.add_argument("--kill-at", choices=("LINK", "PROJECTION"))
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--read-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            worker(args)
            if args.worker
            else restore(args)
            if args.restore or args.read_only
            else verify(args),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

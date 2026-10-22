"""Real DLL first-call canary. Saved-only recovery, no network or business DB."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

from dsio_inventory_engine.infrastructure.sqlite import SqliteInventoryEvidenceUnitOfWork
from dsio_inventory_engine.infrastructure.target_pipeline_artifacts import (
    LocalTargetPipelineArtifactStore,
)
from dsio_inventory_engine.inventory_contracts.runtime import InventoryRuntimeExecutionRequest
from dsio_inventory_engine.recommend_replenishment.application.target_durable import (
    RunDurableTargetPolicyPipelineUseCase,
)
from dsio_inventory_engine.run_inventory.initial_target_execution import (
    InventoryInitialTargetExecution,
    KEY,
)
from tests.support.calculation_adapter_fixtures import DEPLOYMENT
from tests.support.initial_execution_fixtures import initial_world
from verify_target_runtime import numerical_kernel, summary


def terminate_after_commit(store, target_repo, phase):
    commit, bundle_commit, put = store.commit_artifacts, store.commit, target_repo.put

    def artifacts(rows):
        value = commit(rows)
        key = {
            "CHECKPOINT": KEY,
            "LINK": "inventory.target_result_link",
            "PROJECTION": "inventory.dsim_projection",
        }.get(phase)
        if any(a.contract_key == key for a in rows):
            os.kill(os.getpid(), signal.SIGKILL)
        return value

    def bundle(*args, **kwargs):
        value = bundle_commit(*args, **kwargs)
        if phase == "BUNDLE":
            os.kill(os.getpid(), signal.SIGKILL)
        return value

    def part(kind, value):
        ref = put(kind, value)
        if phase == "TARGET_PART" and kind == "PART":
            os.kill(os.getpid(), signal.SIGKILL)
        return ref

    store.commit_artifacts, store.commit, target_repo.put = artifacts, bundle, part


async def worker(args):
    w = initial_world(args.root, numerical_kernel(args.build), mode=args.mode)
    assert w.store.find_outbox(w.request.engine_run_id, 1) is None
    assert w.target_repo.lookup(w.request.engine_run_id, "COMPLETE") is None
    meta = {
        "request": w.request.to_dict(),
        "input_reference": w.input_ref,
        "expected_build": w.build,
        "catalog": w.runtime_kwargs["catalog"],
    }
    (args.root / "resume.json").write_text(json.dumps(meta, sort_keys=True))
    terminate_after_commit(w.store, w.target_repo, args.kill_at)
    result = await w.runtime.execute(request=w.request, target_input_reference=w.input_ref)
    (args.root / "result-pointer.json").write_text(
        json.dumps(result["result_pointer"], sort_keys=True)
    )
    return {**summary(result), "initial_input": result["initial_input"]}


async def restore(args):
    meta = json.loads((args.root / "resume.json").read_text())
    request = InventoryRuntimeExecutionRequest.from_dict(meta["request"])
    store = SqliteInventoryEvidenceUnitOfWork(args.root / "artifacts.sqlite")
    target = RunDurableTargetPolicyPipelineUseCase(
        DEPLOYMENT,
        numerical_kernel(args.build),
        LocalTargetPipelineArtifactStore(args.root / "target"),
    )
    original_outbox = store.find_outbox(request.engine_run_id, request.value["attempt_no"])
    original = (
        None
        if original_outbox is None
        else store.read_artifact(original_outbox.message.payload()["bundle_reference"])
    )
    runtime = InventoryInitialTargetExecution(
        deployment=DEPLOYMENT,
        input_provider=None,
        target_storage=target,
        evidence_repository=store,
        catalog=meta["catalog"],
        expected_build=meta["expected_build"],
    )
    if args.read_only:
        pointer = json.loads((args.root / "result-pointer.json").read_text())

        def no_write(*args, **kwargs):
            raise AssertionError("read wrote")

        store.commit_artifacts = no_write
        result = runtime.read(request=request, result_pointer=pointer)
    else:
        result = await runtime.recover(
            request=request, target_input_reference=meta["input_reference"]
        )
        (args.root / "result-pointer.json").write_text(
            json.dumps(result["result_pointer"], sort_keys=True)
        )
    if original is not None:
        assert store.read_artifact(original.reference) == original
        assert (
            store.find_outbox(request.engine_run_id, request.value["attempt_no"]) == original_outbox
        )
    return {**summary(result), "initial_input": result["initial_input"]}


def verify(args):
    checks = []
    cases = [
        ("ABC_XYZ_SHARED_BUDGET", 32, None),
        ("LEGACY_STYLE", 32, None),
        ("WIDE_REFERENCE", 88, None),
    ]
    cases += [
        ("ABC_V1", 2, phase)
        for phase in ("CHECKPOINT", "BUNDLE", "TARGET_PART", "LINK", "PROJECTION")
    ]
    for mode, count, phase in cases:
        root = args.root / (phase or mode)
        root.mkdir()
        cmd = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--build",
            str(args.build),
            "--root",
            str(root),
        ]
        initial = subprocess.run(
            cmd + ["--worker", "--mode", mode] + (["--kill-at", phase] if phase else []),
            capture_output=True,
            text=True,
            timeout=240,
        )
        assert initial.returncode == (-signal.SIGKILL if phase else 0), initial.stderr
        recovered = subprocess.run(
            cmd + ["--restore"], capture_output=True, text=True, check=True, timeout=240
        )
        read = subprocess.run(
            cmd + ["--read-only"], capture_output=True, text=True, check=True, timeout=240
        )
        result = json.loads(recovered.stdout)
        assert result == json.loads(read.stdout)
        if not phase:
            assert result == json.loads(initial.stdout)
        assert result["counts"]["candidate_count"] == count
        assert not result["automatic_publish_allowed"] and not result["automatic_order_allowed"]
        checks.append(
            {
                "mode": mode,
                "interruption": phase,
                "source_free_fresh_process_recovery": True,
                **result,
            }
        )
    report = {"actual_csharp": True, "database_writes": False, "checks": checks}
    (args.root / "verification-report.json").write_text(
        json.dumps(report, sort_keys=True, indent=2)
    )
    print(
        json.dumps({"verified_cases": len(checks), "actual_csharp": True, "database_writes": False})
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--build", type=Path, required=True)
    p.add_argument("--mode", default="ABC_V1")
    p.add_argument(
        "--kill-at", choices=("CHECKPOINT", "BUNDLE", "TARGET_PART", "LINK", "PROJECTION")
    )
    p.add_argument("--worker", action="store_true")
    p.add_argument("--restore", action="store_true")
    p.add_argument("--read-only", action="store_true")
    args = p.parse_args()
    if args.worker or args.restore or args.read_only:
        print(
            json.dumps(asyncio.run(worker(args) if args.worker else restore(args)), sort_keys=True)
        )
    else:
        verify(args)


if __name__ == "__main__":
    main()

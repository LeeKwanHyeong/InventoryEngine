"""Actual C# -> saved target graph -> unchanged Bundle -> DSIM Artifact canary.

Only a caller-owned temporary directory and SQLite Artifact emulator are used.
No development PostgreSQL, publication API, credentials or shared Runtime.
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
from dsio_inventory_engine.infrastructure.sqlite import SqliteInventoryEvidenceUnitOfWork
from dsio_inventory_engine.infrastructure.target_pipeline_artifacts import (
    LocalTargetPipelineArtifactStore,
)
from dsio_inventory_engine.inventory_contracts.runtime import InventoryRuntimeExecutionRequest
from dsio_inventory_engine.inventory_evidence.application import (
    ReadAndVerifyInventoryResultBundleUseCase,
)
from dsio_inventory_engine.inventory_evidence.projection import (
    PersistDsimProjectionUseCase,
    VerifyDsimProjectionUseCase,
)
from dsio_inventory_engine.inventory_evidence.projection_source import DsimSourceProjector
from dsio_inventory_engine.inventory_evidence.target_result_link import TargetResultLinkUseCase
from dsio_inventory_engine.recommend_replenishment.application.target_durable import (
    RunDurableTargetPolicyPipelineUseCase,
)
from tests.support.calculation_adapter_fixtures import DEPLOYMENT
from tests.support.target_result_link_fixtures import link_world


def kernel(build):
    dll = build / "Inventory.Calculation.dll"
    return CalculationPythonNetKernel(
        dll, expected_dll_sha256=hashlib.sha256(dll.read_bytes()).hexdigest()
    )


def summarize(linked, projection):
    return {
        "link_reference": linked.artifact.reference,
        "link_hash": linked.artifact.content_hash,
        "bundle_content_hash": linked.artifact.document()["bundle_content_hash"],
        "search_content_hash": linked.result["search_result"]["content_hash"],
        "selection_content_hash": linked.result["selection_result"]["content_hash"],
        "psi_content_hash": linked.result["order_plan"]["psi_content_hash"],
        "orders_content_hash": linked.result["order_plan"]["orders_content_hash"],
        "candidate_count": linked.artifact.document()["counts"]["candidate_count"],
        "order_count": linked.artifact.document()["counts"]["order_count"],
        "projection_content_hash": projection.manifest["projection_content_hash"],
        "table_row_counts": projection.manifest["table_row_counts"],
        "verification_status": "RECOMPUTED_VERIFIED",
        "automatic_publish_allowed": False,
        "automatic_order_allowed": False,
    }


def recover(args):
    meta = json.loads((args.root / "resume.json").read_text())
    request = InventoryRuntimeExecutionRequest.from_dict(meta["request"])
    store = SqliteInventoryEvidenceUnitOfWork(args.root / "artifacts.sqlite")
    base = store.read_artifact(meta["bundle_reference"])
    outbox = store.find_outbox(request.engine_run_id, request.value["attempt_no"])
    repo = LocalTargetPipelineArtifactStore(args.root / "target")
    assert repo.lookup(request.engine_run_id, "COMPLETE") == meta["target_manifest_reference"]
    target = RunDurableTargetPolicyPipelineUseCase(DEPLOYMENT, kernel(args.build), repo)
    link = TargetResultLinkUseCase(deployment=DEPLOYMENT, target_storage=target, repository=store)
    link.recover(
        request=request,
        bundle_reference=meta["bundle_reference"],
        expected_input_reference=meta["target_input_reference"],
    )
    linked = link.read(request=request, bundle_reference=meta["bundle_reference"])
    projector = DsimSourceProjector(
        catalog=meta["catalog"],
        bundle_verifier=ReadAndVerifyInventoryResultBundleUseCase(DEPLOYMENT, store),
        target_result_reader=link,
    )
    ptr = PersistDsimProjectionUseCase(projector=projector, store=store).execute(
        request=request, bundle_reference=meta["bundle_reference"], chunk_size=2
    )
    projection = VerifyDsimProjectionUseCase(projector=projector, store=store).execute(
        request=request, reference=ptr["artifact_reference"], expected_hash=ptr["content_hash"]
    )
    assert store.read_artifact(meta["bundle_reference"]) == base
    assert store.find_outbox(request.engine_run_id, request.value["attempt_no"]) == outbox
    return summarize(linked, projection)


def worker(args):
    w = link_world(args.root, kernel(args.build), mode=args.mode)
    original = w.store.read_artifact(w.persisted.bundle_reference)
    outbox = w.store.find_outbox(w.request.engine_run_id, w.request.value["attempt_no"])
    meta = {
        "request": w.request.to_dict(),
        "bundle_reference": w.persisted.bundle_reference,
        "target_input_reference": w.target_repo.lookup(w.request.engine_run_id, "INPUT"),
        "target_manifest_reference": w.target_repo.lookup(w.request.engine_run_id, "COMPLETE"),
        "catalog": w.catalog,
    }
    (args.root / "resume.json").write_text(json.dumps(meta, sort_keys=True))
    if args.kill_at:
        original_commit = w.store.commit_artifacts
        calls = 0

        def terminate_after_commit(artifacts):
            nonlocal calls
            result = original_commit(artifacts)
            calls += 1
            if calls == {"LINK": 1, "PROJECTION": 2}[args.kill_at]:
                os.kill(os.getpid(), signal.SIGKILL)
            return result

        w.store.commit_artifacts = terminate_after_commit
    w.link.execute(
        request=w.request,
        bundle_reference=w.persisted.bundle_reference,
        expected_input_reference=meta["target_input_reference"],
    )
    linked = w.link.read(request=w.request, bundle_reference=w.persisted.bundle_reference)
    ptr = w.producer.execute(
        request=w.request, bundle_reference=w.persisted.bundle_reference, chunk_size=2
    )
    projection = w.verifier.execute(
        request=w.request, reference=ptr["artifact_reference"], expected_hash=ptr["content_hash"]
    )
    assert w.store.read_artifact(w.persisted.bundle_reference) == original
    assert w.store.find_outbox(w.request.engine_run_id, w.request.value["attempt_no"]) == outbox
    return summarize(linked, projection)


def command(args, root, *extra):
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "--build",
        str(args.build),
        "--root",
        str(root),
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
        completed = subprocess.run(
            command(args, root, "--worker", "--mode", mode),
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
        )
        first = json.loads(completed.stdout)
        restored = subprocess.run(
            command(args, root, "--recover"),
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
        )
        assert json.loads(restored.stdout) == first
        assert first["candidate_count"] == count and first["order_count"] > 0
        checks.append(
            {"mode": mode, "fresh_process_verified": True, "parent_bytes_preserved": True, **first}
        )
    for phase in ("LINK", "PROJECTION"):
        root = args.root / ("interrupted-" + phase)
        root.mkdir()
        stopped = subprocess.run(
            command(args, root, "--worker", "--mode", "LEGACY_STYLE", "--kill-at", phase),
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert stopped.returncode == -signal.SIGKILL, stopped.stderr
        restored = subprocess.run(
            command(args, root, "--recover"),
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
        )
        result = json.loads(restored.stdout)
        assert result["candidate_count"] == 32 and result["order_count"] > 0
        checks.append(
            {
                "interruption": phase,
                "exit_code": stopped.returncode,
                "fresh_process_recovered": True,
                "parent_bytes_preserved": True,
                **result,
            }
        )
    return {
        "check_count": len(checks),
        "checks": checks,
        "dll_sha256": kernel(args.build).dll_sha256,
        "development_database_access": False,
        "database_migrations": False,
        "runtime_registered": False,
        "actual_order_publication": False,
        "network_used": False,
        "local_sqlite_usage": "temporary Artifact/Outbox emulator only",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--recover", action="store_true")
    parser.add_argument("--mode", default="LEGACY_STYLE")
    parser.add_argument("--kill-at", choices=("LINK", "PROJECTION"))
    args = parser.parse_args()
    if args.worker or args.recover:
        print(json.dumps(recover(args) if args.recover else worker(args), sort_keys=True))
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
                            k: row[k]
                            for k in ("mode", "interruption", "candidate_count", "order_count")
                            if k in row
                        }
                        for row in report["checks"]
                    ],
                },
                sort_keys=True,
            )
        )

"""Real local quotas, processes, cancellation and manual PSI; no external DB/study."""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from dsio_inventory_engine.evaluate_replenishment.robustness.durable import _slot
from dsio_inventory_engine.evaluate_replenishment.robustness.resource_execution import (
    ControlledCatalogExecutor,
)
from dsio_inventory_engine.infrastructure.robustness_resource_files import locked
from dsio_inventory_engine.infrastructure.robustness_resource_pool import (
    LocalRobustnessResourcePool,
)
from dsio_inventory_engine.infrastructure.robustness_resource_store import resource_store
from dsio_inventory_engine.inventory_contracts.robustness_resources import ResourceLimits
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from scripts.verify_robustness_shared_storage import EXPECTED_RESULTS
from tests.integration.test_robustness_durable_offline import prepared, file_identity
from tests.support.robustness_resource_fixtures import (
    block_catalog_factory,
    catalog_factory,
    fault_factory,
    pause_after_first_leaf_factory,
)


@pytest.fixture
def pool(tmp_path):
    root = tmp_path / "pool"
    root.mkdir()
    return LocalRobustnessResourcePool(root)


def arguments(assembly, path):
    return {
        "execution_id": "HAND",
        "assembly": assembly,
        "expected_assembly_hash": assembly["content_hash"],
        "realization": path.to_dict(),
        "expected_realization_hash": path.content_hash,
    }


def wait_until(predicate, *, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("OFFLINE_HAND_EVENT_NOT_OBSERVED")


@pytest.mark.parametrize("mode", list(EXPECTED_RESULTS))
def test_actual_whole_catalog_is_resource_bound_without_hash_or_psi_change(pool, mode):
    _, assembly, path = prepared(mode=mode)
    executor = ControlledCatalogExecutor(pool, catalog_factory)
    receipt = executor.execute(mode, "run", arguments(assembly, path), seconds=180)
    assert receipt["result_hash"] == EXPECTED_RESULTS[mode]
    bindings = {
        "execution_id": "HAND",
        "expected_input_hash": receipt["input_reference"]["content_hash"],
        "expected_result_hash": receipt["result_hash"],
    }
    root = pool.store_root(mode)
    before = file_identity(root)
    fast = executor.execute(mode, "inspect", bindings, seconds=180)
    full = executor.execute(mode, "verify_recalculation", bindings, seconds=180)
    assert full["psi_recalculation_status"] == "VERIFIED"
    assert fast["psi_recalculation_status"] == "NOT_RUN"
    assert full["result_hash"] == fast["result_hash"] == receipt["result_hash"]
    expected = 88 if mode == "WIDE_REFERENCE" else 32
    assert full["counts"]["candidate_count"] == full["counts"]["successful_count"] == expected
    assert full["counts"]["psi_row_count"] == expected * 65
    assert full["automatic_publish_allowed"] is full["automatic_order_allowed"] is False
    assert file_identity(root) == before
    assert pool.inspect()["raw_bytes"] <= 20_000_000_000


def test_processes_cannot_oversubscribe_one_raw_quota_with_different_stores(pool, tmp_path):
    root = tmp_path / "race"
    root.mkdir()
    resources = LocalRobustnessResourcePool(root, ResourceLimits(18_000))
    barrier = tmp_path / "start"
    children = [
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tests.support.robustness_resource_fixtures",
                "race",
                str(root),
                key,
                str(barrier),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for key in ("A", "B")
    ]
    try:
        wait_until(
            lambda: all(
                barrier.with_name(barrier.name + "-ready-" + key).exists() for key in ("A", "B")
            )
        )
        barrier.write_bytes(b"start")
        actual = sorted(child.communicate(timeout=10)[0].decode().strip() for child in children)
        assert actual == ["RESOURCE_RAW_BUDGET_EXCEEDED", "SUCCEEDED"]
        assert all(child.returncode == 0 for child in children)
        assert 10_000 < resources.inspect()["raw_bytes"] < 18_000
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=2)


def test_unresponsive_worker_is_hard_stopped_and_next_invocation_can_run(pool):
    executor = ControlledCatalogExecutor(pool, fault_factory)
    started = time.monotonic()
    with pytest.raises(InventoryInputError, match="TIMED_OUT"):
        executor.execute("SLEEP", "inspect", {"behavior": "sleep"}, seconds=0.6)
    assert time.monotonic() - started < 4
    assert executor.execute("SLEEP", "inspect", {}, seconds=10)["scope"] == "SUPERVISOR_TEST_ONLY"


def test_queue_wait_is_included_in_deadline(pool):
    executor = ControlledCatalogExecutor(pool, fault_factory)
    with locked(pool.root, pool.identity, "invocation.lock"):
        started = time.monotonic()
        with pytest.raises(InventoryInputError, match="TIMED_OUT"):
            executor.execute("QUEUED", "inspect", {}, seconds=0.6)
        assert time.monotonic() - started < 4


def test_cancelled_partial_write_is_reserved_quarantined_and_not_mistaken_for_checkpoint(pool):
    executor = ControlledCatalogExecutor(pool, fault_factory)
    cancellation = threading.Event()
    root = pool.store_root("PARTIAL")
    with ThreadPoolExecutor(max_workers=1) as thread:
        future = thread.submit(
            executor.execute,
            "PARTIAL",
            "inspect",
            {"behavior": "partial"},
            seconds=20,
            cancellation=cancellation,
        )
        wait_until(lambda: bool(list(root.glob(".robustness-*"))))
        assert (pool.root / "pending.json").exists()
        cancellation.set()
        with pytest.raises(InventoryInputError, match="CANCELLED"):
            future.result(timeout=5)
    usage = pool.inspect()
    assert usage["quarantined_files"] == 2  # Partial artifact and the bounded IPC Job.
    assert not list(root.iterdir())  # Not an input/leaf/complete pointer.
    assert not (pool.root / "pending.json").exists()
    assert not pool.purge_expired_temporaries()["removed_quarantine_files"]
    assert executor.execute("PARTIAL", "inspect", {}, seconds=10)["scope"] == "SUPERVISOR_TEST_ONLY"


def test_interruption_after_actual_first_leaf_recovers_same_input_and_original_result(pool):
    _, assembly, path = prepared()
    executor = ControlledCatalogExecutor(pool, pause_after_first_leaf_factory)
    cancellation = threading.Event()
    store = resource_store(pool, "RESUME")
    checkpoint = store.root / store._slot_name(_slot("HAND", "LEAF", 0))
    with ThreadPoolExecutor(max_workers=1) as thread:
        future = thread.submit(
            executor.execute,
            "RESUME",
            "run",
            arguments(assembly, path),
            seconds=90,
            cancellation=cancellation,
        )
        wait_until(checkpoint.exists, seconds=60)
        cancellation.set()
        with pytest.raises(InventoryInputError, match="CANCELLED"):
            future.result(timeout=5)
    before = file_identity(store.root)
    input_ref = store.lookup(_slot("HAND", "INPUT"))
    assert input_ref is not None and store.lookup(_slot("HAND", "COMPLETE")) is None
    recovered = ControlledCatalogExecutor(pool, catalog_factory).execute(
        "RESUME",
        "recover",
        {"execution_id": "HAND", "expected_input_hash": input_ref["content_hash"]},
        seconds=180,
    )
    assert recovered["result_hash"] == EXPECTED_RESULTS["LEGACY_STYLE"]
    after = file_identity(store.root)
    assert all(after[name] == identity for name, identity in before.items())
    # Exact input binding is still required after a resource stop.
    with pytest.raises(InventoryInputError, match="INPUT_BINDING_MISMATCH"):
        ControlledCatalogExecutor(pool, catalog_factory).execute(
            "RESUME",
            "recover",
            {"execution_id": "HAND", "expected_input_hash": "0" * 64},
            seconds=20,
        )


def test_worker_exit_is_not_reported_as_success(pool):
    with pytest.raises(InventoryInputError, match="WORKER_EXITED"):
        ControlledCatalogExecutor(pool, fault_factory).execute(
            "EXIT", "inspect", {"behavior": "exit"}, seconds=10
        )


def test_supervisor_death_stops_heartbeat_and_releases_single_worker_lease(pool, tmp_path):
    marker = tmp_path / "worker.json"
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    parent = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tests.support.robustness_resource_fixtures",
            str(pool.root),
            str(marker),
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        wait_until(marker.exists)
        heartbeat = marker.with_suffix(".heartbeat")
        wait_until(heartbeat.exists)
        parent.kill()
        parent.wait(timeout=5)
        # The independent watchdog sees the vanished owner and kills the owned group.
        ControlledCatalogExecutor(pool, fault_factory).execute(
            "AFTER_OWNER_DEATH", "inspect", {}, seconds=5
        )
        size = heartbeat.stat().st_size
        time.sleep(0.1)
        assert heartbeat.stat().st_size == size
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.communicate(timeout=5)


def test_resource_boundary_never_unblocks_fsn(pool):
    _, assembly, path = prepared(gate="BLOCK")
    receipt = ControlledCatalogExecutor(pool, block_catalog_factory).execute(
        "BLOCK", "run", arguments(assembly, path), seconds=90
    )
    store = resource_store(pool, "BLOCK")
    manifest = store.read(receipt["manifest_reference"])
    for entry in manifest["leaves"]:
        leaf = store.read(entry["reference"])
        for child in leaf["candidate_executions"]:
            raw = child["causal_result"]
            assert raw["effective_policy_admission"]["effective_order_action"] == "BLOCK"
            assert not raw["recommended_orders"]
            assert raw["automatic_publish_allowed"] is raw["automatic_order_allowed"] is False


def test_cancellation_stops_worker_descendants_too(pool, tmp_path):
    executor = ControlledCatalogExecutor(pool, fault_factory)
    cancellation = threading.Event()
    marker = tmp_path / "descendant.pid"
    with ThreadPoolExecutor(max_workers=1) as thread:
        future = thread.submit(
            executor.execute,
            "DESCENDANTS",
            "inspect",
            {"behavior": "descendant", "marker": str(marker)},
            seconds=10,
            cancellation=cancellation,
        )
        wait_until(marker.exists)
        cancellation.set()
        with pytest.raises(InventoryInputError, match="CANCELLED"):
            future.result(timeout=5)
    time.sleep(0.9)
    assert not marker.with_suffix(".escaped").exists()


@pytest.mark.parametrize(
    "operation,arguments", [("write", {}), ("latest", {}), ([], {}), ("run", [])]
)
def test_unsupported_operation_or_arguments_never_start_a_worker(pool, operation, arguments):
    with pytest.raises(InventoryInputError):
        ControlledCatalogExecutor(pool, fault_factory).execute(
            "INVALID", operation, arguments, seconds=10
        )
    assert not list(pool.root.glob("artifacts-*"))


def test_pre_cancelled_invocation_does_not_start_or_publish(pool):
    cancellation = threading.Event()
    cancellation.set()
    with pytest.raises(InventoryInputError, match="CANCELLED"):
        ControlledCatalogExecutor(pool, fault_factory).execute(
            "CANCELLED", "inspect", {}, cancellation=cancellation
        )
    assert not list(pool.root.glob("artifacts-*"))


def test_interpreter_startup_hang_with_large_input_cannot_block_deadline(
    pool, tmp_path, monkeypatch
):
    customization = tmp_path / "bootstrap"
    customization.mkdir()
    (customization / "sitecustomize.py").write_text("import time\ntime.sleep(60)\n")
    monkeypatch.setenv(
        "PYTHONPATH", str(customization) + os.pathsep + os.environ.get("PYTHONPATH", "")
    )
    started = time.monotonic()
    with pytest.raises(InventoryInputError, match="TIMED_OUT"):
        ControlledCatalogExecutor(pool, fault_factory).execute(
            "STARTUP",
            "inspect",
            {"large_unused_argument": "x" * 1_000_000},
            seconds=0.6,
        )
    assert time.monotonic() - started < 4
    assert not list((pool.root / "jobs").iterdir())
    assert pool.inspect()["quarantined_files"] == 1

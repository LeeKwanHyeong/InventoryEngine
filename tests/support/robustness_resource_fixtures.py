"""Manual resource faults and actual Catalog factories, never official sources."""

import json
import os
from pathlib import Path
import sys
import time

from dsio_inventory_engine.evaluate_replenishment.robustness.contracts import sealed
from dsio_inventory_engine.evaluate_replenishment.robustness.durable import (
    RunDurableItemCatalogUseCase,
)
from dsio_inventory_engine.evaluate_replenishment.robustness.item_execution import (
    RunItemCatalogUseCase,
)
from dsio_inventory_engine.evaluate_replenishment.robustness.resource_execution import (
    ControlledCatalogExecutor,
)
from dsio_inventory_engine.infrastructure.robustness_artifacts import LocalRobustnessArtifactStore
from dsio_inventory_engine.infrastructure.robustness_resource_pool import (
    LocalRobustnessResourcePool,
)
from dsio_inventory_engine.infrastructure.robustness_resource_store import resource_store
from dsio_inventory_engine.inventory_contracts.robustness_resources import ResourceLimits
from tests.support.robustness_item_fixtures import DEV, item_case


def document(marker="A", size=10_000):
    return sealed("inventory-robustness-durable-header", {"marker": marker * size})


def catalog_factory(repository):
    return RunDurableItemCatalogUseCase(
        RunItemCatalogUseCase(DEV, item_case()["producer"]), repository
    )


def block_catalog_factory(repository):
    case = item_case(gate="BLOCK")
    return RunDurableItemCatalogUseCase(RunItemCatalogUseCase(DEV, case["producer"]), repository)


def pause_after_first_leaf_factory(repository):
    original = repository.bind

    def bind(key, ref):
        original(key, ref)
        if key.endswith(":LEAF:0"):
            time.sleep(60)

    repository.bind = bind
    return catalog_factory(repository)


class FaultRunner:
    def __init__(self, repository):
        self.repository = repository

    def inspect(self, *, behavior="quick", marker=None):
        if behavior == "exit":
            os._exit(19)
        if behavior == "partial":

            def interrupted(store, name, payload):
                temporary = store.root / (".robustness-" + "a" * 32)
                with temporary.open("xb") as stream:
                    stream.write(payload[:5])
                    stream.flush()
                    os.fsync(stream.fileno())
                time.sleep(60)

            LocalRobustnessArtifactStore._publish = interrupted
            self.repository.put(document(size=1000))
        if behavior == "heartbeat":
            with Path(marker).open("w") as stream:
                stream.write(json.dumps({"pid": os.getpid(), "pgid": os.getpgrp()}))
            while True:
                with Path(marker).with_suffix(".heartbeat").open("ab") as stream:
                    stream.write(b"x")
                time.sleep(0.02)
        if behavior == "descendant":
            child = os.fork()
            if child == 0:
                time.sleep(0.8)
                Path(marker).with_suffix(".escaped").write_bytes(b"must not happen")
                os._exit(0)
            Path(marker).write_text(str(child))
            time.sleep(60)
        if behavior == "sleep":
            time.sleep(60)
        return {"scope": "SUPERVISOR_TEST_ONLY", "pid": os.getpid()}


def fault_factory(repository):
    return FaultRunner(repository)


def competing_writer(root, store_id, ready, start, results):
    try:
        resources = LocalRobustnessResourcePool(Path(root), ResourceLimits(18_000))
        store = resource_store(resources, store_id, "2.0.0")
        ready.put(store_id)
        start.wait(5)
        store.put(document(marker=store_id))
        results.put("SUCCEEDED")
    except Exception as error:
        results.put(str(error))


if __name__ == "__main__":
    if sys.argv[1] == "race":
        root, store_id, barrier = Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4])
        resources = LocalRobustnessResourcePool(root, ResourceLimits(18_000))
        store = resource_store(resources, store_id, "2.0.0")
        barrier.with_name(barrier.name + "-ready-" + store_id).write_bytes(b"ready")
        deadline = time.monotonic() + 5
        while not barrier.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        try:
            store.put(document(marker=store_id))
            print("SUCCEEDED", flush=True)
        except Exception as error:
            print(str(error), flush=True)
    else:
        resources = LocalRobustnessResourcePool(Path(sys.argv[1]))
        ControlledCatalogExecutor(resources, fault_factory).execute(
            "OWNER", "inspect", {"behavior": "heartbeat", "marker": sys.argv[2]}, seconds=15
        )

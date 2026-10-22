"""Measured local calls through one controlled pool; no official Source producer."""

from time import monotonic, perf_counter_ns

from dsio_inventory_engine.inventory_contracts.robustness_resources import invocation_seconds
from dsio_inventory_engine.inventory_contracts.values import digest, hash_value, identifier, require
from .contracts import input_errors, sealed
from .resource_execution import ControlledCatalogExecutor
from .split_preflight import MODES


class MeasureHandCatalogResources:
    def __init__(self, executor: ControlledCatalogExecutor):
        require(
            type(executor) is ControlledCatalogExecutor, "PREFLIGHT_CONTROLLED_EXECUTOR_REQUIRED"
        )
        require(executor.storage_version == "3.0.0", "PREFLIGHT_STORAGE_VERSION_INVALID")
        self.executor = executor

    @input_errors
    def execute(self, store_id, assembly, realization, expected_result_hash, *, seconds=3600):
        begin = perf_counter_ns()
        deadline = monotonic() + invocation_seconds(seconds)
        identifier(store_id)
        hash_value(expected_result_hash)
        context = assembly["mathematical_request"]["recommendation"]["canonical_input"]["context"]
        require(context["plan_start_date"] == "2055-01-04", "PREFLIGHT_ONLY_MANUAL_PERIOD_ALLOWED")
        require(
            realization["path_id"] == "HAND-CATALOG-PATH"
            and realization["split"] == "NOMINAL"
            and realization["seed"] == 71,
            "PREFLIGHT_ONLY_MANUAL_PATH_ALLOWED",
        )
        mode = assembly["candidate_catalog"]["mode"]
        require(mode in MODES, "PREFLIGHT_MODE_INVALID")
        candidates = assembly["complete_candidates"]
        require(len(candidates) == MODES[mode], "PREFLIGHT_CANDIDATE_POPULATION_CHANGED")
        resources = self.executor.resources
        store = resources.store_root(store_id)
        require(not list(store.iterdir()), "PREFLIGHT_MEASUREMENT_STORE_NOT_EMPTY")
        before = resources.inspect()
        phases = {}

        def call(operation, arguments):
            resources._check_call(deadline, None)
            start = perf_counter_ns()
            result = self.executor.execute(
                store_id,
                operation,
                arguments,
                seconds=invocation_seconds(deadline - monotonic()),
            )
            phases[operation] = perf_counter_ns() - start
            return result

        arguments = {
            "execution_id": "HAND-SPLIT-PREFLIGHT",
            "assembly": assembly,
            "expected_assembly_hash": assembly["content_hash"],
            "realization": realization,
            "expected_realization_hash": digest(realization),
        }
        receipt = call("run", arguments)
        require(receipt["result_hash"] == expected_result_hash, "PREFLIGHT_RESULT_HASH_CHANGED")
        bindings = {
            "execution_id": arguments["execution_id"],
            "expected_input_hash": receipt["input_reference"]["content_hash"],
            "expected_result_hash": expected_result_hash,
        }
        fast = call("inspect", bindings)
        full = call("verify_recalculation", bindings)
        require(
            fast["psi_recalculation_status"] == "NOT_RUN"
            and full["psi_recalculation_status"] == "VERIFIED"
            and full["result_hash"] == fast["result_hash"] == expected_result_hash
            and full["counts"]["candidate_count"]
            == full["counts"]["successful_count"]
            == MODES[mode]
            and full["counts"]["psi_row_count"] == MODES[mode] * 65,
            "PREFLIGHT_COVERAGE_OR_REPLAY_INVALID",
        )
        after = resources.inspect()
        require(monotonic() < deadline, "PREFLIGHT_MEASUREMENT_DEADLINE_EXCEEDED")
        return sealed(
            "inventory-robustness-hand-resource-sample",
            {
                "mode": mode,
                "w0": "2055-01-04",
                "candidate_count": MODES[mode],
                "psi_row_count": full["counts"]["psi_row_count"],
                "result_hash": expected_result_hash,
                "input_hash": bindings["expected_input_hash"],
                "assembly_hash": assembly["content_hash"],
                "realization_hash": digest(realization),
                "catalog_hash": assembly["candidate_catalog"]["content_hash"],
                "candidate_ids_hash": digest([c["candidate_id"] for c in candidates]),
                "pool_id": digest({"root": str(resources.root), "identity": resources.identity}),
                "storage_version": "3.0.0",
                "raw_bytes_added": after["raw_bytes"] - before["raw_bytes"],
                "physical_bytes_added": after["physical_bytes"] - before["physical_bytes"],
                "files_added": after["files"] - before["files"],
                "phase_wall_ns": phases,
                "total_wall_ns": perf_counter_ns() - begin,
                "sample_kind": "HAND_CASE_MEASURED_NOT_OFFICIAL_BOUND",
                "integrity_status": "VERIFIED",
                "psi_recalculation_status": "VERIFIED",
                "official_paths_generated": False,
            },
        )

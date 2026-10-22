"""Pinned local Runtime fixtures; no operational Source or Gate edits."""

import copy
from dataclasses import replace
from unittest.mock import patch

from dsio_inventory_engine.infrastructure.runtime_build import installed_runtime_build
from dsio_inventory_engine.inventory_contracts.result_bundle import seal_strategy_execution_plan
from dsio_inventory_engine.inventory_contracts.runtime import InventoryRuntimeExecutionRequest
from dsio_inventory_engine.run_inventory.target_result_execution import (
    InventoryTargetResultExecution,
)
from tests.support import target_result_link_fixtures as source
from tests.support.runtime_fixtures import reseal_runtime_site_binding


def runtime_world(root, kernel, **kwargs):
    command, config, snapshot = source.command_parts(
        **{k: v for k, v in kwargs.items() if k in {"fsn_action", "all_failed"}}
    )
    build = installed_runtime_build()
    raw = copy.deepcopy(command.runtime_request.to_dict())
    plan = raw["claim"]["strategy_execution_plan"]
    plan.pop("content_hash")
    plan["operational_strategy"]["implementation_content_hash"] = build["implementations"][
        "mathematical"
    ]["implementation_content_hash"]
    plan["psi_simulator"]["implementation_content_hash"] = build["implementations"][
        "psi_simulator"
    ]["implementation_content_hash"]
    raw["claim"]["strategy_execution_plan"] = seal_strategy_execution_plan(plan)
    command = replace(
        command,
        runtime_request=InventoryRuntimeExecutionRequest.from_dict(
            reseal_runtime_site_binding(raw)
        ),
        mathematical_implementation_content_hash=build["implementations"]["mathematical"][
            "implementation_content_hash"
        ],
        psi_simulator_implementation_content_hash=build["implementations"]["psi_simulator"][
            "implementation_content_hash"
        ],
    )
    with patch.object(source, "command_parts", return_value=(command, config, snapshot)):
        w = source.link_world(root, kernel, **kwargs)
    w.build = build
    w.db_path = root / "artifacts.sqlite"
    w.runtime_kwargs = dict(
        deployment=source.DEPLOYMENT,
        target_storage=w.target,
        evidence_repository=w.store,
        catalog=w.catalog,
        expected_build=build,
    )
    w.runtime = InventoryTargetResultExecution(**w.runtime_kwargs)
    w.pointers = {
        "bundle": {
            "artifact_reference": w.persisted.bundle_reference,
            "content_hash": w.run.result_bundle["content_hash"],
        },
        "target_input": w.target_repo.lookup(w.request.engine_run_id, "INPUT"),
        "target_manifest": w.target_repo.lookup(w.request.engine_run_id, "COMPLETE"),
    }
    return w

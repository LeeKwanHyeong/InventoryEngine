"""Literal Platform/Inventory Runtime wire fixture."""

from __future__ import annotations


RUN_ID = "10000000-0000-4000-8000-000000000001"
CONFIG_ID = "20000000-0000-4000-8000-000000000001"
CONFIG_REVISION_ID = "30000000-0000-4000-8000-000000000001"


def runtime_dispatch() -> dict:
    identities = {
        "DEMAND_FORECAST": ("demand.forecast_snapshot", "FCST-1", "1" * 64),
        "INVENTORY_POSITION": ("inventory.position", "BOH-1", "2" * 64),
        "INVENTORY_POLICY": (
            "inventory.policy",
            CONFIG_REVISION_ID,
            "3" * 64,
        ),
        "CALENDAR": ("demand_io.calendar", "CAL-1", "4" * 64),
        "MASTER": ("demand_io.master", "MST-1", "5" * 64),
    }
    return {
        "contract_id": "inventory-engine-execution-request-v1",
        "contract_version": "1.0.0",
        "engine_run_id": RUN_ID,
        "attempt_no": 1,
        "site_row_version": 2,
        "claim": {
            "contract_id": "inventory-engine-run-claim-v1",
            "contract_version": "1.0.0",
            "tenant_id": "tenant-a",
            "project_id": "project-a",
            "planning_cycle_id": "PC-202601",
            "planning_cycle_revision_id": "PCR-202601-01",
            "cycle_site_execution_id": "PCR-202601-01:V100",
            "expected_site_row_version": 1,
            "config_id": CONFIG_ID,
            "config_revision_id": CONFIG_REVISION_ID,
            "config_hash": "3" * 64,
            "plan_id": "PLAN-202601",
            "plan_key_hash": "6" * 64,
            "site_binding_hash": "7" * 64,
            "trigger_type": "scheduled",
            "idempotency_key_hash": "8" * 64,
            "request_id": "request-1",
            "correlation_id": "correlation-1",
            "requested_by": "scheduler",
            "scope": {
                "company_cd": "DSE",
                "subs_cd": "C100",
                "plant_cd": "V100",
                "site_cd": "V100",
            },
            "input_bindings": [
                {
                    "input_type": input_type,
                    "source_contract_key": values[0],
                    "source_snapshot_id": values[1],
                    "source_content_hash": values[2],
                    "source_contract_version": "1.0.0",
                }
                for input_type, values in identities.items()
            ],
        },
    }

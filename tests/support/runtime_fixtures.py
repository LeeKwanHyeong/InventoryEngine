"""Literal Platform/Inventory Runtime wire fixture."""

from __future__ import annotations

import hashlib
import json


RUN_ID = "10000000-0000-4000-8000-000000000001"
CONFIG_ID = "20000000-0000-4000-8000-000000000001"
CONFIG_REVISION_ID = "30000000-0000-4000-8000-000000000001"
CLASSIFICATION_SNAPSHOT_ID = "40000000-0000-4000-8000-000000000001"
DEMAND_RUN_ID = "50000000-0000-4000-8000-000000000001"


def runtime_dispatch() -> dict:
    identities = {
        "DEMAND_FORECAST": ("demand.forecast_snapshot", "FCST-1", "1" * 64),
        "INVENTORY_POSITION": ("inventory.position", "BOH-1", "2" * 64),
        "INVENTORY_POLICY": (
            "inventory.policy",
            CONFIG_REVISION_ID,
            "3" * 64,
        ),
        "REPLENISHMENT_POLICY": (
            "inventory.replenishment_policy",
            "PHYSICAL-POLICY-1",
            "9" * 64,
        ),
        "SUPPLY_RECEIPTS": (
            "inventory.supply_receipts",
            "RECEIPTS-1",
            "a" * 64,
        ),
        "CUSTOMER_ORDERS": (
            "inventory.customer_orders",
            "CUSTOMER-ORDERS-1",
            "b" * 64,
        ),
        "PRIOR_INVENTORY": (
            "inventory.prior_inventory",
            "PRIOR-POSITION-1",
            "c" * 64,
        ),
        "CALENDAR": ("demand_io.calendar", "CAL-1", "4" * 64),
        "MASTER": ("demand_io.master", "MST-1", "5" * 64),
    }
    value = {
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
            "demand_run_id": DEMAND_RUN_ID,
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
    claim = value["claim"]
    claim["plan_key_hash"] = _sha(
        {
            "planning_cycle_revision_id": claim["planning_cycle_revision_id"],
            "plan_id": claim["plan_id"],
            "scope": claim["scope"],
        }
    )
    return value


def runtime_dispatch_v2(
    *,
    config_hash: str,
    effective_policy_content_hash: str,
    expected_automatic_publish_allowed: bool = False,
    expected_automatic_order_allowed: bool = False,
) -> dict:
    value = runtime_dispatch()
    value["claim"]["config_hash"] = config_hash
    value["claim"]["expected_automatic_publish_allowed"] = expected_automatic_publish_allowed
    value["claim"]["expected_automatic_order_allowed"] = expected_automatic_order_allowed
    policy = next(
        row for row in value["claim"]["input_bindings"] if row["input_type"] == "INVENTORY_POLICY"
    )
    policy["source_content_hash"] = config_hash
    policy["source_contract_version"] = "2.0.0"
    value["claim"]["input_bindings"].append(
        {
            "input_type": "INVENTORY_CLASSIFICATION",
            "source_contract_key": "inventory.classification_effective_policy",
            "source_snapshot_id": CLASSIFICATION_SNAPSHOT_ID,
            "source_content_hash": effective_policy_content_hash,
            "source_contract_version": "2.0.0",
        }
    )
    return value


def bind_runtime_dispatch_to_canonical(value: dict, canonical: dict) -> dict:
    """Test-only packaging of an already claimed dispatch to one canonical attempt."""

    context = canonical["context"]
    claim = value["claim"]
    value["engine_run_id"] = context["engine_run_id"]
    for key in (
        "planning_cycle_id",
        "planning_cycle_revision_id",
        "cycle_site_execution_id",
        "plan_id",
    ):
        claim[key] = context[key]
    claim["demand_run_id"] = context["demand_run_id"]
    claim["scope"] = {
        "company_cd": context["company_cd"],
        "subs_cd": context["subs_cd"],
        "plant_cd": context["site_cd"],
        "site_cd": context["site_cd"],
    }
    by_type = {row["input_type"]: row for row in claim["input_bindings"]}
    for input_type, canonical_kind in {
        "DEMAND_FORECAST": "forecast",
        "INVENTORY_POSITION": "inventory",
        "REPLENISHMENT_POLICY": "policies",
        "SUPPLY_RECEIPTS": "receipts",
        "CUSTOMER_ORDERS": "customer_orders",
        "PRIOR_INVENTORY": "prior_inventory",
        "CALENDAR": "calendar",
        "MASTER": "master",
    }.items():
        by_type[input_type]["source_snapshot_id"] = canonical["input_bindings"][canonical_kind][
            "snapshot_id"
        ]
        by_type[input_type]["source_content_hash"] = canonical["input_bindings"][canonical_kind][
            "content_hash"
        ]
    claim["plan_key_hash"] = _sha(
        {
            "planning_cycle_revision_id": claim["planning_cycle_revision_id"],
            "plan_id": claim["plan_id"],
            "scope": claim["scope"],
        }
    )
    return reseal_runtime_site_binding(value)


def reseal_runtime_site_binding(value: dict) -> dict:
    """Recompute the public Site binding hash for adversarial contract fixtures."""

    claim = value["claim"]
    by_type = {row["input_type"]: row for row in claim["input_bindings"]}
    site_identity = {
        "scope": claim["scope"],
        "input_bindings": sorted(claim["input_bindings"], key=lambda row: row["input_type"]),
    }
    if "INVENTORY_CLASSIFICATION" in by_type:
        site_identity["effective_policy_admission"] = {
            "automatic_publish_allowed": claim["expected_automatic_publish_allowed"],
            "automatic_order_allowed": claim["expected_automatic_order_allowed"],
        }
    claim["site_binding_hash"] = _sha(site_identity)
    return value


def runtime_plan_key_hash(value: dict) -> str:
    """Recompute the public logical Plan identity hash for contract probes."""

    claim = value["claim"]
    return _sha(
        {
            "planning_cycle_revision_id": claim["planning_cycle_revision_id"],
            "plan_id": claim["plan_id"],
            "scope": claim["scope"],
        }
    )


def _sha(value: object) -> str:
    document = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(document.encode("utf-8")).hexdigest()

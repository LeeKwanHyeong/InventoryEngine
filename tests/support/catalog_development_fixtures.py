"""Small frozen inputs only: no inventory simulation in this fixture."""

import copy

from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.development_universe import seal_development_universe
from tests.support.catalog_search_fixtures import two_item_bundle, feature_from_bundle
from tests.support.fixtures import reseal
from tests.support.legacy_style_fixtures import RULES_DOCUMENT
from tests.support.math_fixtures import reseal_math
from tests.support.selected_order_plan_fixtures import v2_parts
from tests.support.calculation_adapter_fixtures import DEPLOYMENT


def preparation_case():
    parts = v2_parts(two_item_bundle())
    raw, values, parent = copy.deepcopy(parts)
    history = feature_from_bundle(parts)["sources"]["history"]
    canonical = raw["recommendation"]["canonical_input"]
    upstream = copy.deepcopy(canonical["input_bindings"])
    canonical.update(contract_id="io-canonical-input-v2", contract_version="2.1.0")
    canonical["quantity_rules"] = [
        {
            "uom": "EA",
            "planning_scale": 6,
            "physical_scale": 0,
            "tolerance_qty": "0",
            "approval_reference": "TEST-ONLY",
        }
    ]
    canonical["upstream_bindings"] = {k: upstream[k] for k in ("calendar", "master", "forecast")}
    universe = seal_development_universe(
        {
            "contract_id": "io-development-calculation-universe-v2",
            "environment_scope": "DEVELOPMENT",
            "demand_run_id": canonical["context"]["demand_run_id"],
            "plan_yyyyww": canonical["context"]["plan_yyyyww"],
            "original_master_content_hash": upstream["master"]["content_hash"],
            "original_forecast_content_hash": upstream["forecast"]["content_hash"],
            "source_demand_window_file_sha256": "a" * 64,
            "approval_reference": "TEST-ONLY",
            "included_item_ids": ["ITEM-A", "ITEM-B"],
            "exclusions": [],
            "classification_snapshot_id": parent["classification_snapshot_id"],
            "classification_content_hash": parent["content_hash"],
        }
    )
    canonical["development_universe"] = universe
    canonical["snapshots"]["forecast"]["snapshot_id"] = (
        upstream["forecast"]["snapshot_id"] + ".canonical." + universe["content_hash"][:16]
    )
    canonical = reseal(canonical)
    raw["recommendation"]["canonical_input"] = canonical
    input_hash = CanonicalInputRequest.from_dict(canonical).input_hash
    raw["recommendation"]["execution"]["canonical_input_hash"] = input_hash
    raw["policy_input"]["context"]["canonical_input_hash"] = input_hash
    return dict(
        mathematical_request=reseal_math(raw),
        config_values=values,
        classification_snapshot=parent,
        item_ids=["ITEM-A"],
        history_source=history,
        rules_document=copy.deepcopy(RULES_DOCUMENT),
        reference_calculator_build_hash="f" * 64,
        deployment=DEPLOYMENT,
        cost_assumptions={
            "currency": "USD",
            "holding_cost_per_unit_week": "0.02",
            "backorder_cost_per_unit_week": "5",
            "fixed_order_cost": "20",
            "variable_order_cost_per_unit": "1",
            "source_type": "DEVELOPMENT_ASSUMPTION",
            "allocation_basis": "PER_ITEM_ORDER",
        },
    )

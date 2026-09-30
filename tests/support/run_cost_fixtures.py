"""Claim-bound development lane prices; not operational trade prices."""

from __future__ import annotations

import copy
from dataclasses import replace

from dsio_inventory_engine.inventory_contracts.landed_cost_artifact import seal_shipment_input
from dsio_inventory_engine.inventory_contracts.result_bundle import seal_strategy_execution_plan
from dsio_inventory_engine.inventory_contracts.run_cost import (
    INPUT_ID,
    MODE,
    RECOGNITION,
    PROFILE_ATTRIBUTION,
    seal_run_cost_input,
    cost_binding_from_input,
)
from dsio_inventory_engine.inventory_contracts.runtime import InventoryRuntimeExecutionRequest
from dsio_inventory_engine.inventory_contracts.trade_cost_projection import (
    TradeCostRevisionSetProjection,
)
from dsio_inventory_engine.run_inventory.landed_cost_pipeline import ResolvedRunLandedCost
from tests.integration.test_psi_orchestrator_offline import _bound_command
from tests.support.landed_cost_fixtures import golden_cases, landed_fixture, reseal_projection
from tests.support.runtime_fixtures import reseal_runtime_site_binding


def bound_cost_command(*, action="ALLOW", approval="AUTO", include_stress=True):
    command = _bound_command(action=action, approval=approval, development_registry=True)
    document = command.runtime_request.to_dict()
    claim = document["claim"]
    case = copy.deepcopy(golden_cases()[0])
    case.update(cost_currency="KRW", fx_rate="1500", currency_scale=0)
    projection, template = landed_fixture(case)
    projection["valuation_date"] = claim["canonical_context_binding"]["master_as_of_date"]
    for header in [
        projection["revision_set"],
        *[source["header"] for source in projection["sources"]],
    ]:
        header.update(tenant_id=claim["tenant_id"], project_id=claim["project_id"])
    for source in projection["sources"]:
        for row in source["records"]:
            if "item_id" in row:
                row["item_id"] = "ITEM-A"
    projection = reseal_projection(projection)
    header = projection["revision_set"]
    template.pop("content_hash")
    template["binding"].update(
        tenant_id=claim["tenant_id"],
        project_id=claim["project_id"],
        revision_set_content_hash=projection["revision_set_content_hash"],
        valuation_date=projection["valuation_date"],
    )
    template["lines"][0].update(item_id="ITEM-A", quantity="1", transaction_value="10")
    for row in template["lines"][0]["charges"]:
        row["amount"] = "1" if row["component_type"] == "INSURANCE" else "2"
    template = seal_shipment_input(template)
    inputs = seal_run_cost_input(
        {
            "contract_id": INPUT_ID,
            "contract_version": "1.0.0",
            "snapshot_id": "DEV-COST-INPUT-1",
            **{
                key: header[key]
                for key in (
                    "tenant_id",
                    "project_id",
                    "company_cd",
                    "subs_cd",
                    "origin_site_cd",
                    "revision_set_id",
                )
            },
            "destination_site_cd": "V101",
            "network_revision_id": template["binding"]["network_revision_id"],
            "revision_set_content_hash": projection["revision_set_content_hash"],
            "valuation_date": projection["valuation_date"],
            "judgment_at": template["binding"]["judgment_at"],
            "cost_application_mode": MODE,
            "recognition_rule": RECOGNITION,
            "fixed_charge_rule": "PER_ACCEPTED_ITEM_ORDER",
            "cost_currency": "KRW",
            "cost_profile_content_hash": command.cost_profile.profile_content_hash,
            "profile_component_attribution": PROFILE_ATTRIBUTION,
            "shipment_templates": [template],
        }
    )
    plan = {
        key: row for key, row in claim["strategy_execution_plan"].items() if key != "content_hash"
    }
    plan.update(contract_version="1.2.0", landed_cost_binding=cost_binding_from_input(inputs))
    if not include_stress:
        plan["stress_scenario_bindings"] = []
    claim["strategy_execution_plan"] = seal_strategy_execution_plan(plan)
    claim["input_bindings"].append(
        {
            "input_type": "TRADE_COST_REVISION_SET",
            "source_contract_key": "inventory.trade_cost_revision_set",
            "source_contract_version": "1.0.0",
            "source_snapshot_id": header["revision_set_id"],
            "source_content_hash": projection["revision_set_content_hash"],
        }
    )
    runtime = InventoryRuntimeExecutionRequest.from_dict(reseal_runtime_site_binding(document))
    return replace(
        command,
        runtime_request=runtime,
        stress_scenarios=command.stress_scenarios if include_stress else {},
    ), ResolvedRunLandedCost(inputs, TradeCostRevisionSetProjection.from_dict(projection))


class CostResolver:
    def __init__(self, resolved):
        self.resolved = resolved
        self.calls = 0

    async def resolve(self, request):
        self.calls += 1
        return self.resolved


def rebind_cost_command(command, inputs, projection):
    """Changed pricing/Source is a new sealed Plan, never an identical-input retry."""
    document = command.runtime_request.to_dict()
    plan = document["claim"]["strategy_execution_plan"]
    plan.pop("content_hash")
    plan["landed_cost_binding"] = cost_binding_from_input(inputs)
    document["claim"]["strategy_execution_plan"] = seal_strategy_execution_plan(plan)
    binding = next(
        row
        for row in document["claim"]["input_bindings"]
        if row["input_type"] == "TRADE_COST_REVISION_SET"
    )
    binding["source_content_hash"] = inputs["revision_set_content_hash"]
    runtime = InventoryRuntimeExecutionRequest.from_dict(reseal_runtime_site_binding(document))
    return replace(command, runtime_request=runtime), ResolvedRunLandedCost(
        inputs, TradeCostRevisionSetProjection.from_dict(projection)
    )

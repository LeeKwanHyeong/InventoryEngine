"""Closed development cost inputs pinned before a claimed Runtime Attempt."""

from __future__ import annotations

import copy
from typing import Any, Mapping

from .landed_cost_artifact import (
    LandedCostShipmentInput,
    currency,
    timestamp,
    uuid_value,
)
from .values import choice, day, digest, hash_value, identifier, require, shape

INPUT_KEY = "inventory.landed_cost.run_input"
INPUT_ID = "inventory-landed-cost-run-input-v1"
MANIFEST_KEY = "inventory.run_result_manifest"
MANIFEST_ID = "inventory-run-result-manifest-v1"
VERSION = "1.0.0"
MODE = "DEVELOPMENT_LANE_PRICING_PROXY"
RECOGNITION = "AT_ORDER_ACCEPTANCE_INCLUDING_OUT_OF_HORIZON_RECEIPTS"
PROFILE_ATTRIBUTION = {
    "holding_cost_per_unit_week": "HOLDING",
    "backorder_cost_per_unit_week": "BACKORDER",
    "fixed_order_cost": "ORDER_ADMINISTRATION",
    "variable_order_cost_per_unit": "ORDER_HANDLING_ONLY",
}


def run_cost_binding(value: Any) -> dict:
    return shape(
        value,
        {
            "input_contract_key": choice(INPUT_KEY),
            "input_contract_version": choice(VERSION),
            "input_snapshot_id": identifier,
            "input_content_hash": hash_value,
            "cost_application_mode": choice(MODE),
            "origin_site_cd": choice("V100"),
            "destination_site_cd": choice("V101", "V102", "V103", "V104"),
            "network_revision_id": uuid_value,
            "revision_set_id": uuid_value,
            "revision_set_content_hash": hash_value,
            "valuation_date": day,
            "judgment_at": timestamp,
        },
    )


def _templates(value: Any) -> list[dict]:
    require(type(value) is list and 1 <= len(value) <= 10_000, "RUN_COST_TEMPLATE_COUNT_INVALID")
    result = [LandedCostShipmentInput.from_dict(row).to_dict() for row in value]
    items = []
    for row in result:
        require(len(row["lines"]) == 1, "RUN_COST_SINGLE_ITEM_TEMPLATE_REQUIRED")
        require(row["lines"][0]["quantity"] == "1", "RUN_COST_UNIT_TEMPLATE_REQUIRED")
        require(row["calculation_purpose"] == "DEVELOPMENT_FIXTURE", "RUN_COST_DEVELOPMENT_ONLY")
        items.append(row["lines"][0]["item_id"])
    require(len(items) == len(set(items)), "RUN_COST_TEMPLATE_DUPLICATE")
    return sorted(result, key=lambda row: row["lines"][0]["item_id"])


def validate_run_cost_input(value: Mapping[str, Any]) -> dict:
    result = shape(
        dict(value),
        {
            "contract_id": choice(INPUT_ID),
            "contract_version": choice(VERSION),
            "snapshot_id": identifier,
            "tenant_id": identifier,
            "project_id": identifier,
            "company_cd": identifier,
            "subs_cd": choice("C100"),
            "origin_site_cd": choice("V100"),
            "destination_site_cd": choice("V101", "V102", "V103", "V104"),
            "network_revision_id": uuid_value,
            "revision_set_id": uuid_value,
            "revision_set_content_hash": hash_value,
            "valuation_date": day,
            "judgment_at": timestamp,
            "cost_application_mode": choice(MODE),
            "recognition_rule": choice(RECOGNITION),
            "fixed_charge_rule": choice("PER_ACCEPTED_ITEM_ORDER"),
            "cost_currency": currency,
            "cost_profile_content_hash": hash_value,
            "profile_component_attribution": lambda row: shape(
                row, {key: choice(component) for key, component in PROFILE_ATTRIBUTION.items()}
            ),
            "shipment_templates": _templates,
            "content_hash": hash_value,
        },
    )
    body = {key: row for key, row in result.items() if key != "content_hash"}
    require(digest(body) == result["content_hash"], "RUN_COST_INPUT_HASH_MISMATCH")
    for template in result["shipment_templates"]:
        binding = template["binding"]
        require(
            all(
                binding[key] == result[key]
                for key in (
                    "tenant_id",
                    "project_id",
                    "company_cd",
                    "subs_cd",
                    "origin_site_cd",
                    "network_revision_id",
                    "revision_set_id",
                    "revision_set_content_hash",
                    "valuation_date",
                    "judgment_at",
                )
            )
            and template["destination_site_cd"] == result["destination_site_cd"]
            and template["cost_currency"] == result["cost_currency"],
            "RUN_COST_TEMPLATE_SCOPE_MISMATCH",
        )
    return result


def seal_run_cost_input(body: Mapping[str, Any]) -> dict:
    candidate = copy.deepcopy(dict(body))
    candidate["shipment_templates"] = _templates(candidate["shipment_templates"])
    return validate_run_cost_input({**candidate, "content_hash": digest(candidate)})


def cost_binding_from_input(value: Mapping[str, Any]) -> dict:
    document = validate_run_cost_input(value)
    return run_cost_binding(
        {
            "input_contract_key": INPUT_KEY,
            "input_contract_version": VERSION,
            "input_snapshot_id": document["snapshot_id"],
            "input_content_hash": document["content_hash"],
            **{
                key: document[key]
                for key in (
                    "cost_application_mode",
                    "origin_site_cd",
                    "destination_site_cd",
                    "network_revision_id",
                    "revision_set_id",
                    "revision_set_content_hash",
                    "valuation_date",
                    "judgment_at",
                )
            },
        }
    )


def validate_run_cost_claim(request, document: Mapping[str, Any]) -> dict:
    value = validate_run_cost_input(document)
    plan = request.strategy_execution_plan
    require(
        plan is not None and plan.get("landed_cost_binding") is not None,
        "RUN_COST_CLAIM_BINDING_REQUIRED",
    )
    require(
        plan["landed_cost_binding"] == cost_binding_from_input(value),
        "RUN_COST_CLAIM_BINDING_MISMATCH",
    )
    claim = request.value["claim"]
    require(
        all(value[key] == claim[key] for key in ("tenant_id", "project_id"))
        and value["company_cd"] == claim["scope"]["company_cd"]
        and value["subs_cd"] == claim["scope"]["subs_cd"]
        and value["destination_site_cd"] == claim["scope"]["site_cd"],
        "RUN_COST_CLAIM_SCOPE_MISMATCH",
    )
    require(
        value["valuation_date"] == request.canonical_context_binding["master_as_of_date"],
        "RUN_COST_CLAIM_DATE_MISMATCH",
    )
    require(
        plan["cost_profile_binding"] is not None
        and value["cost_profile_content_hash"]
        == plan["cost_profile_binding"]["profile_content_hash"],
        "RUN_COST_PROFILE_BINDING_MISMATCH",
    )
    return value

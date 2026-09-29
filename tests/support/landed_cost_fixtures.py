"""Full sealed projection fixtures; synthetic rates only, matching Migration 077 record names."""

from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path

from dsio_inventory_engine.inventory_contracts.landed_cost_artifact import (
    CHARGE_TYPES,
    seal_shipment_input,
)
from dsio_inventory_engine.inventory_contracts.values import canonical_json, digest
from tests.support.trade_cost_fixtures import sealed_trade_cost_projection


def golden_cases() -> list[dict]:
    return json.loads(
        (Path(__file__).resolve().parents[1] / "fixtures/golden_landed_cost_v1.json").read_text()
    )["cases"]


def reseal_projection(value: dict) -> dict:
    value = copy.deepcopy(value)
    members = []
    for source in value["sources"]:
        for key in ("documents", "records"):
            source[key].sort(key=canonical_json)
        source["content_hash"] = digest(
            {key: source[key] for key in ("header", "documents", "records")}
        )
        header = source["header"]
        members.append(
            {
                "source_domain": header["source_domain"],
                "jurisdiction_country_code": header["jurisdiction_country_code"],
                "source_revision_id": header["source_revision_id"],
                "content_hash": source["content_hash"],
            }
        )
    value["members"] = sorted(members, key=canonical_json)
    member_order = {
        member["source_revision_id"]: index for index, member in enumerate(value["members"])
    }
    value["sources"].sort(key=lambda row: member_order[row["header"]["source_revision_id"]])
    value["revision_set_content_hash"] = digest(
        {"header": value["revision_set"], "members": value["members"]}
    )
    value["projection_content_hash"] = digest(
        {key: item for key, item in value.items() if key != "projection_content_hash"}
    )
    return value


def landed_fixture(case: dict | None = None) -> tuple[dict, dict]:
    case = case or golden_cases()[0]
    country, site = case["import_country_code"], case["destination_site_cd"]
    projection = sealed_trade_cost_projection()
    template = copy.deepcopy(projection["sources"][0])
    network = "72000000-0000-4000-8000-000000000001"
    records = {
        "ITEM_CLASSIFICATION": {
            "item_id": "DEV-PART-001",
            "destination_country_code": country,
            "hs_code_version": "HS2022-DEV",
            "national_tariff_code": "DEV850110000",
        },
        "ITEM_ORIGIN": {
            "item_id": "DEV-PART-001",
            "jurisdiction_country_code": "KR",
            "manufacturing_origin_country_code": "KR",
        },
        "CUSTOMS_TARIFF": {
            "tariff_rule_id": "DEV-MFN",
            "destination_country_code": country,
            "hs_code_version": "HS2022-DEV",
            "national_tariff_code": "DEV850110000",
            "origin_country_code": None,
            "tariff_basis": "MFN",
            "duty_method": "AD_VALOREM",
            "ad_valorem_rate": case["duty_rate"],
            "specific_rate": None,
            "specific_uom": None,
            "duty_currency": case["cost_currency"],
            "minimum_duty_amount": None,
            "maximum_duty_amount": None,
            "currency_scale": case["currency_scale"],
            "rounding_mode": "HALF_UP",
        },
        "CUSTOMS_FX": {
            "destination_country_code": country,
            "from_currency": "USD",
            "to_currency": case["cost_currency"],
            "effective_date": "2026-01-01",
            "rate": case["fx_rate"],
        },
        "IMPORT_TAX_PROFILE": {
            "tax_profile_id": "DEV-TAX",
            "importer_site_cd": site,
            "destination_country_code": country,
            "import_vat_rate": case["vat_rate"],
            "recoverability_status": "VERIFIED_RECOVERABLE"
            if case["vat_recoverable"]
            else "VERIFIED_NONRECOVERABLE",
            "include_duty_in_vat_base": True,
            "include_excise_in_vat_base": True,
        },
        "LANE_CHARGE": {
            "lane_charge_id": "DEV-FREIGHT",
            "destination_country_code": country,
            "network_revision_id": network,
            "lane_id": f"V100-{site}-SEA",
            "component_type": "INTERNATIONAL_FREIGHT",
            "charge_basis": "SHIPMENT",
            "charge_currency": "USD",
            "charge_amount": "100",
            "charge_rate": None,
            "minimum_charge_amount": None,
            "maximum_charge_amount": None,
            "included_in_customs_value": True,
        },
    }
    sources = []
    for domain, payload in records.items():
        source = copy.deepcopy(template)
        revision_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"fixture:landed:{country}:{domain}"))
        source["header"].update(
            source_revision_id=revision_id,
            source_domain=domain,
            jurisdiction_country_code="KR" if domain == "ITEM_ORIGIN" else country,
        )
        source["documents"][0].update(
            source_revision_id=revision_id,
            source_document_id="DOC-DEV",
            raw_content_hash=digest(payload),
        )
        row = {
            **payload,
            "source_revision_id": revision_id,
            "source_domain": domain,
            "source_document_id": "DOC-DEV",
        }
        if domain != "CUSTOMS_FX":
            row.update(valid_from="2026-01-01", valid_to=None)
        source["records"] = [row]
        sources.append(source)
    projection["sources"] = sources
    projection = reseal_projection(projection)
    header = projection["revision_set"]
    binding = {
        key: header[key]
        for key in (
            "tenant_id",
            "project_id",
            "company_cd",
            "subs_cd",
            "origin_site_cd",
            "revision_set_id",
        )
    }
    binding.update(
        engine_run_id="IO-RUN-LC-1",
        attempt_no=1,
        canonical_input_hash="a" * 64,
        revision_set_content_hash=projection["revision_set_content_hash"],
        network_revision_id=network,
        valuation_date=projection["valuation_date"],
        judgment_at="2026-09-28T00:00:00Z",
    )
    charges = [
        {
            "component_type": kind,
            "amount": amount,
            "currency": "USD",
            "included_in_customs_value": kind == "INSURANCE",
            "included_in_gross_landed_cost": True,
            "recoverable": False,
            "source_status": "DEVELOPMENT_FIXTURE",
            "source_reference": f"fixture:{kind}",
        }
        for kind, amount in (("INSURANCE", "10"), ("DESTINATION_HANDLING", "20"))
    ]
    shipment = seal_shipment_input(
        {
            "contract_id": "io-landed-cost-shipment-input-v1",
            "contract_version": "1.0.0",
            "binding": binding,
            "calculation_purpose": "DEVELOPMENT_FIXTURE",
            "shipment_id": "DEV-SHIP-1",
            "lane_id": f"V100-{site}-SEA",
            "destination_site_cd": site,
            "import_country_code": country,
            "export_country_code": "KR",
            "cost_currency": case["cost_currency"],
            "fixed_cost_allocation_basis": "CUSTOMS_VALUE",
            "tariff_basis": "MFN",
            "currency_scale": case["currency_scale"],
            "rounding_mode": "HALF_UP",
            "rounding_rule_reference": "fixture:rounding",
            "transaction_value_basis": "GOODS_ONLY_EXCLUDING_ADDITIONAL_CHARGES",
            "charge_exemptions": [
                {
                    "component_type": kind,
                    "reason_code": "NOT_APPLICABLE_IN_DEVELOPMENT_SCENARIO",
                    "source_reference": "fixture:charge-profile",
                }
                for kind in CHARGE_TYPES
                if kind not in {"INTERNATIONAL_FREIGHT", "INSURANCE", "DESTINATION_HANDLING"}
            ],
            "lines": [
                {
                    "shipment_line_id": "1",
                    "item_id": "DEV-PART-001",
                    "quantity": "10",
                    "quantity_uom": "EA",
                    "transaction_value": "1000",
                    "transaction_currency": "USD",
                    "source_status": "DEVELOPMENT_FIXTURE",
                    "source_reference": "fixture:invoice",
                    "charges": charges,
                }
            ],
        }
    )
    return projection, shipment


def rebind(projection: dict, shipment: dict) -> dict:
    shipment = copy.deepcopy(shipment)
    shipment["binding"]["revision_set_content_hash"] = projection["revision_set_content_hash"]
    return seal_shipment_input(
        {key: item for key, item in shipment.items() if key != "content_hash"}
    )


def source_for(projection: dict, domain: str) -> dict:
    return next(
        source for source in projection["sources"] if source["header"]["source_domain"] == domain
    )

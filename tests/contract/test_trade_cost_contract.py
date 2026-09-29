"""Trade Cost source and Landed Cost fail-closed contract tests."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

import jsonschema

from dsio_inventory_engine.inventory_contracts.trade_cost import (
    TRADE_COST_FIELDS,
    LandedCostAssessment,
    TradeCostSourceCatalog,
    seal_landed_cost_assessment,
    seal_trade_cost_source_catalog,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError


HASH = "a" * 64
ROOT = Path(__file__).resolve().parents[2]


def source_catalog() -> dict:
    fields = []
    for field_name in sorted(TRADE_COST_FIELDS):
        fields.append(
            {
                "field_name": field_name,
                "status": "NOT_FOUND",
                "source_reference": None,
                "source_revision": None,
                "source_as_of_date": None,
                "coverage_numerator": 0,
                "coverage_denominator": 35000,
                "evidence_hash": None,
                "notes": "No authoritative operational source is bound.",
            }
        )
    for field_name in ("QUANTITY_UOM", "SHIPPING_COUNTRY"):
        row = next(item for item in fields if item["field_name"] == field_name)
        row.update(
            {
                "status": "DEVELOPMENT_FIXTURE",
                "source_reference": "development-postgresql",
                "source_revision": "audit-20260916",
                "source_as_of_date": "2026-09-16",
                "coverage_numerator": 35000,
                "evidence_hash": HASH,
                "notes": "Development-only source; production authority is not established.",
            }
        )
    return seal_trade_cost_source_catalog(
        {
            "contract_id": "io-trade-cost-source-catalog-v1",
            "contract_version": "1.0.0",
            "scope": {
                "company_cd": "DSE",
                "subs_cd": "C100",
                "origin_site_cd": "V100",
                "destination_site_cds": ["V101", "V102", "V103", "V104"],
                "environment": "DEVELOPMENT",
            },
            "audited_at": "2026-09-16T00:00:00Z",
            "fields": fields,
        }
    )


def component(
    component_type: str,
    amount: str,
    *,
    customs: bool,
    gross: bool = True,
    recoverable: bool = False,
    source_status: str = "AUTHORITATIVE",
) -> dict:
    return {
        "component_type": component_type,
        "amount": amount,
        "source_status": source_status,
        "source_reference": f"source:{component_type.lower()}",
        "zero_value_reason": None,
        "included_in_customs_value": customs,
        "included_in_gross_landed_cost": gross,
        "recoverable": recoverable,
    }


def calculable_assessment(*, purpose: str = "OPERATIONAL", fixture: bool = False) -> dict:
    source_status = "DEVELOPMENT_FIXTURE" if fixture else "AUTHORITATIVE"
    components = sorted(
        [
            component("TRANSACTION_VALUE", "1000", customs=True, source_status=source_status),
            component("INTERNATIONAL_FREIGHT", "100", customs=True),
            component("INSURANCE", "10", customs=True),
            component("CUSTOMS_DUTY_AD_VALOREM", "55", customs=False),
            component("IMPORT_VAT", "116.5", customs=False, recoverable=True),
            component("DESTINATION_HANDLING", "20", customs=False),
        ],
        key=lambda item: item["component_type"],
    )
    return seal_landed_cost_assessment(
        {
            "contract_id": "io-landed-cost-assessment-v1",
            "contract_version": "1.0.0",
            "grain": {
                "shipment_id": "SHIP-001",
                "shipment_line_id": "1",
                "lane_id": "C100-V100-V101-SEA",
                "item_id": "PART/001",
                "valuation_date": "2026-09-16",
            },
            "judgment_at": "2026-09-16T00:00:00Z",
            "calculation_purpose": purpose,
            "calculation_status": "CALCULABLE",
            "blocking_reason_codes": [],
            "source_revision_set_hash": HASH,
            "hs_code_version": "HS2022",
            "national_tariff_code": "850110000",
            "manufacturing_origin_country_code": "KR",
            "export_country_code": "KR",
            "import_country_code": "JP",
            "preferential_eligibility_status": "INELIGIBLE",
            "tax_recoverability_status": "VERIFIED_RECOVERABLE",
            "tariff_basis": "MFN",
            "fixed_cost_allocation_basis": "CUSTOMS_VALUE",
            "cost_currency": "JPY",
            "rounding_rule": {
                "currency_scale": 0,
                "mode": "DOWN",
                "application_stage": "LEGAL_RULE",
                "rule_reference": "official-rule-revision-2026-09-16",
            },
            "components": components,
            "customs_value_amount": "1110",
            "gross_landed_cost_amount": "1301.5",
            "recoverable_tax_amount": "116.5",
            "net_landed_cost_amount": "1185",
            "operational_eligible": purpose == "OPERATIONAL" and not fixture,
        }
    )


class TradeCostContractTests(unittest.TestCase):
    def assert_schema_valid(self, schema_name: str, value: dict) -> None:
        schema = json.loads((ROOT / "schemas" / schema_name).read_text())
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.Draft202012Validator(schema).validate(value)

    def test_source_catalog_round_trip_and_hash_are_deterministic(self):
        first = source_catalog()
        second = source_catalog()
        self.assertEqual(first["content_hash"], second["content_hash"])
        self.assertEqual(TradeCostSourceCatalog.from_dict(first).to_dict(), first)
        self.assert_schema_valid("trade_cost_source_catalog.schema.json", first)

    def test_source_catalog_does_not_accept_missing_as_zero_coverage(self):
        value = source_catalog()
        row = next(item for item in value["fields"] if item["field_name"] == "HS_CLASSIFICATION")
        row["coverage_numerator"] = 1
        with self.assertRaisesRegex(InventoryInputError, "SOURCE_NOT_FOUND_HAS_COVERAGE"):
            seal_trade_cost_source_catalog(
                {key: item for key, item in value.items() if key != "content_hash"}
            )

    def test_calculable_component_totals_and_hash_round_trip(self):
        value = calculable_assessment()
        self.assertEqual(value["customs_value_amount"], "1110")
        self.assertEqual(value["net_landed_cost_amount"], "1185")
        self.assertTrue(value["operational_eligible"])
        self.assertEqual(LandedCostAssessment.from_dict(value).to_dict(), value)
        self.assert_schema_valid("landed_cost_assessment.schema.json", value)

    def test_development_fixture_may_not_be_operational(self):
        with self.assertRaisesRegex(InventoryInputError, "OPERATIONAL_FIXTURE_FORBIDDEN"):
            calculable_assessment(fixture=True)
        fixture = calculable_assessment(purpose="DEVELOPMENT_FIXTURE", fixture=True)
        self.assertFalse(fixture["operational_eligible"])

    def test_non_calculable_fx_gap_has_no_financial_totals(self):
        value = calculable_assessment()
        value.update(
            {
                "calculation_status": "MISSING_EXCHANGE_RATE",
                "blocking_reason_codes": ["EXCHANGE_RATE_MISSING"],
                "components": [],
                "customs_value_amount": None,
                "gross_landed_cost_amount": None,
                "recoverable_tax_amount": None,
                "net_landed_cost_amount": None,
                "operational_eligible": False,
            }
        )
        value.pop("content_hash")
        sealed = seal_landed_cost_assessment(value)
        self.assertEqual(sealed["calculation_status"], "MISSING_EXCHANGE_RATE")

    def test_preferential_tariff_requires_verified_eligibility(self):
        value = calculable_assessment()
        value["tariff_basis"] = "PREFERENTIAL"
        value["preferential_eligibility_status"] = "UNVERIFIED"
        value.pop("content_hash")
        with self.assertRaisesRegex(InventoryInputError, "PREFERENTIAL_EVIDENCE_REQUIRED"):
            seal_landed_cost_assessment(value)

    def test_zero_component_requires_explicit_evidence_reason(self):
        value = calculable_assessment()
        value.pop("content_hash")
        row = next(item for item in value["components"] if item["component_type"] == "INSURANCE")
        row["amount"] = "0"
        with self.assertRaisesRegex(InventoryInputError, "ZERO_VALUE_REASON_REQUIRED"):
            seal_landed_cost_assessment(value)

    def test_component_tampering_breaks_hash(self):
        value = calculable_assessment()
        tampered = copy.deepcopy(value)
        next(row for row in tampered["components"] if row["component_type"] == "TRANSACTION_VALUE")[
            "amount"
        ] = "999"
        with self.assertRaisesRegex(InventoryInputError, "CUSTOMS_VALUE_MISMATCH"):
            LandedCostAssessment.from_dict(tampered)


if __name__ == "__main__":
    unittest.main()

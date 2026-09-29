"""Pure Landed Cost calculator and four-country development Golden tests."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from dsio_inventory_engine.calculate_landed_cost import (
    AllocationLine,
    Charge,
    FxQuote,
    LandedCostRequest,
    TariffRule,
    TaxProfile,
    allocate_fixed_charge,
    calculate_landed_cost,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError


ROOT = Path(__file__).resolve().parents[2]
HASH = "b" * 64


def request_for(case: dict) -> LandedCostRequest:
    status = "DEVELOPMENT_FIXTURE"
    return LandedCostRequest(
        grain={
            "shipment_id": f"SHIP-{case['case_id']}",
            "shipment_line_id": "1",
            "lane_id": f"C100-V100-{case['destination_site_cd']}-SEA",
            "item_id": "DEV-PART-001",
            "valuation_date": "2026-09-16",
        },
        judgment_at="2026-09-16T00:00:00Z",
        calculation_purpose="DEVELOPMENT_FIXTURE",
        source_revision_set_hash=HASH,
        hs_code_version="HS2022-DEV",
        national_tariff_code="DEV850110000",
        manufacturing_origin_country_code="KR",
        export_country_code="KR",
        import_country_code=case["import_country_code"],
        preferential_eligibility_status="INELIGIBLE",
        transaction_value=Decimal("1000"),
        transaction_currency="USD",
        transaction_source_status=status,
        transaction_source_reference="fixture:shipment-invoice",
        quantity=Decimal("10"),
        quantity_uom="EA",
        cost_currency=case["cost_currency"],
        charges=(
            Charge(
                "INTERNATIONAL_FREIGHT",
                Decimal("100"),
                "USD",
                True,
                True,
                False,
                status,
                "fixture:freight",
            ),
            Charge(
                "INSURANCE",
                Decimal("10"),
                "USD",
                True,
                True,
                False,
                status,
                "fixture:insurance",
            ),
            Charge(
                "DESTINATION_HANDLING",
                Decimal("20"),
                "USD",
                False,
                True,
                False,
                status,
                "fixture:destination-handling",
            ),
        ),
        fx_quotes=(
            FxQuote(
                "USD",
                case["cost_currency"],
                Decimal(case["fx_rate"]),
                status,
                "fixture:customs-fx",
            ),
        ),
        tariff_rule=TariffRule(
            "MFN",
            "AD_VALOREM",
            case["cost_currency"],
            Decimal(case["duty_rate"]),
            None,
            None,
            None,
            None,
            status,
            "fixture:tariff-rule",
        ),
        tax_profile=TaxProfile(
            Decimal(case["vat_rate"]),
            ("VERIFIED_RECOVERABLE" if case["vat_recoverable"] else "VERIFIED_NONRECOVERABLE"),
            True,
            True,
            status,
            "fixture:tax-profile",
        ),
        fixed_cost_allocation_basis="CUSTOMS_VALUE",
        currency_scale=case["currency_scale"],
        rounding_mode="HALF_UP",
        rounding_rule_reference="fixture:legal-rounding",
    )


class LandedCostCalculatorTests(unittest.TestCase):
    def test_four_country_development_golden(self):
        payload = json.loads((ROOT / "tests/fixtures/golden_landed_cost_v1.json").read_text())
        self.assertIn("Synthetic development fixtures only", payload["notice"])
        for case in payload["cases"]:
            with self.subTest(case=case["case_id"]):
                result = calculate_landed_cost(request_for(case))
                self.assertEqual(result["calculation_status"], "CALCULABLE")
                self.assertFalse(result["operational_eligible"])
                for field, expected in case["expected"].items():
                    self.assertEqual(result[field], expected)
                self.assertEqual(
                    calculate_landed_cost(request_for(case))["content_hash"],
                    result["content_hash"],
                )

    def test_specific_duty_floor_and_cap(self):
        payload = json.loads((ROOT / "tests/fixtures/golden_landed_cost_v1.json").read_text())
        request = request_for(payload["cases"][0])
        floored = replace(
            request,
            tariff_rule=TariffRule(
                "MFN",
                "SPECIFIC",
                "JPY",
                None,
                Decimal("10"),
                "EA",
                Decimal("500"),
                Decimal("800"),
                "DEVELOPMENT_FIXTURE",
                "fixture:specific-floor",
            ),
        )
        result = calculate_landed_cost(floored)
        duty = next(
            row for row in result["components"] if row["component_type"] == "CUSTOMS_DUTY_OTHER"
        )
        self.assertEqual(duty["amount"], "500")

        capped = replace(
            request,
            tariff_rule=TariffRule(
                "MFN",
                "COMPOUND",
                "JPY",
                Decimal("0.05"),
                Decimal("100"),
                "EA",
                None,
                Decimal("900"),
                "DEVELOPMENT_FIXTURE",
                "fixture:compound-cap",
            ),
        )
        result = calculate_landed_cost(capped)
        duty = next(
            row for row in result["components"] if row["component_type"] == "CUSTOMS_DUTY_OTHER"
        )
        self.assertEqual(duty["amount"], "900")

    def test_missing_inputs_return_fail_closed_statuses(self):
        payload = json.loads((ROOT / "tests/fixtures/golden_landed_cost_v1.json").read_text())
        request = request_for(payload["cases"][0])
        cases = (
            (replace(request, hs_code_version=None), "UNVERIFIED_CLASSIFICATION"),
            (replace(request, manufacturing_origin_country_code=None), "UNVERIFIED_ORIGIN"),
            (replace(request, tax_profile=None), "UNVERIFIED_TAX_RECOVERABILITY"),
            (replace(request, fx_quotes=()), "MISSING_EXCHANGE_RATE"),
            (replace(request, tariff_rule=None), "NOT_CALCULABLE"),
        )
        for candidate, expected in cases:
            with self.subTest(status=expected):
                result = calculate_landed_cost(candidate)
                self.assertEqual(result["calculation_status"], expected)
                self.assertIsNone(result["net_landed_cost_amount"])
                self.assertFalse(result["operational_eligible"])

    def test_fixed_charge_allocation_conserves_total(self):
        lines = (
            AllocationLine("1", "A", Decimal("1"), Decimal("1"), None, None),
            AllocationLine("2", "B", Decimal("2"), Decimal("2"), None, None),
            AllocationLine("3", "C", Decimal("3"), Decimal("3"), None, None),
        )
        allocated = allocate_fixed_charge(
            Decimal("100"), lines, basis="CUSTOMS_VALUE", currency_scale=2
        )
        self.assertEqual(allocated, {"1": "16.67", "2": "33.33", "3": "50"})
        self.assertEqual(sum(map(Decimal, allocated.values())), Decimal("100"))

    def test_weight_allocation_requires_weight_source(self):
        lines = (AllocationLine("1", "A", Decimal("1"), Decimal("1"), None, None),)
        with self.assertRaisesRegex(InventoryInputError, "WEIGHT_ALLOCATION_SOURCE_MISSING"):
            allocate_fixed_charge(Decimal("1"), lines, basis="WEIGHT", currency_scale=2)

    def test_operational_calculation_blocks_fixture_sources(self):
        payload = json.loads((ROOT / "tests/fixtures/golden_landed_cost_v1.json").read_text())
        request = replace(
            request_for(payload["cases"][0]),
            calculation_purpose="OPERATIONAL",
        )
        result = calculate_landed_cost(request)
        self.assertEqual(result["calculation_status"], "NOT_CALCULABLE")
        self.assertEqual(result["blocking_reason_codes"], ["SOURCE_NOT_OPERATIONAL"])

    def test_preferential_tariff_without_origin_evidence_is_blocked(self):
        payload = json.loads((ROOT / "tests/fixtures/golden_landed_cost_v1.json").read_text())
        request = request_for(payload["cases"][0])
        candidate = replace(
            request,
            tariff_rule=replace(request.tariff_rule, tariff_basis="PREFERENTIAL"),
            preferential_eligibility_status="UNVERIFIED",
        )
        result = calculate_landed_cost(candidate)
        self.assertEqual(result["calculation_status"], "UNVERIFIED_ORIGIN")
        self.assertEqual(
            result["blocking_reason_codes"],
            ["PREFERENTIAL_ORIGIN_EVIDENCE_UNVERIFIED"],
        )


if __name__ == "__main__":
    unittest.main()

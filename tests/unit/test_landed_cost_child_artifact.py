"""Projection -> calculator -> immutable child Golden and fail-closed tests."""

from __future__ import annotations

import copy
import unittest
from decimal import Decimal, localcontext

from dsio_inventory_engine.calculate_landed_cost.artifacts import build_landed_cost_child
from dsio_inventory_engine.inventory_contracts.landed_cost_artifact import (
    LandedCostShipmentInput,
    seal_shipment_input,
)
from dsio_inventory_engine.inventory_contracts.trade_cost_projection import (
    TradeCostRevisionSetProjection,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from tests.support.landed_cost_fixtures import (
    golden_cases,
    landed_fixture,
    rebind,
    reseal_projection,
    source_for,
)


def build(projection: dict, shipment: dict) -> dict:
    return build_landed_cost_child(
        TradeCostRevisionSetProjection.from_dict(projection),
        LandedCostShipmentInput.from_dict(shipment),
    )


class LandedCostChildTests(unittest.TestCase):
    def test_four_country_golden_totals_and_components(self):
        for case in golden_cases():
            with self.subTest(country=case["import_country_code"]):
                projection, shipment = landed_fixture(case)
                child = build(projection, shipment)
                self.assertEqual(child["calculation_status"], "CALCULABLE")
                self.assertEqual(child["row_count"], 1)
                for key, value in case["expected"].items():
                    if key != "content_hash":
                        self.assertEqual(child["totals"][key], value)
                row = child["rows"][0]["assessment"]
                self.assertFalse(row["operational_eligible"])
                self.assertEqual(child["totals"], {key: row[key] for key in child["totals"]})
                self.assertEqual(child, build(projection, shipment))
                self.assertFalse(child["automatic_order_allowed"])
                self.assertFalse(child["automatic_publish_allowed"])

    def test_fixed_charge_conserved_across_lines_and_order_independent(self):
        projection, shipment = landed_fixture()
        body = {
            key: copy.deepcopy(value) for key, value in shipment.items() if key != "content_hash"
        }
        second = copy.deepcopy(body["lines"][0])
        second.update(shipment_line_id="2", quantity="20", transaction_value="2000")
        body["lines"].append(second)
        inputs = seal_shipment_input(body)
        child = build(projection, inputs)
        freight = [
            Decimal(component["amount"])
            for row in child["rows"]
            for component in row["assessment"]["components"]
            if component["component_type"] == "INTERNATIONAL_FREIGHT"
        ]
        self.assertEqual(sum(freight), Decimal(15000))
        self.assertEqual(len(freight), 2)
        body["lines"].reverse()
        self.assertEqual(inputs, seal_shipment_input(body))
        self.assertEqual(child, build(projection, seal_shipment_input(body)))

    def test_ambient_decimal_precision_does_not_change_hash(self):
        projection, shipment = landed_fixture(golden_cases()[1])
        expected = build(projection, shipment)
        with localcontext() as context:
            context.prec = 6
            self.assertEqual(build(projection, shipment), expected)

    def test_missing_sources_preserve_blocked_rows_and_null_totals(self):
        for domain, reason in (
            ("ITEM_CLASSIFICATION", "HS_CLASSIFICATION_UNVERIFIED"),
            ("ITEM_ORIGIN", "MANUFACTURING_ORIGIN_UNVERIFIED"),
            ("CUSTOMS_TARIFF", "TARIFF_RULE_NOT_BOUND"),
            ("CUSTOMS_FX", "CUSTOMS_EXCHANGE_RATE_MISSING"),
            ("IMPORT_TAX_PROFILE", "TAX_RECOVERABILITY_UNVERIFIED"),
            ("LANE_CHARGE", "LANE_CHARGE_NOT_FOUND"),
        ):
            with self.subTest(domain=domain):
                projection, shipment = landed_fixture()
                source_for(projection, domain)["records"] = []
                projection = reseal_projection(projection)
                child = build(projection, rebind(projection, shipment))
                self.assertEqual(child["calculation_status"], "NOT_CALCULABLE")
                self.assertTrue(all(value is None for value in child["totals"].values()))
                self.assertIsNone(child["rows"][0]["assessment"])
                self.assertIn(reason, child["rows"][0]["blocking_reason_codes"])

    def test_expired_classification_not_applied(self):
        projection, shipment = landed_fixture()
        source_for(projection, "ITEM_CLASSIFICATION")["records"][0]["valid_to"] = "2026-09-28"
        projection = reseal_projection(projection)
        result = build(projection, rebind(projection, shipment))
        self.assertIn("HS_CLASSIFICATION_UNVERIFIED", result["rows"][0]["blocking_reason_codes"])

    def test_ambiguous_record_rejected(self):
        projection, shipment = landed_fixture()
        source = source_for(projection, "CUSTOMS_TARIFF")
        source["records"].append({**source["records"][0], "tariff_rule_id": "OTHER"})
        projection = reseal_projection(projection)
        with self.assertRaisesRegex(InventoryInputError, "LANDED_COST_SOURCE_AMBIGUOUS"):
            build(projection, rebind(projection, shipment))

    def test_future_fx_never_used(self):
        projection, shipment = landed_fixture()
        source = source_for(projection, "CUSTOMS_FX")
        source["records"].append(
            {**source["records"][0], "effective_date": "2026-09-29", "rate": "999"}
        )
        projection = reseal_projection(projection)
        result = build(projection, rebind(projection, shipment))
        self.assertEqual(result["totals"]["customs_value_amount"], "166500")

    def test_hash_scope_network_rounding_and_development_guards(self):
        for kind in ("scope", "set_hash", "network", "rounding", "purpose", "float", "duplicate"):
            with self.subTest(kind=kind):
                projection, shipment = landed_fixture()
                body = {
                    key: copy.deepcopy(value)
                    for key, value in shipment.items()
                    if key != "content_hash"
                }
                if kind == "scope":
                    body["binding"]["project_id"] = "other"
                elif kind == "set_hash":
                    body["binding"]["revision_set_content_hash"] = "f" * 64
                elif kind == "network":
                    body["binding"]["network_revision_id"] = "73000000-0000-4000-8000-000000000001"
                elif kind == "rounding":
                    body["currency_scale"] = 2
                elif kind == "purpose":
                    body["calculation_purpose"] = "OPERATIONAL"
                elif kind == "float":
                    body["lines"][0]["transaction_value"] = 1000.0
                elif kind == "duplicate":
                    body["lines"].append(copy.deepcopy(body["lines"][0]))
                if kind == "network":
                    result = build(projection, seal_shipment_input(body))
                    self.assertIn(
                        "LANE_CHARGE_NOT_FOUND", result["rows"][0]["blocking_reason_codes"]
                    )
                else:
                    with self.assertRaises(InventoryInputError):
                        build(projection, seal_shipment_input(body))

    def test_tampered_source_or_unsealed_shipment_rejected(self):
        projection, shipment = landed_fixture()
        shipment["lines"][0]["quantity"] = "11"
        with self.assertRaisesRegex(InventoryInputError, "LANDED_COST_SHIPMENT_HASH_MISMATCH"):
            build(projection, shipment)
        projection, shipment = landed_fixture()
        source_for(projection, "CUSTOMS_FX")["records"][0]["rate"] = "999"
        with self.assertRaisesRegex(InventoryInputError, "HASH_MISMATCH"):
            build(projection, shipment)

    def test_unknown_input_field_and_falsey_attempt_rejected(self):
        _projection, shipment = landed_fixture()
        for mutation in (
            {"unexpected": True},
            {"binding": {**shipment["binding"], "attempt_no": True}},
        ):
            with self.subTest(mutation=mutation), self.assertRaises(InventoryInputError):
                LandedCostShipmentInput.from_dict({**shipment, **mutation})

    def test_missing_charge_without_exemption_is_not_zero(self):
        projection, shipment = landed_fixture()
        body = {
            key: copy.deepcopy(value) for key, value in shipment.items() if key != "content_hash"
        }
        body["lines"][0]["charges"] = [
            row for row in body["lines"][0]["charges"] if row["component_type"] != "INSURANCE"
        ]
        child = build(projection, seal_shipment_input(body))
        self.assertIn("CHARGE_COVERAGE_INCOMPLETE", child["rows"][0]["blocking_reason_codes"])
        self.assertIsNone(child["totals"]["net_landed_cost_amount"])

    def test_unsupported_lane_rate_and_overlapping_charge_are_rejected(self):
        for kind in ("rate", "overlap", "floor_above_cap"):
            with self.subTest(kind=kind):
                projection, shipment = landed_fixture()
                row = source_for(projection, "LANE_CHARGE")["records"][0]
                if kind == "rate":
                    row.update(charge_basis="QUANTITY", charge_amount=None, charge_rate="1")
                elif kind == "overlap":
                    row["component_type"] = "INSURANCE"
                else:
                    row.update(minimum_charge_amount="200", maximum_charge_amount="100")
                projection = reseal_projection(projection)
                with self.assertRaises(InventoryInputError):
                    build(projection, rebind(projection, shipment))

    def test_future_source_document_and_missing_record_fields_are_rejected(self):
        for kind in ("future", "missing"):
            with self.subTest(kind=kind):
                projection, shipment = landed_fixture()
                source = source_for(projection, "CUSTOMS_TARIFF")
                if kind == "future":
                    source["documents"][0]["collected_at"] = "2026-09-29T00:00:00Z"
                else:
                    del source["records"][0]["ad_valorem_rate"]
                projection = reseal_projection(projection)
                with self.assertRaises(InventoryInputError):
                    build(projection, rebind(projection, shipment))

    def test_source_confirmed_zero_freight_is_calculable(self):
        projection, shipment = landed_fixture()
        source_for(projection, "LANE_CHARGE")["records"][0]["charge_amount"] = "0"
        projection = reseal_projection(projection)
        child = build(projection, rebind(projection, shipment))
        self.assertEqual(child["calculation_status"], "CALCULABLE")
        freight = next(
            row
            for row in child["rows"][0]["assessment"]["components"]
            if row["component_type"] == "INTERNATIONAL_FREIGHT"
        )
        self.assertEqual(freight["amount"], "0")
        self.assertEqual(freight["zero_value_reason"], "SOURCE_CONFIRMED_ZERO")

    def test_specific_compound_floor_and_cap_use_projection_rules(self):
        for method, rate, minimum, maximum, expected in (
            ("SPECIFIC", None, "500", "800", "500"),
            ("COMPOUND", "0.05", None, "900", "900"),
        ):
            with self.subTest(method=method):
                projection, shipment = landed_fixture()
                source_for(projection, "CUSTOMS_TARIFF")["records"][0].update(
                    duty_method=method,
                    ad_valorem_rate=rate,
                    specific_rate="10",
                    specific_uom="EA",
                    minimum_duty_amount=minimum,
                    maximum_duty_amount=maximum,
                )
                projection = reseal_projection(projection)
                child = build(projection, rebind(projection, shipment))
                duty = next(
                    component
                    for component in child["rows"][0]["assessment"]["components"]
                    if component["component_type"] == "CUSTOMS_DUTY_OTHER"
                )
                self.assertEqual(duty["amount"], expected)

    def test_physical_allocation_requires_explicit_unit_source(self):
        projection, shipment = landed_fixture()
        body = {
            key: copy.deepcopy(value) for key, value in shipment.items() if key != "content_hash"
        }
        body["fixed_cost_allocation_basis"] = "WEIGHT"
        with self.assertRaisesRegex(InventoryInputError, "LANDED_COST_PHYSICAL_SOURCE_MISSING"):
            build(projection, seal_shipment_input(body))
        physical = copy.deepcopy(source_for(projection, "ITEM_ORIGIN"))
        revision = "74000000-0000-4000-8000-000000000001"
        physical["header"].update(
            source_revision_id=revision, source_domain="ITEM_PHYSICAL_ATTRIBUTE"
        )
        physical["documents"][0]["source_revision_id"] = revision
        physical["records"] = [
            {
                "source_revision_id": revision,
                "source_domain": "ITEM_PHYSICAL_ATTRIBUTE",
                "source_document_id": "DOC-DEV",
                "item_id": "DEV-PART-001",
                "net_weight": "12.5",
                "weight_uom": "KG",
                "volume": "0.08",
                "volume_uom": "M3",
                "valid_from": "2026-01-01",
                "valid_to": None,
            }
        ]
        projection["sources"].append(physical)
        projection = reseal_projection(projection)
        second = {**copy.deepcopy(body["lines"][0]), "shipment_line_id": "2", "quantity": "20"}
        body["lines"].append(second)
        for basis in ("WEIGHT", "VOLUME"):
            with self.subTest(basis=basis):
                body["fixed_cost_allocation_basis"] = basis
                child = build(projection, rebind(projection, seal_shipment_input(body)))
                freight = {
                    row["grain"]["shipment_line_id"]: next(
                        component["amount"]
                        for component in row["assessment"]["components"]
                        if component["component_type"] == "INTERNATIONAL_FREIGHT"
                    )
                    for row in child["rows"]
                }
                self.assertEqual(freight, {"1": "5000", "2": "10000"})

    def test_latest_fx_duplicate_zero_rate_and_wrong_currency_rejected(self):
        for kind in ("duplicate", "zero", "currency"):
            with self.subTest(kind=kind):
                projection, shipment = landed_fixture()
                source = source_for(projection, "CUSTOMS_FX")
                if kind == "duplicate":
                    source["records"].append({**source["records"][0], "rate": "151"})
                elif kind == "zero":
                    source["records"][0]["rate"] = "0"
                else:
                    source_for(projection, "CUSTOMS_TARIFF")["records"][0]["duty_currency"] = "USD"
                projection = reseal_projection(projection)
                with self.assertRaises(InventoryInputError):
                    build(projection, rebind(projection, shipment))

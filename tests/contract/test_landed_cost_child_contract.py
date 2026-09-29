"""Closed JSON Schema matches Shipment and child contracts; semantic replay is additionally required."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource

from dsio_inventory_engine.calculate_landed_cost.artifacts import build_landed_cost_child
from dsio_inventory_engine.inventory_contracts.landed_cost_artifact import LandedCostShipmentInput
from dsio_inventory_engine.inventory_contracts.trade_cost_projection import (
    TradeCostRevisionSetProjection,
)
from tests.support.landed_cost_fixtures import (
    golden_cases,
    landed_fixture,
    rebind,
    reseal_projection,
    source_for,
)


class LandedCostChildContractTests(unittest.TestCase):
    def setUp(self):
        folder = Path(__file__).resolve().parents[2] / "schemas"
        self.schemas = {
            name: json.loads((folder / name).read_text())
            for name in (
                "landed_cost_shipment_input.schema.json",
                "landed_cost_child_artifact.schema.json",
                "landed_cost_assessment.schema.json",
            )
        }
        self.registry = Registry().with_resources(
            [
                (f"https://schemas.dsio.local/{name}", Resource.from_contents(schema))
                for name, schema in self.schemas.items()
            ]
        )

    def validator(self, name):
        return Draft202012Validator(
            self.schemas[name], registry=self.registry, format_checker=FormatChecker()
        )

    def test_four_country_inputs_and_children_are_valid(self):
        for case in golden_cases():
            with self.subTest(country=case["import_country_code"]):
                projection, shipment = landed_fixture(case)
                self.validator("landed_cost_shipment_input.schema.json").validate(shipment)
                child = build_landed_cost_child(
                    TradeCostRevisionSetProjection.from_dict(projection),
                    LandedCostShipmentInput.from_dict(shipment),
                )
                self.validator("landed_cost_child_artifact.schema.json").validate(child)

    def test_blocked_child_with_no_assessment_is_valid(self):
        projection, shipment = landed_fixture()
        source_for(projection, "CUSTOMS_FX")["records"] = []
        projection = reseal_projection(projection)
        child = build_landed_cost_child(
            TradeCostRevisionSetProjection.from_dict(projection),
            LandedCostShipmentInput.from_dict(rebind(projection, shipment)),
        )
        self.validator("landed_cost_child_artifact.schema.json").validate(child)

    def test_empty_child_and_automatic_flags_are_invalid(self):
        projection, shipment = landed_fixture()
        child = build_landed_cost_child(
            TradeCostRevisionSetProjection.from_dict(projection),
            LandedCostShipmentInput.from_dict(shipment),
        )
        for field in (
            "row_count",
            "rows",
            "automatic_publish_allowed",
            "automatic_order_allowed",
            "operational_eligible",
            "contract_version",
            "extra",
        ):
            with self.subTest(field=field):
                value = copy.deepcopy(child)
                value[field] = {"row_count": 0, "rows": [], "contract_version": "2.0.0"}.get(
                    field, True
                )
                with self.assertRaises(ValidationError):
                    self.validator("landed_cost_child_artifact.schema.json").validate(value)

    def test_float_quantity_unknown_field_and_naive_timestamp_are_invalid(self):
        _, shipment = landed_fixture()
        for field in ("float", "extra", "judgment"):
            with self.subTest(field=field):
                value = copy.deepcopy(shipment)
                if field == "float":
                    value["lines"][0]["quantity"] = 10.0
                elif field == "extra":
                    value["extra"] = True
                else:
                    value["binding"]["judgment_at"] = "2026-09-28T00:00:00"
                with self.assertRaises(ValidationError):
                    self.validator("landed_cost_shipment_input.schema.json").validate(value)

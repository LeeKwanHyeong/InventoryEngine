"""Offline Schema/DTO parity with a local registry; no external resolution."""

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from evaluation_fixtures import fixture
from dsio_inventory_engine.inventory_contracts.evaluation import ProductionEvaluationRequest
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError

try:
    from jsonschema import Draft202012Validator, FormatChecker, ValidationError
    from referencing import Registry, Resource
except ImportError:
    Draft202012Validator = None

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipIf(Draft202012Validator is None, "jsonschema is a development dependency")
class ProductionEvaluationContractTests(unittest.TestCase):
    def validator(self):
        registry = Registry()
        for uri, name in (
            ("canonical-input", "canonical_input"),
            ("training-foundation", "training_foundation"),
            ("replenishment", "replenishment"),
            ("mathematical-policy-input", "mathematical_policy"),
        ):
            schema = json.loads((ROOT / f"schemas/{name}.schema.json").read_text())
            registry = registry.with_resource(f"urn:dsio:{uri}:v1", Resource.from_contents(schema))
        schema = json.loads((ROOT / "schemas/production_evaluation.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())

    def test_full_request_and_roundtrip(self):
        data = fixture()
        self.validator().validate(data)
        self.assertEqual(data, ProductionEvaluationRequest.from_dict(data).to_dict())

    def test_nested_extra_and_minor_version_rejected_by_both(self):
        for change in ("extra", "version", "strategy"):
            data = copy.deepcopy(fixture())
            execution = data["mathematical_request"]["recommendation"]["execution"]
            if change == "extra":
                data["mathematical_request"]["unexpected"] = True
            elif change == "version":
                execution["contract_version"] = "1.0.0"
            else:
                execution["strategy"]["implementation_id"] = "reference-r-s"
            with self.subTest(change=change), self.assertRaises(ValidationError):
                self.validator().validate(data)
            with self.subTest(change=change), self.assertRaises(InventoryInputError):
                ProductionEvaluationRequest.from_dict(data)

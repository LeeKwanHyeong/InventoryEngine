"""Offline model/learning Schema + DTO validation, without network resolution."""

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from learned_fixtures import inference_request, model_fixture, reseal_model
from math_fixtures import build_math
from dsio_inventory_engine.fit_replenishment.demo import build_learning_request
from dsio_inventory_engine.inventory_contracts.model import ModelArtifact
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError

try:
    from jsonschema import Draft202012Validator, FormatChecker, ValidationError
    from referencing import Registry, Resource
except ImportError:
    Draft202012Validator = None

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipIf(Draft202012Validator is None, "jsonschema is a development dependency")
class LearnedContractTests(unittest.TestCase):
    def validator(self, name):
        registry = Registry()
        for path in (ROOT / "schemas").glob("*.schema.json"):
            data = json.loads(path.read_text())
            if "$id" in data:
                registry = registry.with_resource(data["$id"], Resource.from_contents(data))
        for uri, schema_name in (
            ("training-foundation", "training_foundation"),
            ("mathematical-policy-input", "mathematical_policy"),
            ("canonical-input", "canonical_input"),
            ("replenishment", "replenishment"),
        ):
            registry = registry.with_resource(
                f"urn:dsio:{uri}:v1",
                Resource.from_contents(
                    json.loads((ROOT / f"schemas/{schema_name}.schema.json").read_text())
                ),
            )
        schema = json.loads((ROOT / f"schemas/{name}.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())

    def test_learning_and_inference_roundtrip(self):
        data = build_learning_request(quick=True).to_dict()
        self.validator("learned_training").validate(data)
        for family in ("PREDICTIVE_ML", "DEEP_RL"):
            anchor = build_math()
            model = model_fixture(anchor, family)
            self.validator("replenishment_model").validate(model.to_dict())
            self.validator("learned_inference").validate(inference_request(anchor, model).to_dict())

    def test_model_shape_family_and_nonfinite_rejected_by_both(self):
        original = model_fixture(build_math(), "DEEP_RL").to_dict()
        for change in ("shape", "extra", "family", "version", "nan"):
            data = copy.deepcopy(original)
            if change == "shape":
                data["weights"]["fc1.weight"][0].pop()
            elif change == "extra":
                data["url"] = "https://invalid.example/model.pkl"
            elif change == "family":
                data["strategy_type"] = "PREDICTIVE_ML"
            elif change == "version":
                data["contract_version"] = "2.0.0"
            else:
                data["weights"]["actor.bias"][0] = "NaN"
            data = reseal_model(data)
            with self.subTest(change=change), self.assertRaises(ValidationError):
                self.validator("replenishment_model").validate(data)
            with self.subTest(change=change), self.assertRaises(InventoryInputError):
                ModelArtifact.from_dict(data)

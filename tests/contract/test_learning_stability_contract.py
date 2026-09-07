"""Offline experiment Schema and immutable request validation."""

import copy
import json
import tomllib
import unittest
from pathlib import Path

from dsio_inventory_engine.inventory_contracts.stability import StabilityRequest, default_request
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError

try:
    from jsonschema import Draft202012Validator, ValidationError
except ImportError:
    Draft202012Validator = None


@unittest.skipIf(Draft202012Validator is None, "jsonschema is a development dependency")
class LearningStabilityContractTests(unittest.TestCase):
    def validator(self):
        path = Path(__file__).resolve().parents[2] / "schemas/learning_stability.schema.json"
        data = json.loads(path.read_text())
        Draft202012Validator.check_schema(data)
        return Draft202012Validator(data)

    def test_schema_dto_roundtrip(self):
        from dsio_inventory_engine import __version__

        root = Path(__file__).resolve().parents[2]
        metadata = tomllib.loads((root / "pyproject.toml").read_text())
        self.assertEqual(__version__, metadata["project"]["version"])
        data = default_request().to_dict()
        self.validator().validate(data)
        self.assertEqual(StabilityRequest.from_dict(data), default_request())

    def test_both_reject_unbounded_and_unknown_recipe(self):
        for key, value in (
            ("training_seeds", [1]),
            ("generator_version", "v999"),
            ("unexpected", True),
        ):
            data = copy.deepcopy(default_request().to_dict())
            data[key] = value
            with self.assertRaises(ValidationError):
                self.validator().validate(data)
            with self.assertRaises(InventoryInputError):
                StabilityRequest.from_dict(data)

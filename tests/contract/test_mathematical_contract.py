"""Explicit minor-version binding plus policy input Schema/DTO parity."""

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from math_fixtures import build_math
from dsio_inventory_engine.inventory_contracts.mathematical import (
    ADJUSTMENT_FIELDS,
    HISTORY_CALENDAR_FIELDS,
    HISTORY_FIELDS,
    INPUT_CONTEXT_FIELDS,
    POLICY_INPUT_FIELDS,
    POLICY_VALUE_FIELDS,
    PROFILE_FIELDS,
    MathematicalPolicyRequest,
)
from dsio_inventory_engine.inventory_contracts.replenishment import (
    EXECUTION_FIELDS_V1_1,
    EXECUTION_FIELDS_V2,
)

try:
    from jsonschema import Draft202012Validator, FormatChecker, ValidationError
except ImportError:
    Draft202012Validator = None

ROOT = Path(__file__).resolve().parents[2]


class MathematicalContractTests(unittest.TestCase):
    def test_wire_field_parity(self):
        schema = json.loads((ROOT / "schemas/mathematical_policy.schema.json").read_text())
        self.assertEqual(set(schema["required"]), set(POLICY_INPUT_FIELDS))
        for name, fields in (
            ("profile", PROFILE_FIELDS),
            ("context", INPUT_CONTEXT_FIELDS),
            ("calendar", HISTORY_CALENDAR_FIELDS),
            ("history", HISTORY_FIELDS),
            ("policy", POLICY_VALUE_FIELDS),
            ("adjustment", ADJUSTMENT_FIELDS),
        ):
            self.assertEqual(set(schema["$defs"][name]["required"]), set(fields))
        strategy = json.loads((ROOT / "schemas/replenishment.schema.json").read_text())
        self.assertEqual(
            set(strategy["$defs"]["executionV1_1"]["required"]), set(EXECUTION_FIELDS_V1_1)
        )
        self.assertEqual(
            set(strategy["$defs"]["executionV2"]["required"]), set(EXECUTION_FIELDS_V2)
        )

    @unittest.skipIf(Draft202012Validator is None, "jsonschema is a development dependency")
    def test_minor_version_request_and_schema_rejections(self):
        request = MathematicalPolicyRequest.from_dict(build_math()).to_dict()
        for name, value in (
            ("mathematical_policy", request["policy_input"]),
            ("replenishment", request["recommendation"]["execution"]),
        ):
            schema = json.loads((ROOT / f"schemas/{name}.schema.json").read_text())
            Draft202012Validator.check_schema(schema)
            validator = Draft202012Validator(schema, format_checker=FormatChecker())
            validator.validate(value)
            invalid = copy.deepcopy(value)
            invalid["unexpected"] = True
            with self.assertRaises(ValidationError):
                validator.validate(invalid)
        incompatible = copy.deepcopy(request["recommendation"]["execution"])
        incompatible["contract_version"] = "1.0.0"
        with self.assertRaises(ValidationError):
            validator.validate(incompatible)


if __name__ == "__main__":
    unittest.main()

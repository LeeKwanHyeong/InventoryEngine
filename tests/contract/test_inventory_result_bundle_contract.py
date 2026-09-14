"""JSON Schema parity for the sealed strategy plan and Result Bundle."""

import json
import unittest
from pathlib import Path

from dsio_inventory_engine.inventory_contracts.result_bundle import (
    RESULT_BUNDLE_FIELDS,
    STRATEGY_EXECUTION_PLAN_FIELDS,
    seal_inventory_result_bundle,
    seal_strategy_execution_plan,
)
from tests.unit.test_inventory_result_bundle import bundle_body, plan_body

try:
    from jsonschema import Draft202012Validator, ValidationError
except ImportError:
    Draft202012Validator = None
    ValidationError = Exception


ROOT = Path(__file__).resolve().parents[2]


@unittest.skipIf(Draft202012Validator is None, "jsonschema is a dev-only dependency")
class InventoryResultBundleContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        schema = json.loads((ROOT / "schemas/inventory_result_bundle.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        cls.schema = schema
        cls.validator = Draft202012Validator(schema)

    def test_python_and_schema_top_level_field_parity(self):
        self.assertEqual(
            set(self.schema["$defs"]["strategyExecutionPlan"]["required"]),
            set(STRATEGY_EXECUTION_PLAN_FIELDS),
        )
        self.assertEqual(
            set(self.schema["$defs"]["resultBundle"]["required"]),
            set(RESULT_BUNDLE_FIELDS),
        )

    def test_sealed_plan_and_bundle_validate(self):
        plan = seal_strategy_execution_plan(plan_body())
        bundle = seal_inventory_result_bundle(bundle_body(plan), strategy_execution_plan=plan)

        self.validator.validate(plan)
        self.validator.validate(bundle)

    def test_schema_rejects_unclaimed_cost_profile_and_control_character_reference(self):
        plan = seal_strategy_execution_plan(plan_body())
        bundle = seal_inventory_result_bundle(bundle_body(plan), strategy_execution_plan=plan)
        bundle["cost_profile_content_hash"] = "0" * 64
        with self.assertRaises(ValidationError):
            self.validator.validate(bundle)

        plan = seal_strategy_execution_plan(plan_body())
        plan["shadow_challenger_bindings"][0]["approval_reference"] = "A\x1fB"
        with self.assertRaises(ValidationError):
            self.validator.validate(plan)

        valid_plan = seal_strategy_execution_plan(plan_body())
        bundle = seal_inventory_result_bundle(
            bundle_body(valid_plan),
            strategy_execution_plan=valid_plan,
        )
        bundle["result_bundle_id"] = "IO-BUNDLE-1"
        with self.assertRaises(ValidationError):
            self.validator.validate(bundle)


if __name__ == "__main__":
    unittest.main()

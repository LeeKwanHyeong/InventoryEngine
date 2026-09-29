"""JSON Schema parity for the sealed strategy plan and Result Bundle."""

import json
import unittest
from pathlib import Path

from dsio_inventory_engine.inventory_contracts.result_bundle import (
    RESULT_BUNDLE_FIELDS,
    STRATEGY_EXECUTION_PLAN_FIELDS,
    seal_inventory_result_bundle,
    seal_strategy_execution_plan,
    validate_inventory_result_bundle,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
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

    def test_contract_rejects_unclaimed_cost_profile_and_control_character_reference(self):
        plan = seal_strategy_execution_plan(plan_body())
        bundle = seal_inventory_result_bundle(bundle_body(plan), strategy_execution_plan=plan)
        bundle["cost_profile_content_hash"] = "0" * 64
        # JSON Schema checks the field shape. The cross-document claim is
        # enforced by the semantic validator against the sealed Run Plan.
        self.validator.validate(bundle)
        with self.assertRaisesRegex(
            InventoryInputError,
            "RESULT_BUNDLE_COST_PROFILE_BINDING_UNSUPPORTED",
        ):
            validate_inventory_result_bundle(bundle, strategy_execution_plan=plan)

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

    def test_schema_enforces_child_status_action_and_metric_semantics(self):
        plan = seal_strategy_execution_plan(plan_body())

        def valid_bundle():
            return seal_inventory_result_bundle(
                bundle_body(plan),
                strategy_execution_plan=plan,
            )

        cases = []

        baseline_failed = valid_bundle()
        baseline = next(
            row
            for row in baseline_failed["result_children"]
            if row["result_kind"] == "BASELINE_PSI"
        )
        baseline.update(
            status="FAILED",
            artifact_reference=None,
            artifact_contract_key=None,
            artifact_contract_version=None,
            child_content_hash=None,
            row_count=None,
            failure_reason_code="BASELINE_FAILED",
        )
        cases.append(("baseline-required", baseline_failed))

        ppo_skipped = valid_bundle()
        ppo = next(
            row
            for row in ppo_skipped["result_children"]
            if row["strategy_type"] == "DEEP_RL" and row["status"] == "FAILED"
        )
        ppo["status"] = "SKIPPED"
        cases.append(("ppo-skip-forbidden", ppo_skipped))

        stress_skipped = valid_bundle()
        stress = next(
            row for row in stress_skipped["result_children"] if row["result_kind"] == "STRESS_PSI"
        )
        stress.update(
            status="SKIPPED",
            artifact_reference=None,
            artifact_contract_key=None,
            artifact_contract_version=None,
            child_content_hash=None,
            row_count=None,
            failure_reason_code="STRESS_EXECUTION_FAILED",
            action_evidence={key: None for key in stress["action_evidence"]},
        )
        cases.append(("stress-skip-reason", stress_skipped))

        missing_artifact = valid_bundle()
        mathematical = next(
            row
            for row in missing_artifact["result_children"]
            if row["execution_role"] == "OPERATIONAL"
        )
        for key in (
            "artifact_reference",
            "artifact_contract_key",
            "artifact_contract_version",
            "child_content_hash",
            "row_count",
        ):
            mathematical[key] = None
        cases.append(("successful-artifact-required", missing_artifact))

        empty_success = valid_bundle()
        mathematical = next(
            row
            for row in empty_success["result_children"]
            if row["execution_role"] == "OPERATIONAL"
        )
        mathematical["row_count"] = 0
        cases.append(("successful-row-count-positive", empty_success))

        incomplete_action = valid_bundle()
        mathematical = next(
            row
            for row in incomplete_action["result_children"]
            if row["execution_role"] == "OPERATIONAL"
        )
        mathematical["action_evidence"]["raw_action_content_hash"] = None
        cases.append(("action-pair-complete", incomplete_action))

        invalid_metric = valid_bundle()
        invalid_metric["comparisons"][0]["service_level_delta"]["value"] = None
        cases.append(("available-metric-value", invalid_metric))

        invalid_automation = valid_bundle()
        invalid_automation["automatic_publish_allowed"] = False
        invalid_automation["automatic_order_allowed"] = True
        cases.append(("order-requires-publish", invalid_automation))

        for name, value in cases:
            with self.subTest(name=name), self.assertRaises(ValidationError):
                self.validator.validate(value)


if __name__ == "__main__":
    unittest.main()

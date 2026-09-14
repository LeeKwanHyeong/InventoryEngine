"""Execution-plan and Result Bundle semantic tests."""

import copy
import unittest

from dsio_inventory_engine.inventory_contracts.result_bundle import (
    derive_result_bundle_id,
    result_display_code,
    seal_inventory_result_bundle,
    seal_strategy_execution_plan,
    validate_inventory_result_bundle,
    validate_strategy_execution_plan,
)
from dsio_inventory_engine.inventory_contracts.runtime import (
    InventoryRuntimeExecutionRequest,
    validate_result_bundle_runtime_binding,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from tests.support.runtime_fixtures import reseal_runtime_site_binding, runtime_dispatch_v2


def plan_body() -> dict:
    return {
        "contract_id": "inventory-strategy-execution-plan-v1",
        "contract_version": "1.0.0",
        "strategy_execution_plan_id": "IO-PLAN-1",
        "classification_config_hash": "a" * 64,
        "effective_policy_content_hash": "b" * 64,
        "base_scenario_content_hash": "6" * 64,
        "operational_strategy": {
            "strategy_type": "MATHEMATICAL",
            "implementation_id": "math-policy",
            "version": "2.0.0",
            "implementation_content_hash": "c" * 64,
        },
        "shadow_challenger_bindings": [
            {
                "challenger_id": challenger_id,
                "strategy_type": "DEEP_RL",
                "algorithm": "PPO",
                "implementation_id": "ppo-policy",
                "version": version,
                "implementation_content_hash": implementation_hash,
                "model": {
                    "model_id": model_id,
                    "version": version,
                    "content_hash": model_hash,
                },
                "approval_reference": approval,
            }
            for challenger_id, version, implementation_hash, model_id, model_hash, approval in (
                ("ppo-b", "1.1.0", "d" * 64, "ppo-model-b", "e" * 64, "APPROVAL-B"),
                ("ppo-a", "1.0.0", "f" * 64, "ppo-model-a", "1" * 64, "APPROVAL-A"),
            )
        ],
        "stress_scenario_bindings": [
            {
                "scenario_id": "LATE-SUPPLY",
                "scenario_type": "STRESS",
                "scenario_contract_key": "inventory.stress_scenario",
                "scenario_contract_version": "1.0.0",
                "scenario_content_hash": "2" * 64,
            }
        ],
        "result_bundle_contract_key": "inventory.result_bundle",
        "result_bundle_contract_version": "1.0.0",
    }


def no_actions() -> dict:
    return {
        "raw_action_reference": None,
        "raw_action_content_hash": None,
        "constrained_action_reference": None,
        "constrained_action_content_hash": None,
        "adjustment_reasons_reference": None,
        "adjustment_reasons_content_hash": None,
    }


def actions(prefix: str) -> dict:
    return {
        "raw_action_reference": f"artifact:{prefix}:raw",
        "raw_action_content_hash": "3" * 64,
        "constrained_action_reference": f"artifact:{prefix}:constrained",
        "constrained_action_content_hash": "4" * 64,
        "adjustment_reasons_reference": f"artifact:{prefix}:reasons",
        "adjustment_reasons_content_hash": "5" * 64,
    }


def child(
    child_id: str,
    *,
    kind: str,
    role: str,
    strategy: str,
    scenario_id: str = "BASE",
    scenario_hash: str = "6" * 64,
    challenger_id: str | None = None,
    model_hash: str | None = None,
    status: str = "SUCCEEDED",
) -> dict:
    succeeded = status == "SUCCEEDED"
    return {
        "child_result_id": child_id,
        "result_kind": kind,
        "execution_role": role,
        "strategy_type": strategy,
        "scenario_id": scenario_id,
        "scenario_content_hash": scenario_hash,
        "challenger_id": challenger_id,
        "model_content_hash": model_hash,
        "status": status,
        "artifact_reference": f"artifact:{child_id}" if succeeded else None,
        "artifact_contract_key": "inventory.psi_result" if succeeded else None,
        "artifact_contract_version": "1.0.0" if succeeded else None,
        "child_content_hash": "7" * 64 if succeeded else None,
        "row_count": 26 if succeeded else None,
        "failure_reason_code": None if succeeded else "PPO_INFERENCE_FAILED",
        "action_evidence": no_actions()
        if strategy == "NONE" or not succeeded
        else actions(child_id),
    }


def available(value: str) -> dict:
    return {"status": "AVAILABLE", "value": value, "reason_code": None}


def unavailable(reason: str) -> dict:
    return {"status": "NOT_AVAILABLE", "value": None, "reason_code": reason}


def comparison(candidate_id: str, *, failed: bool = False) -> dict:
    reason = "CANDIDATE_RESULT_FAILED"
    return {
        "baseline_child_result_id": "BASELINE-1",
        "candidate_child_result_id": candidate_id,
        "cost_delta": unavailable(reason if failed else "COST_PROFILE_NOT_BOUND"),
        "service_level_delta": unavailable(reason) if failed else available("0.01"),
        "backorder_qty_delta": unavailable(reason) if failed else available("-10"),
    }


def bundle_body(plan: dict) -> dict:
    engine_run_id = "10000000-0000-4000-8000-000000000001"
    attempt_no = 1
    children = [
        child(
            "BASELINE-1",
            kind="BASELINE_PSI",
            role="EVIDENCE_ONLY",
            strategy="NONE",
        ),
        child(
            "MATH-1",
            kind="RECOMMENDED_PSI",
            role="OPERATIONAL",
            strategy="MATHEMATICAL",
        ),
        child(
            "PPO-A-1",
            kind="RECOMMENDED_PSI",
            role="SHADOW",
            strategy="DEEP_RL",
            challenger_id="ppo-a",
            model_hash="1" * 64,
        ),
        child(
            "PPO-B-1",
            kind="RECOMMENDED_PSI",
            role="SHADOW",
            strategy="DEEP_RL",
            challenger_id="ppo-b",
            model_hash="e" * 64,
            status="FAILED",
        ),
        child(
            "STRESS-1",
            kind="STRESS_PSI",
            role="EVIDENCE_ONLY",
            strategy="MATHEMATICAL",
            scenario_id="LATE-SUPPLY",
            scenario_hash="2" * 64,
        ),
    ]
    return {
        "contract_id": "inventory-result-bundle-v1",
        "contract_version": "1.0.0",
        "source_contract_key": "inventory.result_bundle",
        "result_bundle_id": derive_result_bundle_id(engine_run_id, attempt_no),
        "engine_run_id": engine_run_id,
        "attempt_no": attempt_no,
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "planning_cycle_id": "PC-202601",
        "planning_cycle_revision_id": "PCR-202601-01",
        "cycle_site_execution_id": "PCR-202601-01:V100",
        "plan_id": "PLAN-202601",
        "scope": {
            "company_cd": "DSE",
            "subs_cd": "C100",
            "plant_cd": "V100",
            "site_cd": "V100",
        },
        "canonical_input_hash": "8" * 64,
        "site_binding_hash": "9" * 64,
        "strategy_execution_plan_id": plan["strategy_execution_plan_id"],
        "strategy_execution_plan_content_hash": plan["content_hash"],
        "classification_config_hash": plan["classification_config_hash"],
        "effective_policy_content_hash": plan["effective_policy_content_hash"],
        "cost_profile_content_hash": None,
        "result_children": children,
        "effective_child_result_id": "MATH-1",
        "comparisons": [
            comparison("MATH-1"),
            comparison("PPO-A-1"),
            comparison("PPO-B-1", failed=True),
            comparison("STRESS-1"),
        ],
        "automatic_publish_allowed": True,
        "automatic_order_allowed": False,
    }


class InventoryResultBundleTests(unittest.TestCase):
    def test_plan_sorting_and_hash_are_deterministic(self):
        first_body = plan_body()
        second_body = copy.deepcopy(first_body)
        second_body["shadow_challenger_bindings"].reverse()

        first = seal_strategy_execution_plan(first_body)
        second = seal_strategy_execution_plan(second_body)

        self.assertEqual(first, second)
        self.assertEqual(
            [item["challenger_id"] for item in first["shadow_challenger_bindings"]],
            ["ppo-a", "ppo-b"],
        )
        self.assertEqual(validate_strategy_execution_plan(first), first)
        self.assertEqual(
            first["content_hash"],
            "38be99dd40ac629691928893ff0494092752f7c421f56dcf44ec88cbca019bb5",
        )

    def test_cross_repository_plan_golden_hash(self):
        plan = seal_strategy_execution_plan(
            {
                "contract_id": "inventory-strategy-execution-plan-v1",
                "contract_version": "1.0.0",
                "strategy_execution_plan_id": "strategy-plan-1",
                "classification_config_hash": "a" * 64,
                "effective_policy_content_hash": "e" * 64,
                "base_scenario_content_hash": "6" * 64,
                "operational_strategy": {
                    "strategy_type": "MATHEMATICAL",
                    "implementation_id": "inventory.math.sS",
                    "version": "1.0.0",
                    "implementation_content_hash": "1" * 64,
                },
                "shadow_challenger_bindings": [],
                "stress_scenario_bindings": [],
                "result_bundle_contract_key": "inventory.result_bundle",
                "result_bundle_contract_version": "1.0.0",
            }
        )

        self.assertEqual(
            plan["content_hash"],
            "7317e95907f8c6e7a9d00d0408755dde7f03db208f7df2ae6f4605bc5be016e1",
        )

    def test_only_approved_ppo_challengers_are_admitted(self):
        for mutation in (
            lambda item: item.update(algorithm="DQN"),
            lambda item: item.update(strategy_type="PREDICTIVE_ML"),
            lambda item: item.update(approval_reference=""),
            lambda item: item["model"].pop("content_hash"),
        ):
            with self.subTest(mutation=mutation):
                value = plan_body()
                mutation(value["shadow_challenger_bindings"][0])
                with self.assertRaises(InventoryInputError):
                    seal_strategy_execution_plan(value)

    def test_evidence_references_reject_every_ascii_control_character(self):
        for codepoint in (*range(0x20), 0x7F):
            with self.subTest(codepoint=codepoint):
                value = plan_body()
                value["shadow_challenger_bindings"][0]["approval_reference"] = (
                    f"APPROVAL{chr(codepoint)}REFERENCE"
                )
                with self.assertRaisesRegex(InventoryInputError, "INVALID_EVIDENCE_REFERENCE"):
                    seal_strategy_execution_plan(value)

    def test_bundle_is_stable_and_math_is_the_only_effective_result(self):
        plan = seal_strategy_execution_plan(plan_body())
        first_body = bundle_body(plan)
        second_body = copy.deepcopy(first_body)
        second_body["result_children"].reverse()
        second_body["comparisons"].reverse()

        first = seal_inventory_result_bundle(first_body, strategy_execution_plan=plan)
        second = seal_inventory_result_bundle(second_body, strategy_execution_plan=plan)

        self.assertEqual(first, second)
        self.assertEqual(first["effective_child_result_id"], "MATH-1")
        self.assertEqual(
            validate_inventory_result_bundle(first, strategy_execution_plan=plan), first
        )

    def test_bundle_id_and_hash_are_replay_stable_for_the_same_attempt(self):
        plan = seal_strategy_execution_plan(plan_body())

        first = seal_inventory_result_bundle(bundle_body(plan), strategy_execution_plan=plan)
        replay = seal_inventory_result_bundle(bundle_body(plan), strategy_execution_plan=plan)

        self.assertEqual(first["result_bundle_id"], replay["result_bundle_id"])
        self.assertEqual(first["content_hash"], replay["content_hash"])
        self.assertEqual(
            first["result_bundle_id"],
            derive_result_bundle_id(first["engine_run_id"], first["attempt_no"]),
        )
        self.assertEqual(
            first["result_bundle_id"],
            "IOB-a4bdec4e6b27a96c0a6bfafb3eb1eda3c7f05782a32824e12af1b1765a980fa8",
        )

    def test_arbitrary_bundle_id_is_rejected(self):
        plan = seal_strategy_execution_plan(plan_body())
        value = bundle_body(plan)
        value["result_bundle_id"] = "RANDOM-BUNDLE-ID"

        with self.assertRaisesRegex(
            InventoryInputError,
            "RESULT_BUNDLE_ID_DERIVATION_MISMATCH",
        ):
            seal_inventory_result_bundle(value, strategy_execution_plan=plan)

    def test_failed_ppo_is_isolated_and_kept_as_evidence(self):
        plan = seal_strategy_execution_plan(plan_body())
        bundle = seal_inventory_result_bundle(bundle_body(plan), strategy_execution_plan=plan)

        failed = next(
            row for row in bundle["result_children"] if row["child_result_id"] == "PPO-B-1"
        )
        comparison_row = next(
            row for row in bundle["comparisons"] if row["candidate_child_result_id"] == "PPO-B-1"
        )
        self.assertEqual(failed["status"], "FAILED")
        self.assertTrue(bundle["automatic_publish_allowed"])
        self.assertEqual(
            comparison_row["cost_delta"],
            unavailable("CANDIDATE_RESULT_FAILED"),
        )

    def test_unbound_cost_profile_has_explicit_not_available_reason(self):
        plan = seal_strategy_execution_plan(plan_body())
        bundle = seal_inventory_result_bundle(bundle_body(plan), strategy_execution_plan=plan)

        successful = [
            row for row in bundle["comparisons"] if row["candidate_child_result_id"] != "PPO-B-1"
        ]
        self.assertTrue(
            all(row["cost_delta"] == unavailable("COST_PROFILE_NOT_BOUND") for row in successful)
        )

    def test_result_bundle_v1_rejects_an_unclaimed_cost_profile(self):
        plan = seal_strategy_execution_plan(plan_body())
        value = bundle_body(plan)
        value["cost_profile_content_hash"] = "0" * 64

        with self.assertRaisesRegex(
            InventoryInputError,
            "RESULT_BUNDLE_COST_PROFILE_BINDING_UNSUPPORTED",
        ):
            seal_inventory_result_bundle(value, strategy_execution_plan=plan)

    def test_zero_demand_service_delta_stays_explicitly_unavailable(self):
        plan = seal_strategy_execution_plan(plan_body())
        value = bundle_body(plan)
        mathematical = next(
            row for row in value["comparisons"] if row["candidate_child_result_id"] == "MATH-1"
        )
        mathematical["service_level_delta"] = unavailable("NO_DEMAND_IN_HORIZON")

        bundle = seal_inventory_result_bundle(value, strategy_execution_plan=plan)

        self.assertEqual(
            next(
                row for row in bundle["comparisons"] if row["candidate_child_result_id"] == "MATH-1"
            )["service_level_delta"],
            unavailable("NO_DEMAND_IN_HORIZON"),
        )

    def test_effective_pointer_and_model_hash_fail_closed(self):
        plan = seal_strategy_execution_plan(plan_body())
        for mutate, code in (
            (
                lambda value: value.update(effective_child_result_id="PPO-A-1"),
                "RESULT_BUNDLE_EFFECTIVE_POINTER_INVALID",
            ),
            (
                lambda value: next(
                    row for row in value["result_children"] if row["child_result_id"] == "PPO-A-1"
                ).update(model_content_hash="0" * 64),
                "RESULT_BUNDLE_CHALLENGER_BINDING_MISMATCH",
            ),
        ):
            with self.subTest(code=code):
                value = bundle_body(plan)
                mutate(value)
                with self.assertRaisesRegex(InventoryInputError, code):
                    seal_inventory_result_bundle(value, strategy_execution_plan=plan)

    def test_hash_tampering_is_rejected(self):
        plan = seal_strategy_execution_plan(plan_body())
        bundle = seal_inventory_result_bundle(bundle_body(plan), strategy_execution_plan=plan)
        bundle["content_hash"] = "0" * 64

        with self.assertRaisesRegex(InventoryInputError, "RESULT_BUNDLE_HASH_MISMATCH"):
            validate_inventory_result_bundle(bundle, strategy_execution_plan=plan)

    def test_complex_result_bundle_literal_golden_hash_and_decimal_normalization(self):
        plan = seal_strategy_execution_plan(plan_body())
        value = bundle_body(plan)
        successful = [
            row for row in value["comparisons"] if row["candidate_child_result_id"] != "PPO-B-1"
        ]
        successful[0]["service_level_delta"]["value"] = "0.0100"
        successful[0]["backorder_qty_delta"]["value"] = "-10.000000"
        successful[1]["service_level_delta"]["value"] = "0.020000"
        successful[1]["backorder_qty_delta"]["value"] = "0.000000"
        successful[2]["service_level_delta"]["value"] = "-0.030000"
        successful[2]["backorder_qty_delta"]["value"] = "25.500000"

        bundle = seal_inventory_result_bundle(value, strategy_execution_plan=plan)

        normalized = {row["candidate_child_result_id"]: row for row in bundle["comparisons"]}
        self.assertEqual(normalized["MATH-1"]["service_level_delta"]["value"], "0.01")
        self.assertEqual(normalized["MATH-1"]["backorder_qty_delta"]["value"], "-10")
        self.assertEqual(normalized["PPO-A-1"]["backorder_qty_delta"]["value"], "0")
        self.assertEqual(normalized["STRESS-1"]["backorder_qty_delta"]["value"], "25.5")
        self.assertEqual(
            bundle["content_hash"],
            "1319072ea8597ff2a50d6c7e30036b1ecdd296265b1fc716773d2e092dd218b8",
        )

    def test_display_codes_are_derived_not_stored_contract_fields(self):
        plan = seal_strategy_execution_plan(plan_body())
        bundle = seal_inventory_result_bundle(bundle_body(plan), strategy_execution_plan=plan)
        labels = {
            row["child_result_id"]: result_display_code(row) for row in bundle["result_children"]
        }

        self.assertEqual(labels["BASELINE-1"], "BASELINE_PSI")
        self.assertEqual(labels["MATH-1"], "MATHEMATICAL_RECOMMENDED_PSI")
        self.assertEqual(labels["PPO-A-1"], "PPO_SHADOW_RECOMMENDED_PSI")
        self.assertEqual(labels["STRESS-1"], "STRESS_LATE_SUPPLY_PSI")

    def test_bundle_is_bound_to_the_exact_runtime_attempt(self):
        plan = seal_strategy_execution_plan(plan_body())
        dispatch = runtime_dispatch_v2(
            config_hash="a" * 64,
            effective_policy_content_hash="b" * 64,
            expected_automatic_publish_allowed=True,
            expected_automatic_order_allowed=False,
        )
        dispatch["claim"]["strategy_execution_plan"] = plan
        request = InventoryRuntimeExecutionRequest.from_dict(reseal_runtime_site_binding(dispatch))
        value = bundle_body(plan)
        value["site_binding_hash"] = request.value["claim"]["site_binding_hash"]
        bundle = seal_inventory_result_bundle(value, strategy_execution_plan=plan)

        self.assertEqual(
            validate_result_bundle_runtime_binding(
                request,
                bundle,
                expected_canonical_input_hash="8" * 64,
            ),
            bundle,
        )

        with self.assertRaisesRegex(
            InventoryInputError,
            "RUNTIME_RESULT_BUNDLE_CANONICAL_INPUT_MISMATCH",
        ):
            validate_result_bundle_runtime_binding(
                request,
                bundle,
                expected_canonical_input_hash="0" * 64,
            )

        different_attempt = bundle_body(plan)
        different_attempt["engine_run_id"] = "10000000-0000-4000-8000-000000000099"
        different_attempt["result_bundle_id"] = derive_result_bundle_id(
            different_attempt["engine_run_id"],
            different_attempt["attempt_no"],
        )
        different_attempt["site_binding_hash"] = request.value["claim"]["site_binding_hash"]
        sealed = seal_inventory_result_bundle(different_attempt, strategy_execution_plan=plan)
        with self.assertRaisesRegex(
            InventoryInputError,
            "RUNTIME_RESULT_BUNDLE_ATTEMPT_MISMATCH",
        ):
            validate_result_bundle_runtime_binding(
                request,
                sealed,
                expected_canonical_input_hash="8" * 64,
            )


if __name__ == "__main__":
    unittest.main()

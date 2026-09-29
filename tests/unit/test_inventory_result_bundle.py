"""Execution-plan and Result Bundle semantic tests."""

import copy
import unittest

from dsio_inventory_engine.inventory_contracts.result_bundle import (
    BASE_SCENARIO_CONTENT_HASH,
    BASE_SCENARIO_CONTRACT_KEY,
    BASE_SCENARIO_CONTRACT_VERSION,
    PSI_SIMULATOR_IMPLEMENTATION_ID,
    PSI_SIMULATOR_IMPLEMENTATION_VERSION,
    derive_result_bundle_id,
    derive_psi_artifact_references,
    derive_psi_child_result_id,
    derive_stress_scenario_content_hash,
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
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from tests.support.runtime_fixtures import reseal_runtime_site_binding, runtime_dispatch_v2


ENGINE_RUN_ID = "10000000-0000-4000-8000-000000000001"
ATTEMPT_NO = 1
CANONICAL_INPUT_HASH = "8" * 64
PSI_SIMULATOR_IMPLEMENTATION_HASH = "5" * 64
STRESS_PAYLOAD = {"delay_weeks": 1}
STRESS_PAYLOAD_CONTENT_HASH = digest(STRESS_PAYLOAD)
STRESS_SCENARIO_CONTENT_HASH = derive_stress_scenario_content_hash(
    scenario_id="LATE-SUPPLY",
    scenario_contract_key="inventory.stress_scenario",
    scenario_contract_version="1.0.0",
    scenario_payload_content_hash=STRESS_PAYLOAD_CONTENT_HASH,
    deterministic_seed=17,
)


def expected_child_id(
    *,
    kind: str,
    role: str,
    strategy: str,
    scenario_id: str = "BASE",
    scenario_hash: str = BASE_SCENARIO_CONTENT_HASH,
    challenger_id: str | None = None,
    model_hash: str | None = None,
    engine_run_id: str = ENGINE_RUN_ID,
) -> str:
    return derive_psi_child_result_id(
        engine_run_id=engine_run_id,
        attempt_no=ATTEMPT_NO,
        canonical_input_hash=CANONICAL_INPUT_HASH,
        result_kind=kind,
        execution_role=role,
        strategy_type=strategy,
        scenario_id=scenario_id,
        scenario_content_hash=scenario_hash,
        challenger_id=challenger_id,
        model_content_hash=model_hash,
    )


BASELINE_ID = expected_child_id(kind="BASELINE_PSI", role="EVIDENCE_ONLY", strategy="NONE")
MATH_ID = expected_child_id(kind="RECOMMENDED_PSI", role="OPERATIONAL", strategy="MATHEMATICAL")
PPO_A_ID = expected_child_id(
    kind="RECOMMENDED_PSI",
    role="SHADOW",
    strategy="DEEP_RL",
    challenger_id="ppo-a",
    model_hash="1" * 64,
)
PPO_B_ID = expected_child_id(
    kind="RECOMMENDED_PSI",
    role="SHADOW",
    strategy="DEEP_RL",
    challenger_id="ppo-b",
    model_hash="e" * 64,
)
STRESS_ID = expected_child_id(
    kind="STRESS_PSI",
    role="EVIDENCE_ONLY",
    strategy="MATHEMATICAL",
    scenario_id="LATE-SUPPLY",
    scenario_hash=STRESS_SCENARIO_CONTENT_HASH,
)


def plan_body() -> dict:
    return {
        "contract_id": "inventory-strategy-execution-plan-v1",
        "contract_version": "1.1.0",
        "strategy_execution_plan_id": "IO-PLAN-1",
        "classification_config_hash": "a" * 64,
        "effective_policy_content_hash": "b" * 64,
        "base_scenario_contract_key": BASE_SCENARIO_CONTRACT_KEY,
        "base_scenario_contract_version": BASE_SCENARIO_CONTRACT_VERSION,
        "base_scenario_content_hash": BASE_SCENARIO_CONTENT_HASH,
        "psi_simulator": {
            "implementation_id": PSI_SIMULATOR_IMPLEMENTATION_ID,
            "version": PSI_SIMULATOR_IMPLEMENTATION_VERSION,
            "implementation_content_hash": PSI_SIMULATOR_IMPLEMENTATION_HASH,
        },
        "operational_strategy": {
            "strategy_type": "MATHEMATICAL",
            "implementation_id": "historical-normal-r-s",
            "version": "1.0.0",
            "implementation_content_hash": "c" * 64,
            "replenishment_config_content_hash": "7" * 64,
            "strategy_input_binding": {
                "contract_id": "io-mathematical-policy-input-v1",
                "contract_version": "1.0.0",
                "snapshot_id": "MATH-POLICY-INPUT-1",
                "content_hash": "8" * 64,
            },
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
                "scenario_payload_content_hash": STRESS_PAYLOAD_CONTENT_HASH,
                "deterministic_seed": 17,
                "scenario_content_hash": STRESS_SCENARIO_CONTENT_HASH,
                "runner_implementation_id": "inventory.stress.late_supply",
                "runner_implementation_version": "1.0.0",
                "runner_implementation_content_hash": "9" * 64,
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


def actions(child_id: str, *, engine_run_id: str) -> dict:
    references = derive_psi_artifact_references(
        engine_run_id=engine_run_id,
        attempt_no=ATTEMPT_NO,
        child_result_id=child_id,
    )
    return {
        "raw_action_reference": references["raw_action"],
        "raw_action_content_hash": "3" * 64,
        "constrained_action_reference": references["constrained_action"],
        "constrained_action_content_hash": "4" * 64,
        "adjustment_reasons_reference": references["adjustment_reasons"],
        "adjustment_reasons_content_hash": "5" * 64,
    }


def child(
    *,
    engine_run_id: str,
    kind: str,
    role: str,
    strategy: str,
    scenario_id: str = "BASE",
    scenario_hash: str = BASE_SCENARIO_CONTENT_HASH,
    challenger_id: str | None = None,
    model_hash: str | None = None,
    status: str = "SUCCEEDED",
) -> dict:
    succeeded = status == "SUCCEEDED"
    child_id = expected_child_id(
        engine_run_id=engine_run_id,
        kind=kind,
        role=role,
        strategy=strategy,
        scenario_id=scenario_id,
        scenario_hash=scenario_hash,
        challenger_id=challenger_id,
        model_hash=model_hash,
    )
    references = derive_psi_artifact_references(
        engine_run_id=engine_run_id,
        attempt_no=ATTEMPT_NO,
        child_result_id=child_id,
    )
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
        "artifact_reference": references["psi"] if succeeded else None,
        "artifact_contract_key": "inventory.psi_result" if succeeded else None,
        "artifact_contract_version": "1.0.0" if succeeded else None,
        "child_content_hash": "7" * 64 if succeeded else None,
        "row_count": 26 if succeeded else None,
        "failure_reason_code": None if succeeded else "PPO_INFERENCE_FAILED",
        "action_evidence": no_actions()
        if strategy == "NONE" or not succeeded
        else actions(child_id, engine_run_id=engine_run_id),
    }


def available(value: str) -> dict:
    return {"status": "AVAILABLE", "value": value, "reason_code": None}


def unavailable(reason: str) -> dict:
    return {"status": "NOT_AVAILABLE", "value": None, "reason_code": reason}


def comparison(candidate_id: str, *, failed: bool = False, baseline_id: str) -> dict:
    reason = "CANDIDATE_RESULT_FAILED"
    return {
        "baseline_child_result_id": baseline_id,
        "candidate_child_result_id": candidate_id,
        "cost_delta": unavailable(reason if failed else "COST_PROFILE_NOT_BOUND"),
        "service_level_delta": unavailable(reason) if failed else available("0.01"),
        "backorder_qty_delta": unavailable(reason) if failed else available("-10"),
    }


def bundle_body(plan: dict, *, engine_run_id: str = ENGINE_RUN_ID) -> dict:
    attempt_no = ATTEMPT_NO
    children = [
        child(
            engine_run_id=engine_run_id,
            kind="BASELINE_PSI",
            role="EVIDENCE_ONLY",
            strategy="NONE",
        ),
        child(
            engine_run_id=engine_run_id,
            kind="RECOMMENDED_PSI",
            role="OPERATIONAL",
            strategy="MATHEMATICAL",
        ),
        child(
            engine_run_id=engine_run_id,
            kind="RECOMMENDED_PSI",
            role="SHADOW",
            strategy="DEEP_RL",
            challenger_id="ppo-a",
            model_hash="1" * 64,
        ),
        child(
            engine_run_id=engine_run_id,
            kind="RECOMMENDED_PSI",
            role="SHADOW",
            strategy="DEEP_RL",
            challenger_id="ppo-b",
            model_hash="e" * 64,
            status="FAILED",
        ),
        child(
            engine_run_id=engine_run_id,
            kind="STRESS_PSI",
            role="EVIDENCE_ONLY",
            strategy="MATHEMATICAL",
            scenario_id="LATE-SUPPLY",
            scenario_hash=STRESS_SCENARIO_CONTENT_HASH,
        ),
    ]
    baseline_id = children[0]["child_result_id"]
    mathematical_id = children[1]["child_result_id"]
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
        "canonical_input_hash": CANONICAL_INPUT_HASH,
        "site_binding_hash": "9" * 64,
        "strategy_execution_plan_id": plan["strategy_execution_plan_id"],
        "strategy_execution_plan_content_hash": plan["content_hash"],
        "classification_config_hash": plan["classification_config_hash"],
        "effective_policy_content_hash": plan["effective_policy_content_hash"],
        "cost_profile_content_hash": None,
        "result_children": children,
        "effective_child_result_id": mathematical_id,
        "comparisons": [
            comparison(mathematical_id, baseline_id=baseline_id),
            comparison(children[2]["child_result_id"], baseline_id=baseline_id),
            comparison(children[3]["child_result_id"], failed=True, baseline_id=baseline_id),
            comparison(children[4]["child_result_id"], baseline_id=baseline_id),
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
            "7ac2889113dbe8816df0fd5c751dcdcbbb9dee59fab14c8ce321e2c9b1063f02",
        )

    def test_plan_rejects_an_arbitrary_base_scenario_or_simulator_identity(self):
        for mutate in (
            lambda value: value.update(base_scenario_content_hash="0" * 64),
            lambda value: value["psi_simulator"].update(
                implementation_id="inventory.psi.unclaimed"
            ),
        ):
            with self.subTest(mutate=mutate):
                value = plan_body()
                mutate(value)
                with self.assertRaisesRegex(InventoryInputError, "INVALID_CODE"):
                    seal_strategy_execution_plan(value)

    def test_plan_rejects_a_stress_hash_not_derived_from_payload_and_seed(self):
        value = plan_body()
        value["stress_scenario_bindings"][0]["scenario_content_hash"] = "0" * 64

        with self.assertRaisesRegex(
            InventoryInputError,
            "STRESS_SCENARIO_CONTENT_HASH_MISMATCH",
        ):
            seal_strategy_execution_plan(value)

    def test_cross_repository_plan_golden_hash(self):
        plan = seal_strategy_execution_plan(
            {
                "contract_id": "inventory-strategy-execution-plan-v1",
                "contract_version": "1.1.0",
                "strategy_execution_plan_id": "strategy-plan-1",
                "classification_config_hash": "a" * 64,
                "effective_policy_content_hash": "e" * 64,
                "base_scenario_contract_key": BASE_SCENARIO_CONTRACT_KEY,
                "base_scenario_contract_version": BASE_SCENARIO_CONTRACT_VERSION,
                "base_scenario_content_hash": BASE_SCENARIO_CONTENT_HASH,
                "psi_simulator": {
                    "implementation_id": PSI_SIMULATOR_IMPLEMENTATION_ID,
                    "version": PSI_SIMULATOR_IMPLEMENTATION_VERSION,
                    "implementation_content_hash": "4" * 64,
                },
                "operational_strategy": {
                    "strategy_type": "MATHEMATICAL",
                    "implementation_id": "historical-normal-r-s",
                    "version": "1.0.0",
                    "implementation_content_hash": "1" * 64,
                    "replenishment_config_content_hash": "2" * 64,
                    "strategy_input_binding": {
                        "contract_id": "io-mathematical-policy-input-v1",
                        "contract_version": "1.0.0",
                        "snapshot_id": "MATH-POLICY-INPUT-1",
                        "content_hash": "3" * 64,
                    },
                },
                "shadow_challenger_bindings": [],
                "stress_scenario_bindings": [],
                "result_bundle_contract_key": "inventory.result_bundle",
                "result_bundle_contract_version": "1.0.0",
            }
        )

        self.assertEqual(
            plan["content_hash"],
            "3eecb856cf7fd0eb5219b656e40e73f7060a2e7300f7674092d57427355092c3",
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

    def test_challenger_approval_identifier_rejects_every_ascii_control_character(self):
        for codepoint in (*range(0x20), 0x7F):
            with self.subTest(codepoint=codepoint):
                value = plan_body()
                value["shadow_challenger_bindings"][0]["approval_reference"] = (
                    f"APPROVAL{chr(codepoint)}REFERENCE"
                )
                with self.assertRaisesRegex(InventoryInputError, "INVALID_IDENTIFIER"):
                    seal_strategy_execution_plan(value)

    def test_challenger_approval_reference_matches_model_approval_identifier_contract(self):
        value = plan_body()
        value["shadow_challenger_bindings"][0]["approval_reference"] = "CAB / Approval 1"

        with self.assertRaisesRegex(InventoryInputError, "INVALID_IDENTIFIER"):
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
        self.assertEqual(first["effective_child_result_id"], MATH_ID)
        self.assertEqual(
            validate_inventory_result_bundle(first, strategy_execution_plan=plan), first
        )

    def test_optional_child_status_cannot_hide_failures_as_skips(self):
        plan = seal_strategy_execution_plan(plan_body())
        value = bundle_body(plan)
        ppo_failure = next(
            row
            for row in value["result_children"]
            if row["strategy_type"] == "DEEP_RL" and row["status"] == "FAILED"
        )
        ppo_failure["status"] = "SKIPPED"
        comparison_row = next(
            row
            for row in value["comparisons"]
            if row["candidate_child_result_id"] == ppo_failure["child_result_id"]
        )
        for metric in ("cost_delta", "service_level_delta", "backorder_qty_delta"):
            comparison_row[metric] = unavailable("CANDIDATE_RESULT_SKIPPED")

        with self.assertRaisesRegex(InventoryInputError, "PPO_CHILD_SKIP_FORBIDDEN"):
            seal_inventory_result_bundle(value, strategy_execution_plan=plan)

    def test_stress_unavailable_is_the_only_skipped_optional_child_reason(self):
        plan = seal_strategy_execution_plan(plan_body())
        value = bundle_body(plan)
        stress = next(row for row in value["result_children"] if row["result_kind"] == "STRESS_PSI")
        stress.update(
            status="SKIPPED",
            artifact_reference=None,
            artifact_contract_key=None,
            artifact_contract_version=None,
            child_content_hash=None,
            row_count=None,
            failure_reason_code="STRESS_EXECUTION_FAILED",
            action_evidence=no_actions(),
        )

        with self.assertRaisesRegex(
            InventoryInputError,
            "STRESS_CHILD_FAILURE_STATUS_INVALID",
        ):
            seal_inventory_result_bundle(value, strategy_execution_plan=plan)

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

    def test_child_identity_and_logical_artifact_references_are_derived(self):
        plan = seal_strategy_execution_plan(plan_body())
        for mutate, code in (
            (
                lambda child: child.update(child_result_id="FORGED-CHILD"),
                "RESULT_BUNDLE_CHILD_ID_DERIVATION_MISMATCH",
            ),
            (
                lambda child: child.update(artifact_reference="artifact:forged"),
                "RESULT_BUNDLE_CHILD_ARTIFACT_REFERENCE_MISMATCH",
            ),
            (
                lambda child: child["action_evidence"].update(
                    raw_action_reference="artifact:forged:raw"
                ),
                "RESULT_BUNDLE_ACTION_REFERENCE_MISMATCH",
            ),
        ):
            with self.subTest(code=code):
                value = bundle_body(plan)
                challenger = next(
                    row for row in value["result_children"] if row["child_result_id"] == PPO_A_ID
                )
                mutate(challenger)
                with self.assertRaisesRegex(InventoryInputError, code):
                    seal_inventory_result_bundle(value, strategy_execution_plan=plan)

    def test_failed_child_cannot_claim_artifacts_or_actions(self):
        plan = seal_strategy_execution_plan(plan_body())
        value = bundle_body(plan)
        failed = next(row for row in value["result_children"] if row["child_result_id"] == PPO_B_ID)
        references = derive_psi_artifact_references(
            engine_run_id=ENGINE_RUN_ID,
            attempt_no=ATTEMPT_NO,
            child_result_id=PPO_B_ID,
        )
        failed.update(
            artifact_reference=references["psi"],
            artifact_contract_key="inventory.psi_result",
            artifact_contract_version="1.0.0",
            child_content_hash="7" * 64,
            row_count=1,
            action_evidence={
                "raw_action_reference": references["raw_action"],
                "raw_action_content_hash": "3" * 64,
                "constrained_action_reference": references["constrained_action"],
                "constrained_action_content_hash": "4" * 64,
                "adjustment_reasons_reference": references["adjustment_reasons"],
                "adjustment_reasons_content_hash": "5" * 64,
            },
        )

        with self.assertRaisesRegex(
            InventoryInputError, "RESULT_BUNDLE_FAILED_CHILD_ARTIFACT_FORBIDDEN"
        ):
            seal_inventory_result_bundle(value, strategy_execution_plan=plan)

    def test_succeeded_child_cannot_claim_an_empty_artifact(self):
        plan = seal_strategy_execution_plan(plan_body())
        value = bundle_body(plan)
        mathematical = next(
            row for row in value["result_children"] if row["execution_role"] == "OPERATIONAL"
        )
        mathematical["row_count"] = 0

        with self.assertRaisesRegex(
            InventoryInputError,
            "CHILD_RESULT_EMPTY_SUCCESS_FORBIDDEN",
        ):
            seal_inventory_result_bundle(value, strategy_execution_plan=plan)

    def test_failed_ppo_is_isolated_and_kept_as_evidence(self):
        plan = seal_strategy_execution_plan(plan_body())
        bundle = seal_inventory_result_bundle(bundle_body(plan), strategy_execution_plan=plan)

        failed = next(
            row for row in bundle["result_children"] if row["child_result_id"] == PPO_B_ID
        )
        comparison_row = next(
            row for row in bundle["comparisons"] if row["candidate_child_result_id"] == PPO_B_ID
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
            row for row in bundle["comparisons"] if row["candidate_child_result_id"] != PPO_B_ID
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
            row for row in value["comparisons"] if row["candidate_child_result_id"] == MATH_ID
        )
        mathematical["service_level_delta"] = unavailable("NO_DEMAND_IN_HORIZON")

        bundle = seal_inventory_result_bundle(value, strategy_execution_plan=plan)

        self.assertEqual(
            next(
                row for row in bundle["comparisons"] if row["candidate_child_result_id"] == MATH_ID
            )["service_level_delta"],
            unavailable("NO_DEMAND_IN_HORIZON"),
        )

    def test_effective_pointer_and_model_hash_fail_closed(self):
        plan = seal_strategy_execution_plan(plan_body())
        for mutate, code in (
            (
                lambda value: value.update(effective_child_result_id=PPO_A_ID),
                "RESULT_BUNDLE_EFFECTIVE_POINTER_INVALID",
            ),
            (
                lambda value: next(
                    row for row in value["result_children"] if row["child_result_id"] == PPO_A_ID
                ).update(model_content_hash="0" * 64),
                "RESULT_BUNDLE_CHILD_ID_DERIVATION_MISMATCH",
            ),
        ):
            with self.subTest(code=code):
                value = bundle_body(plan)
                mutate(value)
                with self.assertRaisesRegex(InventoryInputError, code):
                    seal_inventory_result_bundle(value, strategy_execution_plan=plan)

    def test_stress_child_is_exactly_the_plan_bound_mathematical_evidence(self):
        plan = seal_strategy_execution_plan(plan_body())
        value = bundle_body(plan)
        forged = child(
            engine_run_id=ENGINE_RUN_ID,
            kind="STRESS_PSI",
            role="EVIDENCE_ONLY",
            strategy="DEEP_RL",
            scenario_id="LATE-SUPPLY",
            scenario_hash=STRESS_SCENARIO_CONTENT_HASH,
            challenger_id="ppo-a",
            model_hash="1" * 64,
            status="SUCCEEDED",
        )
        value["result_children"].append(forged)
        value["comparisons"].append(
            comparison(
                forged["child_result_id"],
                failed=False,
                baseline_id=BASELINE_ID,
            )
        )

        with self.assertRaisesRegex(InventoryInputError, "STRESS_RESULT_ROLE_INVALID"):
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
            row for row in value["comparisons"] if row["candidate_child_result_id"] != PPO_B_ID
        ]
        successful[0]["service_level_delta"]["value"] = "0.0100"
        successful[0]["backorder_qty_delta"]["value"] = "-10.000000"
        successful[1]["service_level_delta"]["value"] = "0.020000"
        successful[1]["backorder_qty_delta"]["value"] = "0.000000"
        successful[2]["service_level_delta"]["value"] = "-0.030000"
        successful[2]["backorder_qty_delta"]["value"] = "25.500000"

        bundle = seal_inventory_result_bundle(value, strategy_execution_plan=plan)

        normalized = {row["candidate_child_result_id"]: row for row in bundle["comparisons"]}
        self.assertEqual(normalized[MATH_ID]["service_level_delta"]["value"], "0.01")
        self.assertEqual(normalized[MATH_ID]["backorder_qty_delta"]["value"], "-10")
        self.assertEqual(normalized[PPO_A_ID]["backorder_qty_delta"]["value"], "0")
        self.assertEqual(normalized[STRESS_ID]["backorder_qty_delta"]["value"], "25.5")
        self.assertEqual(
            bundle["content_hash"],
            "00732351e0d2abb3d7722b90c265f29a236e1e714f4d0e0bd53eadefa63211f9",
        )

    def test_bundle_delta_uses_the_aggregate_not_per_row_quantity_range(self):
        plan = seal_strategy_execution_plan(plan_body())
        value = bundle_body(plan)
        value["comparisons"][0]["backorder_qty_delta"]["value"] = "1200000000000.000000"

        bundle = seal_inventory_result_bundle(value, strategy_execution_plan=plan)

        comparison_by_child = {
            row["candidate_child_result_id"]: row for row in bundle["comparisons"]
        }
        self.assertEqual(
            comparison_by_child[MATH_ID]["backorder_qty_delta"]["value"],
            "1200000000000",
        )

    def test_bundle_delta_rejects_values_above_the_aggregate_contract_limit(self):
        plan = seal_strategy_execution_plan(plan_body())
        value = bundle_body(plan)
        value["comparisons"][0]["backorder_qty_delta"]["value"] = "200000000000000000001"

        with self.assertRaisesRegex(
            InventoryInputError,
            "RESULT_METRIC_QUANTITY_RANGE",
        ):
            seal_inventory_result_bundle(value, strategy_execution_plan=plan)

    def test_display_codes_are_derived_not_stored_contract_fields(self):
        plan = seal_strategy_execution_plan(plan_body())
        bundle = seal_inventory_result_bundle(bundle_body(plan), strategy_execution_plan=plan)
        labels = {
            row["child_result_id"]: result_display_code(row) for row in bundle["result_children"]
        }

        self.assertEqual(labels[BASELINE_ID], "BASELINE_PSI")
        self.assertEqual(labels[MATH_ID], "MATHEMATICAL_RECOMMENDED_PSI")
        self.assertEqual(labels[PPO_A_ID], "PPO_SHADOW_RECOMMENDED_PSI")
        self.assertEqual(labels[STRESS_ID], "STRESS_LATE_SUPPLY_PSI")

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

        different_attempt = bundle_body(
            plan,
            engine_run_id="10000000-0000-4000-8000-000000000099",
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

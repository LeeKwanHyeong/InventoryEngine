"""Offline orchestration tests for one canonical PSI Result Bundle attempt."""

from __future__ import annotations

import copy
import unittest
from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch

from dsio_inventory_engine.inventory_contracts.mathematical import (
    MATH_DESCRIPTOR,
    MathematicalPolicyRequest,
)
from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.result_bundle import (
    PSI_SIMULATOR_IMPLEMENTATION_ID,
    PSI_SIMULATOR_IMPLEMENTATION_VERSION,
    derive_stress_scenario_content_hash,
    seal_strategy_execution_plan,
)
from dsio_inventory_engine.inventory_contracts.replenishment import (
    replenishment_config_content_hash,
)
from dsio_inventory_engine.inventory_contracts.runtime import InventoryRuntimeExecutionRequest
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    canonical_json,
    digest,
)
from dsio_inventory_engine.prepare_inventory.application.inventory_input import (
    PrepareInventoryInputUseCase,
    PreparedInventoryInput,
)
from dsio_inventory_engine.recommend_replenishment.application.run import (
    apply_effective_policy_controls as apply_real_effective_policy_controls,
)
from dsio_inventory_engine.run_inventory.psi_orchestrator import (
    PsiRunCommand,
    ResolvedPpoChallenger,
    ResolvedStressScenario,
    RunPsiBundleUseCase,
    _validate_stress_prepared_projection,
)
from dsio_inventory_engine.run_inventory.simulation_registry import (
    DevelopmentSimulationRegistry,
)
from tests.support.learned_fixtures import model_fixture
from tests.support.math_fixtures import reseal_math
from tests.support.runtime_fixtures import reseal_runtime_site_binding
from tests.unit.test_runtime_effective_policy_v2 import DEPLOYMENT, _requests


_MATH_IMPLEMENTATION_HASH = "d" * 64
_PPO_IMPLEMENTATION_HASH = "e" * 64
_STRESS_IMPLEMENTATION_HASH = "4" * 64
_PSI_SIMULATOR_IMPLEMENTATION_HASH = "5" * 64
_STRESS_PAYLOAD = {"demand_multiplier": "2"}
_STRESS_PAYLOAD_CONTENT_HASH = digest(_STRESS_PAYLOAD)
_STRESS_SEED = 202640
_STRESS_SCENARIO_CONTENT_HASH = derive_stress_scenario_content_hash(
    scenario_id="DEMAND_X2",
    scenario_contract_key="inventory.stress.demand_multiplier",
    scenario_contract_version="1.0.0",
    scenario_payload_content_hash=_STRESS_PAYLOAD_CONTENT_HASH,
    deterministic_seed=_STRESS_SEED,
)


class _CountingPrepare:
    def __init__(self) -> None:
        self.calls = 0
        self.delegate = PrepareInventoryInputUseCase(DEPLOYMENT)

    def execute(self, request):
        self.calls += 1
        return self.delegate.execute(request)


class _ResealedTamperingPrepare:
    def __init__(self) -> None:
        self.delegate = PrepareInventoryInputUseCase(DEPLOYMENT)

    def execute(self, request):
        value = self.delegate.execute(request).to_dict()
        value["positions"][0]["available_qty"] = "999"
        unsigned_manifest = {
            key: row for key, row in value["manifest"].items() if key != "prepared_content_hash"
        }
        value["manifest"]["prepared_content_hash"] = digest(
            {
                "manifest": unsigned_manifest,
                **{key: row for key, row in value.items() if key != "manifest"},
            }
        )
        return PreparedInventoryInput(canonical_json(value))


class _TimeVaryingPrepared(PreparedInventoryInput):
    def to_dict(self):
        value = super().to_dict()
        calls = getattr(self, "calls", 0) + 1
        object.__setattr__(self, "calls", calls)
        if calls > 1:
            value["positions"][0]["available_qty"] = "999"
        return value


class _TimeVaryingPrepare:
    def __init__(self) -> None:
        self.delegate = PrepareInventoryInputUseCase(DEPLOYMENT)
        self.candidate = None

    def execute(self, request):
        prepared = self.delegate.execute(request)
        self.candidate = _TimeVaryingPrepared(prepared.document_json)
        return self.candidate


class _DemandShockRunner:
    def __init__(self) -> None:
        self.calls = 0

    def transform(
        self,
        *,
        prepared_input,
        scenario_payload,
        deterministic_seed,
    ):
        self.calls += 1
        self.seed = deterministic_seed
        stressed = prepared_input.to_dict()
        multiplier = Decimal(scenario_payload["demand_multiplier"])
        for demand in stressed["demands"]:
            net = Decimal(demand["net_forecast_qty"]) * multiplier
            consumed = demand["forecast_consumed_qty"]
            demand["net_forecast_qty"] = str(net)
            if consumed is not None:
                demand["gross_forecast_qty"] = str(net + Decimal(consumed))
        return _reseal_prepared(stressed)


class _DifferentUniverseStressRunner(_DemandShockRunner):
    def transform(self, **kwargs):
        result = super().transform(**kwargs).to_dict()
        for row in result["demands"]:
            row["item_id"] = "ITEM-OUTSIDE"
        return _reseal_prepared(result)


class _InvalidDemandNettingStressRunner(_DemandShockRunner):
    def transform(self, **kwargs):
        result = super().transform(**kwargs).to_dict()
        result["demands"][0]["gross_forecast_qty"] = "999"
        return _reseal_prepared(result)


class _FailingStressRunner:
    def transform(self, **kwargs):
        raise RuntimeError("deterministic stress failure")


class _ReservedReasonStressRunner:
    def transform(self, **kwargs):
        raise InventoryInputError("STRESS_SCENARIO_UNAVAILABLE")


class _ImmutableSectionMutatingStressRunner(_DemandShockRunner):
    def transform(self, **kwargs):
        result = super().transform(**kwargs).to_dict()
        result["positions"][0]["available_qty"] = "999"
        return _reseal_prepared(result)


class _RogueFullResultStressRunner:
    def transform(self, **kwargs):
        return {
            "execution": {"strategy": MATH_DESCRIPTOR},
            "psi_rows": [],
            "decision_evidence": [],
        }


class _CrossInstanceStateStressRunner(_DemandShockRunner):
    calls = 0

    def transform(self, **kwargs):
        type(self).calls += 1
        kwargs["scenario_payload"] = {"demand_multiplier": str(type(self).calls + 1)}
        return super().transform(**kwargs)


class _SharedRunnerFactory:
    def __init__(self, runner):
        self.runner = runner

    def __call__(self):
        return self.runner


def _reseal_prepared(value: dict) -> PreparedInventoryInput:
    value["manifest"]["receipt_decisions_hash"] = digest(value["receipt_decisions"])
    unsigned_manifest = {
        key: row for key, row in value["manifest"].items() if key != "prepared_content_hash"
    }
    value["manifest"]["prepared_content_hash"] = digest(
        {
            "manifest": unsigned_manifest,
            **{key: row for key, row in value.items() if key != "manifest"},
        }
    )
    return PreparedInventoryInput(canonical_json(value))


def _bound_command(
    *,
    stress_runner=None,
    include_stress=False,
    action="REVIEW",
    approval="HIGH_VALUE",
    development_registry: bool = False,
) -> PsiRunCommand:
    mathematical, runtime = _requests(
        action=action,
        approval=approval,
        p90_days="7",
    )
    model = model_fixture(mathematical.to_dict(), "DEEP_RL", action=3)
    runtime_data = runtime.to_dict()
    execution = mathematical.to_dict()["recommendation"]["execution"]
    plan = {
        key: copy.deepcopy(value)
        for key, value in runtime.strategy_execution_plan.items()
        if key != "content_hash"
    }
    plan["operational_strategy"] = {
        "strategy_type": MATH_DESCRIPTOR["strategy_type"],
        "implementation_id": MATH_DESCRIPTOR["implementation_id"],
        "version": MATH_DESCRIPTOR["version"],
        "implementation_content_hash": _MATH_IMPLEMENTATION_HASH,
        "replenishment_config_content_hash": replenishment_config_content_hash(execution),
        "strategy_input_binding": execution["strategy_input_binding"],
    }
    plan["psi_simulator"] = {
        "implementation_id": PSI_SIMULATOR_IMPLEMENTATION_ID,
        "version": PSI_SIMULATOR_IMPLEMENTATION_VERSION,
        "implementation_content_hash": _PSI_SIMULATOR_IMPLEMENTATION_HASH,
    }
    plan["shadow_challenger_bindings"] = [
        {
            "challenger_id": "ppo-shadow-1",
            "strategy_type": "DEEP_RL",
            "algorithm": "PPO",
            "implementation_id": model.descriptor["implementation_id"],
            "version": model.descriptor["version"],
            "implementation_content_hash": _PPO_IMPLEMENTATION_HASH,
            "model": model.reference,
            "approval_reference": "MODEL-APPROVAL-1",
        }
    ]
    registry = DevelopmentSimulationRegistry() if development_registry else None
    plan["stress_scenario_bindings"] = (
        list(registry.stress_scenario_bindings())
        if registry is not None
        else [
            {
                "scenario_id": "DEMAND_X2",
                "scenario_type": "STRESS",
                "scenario_contract_key": "inventory.stress.demand_multiplier",
                "scenario_contract_version": "1.0.0",
                "scenario_payload_content_hash": _STRESS_PAYLOAD_CONTENT_HASH,
                "deterministic_seed": _STRESS_SEED,
                "scenario_content_hash": _STRESS_SCENARIO_CONTENT_HASH,
                "runner_implementation_id": "inventory.stress.demand_multiplier",
                "runner_implementation_version": "1.0.0",
                "runner_implementation_content_hash": _STRESS_IMPLEMENTATION_HASH,
            }
        ]
        if stress_runner is not None or include_stress
        else []
    )
    plan["cost_profile_binding"] = registry.cost_profile_binding() if registry is not None else None
    runtime_data["claim"]["strategy_execution_plan"] = seal_strategy_execution_plan(plan)
    runtime = InventoryRuntimeExecutionRequest.from_dict(reseal_runtime_site_binding(runtime_data))
    return PsiRunCommand(
        runtime_request=runtime,
        mathematical_request=mathematical,
        mathematical_implementation_content_hash=_MATH_IMPLEMENTATION_HASH,
        psi_simulator_implementation_content_hash=_PSI_SIMULATOR_IMPLEMENTATION_HASH,
        ppo_challengers={
            "ppo-shadow-1": ResolvedPpoChallenger(
                model=model,
                implementation_content_hash=_PPO_IMPLEMENTATION_HASH,
                approval_reference="MODEL-APPROVAL-1",
            )
        },
        stress_scenarios=(
            registry.resolved_stress_scenarios()
            if registry is not None
            else {
                "DEMAND_X2": ResolvedStressScenario(
                    scenario_id="DEMAND_X2",
                    scenario_contract_key="inventory.stress.demand_multiplier",
                    scenario_contract_version="1.0.0",
                    scenario_payload_json=canonical_json(_STRESS_PAYLOAD),
                    scenario_payload_content_hash=_STRESS_PAYLOAD_CONTENT_HASH,
                    deterministic_seed=_STRESS_SEED,
                    scenario_content_hash=_STRESS_SCENARIO_CONTENT_HASH,
                    runner_implementation_id="inventory.stress.demand_multiplier",
                    runner_implementation_version="1.0.0",
                    runner_implementation_content_hash=_STRESS_IMPLEMENTATION_HASH,
                    runner_factory=type(stress_runner),
                )
            }
            if stress_runner is not None
            else {}
        ),
        cost_profile=None if registry is None else registry.resolved_cost_profile(),
    )


class PsiOrchestratorOfflineTests(unittest.TestCase):
    def test_development_cost_and_stress_registry_are_bound_into_one_run_plan(self) -> None:
        command = _bound_command(development_registry=True)

        result = RunPsiBundleUseCase(DEPLOYMENT).execute(command)

        plan = command.runtime_request.strategy_execution_plan
        registry = DevelopmentSimulationRegistry()
        self.assertEqual(plan["cost_profile_binding"], registry.cost_profile_binding())
        self.assertEqual(
            plan["stress_scenario_bindings"],
            list(registry.stress_scenario_bindings()),
        )
        self.assertEqual(
            result.result_bundle["cost_profile_content_hash"],
            registry.cost_profile_binding()["profile_content_hash"],
        )
        self.assertEqual(
            [row["cost_delta"]["status"] for row in result.result_bundle["comparisons"]],
            ["AVAILABLE"] * 4,
        )
        self.assertEqual(
            [
                row["scenario_id"]
                for row in result.result_bundle["result_children"]
                if row["result_kind"] == "STRESS_PSI"
            ],
            ["CONFIRMED_RECEIPT_DELAY_7_DAYS", "DEMAND_SURGE_20_PERCENT"],
        )

    def test_one_prepare_builds_real_baseline_math_and_ppo_artifacts(self) -> None:
        prepare = _CountingPrepare()
        use_case = RunPsiBundleUseCase(DEPLOYMENT, prepare_use_case=prepare)

        first = use_case.execute(_bound_command())
        replay = RunPsiBundleUseCase(DEPLOYMENT).execute(_bound_command())

        self.assertEqual(prepare.calls, 1)
        self.assertEqual(first.result_bundle, replay.result_bundle)
        self.assertEqual(first.artifacts, replay.artifacts)
        self.assertEqual(first.detailed_comparisons, replay.detailed_comparisons)

        children = first.result_bundle["result_children"]
        self.assertEqual(
            [(row["result_kind"], row["strategy_type"], row["status"]) for row in children],
            [
                ("BASELINE_PSI", "NONE", "SUCCEEDED"),
                ("RECOMMENDED_PSI", "MATHEMATICAL", "SUCCEEDED"),
                ("RECOMMENDED_PSI", "DEEP_RL", "SUCCEEDED"),
            ],
        )
        self.assertEqual(
            first.result_bundle["effective_child_result_id"], children[1]["child_result_id"]
        )
        self.assertFalse(
            first.execution_results[children[2]["child_result_id"]]["automatic_publish_allowed"]
        )
        self.assertFalse(
            first.execution_results[children[2]["child_result_id"]]["automatic_order_allowed"]
        )

        for artifact in first.artifacts.values():
            self.assertEqual(artifact["canonical_input_hash"], first.canonical_input.input_hash)

        baseline_metrics = first.child_metrics[children[0]["child_result_id"]]
        math_metrics = first.child_metrics[children[1]["child_result_id"]]
        math_comparison = first.result_bundle["comparisons"][0]
        expected_backorder_delta = Decimal(math_metrics["ending_backorder_qty"]) - Decimal(
            baseline_metrics["ending_backorder_qty"]
        )
        self.assertEqual(
            Decimal(math_comparison["backorder_qty_delta"]["value"]),
            expected_backorder_delta,
        )
        self.assertEqual(math_comparison["cost_delta"]["status"], "NOT_AVAILABLE")
        self.assertEqual(math_comparison["cost_delta"]["reason_code"], "COST_PROFILE_NOT_BOUND")

    def test_resealed_prepared_projection_tampering_is_rejected_before_children(self) -> None:
        use_case = RunPsiBundleUseCase(
            DEPLOYMENT,
            prepare_use_case=_ResealedTamperingPrepare(),
        )

        with self.assertRaisesRegex(
            InventoryInputError,
            "PREPARED_INPUT_PROJECTION_MISMATCH",
        ):
            use_case.execute(_bound_command())

    def test_validated_prepared_projection_is_rebuilt_as_a_trusted_base_snapshot(self) -> None:
        prepare = _TimeVaryingPrepare()

        result = RunPsiBundleUseCase(DEPLOYMENT, prepare_use_case=prepare).execute(_bound_command())

        self.assertIs(type(result.prepared_input), PreparedInventoryInput)
        self.assertEqual(prepare.candidate.calls, 1)
        self.assertNotEqual(
            result.prepared_input.to_dict()["positions"][0]["available_qty"],
            "999",
        )

    def test_wrong_mathematical_implementation_hash_is_rejected_before_execution(self) -> None:
        command = replace(
            _bound_command(),
            mathematical_implementation_content_hash="0" * 64,
        )

        with self.assertRaisesRegex(
            InventoryInputError, "MATHEMATICAL_IMPLEMENTATION_HASH_MISMATCH"
        ):
            RunPsiBundleUseCase(DEPLOYMENT).execute(command)

    def test_wrong_psi_simulator_implementation_hash_is_rejected_before_execution(self) -> None:
        command = replace(
            _bound_command(),
            psi_simulator_implementation_content_hash="0" * 64,
        )

        with self.assertRaisesRegex(
            InventoryInputError, "PSI_SIMULATOR_IMPLEMENTATION_HASH_MISMATCH"
        ):
            RunPsiBundleUseCase(DEPLOYMENT).execute(command)

    def test_plan_pins_mathematical_policy_input_snapshot(self) -> None:
        command = _bound_command()
        changed = command.mathematical_request.to_dict()
        changed["policy_input"]["history"][0]["demand_qty"] = "999"
        changed_request = MathematicalPolicyRequest.from_dict(reseal_math(changed))

        with self.assertRaisesRegex(
            InventoryInputError, "MATHEMATICAL_STRATEGY_INPUT_BINDING_MISMATCH"
        ):
            RunPsiBundleUseCase(DEPLOYMENT).execute(
                replace(command, mathematical_request=changed_request)
            )

    def test_plan_pins_all_replenishment_execution_controls(self) -> None:
        command = _bound_command()
        changed = command.mathematical_request.to_dict()
        changed["recommendation"]["execution"]["item_controls"][0]["max_order_qty"] = "999"
        changed_request = MathematicalPolicyRequest.from_dict(changed)

        with self.assertRaisesRegex(InventoryInputError, "MATHEMATICAL_EXECUTION_BINDING_MISMATCH"):
            RunPsiBundleUseCase(DEPLOYMENT).execute(
                replace(command, mathematical_request=changed_request)
            )

    def test_ppo_failure_isolated_without_changing_mathematical_result(self) -> None:
        successful = RunPsiBundleUseCase(DEPLOYMENT).execute(_bound_command())

        with patch(
            "dsio_inventory_engine.run_inventory.psi_orchestrator.compile_strategy",
            side_effect=RuntimeError("deterministic test failure"),
        ):
            isolated = RunPsiBundleUseCase(DEPLOYMENT).execute(_bound_command())

        successful_children = successful.result_bundle["result_children"]
        isolated_children = isolated.result_bundle["result_children"]
        self.assertEqual(isolated_children[2]["status"], "FAILED")
        self.assertEqual(isolated_children[2]["failure_reason_code"], "PPO_EXECUTION_FAILED")
        self.assertEqual(
            isolated.result_bundle["effective_child_result_id"],
            isolated_children[1]["child_result_id"],
        )
        self.assertEqual(isolated_children[:2], successful_children[:2])
        self.assertEqual(
            isolated.child_metrics[isolated_children[1]["child_result_id"]],
            successful.child_metrics[successful_children[1]["child_result_id"]],
        )
        ppo_comparison = isolated.result_bundle["comparisons"][1]
        self.assertEqual(ppo_comparison["service_level_delta"]["status"], "NOT_AVAILABLE")
        self.assertEqual(
            ppo_comparison["service_level_delta"]["reason_code"],
            "CANDIDATE_RESULT_FAILED",
        )

    def test_forged_ppo_policy_admission_fails_only_the_shadow_child(self) -> None:
        successful = RunPsiBundleUseCase(DEPLOYMENT).execute(_bound_command())

        def forged_shadow_admission(prepared, execution, **kwargs):
            admissions = apply_real_effective_policy_controls(
                prepared,
                execution,
                **kwargs,
            )
            if execution["execution_purpose"] == "SHADOW":
                for admission in admissions.values():
                    admission["execution_purpose"] = "OPERATIONAL"
                    admission["automatic_publish_allowed"] = True
                    admission["automatic_order_allowed"] = True
            return admissions

        with patch(
            "dsio_inventory_engine.run_inventory.psi_orchestrator.apply_effective_policy_controls",
            side_effect=forged_shadow_admission,
        ):
            isolated = RunPsiBundleUseCase(DEPLOYMENT).execute(_bound_command())

        successful_children = successful.result_bundle["result_children"]
        isolated_children = isolated.result_bundle["result_children"]
        self.assertEqual(isolated_children[1], successful_children[1])
        self.assertEqual(isolated_children[2]["status"], "FAILED")
        self.assertEqual(
            isolated_children[2]["failure_reason_code"],
            "ACTION_EFFECTIVE_POLICY_ADMISSION_AUTHORITY_MISMATCH",
        )
        self.assertEqual(
            isolated.result_bundle["effective_child_result_id"],
            isolated_children[1]["child_result_id"],
        )

    def test_ppo_resolution_mismatches_fail_only_the_shadow_child(self) -> None:
        command = _bound_command()
        resolved = command.ppo_challengers["ppo-shadow-1"]
        alternate_model = model_fixture(command.mathematical_request.to_dict(), "DEEP_RL", action=2)
        cases = (
            (
                {},
                "PPO_MODEL_UNAVAILABLE",
            ),
            (
                {
                    "ppo-shadow-1": ResolvedPpoChallenger(
                        model=alternate_model,
                        implementation_content_hash=resolved.implementation_content_hash,
                        approval_reference=resolved.approval_reference,
                    )
                },
                "PPO_MODEL_BINDING_MISMATCH",
            ),
            (
                {
                    "ppo-shadow-1": ResolvedPpoChallenger(
                        model=resolved.model,
                        implementation_content_hash="0" * 64,
                        approval_reference=resolved.approval_reference,
                    )
                },
                "PPO_IMPLEMENTATION_BINDING_MISMATCH",
            ),
            (
                {
                    "ppo-shadow-1": ResolvedPpoChallenger(
                        model=resolved.model,
                        implementation_content_hash=resolved.implementation_content_hash,
                        approval_reference="WRONG-APPROVAL",
                    )
                },
                "PPO_APPROVAL_BINDING_MISMATCH",
            ),
        )

        for challengers, reason in cases:
            with self.subTest(reason=reason):
                result = RunPsiBundleUseCase(DEPLOYMENT).execute(
                    replace(command, ppo_challengers=challengers)
                )
                children = result.result_bundle["result_children"]
                self.assertEqual(children[1]["status"], "SUCCEEDED")
                self.assertEqual(children[2]["status"], "FAILED")
                self.assertEqual(children[2]["failure_reason_code"], reason)

    def test_registered_stress_runner_produces_an_independent_psi_child(self) -> None:
        stress_runner = _DemandShockRunner()

        result = RunPsiBundleUseCase(DEPLOYMENT).execute(
            _bound_command(stress_runner=stress_runner)
        )

        stress_child = result.result_bundle["result_children"][3]
        self.assertEqual(
            (stress_child["result_kind"], stress_child["status"], stress_child["scenario_id"]),
            ("STRESS_PSI", "SUCCEEDED", "DEMAND_X2"),
        )
        baseline_child = result.result_bundle["result_children"][0]
        baseline_demand = Decimal(
            result.child_metrics[baseline_child["child_result_id"]]["horizon_demand_qty"]
        )
        stress_demand = Decimal(
            result.child_metrics[stress_child["child_result_id"]]["horizon_demand_qty"]
        )
        self.assertGreater(stress_demand, baseline_demand)
        stress_execution = result.execution_results[stress_child["child_result_id"]]
        self.assertEqual(stress_execution["execution"]["execution_purpose"], "SHADOW")
        self.assertFalse(stress_execution["automatic_publish_allowed"])
        self.assertFalse(stress_execution["automatic_order_allowed"])

    def test_stress_replay_uses_fresh_runner_instances_and_stable_content(self) -> None:
        command = _bound_command(stress_runner=_DemandShockRunner())

        first = RunPsiBundleUseCase(DEPLOYMENT).execute(command)
        replay = RunPsiBundleUseCase(DEPLOYMENT).execute(command)

        first_child = first.result_bundle["result_children"][3]
        replay_child = replay.result_bundle["result_children"][3]
        self.assertEqual(first_child, replay_child)
        self.assertEqual(first.result_bundle["content_hash"], replay.result_bundle["content_hash"])

    def test_stress_runner_factory_must_return_isolated_instances(self) -> None:
        command = _bound_command(stress_runner=_DemandShockRunner())
        resolved = command.stress_scenarios["DEMAND_X2"]
        shared = replace(
            resolved,
            runner_factory=_SharedRunnerFactory(_DemandShockRunner()),
        )

        result = RunPsiBundleUseCase(DEPLOYMENT).execute(
            replace(command, stress_scenarios={"DEMAND_X2": shared})
        )

        stress_child = result.result_bundle["result_children"][3]
        self.assertEqual(stress_child["status"], "FAILED")
        self.assertEqual(
            stress_child["failure_reason_code"],
            "STRESS_RUNNER_INSTANCE_NOT_ISOLATED",
        )

    def test_stress_runner_must_replay_the_same_payload_for_the_same_seed(self) -> None:
        _CrossInstanceStateStressRunner.calls = 0

        result = RunPsiBundleUseCase(DEPLOYMENT).execute(
            _bound_command(stress_runner=_CrossInstanceStateStressRunner())
        )

        stress_child = result.result_bundle["result_children"][3]
        self.assertEqual(stress_child["status"], "FAILED")
        self.assertEqual(
            stress_child["failure_reason_code"],
            "STRESS_DETERMINISM_REPLAY_MISMATCH",
        )

    def test_stress_payload_and_seed_must_match_the_plan(self) -> None:
        command = _bound_command(stress_runner=_DemandShockRunner())
        resolved = command.stress_scenarios["DEMAND_X2"]
        changed_seed = replace(
            resolved,
            deterministic_seed=_STRESS_SEED + 1,
            scenario_content_hash=derive_stress_scenario_content_hash(
                scenario_id="DEMAND_X2",
                scenario_contract_key="inventory.stress.demand_multiplier",
                scenario_contract_version="1.0.0",
                scenario_payload_content_hash=_STRESS_PAYLOAD_CONTENT_HASH,
                deterministic_seed=_STRESS_SEED + 1,
            ),
        )

        result = RunPsiBundleUseCase(DEPLOYMENT).execute(
            replace(command, stress_scenarios={"DEMAND_X2": changed_seed})
        )
        child = result.result_bundle["result_children"][3]
        self.assertEqual(child["status"], "FAILED")
        self.assertEqual(child["failure_reason_code"], "STRESS_SCENARIO_BINDING_MISMATCH")

        with self.assertRaisesRegex(
            InventoryInputError,
            "STRESS_SCENARIO_PAYLOAD_HASH_MISMATCH",
        ):
            replace(
                resolved,
                scenario_payload_json=canonical_json({"demand_multiplier": "3"}),
            )

    def test_stress_runner_must_match_the_plan_implementation_hash(self) -> None:
        command = _bound_command(stress_runner=_DemandShockRunner())
        resolved = command.stress_scenarios["DEMAND_X2"]
        mismatched = replace(resolved, runner_implementation_content_hash="0" * 64)

        result = RunPsiBundleUseCase(DEPLOYMENT).execute(
            replace(command, stress_scenarios={"DEMAND_X2": mismatched})
        )

        children = result.result_bundle["result_children"]
        self.assertEqual(children[1]["status"], "SUCCEEDED")
        self.assertEqual(children[3]["status"], "FAILED")
        self.assertEqual(
            children[3]["failure_reason_code"],
            "STRESS_RUNNER_IMPLEMENTATION_BINDING_MISMATCH",
        )

    def test_stress_remains_evidence_only_for_allow_auto_items(self) -> None:
        result = RunPsiBundleUseCase(DEPLOYMENT).execute(
            _bound_command(
                stress_runner=_DemandShockRunner(),
                action="ALLOW",
                approval="AUTO",
            )
        )

        stress_child = result.result_bundle["result_children"][3]
        stress_execution = result.execution_results[stress_child["child_result_id"]]
        self.assertEqual(stress_child["execution_role"], "EVIDENCE_ONLY")
        self.assertFalse(stress_execution["automatic_publish_allowed"])
        self.assertFalse(stress_execution["automatic_order_allowed"])
        self.assertEqual(stress_execution["publication_disposition"], "REVIEW_REQUIRED")

    def test_stress_failure_and_universe_mismatch_do_not_discard_math_result(self) -> None:
        for runner, reason in (
            (_FailingStressRunner(), "STRESS_EXECUTION_FAILED"),
            (_ReservedReasonStressRunner(), "STRESS_EXECUTION_FAILED"),
            (_DifferentUniverseStressRunner(), "STRESS_DEMAND_UNIVERSE_MISMATCH"),
            (_InvalidDemandNettingStressRunner(), "STRESS_DEMAND_NETTING_MISMATCH"),
            (
                _ImmutableSectionMutatingStressRunner(),
                "STRESS_PREPARED_IMMUTABLE_SECTION_MISMATCH",
            ),
        ):
            with self.subTest(reason=reason):
                result = RunPsiBundleUseCase(DEPLOYMENT).execute(
                    _bound_command(stress_runner=runner)
                )
                children = result.result_bundle["result_children"]
                self.assertEqual(children[1]["status"], "SUCCEEDED")
                self.assertEqual(children[3]["status"], "FAILED")
                self.assertEqual(children[3]["failure_reason_code"], reason)
                self.assertEqual(
                    result.result_bundle["effective_child_result_id"],
                    children[1]["child_result_id"],
                )

    def test_stress_runner_cannot_supply_strategy_or_psi_result(self) -> None:
        result = RunPsiBundleUseCase(DEPLOYMENT).execute(
            _bound_command(stress_runner=_RogueFullResultStressRunner())
        )

        children = result.result_bundle["result_children"]
        self.assertEqual(children[1]["status"], "SUCCEEDED")
        self.assertEqual(children[3]["status"], "FAILED")
        self.assertEqual(
            children[3]["failure_reason_code"],
            "STRESS_SCENARIO_RESULT_INVALID",
        )
        self.assertEqual(
            result.result_bundle["effective_child_result_id"], children[1]["child_result_id"]
        )

    def test_stress_receipt_may_change_only_due_projection_and_quantity(self) -> None:
        command = _bound_command()
        canonical = CanonicalInputRequest.from_dict(
            command.mathematical_request.to_dict()["recommendation"]["canonical_input"]
        )
        base_value = PrepareInventoryInputUseCase(DEPLOYMENT).execute(canonical).to_dict()
        base_value["receipt_decisions"] = [
            {
                "item_id": "ITEM-A",
                "uom": "EA",
                "receipt_id": "RECEIPT-1",
                "due_date": "2026-10-05",
                "due_qty": "5",
                "status": "CONFIRMED",
                "supply_type": "PURCHASE_ORDER",
                "yyyyww": "202641",
                "included_qty": "5",
                "exclusion_reason": None,
            }
        ]
        base = _reseal_prepared(base_value)
        delayed = base.to_dict()
        delayed["receipt_decisions"][0].update(
            {
                "due_date": "2026-10-12",
                "due_qty": "7",
                "yyyyww": "202642",
                "included_qty": "7",
            }
        )

        stressed = _validate_stress_prepared_projection(
            base=base,
            candidate=_reseal_prepared(delayed).to_dict(),
        )
        self.assertEqual(stressed.to_dict()["receipt_decisions"][0]["due_qty"], "7")

        changed_status = stressed.to_dict()
        changed_status["receipt_decisions"][0]["status"] = "ORDERED"
        with self.assertRaisesRegex(
            InventoryInputError,
            "STRESS_RECEIPT_UNIVERSE_MISMATCH",
        ):
            _validate_stress_prepared_projection(
                base=base,
                candidate=_reseal_prepared(changed_status).to_dict(),
            )

    def test_external_stress_runner_cannot_replace_the_verified_base_position(self) -> None:
        result = RunPsiBundleUseCase(DEPLOYMENT).execute(
            _bound_command(stress_runner=_ImmutableSectionMutatingStressRunner())
        )

        self.assertEqual(result.result_bundle["tenant_id"], "tenant-a")
        self.assertEqual(result.result_bundle["result_children"][1]["status"], "SUCCEEDED")
        self.assertEqual(result.result_bundle["result_children"][3]["status"], "FAILED")
        self.assertEqual(
            result.prepared_input.to_dict()["positions"][0]["available_qty"],
            "10",
        )

    def test_unresolved_registered_stress_scenario_is_skipped(self) -> None:
        result = RunPsiBundleUseCase(DEPLOYMENT).execute(_bound_command(include_stress=True))

        stress_child = result.result_bundle["result_children"][3]
        self.assertEqual(stress_child["status"], "SKIPPED")
        self.assertEqual(stress_child["failure_reason_code"], "STRESS_SCENARIO_UNAVAILABLE")
        comparison = result.result_bundle["comparisons"][2]
        self.assertEqual(comparison["cost_delta"]["status"], "NOT_AVAILABLE")
        self.assertEqual(comparison["cost_delta"]["reason_code"], "CANDIDATE_RESULT_SKIPPED")


if __name__ == "__main__":
    unittest.main()

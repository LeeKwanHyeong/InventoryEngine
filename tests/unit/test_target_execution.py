import copy
from decimal import Decimal

import pytest
from unittest.mock import patch

from dsio_inventory_engine.inventory_contracts.calculation_boundary import (
    CalculationBoundaryBinding,
)
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.replenishment import (
    RecommendationRequest,
    ReplenishmentProposal,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from dsio_inventory_engine.recommend_replenishment.application.target_execution import (
    RunTargetPolicyPipelineUseCase,
    RunTargetRecommendedPsiUseCase,
    RunTargetMathematicalReplenishmentUseCase,
    _TargetNumerics,
)
from dsio_inventory_engine.recommend_replenishment.application.run import RunRecommendedPsiUseCase
from dsio_inventory_engine.simulate_inventory.application.step import InventoryState, advance_bucket
from tests.support.calculation_adapter_fixtures import DEPLOYMENT
from tests.support.selected_order_plan_fixtures import execution_case, v2_parts
from tests.support.target_execution_fixtures import boundary
from tests.support.math_fixtures import build_math


class ReferenceKernel:
    """Unit dependency probe, NOT actual C# verification."""

    dll_sha256 = "a" * 64

    def __init__(self):
        self.inputs = []

    def advance_target(self, given):
        self.inputs.append(copy.deepcopy(given))
        state = InventoryState(
            *(Decimal(given[k]) for k in ("available_qty", "reserved_qty", "backorder_qty"))
        )
        return advance_bucket(
            state,
            **{
                k: Decimal(v)
                for k, v in given.items()
                if k not in ("available_qty", "reserved_qty", "backorder_qty")
            },
        )[1]


def test_boundary_reuse_preserves_the_uncached_binding_for_every_candidate():
    _, args = execution_case()
    request = args["request"]
    rec = RecommendationRequest.from_dict(request.to_dict()["recommendation"])
    numeric = _TargetNumerics(DEPLOYMENT, ReferenceKernel(), rec, boundary(rec))
    original = CalculationBoundaryBinding.from_sources
    values = []
    for digit in ("b", "c", "d"):
        value = copy.deepcopy(rec.to_dict()["execution"])
        value["strategy_input_binding"]["content_hash"] = digit * 64
        values.append(value)
    expected = [
        original(numeric.canonical, **numeric._binding_arguments(v)).to_dict() for v in values
    ]
    with patch.object(CalculationBoundaryBinding, "from_sources", wraps=original) as factory:
        assert [numeric._bind(v) for v in values] == expected
    assert factory.call_count == 1
    returned = numeric._bind(values[0])
    returned["replenishment_execution_binding"]["strategy_input_binding"]["content_hash"] = "e" * 64
    assert numeric._bind(values[0]) == expected[0]
    bad = copy.deepcopy(values[0])
    bad["capacity_mode"] = "OTHER"
    with pytest.raises(InventoryInputError, match="TARGET_EXECUTION_CONTROLS_CHANGED"):
        numeric._bind(bad)
    bad = copy.deepcopy(values[0])
    bad["strategy_input_binding"]["content_hash"] = "invalid"
    with pytest.raises(InventoryInputError):
        numeric._bind(bad)
    from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest

    changed = numeric.canonical.to_dict()
    changed["context"]["engine_run_id"] = "another-attempt"
    numeric.canonical = CanonicalInputRequest.from_dict(changed)
    with pytest.raises(InventoryInputError, match="TARGET_EXECUTION_INPUT_CHANGED"):
        numeric._bind(values[0])


class FixedProposal:
    def __init__(self, recommendation, quantity="137", action="ORDER_QTY"):
        self.descriptor = recommendation.to_dict()["execution"]["strategy"]
        self.input_binding = recommendation.to_dict()["execution"]["strategy_input_binding"]
        self.quantity, self.action = quantity, action
        self.observations = []

    def decide(self, observation):
        obs = observation.to_dict()
        self.observations.append(obs)
        return ReplenishmentProposal.from_dict(
            {
                "contract_id": "io-replenishment-decision-v1",
                "decision_id": obs["decision_id"],
                "observation_hash": observation.content_hash,
                "strategy": self.descriptor,
                "action_type": self.action,
                "quantity": self.quantity,
                "calculated_policy": {
                    "safety_stock_qty": None,
                    "rop_qty": None,
                    "target_inventory_qty": None,
                },
                "reason_codes": ["TEST-ONLY-FIXED-PROPOSAL"],
            }
        )


def recommendation(**policy):
    raw = build_math(
        {
            "id": "target-guard",
            "history": "constant",
            "boh": "0",
            "forecast": ["10", "10", "10"],
            "policy": policy,
        }
    )
    return RecommendationRequest.from_dict(raw["recommendation"])


def pipeline_case(mode="ABC_V1", parts=None):
    original, args = execution_case(parts, mode=mode)
    kernel = ReferenceKernel()
    runner = RunTargetPolicyPipelineUseCase(DEPLOYMENT, kernel, original.producer)
    extra = {
        k: v
        for k, v in args.items()
        if k in ("config_values", "classification_snapshot", "catalog", "expected_catalog_hash")
    }
    binding = boundary(args["request"].to_dict()["recommendation"])
    actual = runner.execute(args["request"], binding, **extra)
    return original, args, runner, kernel, binding, extra, actual


@pytest.mark.parametrize("mode", ["ABC_V1", "LEGACY_STYLE"])
def test_all_stages_use_explicit_numerics_and_preserve_existing_results_and_hashes(mode):
    original, args, runner, kernel, binding, extra, actual = pipeline_case(mode)
    assert actual["search_result"] == args["source"]
    assert actual["selection_result"] == args["selection_result"]
    assert actual["order_plan"] == original.execute(**args)
    assert set(actual["completed_transition_calls"]) == {
        "CANDIDATE_EVALUATION",
        "POLICY_SELECTION",
        "ORDER_PLAN_REPLAY",
    }
    assert all(n > 0 for n in actual["completed_transition_calls"].values())
    assert sum(actual["completed_transition_calls"].values()) == len(kernel.inputs)
    for value in actual["execution_bindings"]:
        assert value["semantics_profile"] == "TARGET_GUARDED"
        assert value["psi_simulator"] == binding.to_dict()["psi_simulator"]
        assert value["canonical_input_hash"] == binding.to_dict()["canonical_input_hash"]
    runner.verify_result(actual, args["request"], binding, **extra)
    assert not actual["automatic_order_allowed"] and not actual["automatic_publish_allowed"]


def test_guard_reduction_not_raw_proposal_enters_supply_and_next_observation():
    rec = recommendation(
        moq="50", order_multiple="50", physical_max_capacity="220", lead_time_days=0
    )
    probe, kernel = FixedProposal(rec), ReferenceKernel()
    actual = RunTargetRecommendedPsiUseCase(DEPLOYMENT, kernel).execute(rec, probe, boundary(rec))
    result = actual["recommended_result"]
    assert [i["recommended_receipt_qty"] for i in kernel.inputs] == ["150", "50", "0"]
    assert [o["quantity"] for o in result["recommended_orders"]] == ["150", "50"]
    assert probe.observations[1]["state"]["available_qty"] == "140"
    assert result["decision_evidence"][1]["validation"]["lot_rounded_qty"] == "150"
    assert result["decision_evidence"][1]["validation"]["accepted_order_qty"] == "50"
    assert all(Decimal(i["recommended_receipt_qty"]) != Decimal(137) for i in kernel.inputs)
    expected = RunRecommendedPsiUseCase(DEPLOYMENT).execute(rec, FixedProposal(rec))
    assert result == expected


def test_pending_accepted_supply_is_observed_to_prevent_duplicate_order_up_to():
    rec = recommendation(lead_time_days=14)
    probe, kernel = FixedProposal(rec, quantity="100", action="ORDER_UP_TO"), ReferenceKernel()
    result = RunTargetRecommendedPsiUseCase(DEPLOYMENT, kernel).execute(rec, probe, boundary(rec))[
        "recommended_result"
    ]
    assert [o["quantity"] for o in result["recommended_orders"]] == ["100"]
    assert probe.observations[1]["pending_supply"][0]["quantity"] == "100"
    assert [r["recommended_receipt_qty"] for r in result["psi_rows"]] == ["0", "0", "100"]


@pytest.mark.parametrize("mutation", ["build", "profile", "version", "scenario", "guard", "run"])
def test_wrong_boundary_refused_before_transition(mutation):
    rec, kernel = recommendation(), ReferenceKernel()
    value = boundary(rec).to_dict()
    if mutation == "build":
        value["psi_simulator"]["implementation_content_hash"] = "b" * 64
    elif mutation == "profile":
        value["semantics_profile"] = "LEGACY_REPLAY"
    elif mutation == "version":
        value["psi_simulator"]["version"] = "9.0.0"
    elif mutation == "scenario":
        value["psi_scenario_type"] = "BASELINE"
    elif mutation == "guard":
        value["guard_order"] = "DIAGNOSTIC_POSTCHECK_NO_MUTATION"
    else:
        value["engine_run_id"] = "OTHER-RUN"
    with pytest.raises(InventoryInputError):
        binding = CalculationBoundaryBinding.from_dict(value)
        RunTargetRecommendedPsiUseCase(DEPLOYMENT, kernel).execute(rec, FixedProposal(rec), binding)
    assert kernel.inputs == []


@pytest.mark.parametrize(
    "mutation", ["cost", "order", "selection", "profile", "build", "permission", "failure"]
)
def test_result_tampering_cannot_pass_full_pipeline_replay(mutation):
    _, args, runner, _, binding, extra, result = pipeline_case()
    changed = copy.deepcopy(result)
    if mutation == "cost":
        changed["search_result"]["candidate_evaluations"][0]["metrics"]["total_cost"] = "0"
    elif mutation == "order":
        changed["order_plan"]["recommended_orders"][0]["quantity"] = "999"
    elif mutation == "selection":
        changed["selection_result"]["item_selections"][0]["selected_policy"]["emos"] = "99"
    elif mutation in ("profile", "build"):
        child = changed["execution_bindings"][0]
        if mutation == "profile":
            child["semantics_profile"] = "LEGACY_REPLAY"
        else:
            child["psi_simulator"]["implementation_content_hash"] = "b" * 64
    elif mutation == "permission":
        changed["automatic_order_allowed"] = True
    else:
        changed["search_result"]["candidate_evaluations"][0]["status"] = "FAILED"
    with pytest.raises(InventoryInputError, match="TARGET_PIPELINE_REPLAY_MISMATCH"):
        runner.verify_result(changed, args["request"], binding, **extra)


def test_transition_exception_does_not_fall_back_to_python():
    class Broken(ReferenceKernel):
        def advance_target(self, given):
            raise RuntimeError("broken DLL")

    rec = recommendation()
    with pytest.raises(InventoryInputError, match="TARGET_TRANSITION_FAILED"):
        RunTargetRecommendedPsiUseCase(DEPLOYMENT, Broken()).execute(
            rec, FixedProposal(rec), boundary(rec)
        )


def test_corrupt_transition_fails_before_next_observation():
    class Corrupt(ReferenceKernel):
        def advance_target(self, given):
            value = super().advance_target(given)
            value["eoh_qty"] = "999"
            return value

    rec, probe = recommendation(), None
    probe = FixedProposal(rec)
    with pytest.raises(InventoryInputError):
        RunTargetRecommendedPsiUseCase(DEPLOYMENT, Corrupt()).execute(rec, probe, boundary(rec))
    assert len(probe.observations) == 1


def test_production_scope_is_not_enabled():
    rec = recommendation()
    with pytest.raises(InventoryInputError, match="TARGET_EXECUTION_DEVELOPMENT_ONLY"):
        RunTargetRecommendedPsiUseCase(
            DeploymentScope("DSE", "PRODUCTION"), ReferenceKernel()
        ).execute(rec, FixedProposal(rec), boundary(rec))


def test_real_mathematical_strategy_uses_guard_and_target_transition():
    from dsio_inventory_engine.recommend_replenishment.application.mathematical import (
        RunMathematicalReplenishmentUseCase,
    )

    request = MathematicalPolicyRequest.from_dict(build_math())
    kernel = ReferenceKernel()
    result = RunTargetMathematicalReplenishmentUseCase(DEPLOYMENT, kernel).execute(
        request, boundary(request.to_dict()["recommendation"])
    )
    assert result["recommended_result"] == RunMathematicalReplenishmentUseCase(DEPLOYMENT).execute(
        request
    )
    assert result["completed_transition_calls"] == {"MATHEMATICAL_RECOMMENDED": 3}


@pytest.mark.parametrize("action", ["REVIEW", "BLOCK"])
@pytest.mark.parametrize("v2", [False, True])
def test_unchanged_fsn_gates_are_kept_in_target_pipeline(action, v2):
    from tests.support.catalog_search_fixtures import run_bundle
    from tests.support.catalog_selection_fixtures import feasible_parts

    _, config, _ = run_bundle()
    config["policy_overlays"]["fsn_order_action"]["N"] = action
    parts = feasible_parts(boh="10", values=config)
    if v2:
        parts = v2_parts(parts)
    _, _, _, _, _, _, result = pipeline_case("LEGACY_STYLE", parts)
    plan = result["order_plan"]
    assert plan["item_plans"][0]["effective_policy_admission"]["effective_order_action"] == action
    assert plan["automatic_order_allowed"] is plan["automatic_publish_allowed"] is False
    if action == "BLOCK":
        assert plan["recommended_orders"] == [] and plan["selected_item_count"] == 0
    else:
        assert plan["item_plans"][0]["status"] == "REVIEW_REQUIRED"
        assert plan["recommended_orders"] and all(
            not o["automatic_order_allowed"] for o in plan["recommended_orders"]
        )


def test_moq_is_not_violated_to_fit_capacity():
    rec = recommendation(
        moq="100", order_multiple="50", physical_max_capacity="220", lead_time_days=0
    )
    kernel = ReferenceKernel()
    result = RunTargetRecommendedPsiUseCase(DEPLOYMENT, kernel).execute(
        rec, FixedProposal(rec), boundary(rec)
    )
    assert [r["recommended_receipt_qty"] for r in kernel.inputs] == ["150", "0", "0"]
    assert result["recommended_result"]["decision_evidence"][1]["validation"]["reason_codes"] == [
        "NO_FEASIBLE_ORDER_QUANTITY"
    ]


def test_fsn_block_cannot_be_ignored_by_a_hostile_strategy():
    from tests.support.catalog_search_fixtures import run_bundle
    from tests.support.catalog_selection_fixtures import feasible_parts

    _, config, _ = run_bundle()
    config["policy_overlays"]["fsn_order_action"]["N"] = "BLOCK"
    raw, _, _ = v2_parts(feasible_parts(values=config))
    rec = RecommendationRequest.from_dict(raw["recommendation"])
    kernel = ReferenceKernel()
    with pytest.raises(InventoryInputError, match="STRATEGY_EXECUTION_FAILED"):
        RunTargetRecommendedPsiUseCase(DEPLOYMENT, kernel).execute(
            rec, FixedProposal(rec), boundary(rec)
        )
    assert kernel.inputs == []


def test_build_change_during_strategy_is_rejected_without_fallback():
    rec, kernel = recommendation(), ReferenceKernel()

    class Changed(FixedProposal):
        def decide(self, observation):
            kernel.dll_sha256 = "b" * 64
            return super().decide(observation)

    with pytest.raises(InventoryInputError, match="TARGET_EXECUTION_BUILD_CHANGED"):
        RunTargetRecommendedPsiUseCase(DEPLOYMENT, kernel).execute(rec, Changed(rec), boundary(rec))
    assert kernel.inputs == []

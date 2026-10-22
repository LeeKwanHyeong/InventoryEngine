"""Actual C# strategy/Guard/policy pipeline verification in an isolated worker.

No pytest dependency, DB, network, Artifact publication or Runtime registration.
Literal expectations and comparison Python outputs remain independent.
"""

import argparse
import copy
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from dsio_inventory_engine.infrastructure.calculation_pythonnet import CalculationPythonNetKernel
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.replenishment import (
    RecommendationRequest,
    ReplenishmentProposal,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, canonical_json
from dsio_inventory_engine.recommend_replenishment.application.mathematical import (
    RunMathematicalReplenishmentUseCase,
)
from dsio_inventory_engine.recommend_replenishment.application.target_execution import (
    RunTargetMathematicalReplenishmentUseCase,
    RunTargetPolicyPipelineUseCase,
    RunTargetRecommendedPsiUseCase,
)
from tests.support.calculation_adapter_fixtures import DEPLOYMENT
from tests.support.selected_order_plan_fixtures import execution_case, golden_parts, v2_parts
from tests.support.catalog_selection_fixtures import feasible_parts
from tests.support.catalog_search_fixtures import run_bundle
from tests.support.policy_search_fixtures import bundle, multi_item_bundle
from tests.support.math_fixtures import build_math
from tests.support.target_execution_fixtures import boundary

ROOT = Path(__file__).resolve().parents[1]


class MeasuredPort:
    """Call count only, not a speed/memory benchmark or alternate implementation."""

    def __init__(self, kernel):
        self.kernel, self.dll_sha256, self.calls = kernel, kernel.dll_sha256, 0

    def advance_target(self, given):
        self.calls += 1
        return self.kernel.advance_target(given)


class FixedProposal:
    def __init__(self, rec, action="ORDER_QTY", quantity="137"):
        self.descriptor = rec.to_dict()["execution"]["strategy"]
        self.input_binding = rec.to_dict()["execution"]["strategy_input_binding"]
        self.action, self.quantity, self.observations = action, quantity, []

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
                "reason_codes": ["TEST-ONLY-NOT-A-MODEL"],
            }
        )


def verify(build):
    dll = build / "Inventory.Calculation.dll"
    build_hash = hashlib.sha256(dll.read_bytes()).hexdigest()
    kernel = MeasuredPort(CalculationPythonNetKernel(dll, expected_dll_sha256=build_hash))
    checks, pipeline_results = [], []

    def pipeline(mode, parts=None):
        old_runner, args = execution_case(parts, mode=mode)
        before = canonical_json(
            {
                k: v.to_dict() if hasattr(v, "to_dict") else v
                for k, v in args.items()
                if k not in {"catalog"}
            }
        )
        runner = RunTargetPolicyPipelineUseCase(DEPLOYMENT, kernel, old_runner.producer)
        sources = {
            k: v
            for k, v in args.items()
            if k in {"config_values", "classification_snapshot", "catalog", "expected_catalog_hash"}
        }
        binding = boundary(args["request"].to_dict()["recommendation"], build_hash)
        previous_calls = kernel.calls
        result = runner.execute(args["request"], binding, **sources)
        assert sum(result["completed_transition_calls"].values()) == kernel.calls - previous_calls
        assert result["search_result"] == args["source"]
        assert result["selection_result"] == args["selection_result"]
        assert result["order_plan"] == old_runner.execute(**args)
        assert not result["automatic_order_allowed"] and not result["automatic_publish_allowed"]
        assert result["calculation_binding"] == binding.to_dict()
        assert all(
            row["psi_simulator"] == binding.to_dict()["psi_simulator"]
            and row["semantics_profile"] == "TARGET_GUARDED"
            for row in result["execution_bindings"]
        )
        assert before == canonical_json(
            {
                k: v.to_dict() if hasattr(v, "to_dict") else v
                for k, v in args.items()
                if k not in {"catalog"}
            }
        )
        pipeline_results.append(
            {
                "mode": mode,
                "candidates": len(result["search_result"]["candidate_evaluations"]),
                "selected": result["order_plan"]["selected_item_count"],
                "order_rows": len(result["order_plan"]["recommended_orders"]),
                "source_hash": result["search_result"]["content_hash"],
                "selection_hash": result["selection_result"]["content_hash"],
                "psi_hash": result["order_plan"]["psi_content_hash"],
                "orders_hash": result["order_plan"]["orders_content_hash"],
                "transitions": result["completed_transition_calls"],
            }
        )
        return runner, args, sources, binding, result

    # Every candidate, including all 88 reference candidates, goes through Guard and C#.
    for mode, count in (
        ("ABC_V1", None),
        ("ABC_XYZ_SHARED_BUDGET", 32),
        ("LEGACY_STYLE", 32),
        ("WIDE_REFERENCE", 88),
    ):
        last = pipeline(mode)
        if count is not None:
            assert len(last[-1]["search_result"]["candidate_evaluations"]) == count
        assert last[-1]["order_plan"]["selected_item_count"] > 0
        checks.append("candidate-pipeline-" + mode)

    golden = json.loads((ROOT / "tests/fixtures/golden_selected_order_plan.json").read_text())
    for literal in golden["cases"]:
        _, _, _, _, result = pipeline("ABC_V1", golden_parts(literal))
        plan = result["order_plan"]
        assert [r["eoh_qty"] for r in plan["psi_rows"]] == literal["eoh"]
        assert [
            [o[k] for k in ("order_date", "due_date", "receipt_date", "quantity")]
            for o in plan["recommended_orders"]
        ] == literal["orders"]
        assert [r["validation"]["reason_codes"] for r in plan["decision_evidence"]] == literal[
            "validation_reasons"
        ]
        checks.append("literal-" + literal["id"])

    for action in ("REVIEW", "BLOCK"):
        for v2 in (False, True):
            _, config, _ = run_bundle()
            config["policy_overlays"]["fsn_order_action"]["N"] = action
            parts = feasible_parts(boh="10", values=config)
            if v2:
                parts = v2_parts(parts)
            plan = pipeline("LEGACY_STYLE", parts)[-1]["order_plan"]
            assert (
                plan["item_plans"][0]["effective_policy_admission"]["effective_order_action"]
                == action
            )
            if action == "BLOCK":
                assert plan["recommended_orders"] == [] and plan["selected_item_count"] == 0
            else:
                assert plan["item_plans"][0]["status"] == "REVIEW_REQUIRED"
            assert all(not o["automatic_order_allowed"] for o in plan["recommended_orders"])
            checks.append("unchanged-fsn-" + action + ("-v2" if v2 else "-v1"))

    raw, config, _ = bundle()
    config["segmentation"]["sde"]["application_mode"] = "OPERATIONAL"
    parts = bundle(
        raw,
        config_values=config,
        metric_changes={
            "supplier_count": 1,
            "planning_lead_time_days": Decimal("7"),
            "p50_lead_time_days": Decimal("7"),
            "p90_lead_time_days": Decimal("21"),
            "on_time_delivery_rate": Decimal("0.99"),
            "receipt_sample_count": 10,
            "sde_source_status": "VERIFIED",
        },
    )
    plan = pipeline("ABC_V1", v2_parts(parts))[-1]["order_plan"]
    assert plan["recommended_orders"] and all(
        (date.fromisoformat(o["due_date"]) - date.fromisoformat(o["order_date"])).days == 7
        for o in plan["recommended_orders"]
    )
    assert all(
        r["candidate_policy_evidence"]["protection_lead_time_days"] == "21"
        for r in plan["decision_evidence"]
    )
    checks.append("sde-physical-7-protection-21")
    pipeline("ABC_V1", multi_item_bundle())
    checks.append("separate-two-item-states")

    request = MathematicalPolicyRequest.from_dict(build_math())
    actual = RunTargetMathematicalReplenishmentUseCase(DEPLOYMENT, kernel).execute(
        request, boundary(request.to_dict()["recommendation"], build_hash)
    )
    assert actual["recommended_result"] == RunMathematicalReplenishmentUseCase(DEPLOYMENT).execute(
        request
    )
    checks.append("existing-mathematical-strategy")

    raw = build_math(
        {
            "id": "native-guard-feedback",
            "history": "constant",
            "boh": "0",
            "forecast": ["10"] * 3,
            "policy": {
                "moq": "50",
                "order_multiple": "50",
                "physical_max_capacity": "220",
                "lead_time_days": 0,
            },
        }
    )
    rec = RecommendationRequest.from_dict(raw["recommendation"])
    strategy = FixedProposal(rec)
    result = RunTargetRecommendedPsiUseCase(DEPLOYMENT, kernel).execute(
        rec, strategy, boundary(rec, build_hash)
    )
    assert [r["recommended_receipt_qty"] for r in result["recommended_result"]["psi_rows"]] == [
        "150",
        "50",
        "0",
    ]
    assert strategy.observations[1]["state"]["available_qty"] == "140"
    assert (
        result["recommended_result"]["decision_evidence"][1]["validation"]["accepted_order_qty"]
        == "50"
    )
    checks.append("raw-137-guard-150-50-0-feedback")

    # Recompute the closed chain rather than trusting content-hash self-seals.
    runner, args, sources, binding, original = last
    runner.verify_result(original, args["request"], binding, **sources)
    for field in ("build", "profile", "permission", "orders", "selection", "cost"):
        changed = copy.deepcopy(original)
        if field in ("build", "profile"):
            child = changed["execution_bindings"][0]
            if field == "build":
                child["psi_simulator"]["implementation_content_hash"] = "a" * 64
            else:
                child["semantics_profile"] = "LEGACY_REPLAY"
        elif field == "permission":
            changed["automatic_order_allowed"] = True
        elif field == "orders":
            changed["order_plan"]["recommended_orders"][0]["quantity"] = "999"
        elif field == "selection":
            changed["selection_result"]["item_selections"][0]["selected_policy"]["emos"] = "99"
        else:
            changed["search_result"]["candidate_evaluations"][0]["metrics"]["total_cost"] = "0"
        try:
            runner.verify_result(changed, args["request"], binding, **sources)
        except InventoryInputError as error:
            assert str(error) == "TARGET_PIPELINE_REPLAY_MISMATCH"
        else:
            raise AssertionError("TAMPERED_TARGET_RESULT_ACCEPTED")
        checks.append("tamper-rejected-" + field)

    return {
        "status": "ACTUAL_CSHARP_TARGET_PIPELINE_VERIFIED_OFFLINE",
        "dll_sha256": build_hash,
        "checks": checks,
        "pipelines": pipeline_results,
        "native_transition_calls": kernel.calls,
        "python_fallback": False,
        "database_writes": False,
        "runtime_registered": False,
        "automatic_publish_allowed": False,
        "automatic_order_allowed": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.build), sort_keys=True))

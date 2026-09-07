"""Literal policy goldens, pinned model identity and inference safety boundaries."""

import copy
import math
import sys
import unittest
from decimal import Inexact, ROUND_FLOOR, localcontext
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from learned_fixtures import inference_request, model_fixture, reseal_model
from math_fixtures import adjustment, build_math, reseal_math
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.model import ModelArtifact
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from dsio_inventory_engine.learned.application import RunLearnedPsiUseCase, compile_strategy
from dsio_inventory_engine.learned.features import normalized
from dsio_inventory_engine.learned.predict import predict

SCOPE = DeploymentScope("DSE", "DEVELOPMENT")


class LearnedInferenceTests(unittest.TestCase):
    def execute(self, anchor=None, family="PREDICTIVE_ML", action=3):
        anchor = anchor or build_math()
        model = model_fixture(anchor, family, action)
        return RunLearnedPsiUseCase(SCOPE).execute(inference_request(anchor, model))

    def test_ml_literal_safety_rop_target(self):
        result = self.execute()
        first = result["learned_decision_evidence"][0]
        self.assertEqual(
            first["effective_policy"],
            {"safety_stock_qty": "5", "rop_qty": "15", "target_inventory_qty": "25"},
        )
        self.assertEqual(result["strategy"]["strategy_type"], "PREDICTIVE_ML")
        self.assertTrue(result["model_artifact_hash_verified"])
        self.assertFalse(
            result["artifact_sealed"]
            or result["database_writes"]
            or result["operational_model_approval_verified"]
        )
        self.assertEqual(
            result["content_hash"], digest({k: v for k, v in result.items() if k != "content_hash"})
        )

    def test_ppo_literal_targets_and_hold(self):
        for action, target in ((1, "18"), (2, "24"), (3, "30")):
            result = self.execute(family="DEEP_RL", action=action)
            self.assertEqual(
                result["learned_decision_evidence"][0]["effective_policy"]["target_inventory_qty"],
                target,
            )
        self.assertEqual(self.execute(family="DEEP_RL", action=0)["recommended_orders"], [])

    def test_override_and_expiry_are_not_overridden_by_learner(self):
        anchor = build_math()
        anchor["policy_input"]["adjustments"] = [adjustment(effective_to="2026-10-05")]
        for family in ("PREDICTIVE_ML", "DEEP_RL"):
            result = self.execute(reseal_math(anchor), family, action=0)
            first, later = result["learned_decision_evidence"][:2]
            self.assertEqual(first["effective_policy"]["target_inventory_qty"], "40")
            self.assertFalse(first["learned_policy_applied"])
            self.assertTrue(later["learned_policy_applied"])

    def test_missing_history_fallback_or_exclusion(self):
        anchor = build_math()
        anchor["policy_input"]["history"].pop()
        for family in ("PREDICTIVE_ML", "DEEP_RL"):
            result = self.execute(reseal_math(anchor), family)
            self.assertEqual(result["recommended_orders"], [])
            self.assertTrue(
                all(
                    r["base_policy_source"] == "EXCLUDED"
                    for r in result["learned_decision_evidence"]
                )
            )
            fallback = copy.deepcopy(anchor)
            fallback["policy_input"]["adjustments"] = [adjustment(kind="SOURCE_FALLBACK")]
            result = self.execute(reseal_math(fallback), family)
            self.assertEqual(
                result["learned_decision_evidence"][0]["effective_policy"]["target_inventory_qty"],
                "40",
            )

    def test_model_hash_version_scope_and_train_cutoff_fail_closed(self):
        anchor = build_math()
        model = model_fixture(anchor)
        data = model.to_dict()
        data["weights"]["linear.bias"] = ["1"]
        with self.assertRaisesRegex(InventoryInputError, "MODEL_HASH_MISMATCH"):
            ModelArtifact.from_dict(data)
        for change in ("version", "scope", "date"):
            data = model.to_dict()
            if change == "version":
                expected = {**model.reference, "version": "other"}
            elif change == "scope":
                data["scope"]["site_cd"] = "OTHER"
                expected = None
            else:
                data["trained_through_date"] = anchor["recommendation"]["canonical_input"][
                    "context"
                ]["plan_start_date"]
                expected = None
            altered = ModelArtifact.from_dict(reseal_model(data))
            with self.assertRaises(InventoryInputError):
                altered.validate_for(
                    expected or altered.reference,
                    anchor["recommendation"]["canonical_input"]["context"],
                )

    def test_policy_profile_mismatch_rejected_before_inference(self):
        anchor = build_math()
        data = model_fixture(anchor).to_dict()
        data["policy_contract"]["replenishment_cycle_weeks"] = 2
        model = ModelArtifact.from_dict(reseal_model(data))
        with self.assertRaisesRegex(InventoryInputError, "MODEL_POLICY_CONTRACT_MISMATCH"):
            compile_strategy(MathematicalPolicyRequest.from_dict(anchor), model, SCOPE)

    def test_unknown_fields_shapes_numbers_and_normalizer_rejected(self):
        original = model_fixture(build_math()).to_dict()
        for kind in (
            "extra",
            "weight_name",
            "shape",
            "nan",
            "infinity",
            "nonstr",
            "invalid",
            "zero_std",
            "tiny_std",
            "algorithm",
            "feature",
        ):
            data = copy.deepcopy(original)
            if kind == "extra":
                data["pickle_path"] = "anything"
            elif kind == "weight_name":
                data["weights"]["anything"] = ["0"]
            elif kind == "shape":
                data["weights"]["linear.weight"] = [["0"]]
            elif kind in ("nan", "infinity", "nonstr", "invalid"):
                data["weights"]["linear.bias"] = [
                    {"nan": "NaN", "infinity": "Infinity", "nonstr": 1, "invalid": "xyz"}[kind]
                ]
            elif kind == "zero_std":
                data["normalizer"]["std"][0] = "0"
            elif kind == "tiny_std":
                data["normalizer"]["std"][0] = "1e-999"
            elif kind == "algorithm":
                data["algorithm"] = "unknown"
            else:
                data["feature_contract_id"] = "unknown"
            with self.subTest(kind=kind), self.assertRaises(InventoryInputError):
                ModelArtifact.from_dict(reseal_model(data))

    def test_pure_json_prediction_and_tie_break(self):
        anchor = build_math()
        self.assertAlmostEqual(predict(model_fixture(anchor).to_dict(), [0.0] * 14), 0.5)
        data = model_fixture(anchor, "DEEP_RL").to_dict()
        data["weights"]["actor.bias"] = ["0"] * 4
        self.assertEqual(predict(data, [0.0] * 14), 0)
        self.assertEqual(
            normalized([1e20] * 14, {"mean": ["0"] * 14, "std": ["1"] * 14}), [10.0] * 14
        )
        self.assertTrue(math.isfinite(predict(model_fixture(anchor).to_dict(), [1e20] * 14)))

    def test_rounding_is_independent_of_callers_decimal_context(self):
        expected = self.execute()
        with localcontext() as context:
            context.prec = 8
            context.rounding = ROUND_FLOOR
            context.traps[Inexact] = True
            result = self.execute()
        self.assertEqual(expected, result)

    def test_cutoff_gate_runs_before_model_prediction(self):
        anchor = build_math()
        anchor["policy_input"]["status"] = "DRAFT"
        with patch("dsio_inventory_engine.learned.strategy.predict") as mock:
            with self.assertRaises(InventoryInputError):
                self.execute(reseal_math(anchor))
            mock.assert_not_called()

    def test_operational_environment_is_rejected(self):
        anchor = build_math()
        with self.assertRaisesRegex(InventoryInputError, "LOCAL_LEARNED_INFERENCE_ONLY"):
            RunLearnedPsiUseCase(DeploymentScope("DSE", "PRODUCTION")).execute(
                inference_request(anchor, model_fixture(anchor))
            )

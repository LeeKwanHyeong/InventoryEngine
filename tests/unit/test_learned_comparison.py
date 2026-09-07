"""Same-world paired comparison, shared production guards and causal features."""

import copy
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from learned_fixtures import model_fixture
from dsio_inventory_engine.evaluate_replenishment.demo import build_evaluation_request
from dsio_inventory_engine.fit_replenishment.data import repackage
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from dsio_inventory_engine.learned.application import evaluate_learned
from dsio_inventory_engine.learned.comparison import compare_strategies
from dsio_inventory_engine.training.demo import build_demo_request

SCOPE = DeploymentScope("DSE", "DEVELOPMENT")


class LearnedComparisonTests(unittest.TestCase):
    def setup_request(self):
        data = build_demo_request().to_dict()
        data["scenarios"] = [
            s for s in data["scenarios"] if s["scenario_id"] in ("BASE", "DELAY_2W")
        ]
        training = repackage(data)
        request = build_evaluation_request(training, SCOPE, split="TEST")
        models = [
            model_fixture(request.to_dict()["mathematical_request"], family)
            for family in ("PREDICTIVE_ML", "DEEP_RL")
        ]
        return training, request, models

    def test_all_families_use_same_initial_state_cost_and_horizon(self):
        training, _, models = self.setup_request()
        comparison = compare_strategies(training, models, SCOPE)
        self.assertEqual(len(comparison["comparisons"]), 2)
        for row in comparison["comparisons"]:
            strategies = row["strategies"]
            self.assertEqual(
                set(strategies),
                {"NO_NEW_SUPPLY_CONTROL", "MATHEMATICAL", "PREDICTIVE_ML", "DEEP_RL"},
            )
            self.assertEqual(len({v["initial_state_hash"] for v in strategies.values()}), 1)
            self.assertEqual({v["item_weeks"] for v in strategies.values()}, {65})
            self.assertEqual(len({v["cost_convention"] for v in strategies.values()}), 1)
        self.assertFalse(comparison["performance_superiority_claimed"])

    def test_all_orders_use_production_guard_and_world_keeps_balance(self):
        _, request, models = self.setup_request()
        for model in models:
            result = evaluate_learned(request, model, model.reference, SCOPE)
            for row in result["decision_evidence"]:
                accepted = Decimal(row["validation"]["accepted_order_qty"])
                self.assertEqual(accepted % 5, 0)
                self.assertTrue(accepted == 0 or accepted >= 10)
                world = row["world_ledger_row"]
                self.assertEqual(accepted, Decimal(world["order_qty"]))
                self.assertEqual(
                    Decimal(world["boh_qty"]) + Decimal(world["receipt_qty"]),
                    Decimal(world["eoh_qty"])
                    + Decimal(world["backorder_fulfilled_qty"])
                    + Decimal(world["current_demand_fulfilled_qty"]),
                )

    def test_model_reference_and_train_split_rejected(self):
        training, request, models = self.setup_request()
        with self.assertRaisesRegex(InventoryInputError, "HELD_OUT_COMPARISON_ONLY"):
            compare_strategies(training, models, SCOPE, split="TRAIN")
        with self.assertRaisesRegex(InventoryInputError, "MODEL_REFERENCE_MISMATCH"):
            evaluate_learned(
                request, models[0], {**models[0].reference, "content_hash": "0" * 64}, SCOPE
            )

    def test_current_hidden_actual_and_delay_never_enter_first_features(self):
        training, request, models = self.setup_request()
        original = evaluate_learned(request, models[1], models[1].reference, SCOPE)
        data = copy.deepcopy(training.to_dict())
        future = {r["yyyyww"] for r in data["calendar"][117:]}
        for row in data["demand"]:
            if row["yyyyww"] in future:
                row["demand_qty"] = "1000"
        changed = build_evaluation_request(repackage(data), SCOPE, split="TEST")
        altered = evaluate_learned(changed, models[1], models[1].reference, SCOPE)
        for a, b in zip(
            original["learned_decision_evidence"][:5],
            altered["learned_decision_evidence"][:5],
            strict=True,
        ):
            self.assertEqual(a["features"], b["features"])
            self.assertEqual(a["prediction"], b["prediction"])
            self.assertEqual(len(a["features"]), 14)

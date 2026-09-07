"""Actual CPU optimization, independent loss goldens and chronological boundaries."""

import copy
import importlib.util
import unittest
from unittest.mock import patch

from dsio_inventory_engine.fit_replenishment.data import repackage, supervised, training_fingerprint
from dsio_inventory_engine.fit_replenishment.demo import build_learning_request
from dsio_inventory_engine.fit_replenishment.train import TrainLearnedStrategiesUseCase
from dsio_inventory_engine.inventory_contracts.learning import LearningRequest
from dsio_inventory_engine.inventory_contracts.model import ModelArtifact
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from dsio_inventory_engine.learned.features import normalized
from dsio_inventory_engine.learned.predict import predict
from dsio_inventory_engine.training.demo import build_demo_request

SCOPE = DeploymentScope("DSE", "DEVELOPMENT")


class LearningDataTests(unittest.TestCase):
    def test_labels_are_causal_and_never_cross_training_end(self):
        data = build_demo_request().to_dict()
        labels = supervised(data)
        self.assertEqual(len(labels), 120)
        for row in labels:
            self.assertEqual(row["origin_index"], row["history_end_exclusive"])
            self.assertLessEqual(row["label_end_exclusive"], 104)
            self.assertGreaterEqual(row["origin_index"], 78)
        self.assertTrue(all(r["target"] == 0 for r in labels if r["item_id"] == "ZERO"))

    def test_future_changes_do_not_change_labels_or_training_fingerprint(self):
        data = build_demo_request().to_dict()
        changed = copy.deepcopy(data)
        future = {b["yyyyww"] for b in data["calendar"][104:]}
        for row in changed["demand"]:
            if row["yyyyww"] in future:
                row["demand_qty"] = "9876"
        changed = repackage(changed).to_dict()
        self.assertEqual(supervised(data), supervised(changed))
        self.assertEqual(training_fingerprint(data), training_fingerprint(changed))

    def test_bounded_recipe_rejects_excess_work_and_invalid_types(self):
        original = build_learning_request(quick=True).to_dict()
        for field, value in (
            ("seed", True),
            ("seed", -1),
            ("ppo_iterations", 65),
            ("ppo_epochs", 11),
            ("ml_epochs", 2001),
        ):
            with self.subTest(field=field), self.assertRaises(InventoryInputError):
                LearningRequest.from_dict({**original, field: value})

    def test_scope_rejected_before_training_or_preflight(self):
        with patch("dsio_inventory_engine.fit_replenishment.train.preflight") as mock:
            for scope in (
                DeploymentScope("OTHER", "DEVELOPMENT"),
                DeploymentScope("DSE", "PRODUCTION"),
            ):
                with self.assertRaises(InventoryInputError):
                    TrainLearnedStrategiesUseCase(scope).execute(build_learning_request(quick=True))
            mock.assert_not_called()


@unittest.skipUnless(importlib.util.find_spec("torch"), "optional learning extra requires torch")
class ActualLearningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import torch

        cls.before_rng = torch.random.get_rng_state().clone()
        cls.before_threads = torch.get_num_threads()
        cls.result = TrainLearnedStrategiesUseCase(SCOPE).execute(
            build_learning_request(quick=True)
        )
        cls.after_rng = torch.random.get_rng_state().clone()
        cls.after_threads = torch.get_num_threads()

    def test_both_optimizers_really_update_finite_weights(self):
        for family in ("ml_training", "ppo_training"):
            log = self.result[family]
            self.assertGreater(log["optimizer_steps"], 0)
            self.assertNotEqual(log["initial_weights_hash"], log["final_weights_hash"])
        self.assertLess(
            self.result["ml_training"]["final_pinball_loss"],
            self.result["ml_training"]["initial_pinball_loss"],
        )
        self.assertEqual(self.result["ppo_training"]["transitions"], 130)
        self.assertTrue(self.result["model_trained"])
        self.assertFalse(
            self.result["convergence_claimed"] or self.result["test_actuals_used_for_training"]
        )

    def test_rng_and_thread_settings_are_restored(self):
        import torch

        self.assertTrue(torch.equal(self.before_rng, self.after_rng))
        self.assertEqual(self.before_threads, self.after_threads)

    def test_independent_loss_and_finite_terminal_gae_goldens(self):
        import torch
        from dsio_inventory_engine.fit_replenishment.torch_models import (
            advantages,
            clipped_objective,
            pinball,
        )

        self.assertEqual(advantages([1, 2], [0.5, 0.25], gamma=1, lam=1), ([2.5, 1.75], [3.0, 2.0]))
        self.assertAlmostEqual(
            float(pinball(torch.tensor([1.0]), torch.tensor([2.0]), torch.tensor([0.9]))),
            0.9,
            places=6,
        )
        self.assertAlmostEqual(
            float(pinball(torch.tensor([1.0]), torch.tensor([0.0]), torch.tensor([0.9]))),
            0.1,
            places=6,
        )
        actual = clipped_objective(
            torch.tensor([1.5, 0.5, 1.5, 0.5]), torch.tensor([1.0, 1.0, -1.0, -1.0])
        )
        for value, expected in zip(actual.tolist(), [1.2, 0.5, -1.5, -0.8], strict=True):
            self.assertAlmostEqual(value, expected, places=6)

    def test_json_and_torch_inference_match(self):
        import torch
        from dsio_inventory_engine.fit_replenishment.torch_models import ActorCritic, QuantileModel

        for artifact in self.result["models"]:
            network = (
                QuantileModel() if artifact["strategy_type"] == "PREDICTIVE_ML" else ActorCritic()
            )

            def floats(value):
                return [floats(v) for v in value] if isinstance(value, list) else float(value)

            network.load_state_dict(
                {
                    k: torch.tensor(floats(v), dtype=torch.float64)
                    for k, v in artifact["weights"].items()
                }
            )
            for features in ([0.0] * 14, [0.1 * i for i in range(14)], [-4.0] * 14):
                scaled = normalized(features, artifact["normalizer"])
                if artifact["strategy_type"] == "PREDICTIVE_ML":
                    value = float(network(torch.tensor(scaled[6:10], dtype=torch.float64)).detach())
                    self.assertAlmostEqual(predict(artifact, features), value, places=10)
                else:
                    logits, _ = network(torch.tensor(scaled, dtype=torch.float64))
                    self.assertEqual(predict(artifact, features), int(logits.argmax()))

    def test_preflight_pairs_initial_state_and_compares_horizon_and_terminal(self):
        pre = self.result["preflight"]
        self.assertEqual((pre["short_weeks"], pre["long_weeks"]), (13, 26))
        self.assertEqual(pre["ppo_updates_before_preflight"], 0)
        self.assertEqual(len(pre["cases"]), 32)
        self.assertEqual(pre["synthetic_stress_horizons"], [52, 104])
        initial = {}
        for row in pre["cases"]:
            a, b = row["MATHEMATICAL"], row["NO_NEW_SUPPLY_CONTROL"]
            self.assertEqual(a["initial_state_hash"], b["initial_state_hash"])
            initial.setdefault(row["scenario_id"], set()).add(a["initial_state_hash"])
        self.assertTrue(all(len(hashes) == 1 for hashes in initial.values()))
        base = [r for r in pre["cases"] if r["scenario_id"] == "BASE"]
        self.assertGreater(len({r["NO_NEW_SUPPLY_CONTROL"]["total_cost"] for r in base}), 2)

    def test_repeat_training_ignores_changed_heldout_actuals(self):
        recipe = build_learning_request(quick=True).to_dict()
        data = recipe["training_request"]
        future = {b["yyyyww"] for b in data["calendar"][104:]}
        for row in data["demand"]:
            if row["yyyyww"] in future:
                row["demand_qty"] = "4567"
        recipe["training_request"] = repackage(data).to_dict()
        repeated = TrainLearnedStrategiesUseCase(SCOPE).execute(LearningRequest.from_dict(recipe))
        self.assertEqual(self.result["models"], repeated["models"])
        self.assertEqual(self.result["ml_training"], repeated["ml_training"])
        # World observation hashes bind the complete fixture, but policy samples and updates must match.
        for a, b in zip(
            self.result["ppo_training"]["iterations"],
            repeated["ppo_training"]["iterations"],
            strict=True,
        ):
            self.assertEqual(a["optimizer_updates"], b["optimizer_updates"])
        for model in repeated["models"]:
            ModelArtifact.from_dict(model)
        self.assertEqual(
            repeated["content_hash"],
            digest({k: v for k, v in repeated.items() if k != "content_hash"}),
        )

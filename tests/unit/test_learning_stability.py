"""Independent numeric gates, causal split generation and fail-closed study admission."""

import copy
import json
import unittest
from decimal import Decimal, localcontext
from pathlib import Path
from unittest.mock import patch

from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.stability import StabilityRequest, default_request
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from dsio_inventory_engine.stability.application import LearningStabilityUseCase, sealed
from dsio_inventory_engine.stability.data import (
    HOLDOUT_START,
    NAMES,
    build_dataset,
    partition_hashes,
    quantity,
    sensitivity_dataset,
)
from dsio_inventory_engine.stability.selection import assess, select_candidates, spread


def summary(cost="100", fill="0.8", item_fill="0.8", capacity="0"):
    return {
        "initial_state_hash": "1" * 64,
        "total_cost": cost,
        "on_time_fill_rate": fill,
        "currency": "USD",
        "item_weeks": 260,
        "cost_convention": "SAME",
        "physical_capacity_excess_qty": capacity,
        "metrics": [
            {"item_id": "A", "demand_qty": "100", "on_time_fill_rate": item_fill},
            {"item_id": "ZERO", "demand_qty": "0", "on_time_fill_rate": None},
        ],
    }


def validation_matrix(plan):
    return [
        {
            "candidate_id": c["candidate_id"],
            "family": f,
            "seed": seed,
            "scenario_id": scenario,
            "mathematical": summary(),
            "learned": summary(cost="95"),
        }
        for c in plan["candidates"]
        for seed in plan["training_seeds"]
        for scenario in ("BASE", "HIGH_SHORTAGE_COST", "SERVICE_99", "DELAY_2W")
        for f in ("PREDICTIVE_ML", "DEEP_RL")
    ]


class StabilityDataTests(unittest.TestCase):
    def test_roundtrip_and_bounded_exact_recipe(self):
        request = default_request()
        self.assertEqual(StabilityRequest.from_dict(request.to_dict()), request)
        for change in (
            "duplicate_seed",
            "reused_holdout",
            "budget",
            "unknown",
            "bool",
            "one_seed",
            "duplicate_candidate",
        ):
            data = request.to_dict()
            if change == "duplicate_seed":
                data["training_seeds"] = [1, 1, 2]
            elif change == "reused_holdout":
                data["holdout_data_seeds"][0] = data["development_data_seed"]
            elif change == "budget":
                data["candidates"][0]["ppo_iterations"] = 25
            elif change == "unknown":
                data["auto_deploy"] = True
            elif change == "bool":
                data["training_seeds"][0] = True
            elif change == "one_seed":
                data["training_seeds"] = [1]
            else:
                data["candidates"][1]["candidate_id"] = data["candidates"][0]["candidate_id"]
            with self.subTest(change=change), self.assertRaises(InventoryInputError):
                StabilityRequest.from_dict(data)

    def test_selection_does_not_generate_holdout_actuals(self):
        request = default_request()
        with patch("dsio_inventory_engine.stability.data.quantity", wraps=quantity) as generate:
            training = build_dataset(request)
        self.assertEqual(generate.call_count, HOLDOUT_START * len(NAMES))
        self.assertTrue(all(call.args[2] < HOLDOUT_START for call in generate.call_args_list))
        data = training.to_dict()
        future = {r["yyyyww"] for r in data["calendar"][HOLDOUT_START:]}
        self.assertEqual({r["demand_qty"] for r in data["demand"] if r["yyyyww"] in future}, {"0"})

    def test_new_holdouts_share_prefix_not_future_and_are_nonrepeating(self):
        request = default_request()
        hashes = []
        original = partition_hashes(build_dataset(request))
        for seed in request.to_dict()["holdout_data_seeds"]:
            data = build_dataset(request, holdout_seed=seed)
            parts = partition_hashes(data)
            self.assertEqual(
                {k: parts[k] for k in original if k != "TEST"},
                {k: original[k] for k in original if k != "TEST"},
            )
            self.assertGreater(parts["TEST"]["start_date"], "2027-06-30")
            self.assertEqual(parts["TEST"]["weeks"], 104)
            hashes.append(parts["TEST"]["content_hash"])
            for item in NAMES[:-1]:
                series = [
                    quantity(seed, item, i) for i in range(HOLDOUT_START, HOLDOUT_START + 104)
                ]
                self.assertNotEqual(series[:52], series[52:])
                self.assertNotEqual(series[:26], series[26:52])
            self.assertEqual(build_dataset(request, holdout_seed=seed), data)
        self.assertEqual(len(set(hashes)), 3)
        with self.assertRaisesRegex(InventoryInputError, "UNDECLARED_HOLDOUT"):
            build_dataset(request, holdout_seed=999)

    def test_horizon_and_terminal_probes_keep_identical_truth_prefix(self):
        full = build_dataset(default_request(), holdout_seed=81001)
        short = sensitivity_dataset(full, 52, "SOURCE").to_dict()
        zero = sensitivity_dataset(full, 104, "ZERO").to_dict()
        weeks = {r["yyyyww"] for r in short["calendar"]}
        self.assertEqual(
            short["demand"], [r for r in full.to_dict()["demand"] if r["yyyyww"] in weeks]
        )
        self.assertEqual(zero["demand"], full.to_dict()["demand"])
        self.assertEqual({s["cost"]["terminal_backlog_per_unit"] for s in zero["scenarios"]}, {"0"})
        with self.assertRaises(InventoryInputError):
            sensitivity_dataset(full, 53, "SOURCE")


class StabilityGateTests(unittest.TestCase):
    def test_numeric_spread_and_decimal_context_independence(self):
        self.assertEqual(
            spread([Decimal(1), Decimal(2), Decimal(3)]),
            {"n": 3, "mean": "2", "sample_std": "1", "min": "1", "max": "3"},
        )
        rows = validation_matrix(default_request().to_dict())[:1]
        expected = assess(rows)
        with localcontext() as ctx:
            ctx.prec = 4
            self.assertEqual(assess(rows), expected)
        self.assertTrue(expected["passed"])

    def test_low_cost_cannot_hide_service_capacity_or_mean_regression(self):
        row = validation_matrix(default_request().to_dict())[0]
        for value, reason in (
            (summary("50", "0.7"), "SERVICE_REGRESSION"),
            (summary("50", item_fill="0.7"), "ITEM_SERVICE_REGRESSION"),
            (summary("50", capacity="1"), "CAPACITY_EXCESS"),
            (summary("111"), "COST_REGRESSION"),
        ):
            result = assess([{**row, "learned": value}])
            self.assertFalse(result["passed"])
            self.assertIn(reason, result["violation_cells"][0]["reasons"])
        result = assess([{**row, "learned": summary("101")}])
        self.assertFalse(result["passed"])
        self.assertFalse(result["mean_cost_passed"])

    def test_exact_threshold_and_zero_demand_not_fake_fill(self):
        row = validation_matrix(default_request().to_dict())[0]
        result = assess([{**row, "learned": summary("100", "0.78", "0.75")}])
        self.assertTrue(result["passed"])
        self.assertEqual(Decimal(result["worst_item_fill_delta"]), Decimal("-0.05"))

    def test_complete_matrix_tie_break_and_keep_all_seeds(self):
        plan = default_request().to_dict()
        rows = validation_matrix(plan)
        result = select_candidates(plan, rows)
        self.assertEqual(result, select_candidates(plan, list(reversed(rows))))
        for selected in result["families"].values():
            self.assertEqual(selected["candidate_id"], "BUDGET_06")
            self.assertTrue(selected["all_seed_models_retained"])
        for broken in (rows[:-1], rows + rows[:1], rows[:-1] + rows[:1]):
            with self.assertRaisesRegex(InventoryInputError, "INCOMPLETE_VALIDATION_MATRIX"):
                select_candidates(plan, broken)

    def test_one_bad_seed_blocks_promotion_but_research_nominee_exists(self):
        plan = default_request().to_dict()
        rows = validation_matrix(plan)
        for row in rows:
            if row["seed"] == 303:
                row["learned"] = summary("50", "0.1")
        result = select_candidates(plan, rows)
        for chosen in result["families"].values():
            self.assertFalse(chosen["validation_gate_passed"])
            self.assertEqual(chosen["effective_strategy"], "MATHEMATICAL")
            self.assertEqual(chosen["purpose"], "RESEARCH_COMPARISON_ONLY")

    def test_unpaired_evidence_rejected(self):
        row = validation_matrix(default_request().to_dict())[0]
        for key, value in (
            ("initial_state_hash", "0" * 64),
            ("currency", "KRW"),
            ("item_weeks", 1),
        ):
            data = copy.deepcopy(row)
            data["learned"][key] = value
            with self.assertRaisesRegex(InventoryInputError, "UNPAIRED_STABILITY"):
                assess([data])


class StabilityBoundaryTests(unittest.TestCase):
    def test_development_company_guard_before_training(self):
        for scope in (
            DeploymentScope("DSE", "PRODUCTION"),
            DeploymentScope("OTHER", "DEVELOPMENT"),
        ):
            with patch("dsio_inventory_engine.stability.application.build_dataset") as build:
                with self.assertRaises(InventoryInputError):
                    LearningStabilityUseCase(scope).select(default_request())
                build.assert_not_called()

    def test_pinned_hash_and_recomputed_selection_before_holdout_generation(self):
        engine = LearningStabilityUseCase(DeploymentScope("DSE", "DEVELOPMENT"))
        plan = default_request().to_dict()
        document = sealed(
            {
                "contract_id": "io-stability-selection-v1",
                "contract_version": "1.0.0",
                "request": plan,
                "request_hash": digest(plan),
                "validation_rows": validation_matrix(plan),
                "selection": {},
            }
        )
        with patch("dsio_inventory_engine.stability.application.build_dataset") as build:
            with self.assertRaisesRegex(InventoryInputError, "SELECTION_HASH_MISMATCH"):
                engine.holdout(document, "0" * 64)
            with self.assertRaisesRegex(InventoryInputError, "SELECTION_RECOMPUTATION_MISMATCH"):
                engine.holdout(document, document["content_hash"])
            build.assert_not_called()


class StoredStabilityStudyTests(unittest.TestCase):
    """Replay orchestration using saved actual optimizer/evaluation evidence, not new training."""

    @classmethod
    def setUpClass(cls):
        path = (
            Path(__file__).resolve().parents[2]
            / "docs/architecture/evidence/learning-stability-selection-20260903.json"
        )
        cls.document = json.loads(path.read_text())
        cls.engine = LearningStabilityUseCase(DeploymentScope("DSE", "DEVELOPMENT"))

    def test_frozen_selection_validates_all_models_and_keeps_three_seeds(self):
        request, selected = self.engine.validate_selection(
            self.document, self.document["content_hash"]
        )
        self.assertEqual(request, default_request())
        self.assertEqual(len(selected), 6)
        for family in ("PREDICTIVE_ML", "DEEP_RL"):
            self.assertEqual(
                {r["seed"] for r in selected if r["family"] == family}, {101, 202, 303}
            )

    def test_tampered_bindings_rejected_even_after_outer_rehash(self):
        for change, error in (
            ("partition", "STABILITY_PARTITION"),
            ("recipe", "STABILITY_RECIPE"),
            ("model_ref", "STABILITY_VALIDATION_MODEL"),
            ("runs", "STABILITY_RUN_MATRIX"),
            ("result_hash", "STABILITY_TRAINING_RESULT_HASH"),
            ("request_hash", "STABILITY_REQUEST_HASH"),
        ):
            doc = copy.deepcopy(self.document)
            if change == "partition":
                doc["dataset_partitions"]["TRAIN"]["content_hash"] = "0" * 64
            elif change == "recipe":
                doc["runs"][0]["recipe_hash"] = "0" * 64
            elif change == "model_ref":
                doc["validation_rows"][0]["model_reference"]["content_hash"] = "0" * 64
            elif change == "runs":
                doc["runs"].pop()
            elif change == "request_hash":
                doc["request_hash"] = "0" * 64
            else:
                doc["runs"][0]["training_result"]["content_hash"] = "0" * 64
            doc.pop("content_hash")
            doc = sealed(doc)
            with self.subTest(change=change), self.assertRaisesRegex(InventoryInputError, error):
                self.engine.validate_selection(doc, doc["content_hash"])

    def test_selection_replays_exactly_without_learning_or_test_actuals(self):
        doc = self.document
        models = {
            m["content_hash"]: m["strategy_type"]
            for run in doc["runs"]
            for m in run["training_result"]["models"]
        }

        def train(recipe):
            return next(
                r["training_result"] for r in doc["runs"] if r["recipe_hash"] == recipe.input_hash
            )

        def mathematical(req):
            return next(
                r["mathematical"]
                for r in doc["validation_rows"]
                if r["evaluation_input_hash"] == req.input_hash
            )

        def learned(req, model, reference, scope):
            self.assertIn(reference["content_hash"], models)
            return next(
                r["learned"]
                for r in doc["validation_rows"]
                if r["model_reference"] == reference
                and r["evaluation_input_hash"] == req.input_hash
            )

        with (
            patch(
                "dsio_inventory_engine.stability.application.TrainLearnedStrategiesUseCase.execute",
                side_effect=train,
            ) as fit,
            patch(
                "dsio_inventory_engine.stability.application.EvaluateMathematicalStrategyUseCase.execute",
                side_effect=mathematical,
            ),
            patch(
                "dsio_inventory_engine.stability.application.evaluate_learned", side_effect=learned
            ),
            patch(
                "dsio_inventory_engine.stability.application.summarize",
                side_effect=lambda result: result,
            ),
        ):
            self.assertEqual(self.engine.select(default_request()), doc)
            self.assertEqual(fit.call_count, 6)

    def test_holdout_never_calls_training_or_reselects_from_test_scores(self):
        selected_before = copy.deepcopy(self.document["selection"])
        with (
            patch(
                "dsio_inventory_engine.stability.application.TrainLearnedStrategiesUseCase.execute"
            ) as fit,
            patch(
                "dsio_inventory_engine.stability.application.EvaluateMathematicalStrategyUseCase.execute",
                return_value=summary(),
            ),
            patch(
                "dsio_inventory_engine.stability.application.evaluate_hold", return_value=summary()
            ),
            patch(
                "dsio_inventory_engine.stability.application.evaluate_learned",
                return_value=summary("10", "0.99", "0.99"),
            ),
            patch(
                "dsio_inventory_engine.stability.application.summarize",
                side_effect=lambda result: result,
            ),
        ):
            result = self.engine.holdout(self.document, self.document["content_hash"])
            fit.assert_not_called()
        self.assertEqual(self.document["selection"], selected_before)
        self.assertFalse(result["selection_changed_after_holdout"])
        self.assertEqual(len(result["rows"]), 96)
        self.assertEqual(len(result["cases"]), 16)
        self.assertEqual(len(result["assessments"]), 6)
        self.assertEqual(
            {p["data_seed"] for p in result["dataset_partitions"]}, {81001, 81002, 81003}
        )

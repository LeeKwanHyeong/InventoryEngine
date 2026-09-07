"""Independent numerical Goldens, temporal leakage and rejection-path tests."""

import ast
import copy
import json
import sys
import unittest
from decimal import Decimal, Inexact, localcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from training_fixtures import reseal_training, training_fixture
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.training import TrainingRequest
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from dsio_inventory_engine.training.dataset import build_supervised
from dsio_inventory_engine.training.demo import build_demo_request
from dsio_inventory_engine.training.evaluation import ReferenceEpisode
from dsio_inventory_engine.training.prepare import (
    PrepareTrainingFoundationUseCase,
    run_reference_benchmark,
)
from dsio_inventory_engine.training.reference import (
    constrained_order,
    generate_warmup,
    order_delay,
    reference_flow,
    reference_policy,
    observed_demand,
    make_order,
)

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = json.loads((ROOT / "tests/fixtures/golden_training.json").read_text())
DEPLOYMENT = DeploymentScope("DSE", "DEVELOPMENT")


class TrainingFoundationTests(unittest.TestCase):
    def setUp(self):
        self.data = training_fixture()
        self.item = self.data["items"][0]
        self.scenario = self.data["scenarios"][0]

    def request(self):
        return TrainingRequest.from_dict(reseal_training(self.data))

    def warmup(self, origin=78):
        return generate_warmup(self.request().to_dict(), origin, self.scenario)

    def assert_subset(self, row, expected):
        self.assertEqual({k: row[k] for k in expected}, expected)

    def test_g01_g06_g09_constant_warmup_and_legacy_separation(self):
        result, expected = self.warmup(), GOLDEN["constant_warmup"]
        self.assertEqual(len(result["ledger"]), 52)
        self.assert_subset(result["ledger"][0], expected["first"])
        self.assert_subset(result["ledger"][-1], expected["last"])
        self.assertEqual(result["positions"][0]["on_hand_qty"], expected["boh"])
        self.assertEqual(
            [o["quantity"] for o in result["open_orders"]], expected["open_quantities"]
        )
        self.assertEqual(
            [o["planned_due_index"] for o in result["open_orders"]], expected["due_indices"]
        )
        self.assertEqual(result["positions"][0]["legacy_target_inventory_qty"], "100")
        self.assertEqual(result["positions"][0]["synthetic_target_inventory_qty"], "30")
        for row in result["policies"]:
            self.assert_subset(
                row,
                {k: expected[k] for k in ("safety_stock_qty", "rop_qty", "target_inventory_qty")},
            )
            self.assertEqual(row["history_end_index_exclusive"], row["week_index"])
        for previous, current in zip(result["ledger"], result["ledger"][1:]):
            self.assertEqual(previous["eoh_qty"], current["boh_qty"])

    def test_g02_surge_creates_backlog_and_linked_pipeline(self):
        self.data["demand"][77]["demand_qty"] = "50"
        result = self.warmup()
        self.assert_subset(result["ledger"][-1], GOLDEN["surge_last_week"])
        self.assertEqual(result["positions"][0]["backorder_qty"], "30")
        self.assertEqual(result["positions"][0]["prior_eoh_qty"], "0")

    def test_g03_zero_demand_does_not_inflate_moq(self):
        for row in self.data["demand"]:
            row["demand_qty"] = "0"
        self.item["moq"] = "100"
        result = self.warmup()
        self.assertEqual(result["positions"][0]["available_qty"], "0")
        self.assertEqual(result["orders"], [])
        evaluated = run_reference_benchmark(self.request(), DEPLOYMENT, "TEST", "BASE", "HOLD")
        self.assertIsNone(evaluated["metrics"][0]["on_time_fill_rate"])

    def test_g04_g05_joint_moq_multiple_capacity_goldens(self):
        for case in GOLDEN["joint_constraints"]:
            with self.subTest(case=case):
                item = {
                    **self.item,
                    "moq": case["moq"],
                    "lot_multiple": case["lot"],
                    "physical_capacity_qty": case["capacity"],
                }
                qty, _ = constrained_order(Decimal(case["raw"]), Decimal(case["occupied"]), item)
                self.assertEqual(qty, Decimal(case["expected"]))

    def test_g07_delay_goldens_and_original_due_preserved(self):
        for delay in (1, 2):
            with self.subTest(delay=delay):
                self.scenario.update(delay_kind="FIXED_DELAY", delay_weeks=delay)
                result = self.warmup()
                expected = GOLDEN[f"delay_{delay}w"]
                self.assertEqual(result["positions"][0]["available_qty"], expected["boh"])
                self.assertEqual(result["positions"][0]["backorder_qty"], expected["backorder"])
                self.assertEqual(
                    sum(Decimal(o["quantity"]) for o in result["open_orders"]),
                    Decimal(expected["open_quantity"]),
                )
                for order in result["orders"]:
                    self.assertEqual(order["actual_due_index"] - order["planned_due_index"], delay)

    def test_g08_intermittent_policy_has_independent_expected_values(self):
        self.item["lead_time_weeks"] = 1
        self.data["lookback_weeks"] = 13
        series = [Decimal(0)] * 12 + [Decimal(13)]
        policy = reference_policy(series, 13, self.item, self.data, self.scenario)
        self.assert_subset(
            policy,
            {
                "mean_demand_qty": "1",
                "safety_stock_qty": "6",
                "rop_qty": "7",
                "target_inventory_qty": "8",
            },
        )

    def test_g10_g12_active_period_13_week_fallback(self):
        self.item["active_from_index"] = 13
        inactive = {c["yyyyww"] for c in self.data["calendar"][:13]}
        self.data["demand"] = [r for r in self.data["demand"] if r["yyyyww"] not in inactive]
        result = self.warmup()
        self.assertEqual(result["policies"][0]["lookback_weeks"], 13)
        self.assertTrue(result["policies"][0]["lookback_fallback"])
        self.assertEqual(result["policies"][13]["lookback_weeks"], 26)

    def test_g11_g13_g14_invalid_history_policy_or_orphan_rejected(self):
        mutations = [
            (lambda d: d["items"][0].update(active_from_index=14), "INSUFFICIENT_HISTORY"),
            (lambda d: d["items"][0].pop("lead_time_weeks"), "CONTRACT_FIELDS"),
            (lambda d: d["demand"][0].update(item_id="ORPHAN"), "ORPHAN_DEMAND"),
        ]
        for mutate, code in mutations:
            data = training_fixture()
            mutate(data)
            with self.subTest(code=code), self.assertRaisesRegex(InventoryInputError, code):
                TrainingRequest.from_dict(reseal_training(data))

    def test_hold_cost_and_service_goldens(self):
        result = run_reference_benchmark(self.request(), DEPLOYMENT, "TEST", "BASE", "HOLD")
        expected = GOLDEN["hold_test"]
        for key in ("eoh_qty", "receipt_qty", "backorder_close_qty"):
            self.assertEqual([r[key] for r in result["ledger"]], expected[key])
        self.assertEqual(result["total_cost"], expected["total_cost"])
        self.assertEqual(result["metrics"][0]["on_time_fill_rate"], expected["fill_rate"])
        self.assertEqual(result["ledger"][-1]["terminal_backlog_cost"], "100")

    def test_future_mutation_cannot_change_warmup_features_or_first_decision(self):
        original = self.request()
        before = self.warmup()
        observed = ReferenceEpisode(original, DEPLOYMENT, "TEST", "BASE").observe()
        self.data["demand"][80]["demand_qty"] = "1000"
        changed = self.request()
        self.assertEqual(before, self.warmup())
        self.assertEqual(observed, ReferenceEpisode(changed, DEPLOYMENT, "TEST", "BASE").observe())
        first = run_reference_benchmark(original, DEPLOYMENT, "TEST", "BASE", "REFERENCE_R_S")
        second = run_reference_benchmark(changed, DEPLOYMENT, "TEST", "BASE", "REFERENCE_R_S")
        self.assertEqual(first["decisions"][0], second["decisions"][0])
        self.assertNotEqual(first["ledger"][0], second["ledger"][0])

    def test_split_boundaries_label_availability_and_no_random_shuffle(self):
        data = build_supervised(self.request().to_dict())
        self.assertEqual([p["from_index"] for p in data["partitions"]], [78, 79, 80])
        self.assertEqual([p["to_index_exclusive"] for p in data["partitions"]], [79, 80, 84])
        for row, label in zip(data["features"], data["labels"], strict=True):
            self.assertEqual(row["row_id"], label["row_id"])
            self.assertNotIn("actual_demand_qty", row)
            self.assertGreater(label["available_on"], row["issued_on"])
            self.assertEqual(row["history_end_index_exclusive"], row["origin_index"])

    def test_delayed_supply_truth_hidden_and_observation_detached(self):
        self.scenario.update(delay_kind="FIXED_DELAY", delay_weeks=2)
        episode = ReferenceEpisode(self.request(), DEPLOYMENT, "TEST", "BASE")
        observation = episode.observe()
        self.assertNotIn("actual_due", json.dumps(observation))
        self.assertNotIn("delay_weeks", json.dumps(observation))
        observation["items"][0]["forecast"]["demand_history_qty"][0] = "999"
        self.assertNotEqual(observation, episode.observe())

    def test_horizon_tail_orders_preserved_and_costed_at_order(self):
        episode = ReferenceEpisode(self.request(), DEPLOYMENT, "TEST", "BASE")
        for index in range(4):
            transition = episode.step({"A": "10" if index == 3 else "0"})
        result = episode.result()
        self.assertEqual(result["terminal_open_orders"][-1]["quantity"], "10")
        self.assertEqual(result["terminal_open_orders"][-1]["planned_due_index"], 85)
        self.assertEqual(result["ledger"][-1]["purchase_cost"], "30")
        self.assertEqual(result["ledger"][-1]["order_fixed_cost"], "2")
        self.assertEqual(transition["reward"], "-182")
        result["ledger"][0]["eoh_qty"] = "999"
        self.assertNotEqual(result, episode.result())

    def test_episode_rejects_bad_actions_without_advancing(self):
        episode = ReferenceEpisode(self.request(), DEPLOYMENT, "TEST", "BASE")
        original = episode.observe()
        for actions in ({}, {"A": "-1"}, {"A": "NaN"}, {"A": 1}, {"A": "1", "B": "1"}):
            with self.subTest(actions=actions), self.assertRaises(InventoryInputError):
                episode.step(actions)
            self.assertEqual(original, episode.observe())
        with self.assertRaisesRegex(InventoryInputError, "EPISODE_NOT_FINISHED"):
            episode.result()
        while not episode.done:
            episode.step({"A": "0"})
        with self.assertRaisesRegex(InventoryInputError, "EPISODE_FINISHED"):
            episode.step({"A": "0"})

    def test_sparse_history_requires_explicit_profile_and_keeps_evidence(self):
        self.data["demand"].pop(0)
        with self.assertRaisesRegex(InventoryInputError, "MISSING_DEMAND"):
            self.request()
        self.data["missing_history"] = "MISSING_ACTIVE_WEEK_IS_ZERO"
        result = PrepareTrainingFoundationUseCase(DEPLOYMENT).execute(self.request())
        self.assertEqual(len(result["imputation_evidence"]), 1)
        self.data["demand"].pop()
        with self.assertRaisesRegex(InventoryInputError, "MISSING_DEMAND"):
            self.request()

    def test_selected_and_disruption_delay_rules(self):
        selected = {
            **self.scenario,
            "delay_kind": "SELECTED_ORDER_DELAY",
            "delay_weeks": 2,
            "selected_order_ids": ["WARM:A:77"],
        }
        self.assertEqual(order_delay(selected, "WARM:A:77", 79), 2)
        self.assertEqual(order_delay(selected, "WARM:A:76", 78), 0)
        disruption = {
            **self.scenario,
            "delay_kind": "DISRUPTION_WINDOW",
            "delay_weeks": 1,
            "disruption_from_index": 78,
            "disruption_to_index": 80,
        }
        self.assertEqual([order_delay(disruption, "X", i) for i in (77, 78, 79, 80)], [0, 1, 1, 0])

    def test_reproducible_hash_reordering_and_external_decimal_context(self):
        request = self.request()
        first = PrepareTrainingFoundationUseCase(DEPLOYMENT).execute(request)
        self.data["demand"].reverse()
        self.assertEqual(request, self.request())
        with localcontext() as context:
            context.prec = 9
            context.traps[Inexact] = True
            second = PrepareTrainingFoundationUseCase(DEPLOYMENT).execute(request)
        self.assertEqual(first, second)
        self.assertEqual(
            first["content_hash"], digest({k: v for k, v in first.items() if k != "content_hash"})
        )

    def test_scope_environment_and_unknown_selection_fail_closed(self):
        request = self.request()
        for scope, code in (
            (DeploymentScope("DSE", "PRODUCTION"), "SYNTHETIC_PRODUCTION_FORBIDDEN"),
            (DeploymentScope("OTHER", "DEVELOPMENT"), "DEPLOYMENT_COMPANY_MISMATCH"),
        ):
            with self.assertRaisesRegex(InventoryInputError, code):
                PrepareTrainingFoundationUseCase(scope).execute(request)
            with self.assertRaisesRegex(InventoryInputError, code):
                ReferenceEpisode(request, scope, "TEST", "BASE")
        for split, scenario, code in (
            ("OTHER", "BASE", "UNKNOWN_SPLIT"),
            ("TEST", "X", "UNKNOWN_SCENARIO"),
        ):
            with self.assertRaisesRegex(InventoryInputError, code):
                ReferenceEpisode(request, DEPLOYMENT, split, scenario)
        with self.assertRaisesRegex(InventoryInputError, "UNKNOWN_BENCHMARK"):
            run_reference_benchmark(request, DEPLOYMENT, "TEST", "BASE", "PPO")

    def test_contract_adversarial_cases(self):
        cases = [
            (lambda d: d.update(content_hash="0" * 64), "TRAINING_INPUT_HASH_MISMATCH", False),
            (lambda d: d.update(extra=True), "CONTRACT_FIELDS", True),
            (lambda d: d.update(lookback_weeks=12), "LOOKBACK_WEEKS", True),
            (lambda d: d.update(warmup_weeks=51), "INSUFFICIENT_HISTORY", True),
            (lambda d: d.update(test_weeks=3), "TIME_SPLIT_RANGE", True),
            (lambda d: d["demand"].append(copy.deepcopy(d["demand"][0])), "DUPLICATE_DEMAND", True),
            (lambda d: d["demand"][0].update(uom="KG"), "UOM_MISMATCH", True),
            (lambda d: d["demand"][0].update(demand_qty="0.1"), "QUANTITY_STEP", True),
            (
                lambda d: d["calendar"][0].update(end_date=d["calendar"][0]["start_date"]),
                "FULL_WEEK_REQUIRED",
                True,
            ),
            (lambda d: d["items"][0].update(lead_time_weeks=0), "LEAD_TIME_RANGE", True),
            (lambda d: d["scenarios"][0].update(delay_weeks=1), "DELAY_CONFIGURATION", True),
        ]
        for mutate, code, seal in cases:
            value = training_fixture()
            mutate(value)
            with self.subTest(code=code), self.assertRaisesRegex(InventoryInputError, code):
                TrainingRequest.from_dict(reseal_training(value) if seal else value)

    def test_generator_has_no_production_calculator_imports(self):
        for path in (ROOT / "src/dsio_inventory_engine/training").glob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    imported = ast.unparse(node)
                    self.assertNotIn("simulate_inventory", imported)
                    self.assertNotIn("recommend_replenishment", imported)

    def test_reference_flow_prior_backlog_before_current_demand(self):
        row = reference_flow(Decimal(10), Decimal(8), Decimal(0), Decimal(10))
        self.assert_subset(
            row,
            {
                "current_demand_fulfilled_qty": "2",
                "backorder_fulfilled_qty": "8",
                "eoh_qty": "0",
                "backorder_close_qty": "8",
            },
        )

    def test_capacity_seed_rejection(self):
        self.item["physical_capacity_qty"] = "10"
        with self.assertRaisesRegex(InventoryInputError, "WARMUP_SEED_EXCEEDS_CAPACITY"):
            self.warmup()

    def test_demo_profiles_are_explicit_deterministic_synthetic(self):
        data = build_demo_request().to_dict()
        self.assertEqual(len(data["calendar"]), 130)
        self.assertEqual(len(data["demand"]), 650)
        self.assertEqual(len(data["scenarios"]), 6)
        self.assertEqual(data, build_demo_request().to_dict())

    def test_cost_service_profiles_visible_and_cost_only_change_preserves_inventory(self):
        request = self.request()
        baseline = run_reference_benchmark(request, DEPLOYMENT, "TEST", "BASE", "REFERENCE_R_S")
        self.scenario["cost"]["holding_per_unit_week"] = "10"
        episode = ReferenceEpisode(self.request(), DEPLOYMENT, "TEST", "BASE")
        self.assertEqual(episode.observe()["cost_profile"]["holding_per_unit_week"], "10")
        high_cost = run_reference_benchmark(
            self.request(), DEPLOYMENT, "TEST", "BASE", "REFERENCE_R_S"
        )
        self.assertEqual(
            [r["eoh_qty"] for r in baseline["ledger"]], [r["eoh_qty"] for r in high_cost["ledger"]]
        )
        self.assertGreater(Decimal(high_cost["total_cost"]), Decimal(baseline["total_cost"]))
        self.scenario["service_level_code"] = "SL_99"
        self.assertEqual(
            ReferenceEpisode(self.request(), DEPLOYMENT, "TEST", "BASE").observe()["items"][0][
                "effective_service_level_code"
            ],
            "SL_99",
        )

    def test_unknown_future_disruption_does_not_leak_at_episode_start(self):
        before = ReferenceEpisode(self.request(), DEPLOYMENT, "TEST", "BASE").observe()
        self.scenario.update(
            delay_kind="DISRUPTION_WINDOW",
            delay_weeks=2,
            disruption_from_index=82,
            disruption_to_index=84,
        )
        after = ReferenceEpisode(self.request(), DEPLOYMENT, "TEST", "BASE").observe()
        self.assertEqual(before, after)

    def test_all_demo_paths_conserve_inventory_backlog_and_supply(self):
        data = PrepareTrainingFoundationUseCase(DEPLOYMENT).execute(build_demo_request())
        for result in data["evaluations"]:
            for item in result["metrics"]:
                item_id = item["item_id"]
                rows = [r for r in result["ledger"] if r["item_id"] == item_id]
                for row in rows:
                    self.assertEqual(
                        Decimal(row["boh_qty"])
                        + Decimal(row["receipt_qty"])
                        - Decimal(row["fulfilled_qty"]),
                        Decimal(row["eoh_qty"]),
                    )
                    self.assertEqual(
                        Decimal(row["backorder_open_qty"])
                        + Decimal(row["demand_qty"])
                        - Decimal(row["fulfilled_qty"]),
                        Decimal(row["backorder_close_qty"]),
                    )
                for prev, cur in zip(rows, rows[1:]):
                    self.assertEqual(prev["eoh_qty"], cur["boh_qty"])
                    self.assertEqual(prev["backorder_close_qty"], cur["backorder_open_qty"])
                supplied = sum(
                    Decimal(o["quantity"])
                    for o in result["initial"]["open_orders"] + result["orders"]
                    if o["item_id"] == item_id
                )
                received = sum(Decimal(r["receipt_qty"]) for r in rows)
                self.assertEqual(supplied, received + Decimal(item["terminal_open_supply_qty"]))

    def test_fractional_uom_and_canonical_quantity_bound(self):
        self.item.update(uom="KG", quantity_step="0.1", moq="0.2", lot_multiple="0.2")
        for row in self.data["demand"]:
            row.update(uom="KG", demand_qty="0.5")
        result = self.warmup()
        for order in result["orders"]:
            self.assertEqual(Decimal(order["quantity"]) % Decimal("0.2"), 0)
        self.assertEqual(result["positions"][0]["uom"], "KG")
        with self.assertRaisesRegex(InventoryInputError, "QUANTITY_RANGE"):
            make_order(
                self.data,
                self.item,
                self.scenario,
                78,
                Decimal("1000000000001"),
                "WARM",
                "BUCKET_END",
            )

    def test_long_item_identifier_and_inactive_reference_guard(self):
        item = {**self.item, "item_id": "A" * 128}
        order = make_order(self.data, item, self.scenario, 78, Decimal("10"), "WARM", "BUCKET_END")
        self.assertLessEqual(len(order["order_id"]), 128)
        with self.assertRaisesRegex(InventoryInputError, "INACTIVE_DEMAND"):
            observed_demand([None], 0)
        with self.assertRaisesRegex(InventoryInputError, "INSUFFICIENT_HISTORY"):
            reference_policy([], 12, item, self.data, self.scenario)

    def test_multi_item_rounding_failure_is_atomic(self):
        other = {**self.item, "item_id": "B", "lot_multiple": "3"}
        self.data["items"].append(other)
        self.data["demand"] += [{**r, "item_id": "B"} for r in list(self.data["demand"])]
        episode = ReferenceEpisode(self.request(), DEPLOYMENT, "TEST", "BASE")
        before = episode.observe()
        with self.assertRaisesRegex(InventoryInputError, "QUANTITY_RANGE"):
            episode.step({"A": "10", "B": "1000000000000"})
        self.assertEqual(before, episode.observe())
        while not episode.done:
            episode.step({"A": "0", "B": "0"})
        self.assertEqual(episode.result()["orders"], [])


if __name__ == "__main__":
    unittest.main()

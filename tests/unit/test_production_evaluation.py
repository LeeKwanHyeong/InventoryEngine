"""Production policy is executed, World arithmetic is independently hand-checked."""

import copy
import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from evaluation_fixtures import DEV, fixture, override, reseal_evaluation
from training_fixtures import training_fixture, reseal_training
from dsio_inventory_engine.evaluate_replenishment.application import (
    EvaluateMathematicalStrategyUseCase,
)
from dsio_inventory_engine.evaluate_replenishment.demo import build_evaluation_request
from dsio_inventory_engine.inventory_contracts.evaluation import ProductionEvaluationRequest
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.training import TrainingRequest
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    canonical_json,
    digest,
)
from dsio_inventory_engine.recommend_replenishment.application.mathematical import (
    prepare_mathematical_strategy,
    RunMathematicalReplenishmentUseCase,
)
from dsio_inventory_engine.recommend_replenishment.application.guard import validate_action
from dsio_inventory_engine.training.demo import build_demo_request
from dsio_inventory_engine.training.evaluation import ReferenceEpisode

GOLDEN = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures/golden_production_evaluation.json").read_text()
)


def evaluate(data):
    return EvaluateMathematicalStrategyUseCase(DEV).execute(
        ProductionEvaluationRequest.from_dict(data)
    )


class ProductionEvaluationTests(unittest.TestCase):
    def test_hand_calculated_base_and_delay_goldens(self):
        for case in GOLDEN["cases"]:
            with self.subTest(case=case["scenario"]):
                training = training_fixture()
                training["scenarios"][0].update(
                    scenario_id=case["scenario"],
                    delay_kind="FIXED_DELAY" if case["delay_weeks"] else "NO_DELAY",
                    delay_weeks=case["delay_weeks"],
                )
                result = evaluate(fixture(reseal_training(training), case["scenario"]))
                world = result["world_result"]
                for key in ("order_qty", "receipt_qty", "eoh_qty", "backorder_close_qty"):
                    self.assertEqual([r[key] for r in world["ledger"]], case[key])
                for key in ("total_cost", "on_time_fill_rate", "terminal_open_supply_qty"):
                    self.assertEqual(world["metrics"][0][key], case[key])
                self.assertEqual(
                    result["mathematical_policy_report"]["policy_evidence"][0]["effective_policy"],
                    {"safety_stock_qty": "0", "rop_qty": "20", "target_inventory_qty": "30"},
                )
                self.assertIn(
                    "ARRIVAL_OUTSIDE_PLAN_HORIZON",
                    result["decision_evidence"][-1]["validation"]["reason_codes"],
                )
                for r in world["ledger"]:

                    def d(key):
                        return Decimal(r[key])

                    self.assertEqual(
                        d("boh_qty") + d("receipt_qty") - d("fulfilled_qty"), d("eoh_qty")
                    )
                    self.assertEqual(
                        d("backorder_open_qty") + d("demand_qty") - d("fulfilled_qty"),
                        d("backorder_close_qty"),
                    )

    def test_calls_actual_production_policy_and_guard(self):
        data = fixture()
        module = "dsio_inventory_engine.evaluate_replenishment.application"
        with (
            patch(
                module + ".prepare_mathematical_strategy", wraps=prepare_mathematical_strategy
            ) as policy,
            patch(module + ".validate_action", wraps=validate_action) as guard,
        ):
            result = evaluate(data)
        self.assertEqual(policy.call_count, 1)
        self.assertEqual(guard.call_count, 4)
        planned = RunMathematicalReplenishmentUseCase(DEV).execute(
            MathematicalPolicyRequest.from_dict(data["mathematical_request"])
        )
        self.assertEqual(
            result["mathematical_policy_report"], planned["mathematical_policy_report"]
        )
        self.assertEqual(result["strategy"]["implementation_id"], "historical-normal-r-s")
        self.assertTrue(result["production_strategy_executed"])
        self.assertEqual(result["evaluation_kind"], "PRODUCTION_MATHEMATICAL")
        self.assertFalse(result["performance_superiority_claimed"])

    def test_future_truth_hidden_but_next_observation_responds(self):
        data = fixture()
        base = evaluate(data)
        truth = data["training_request"]
        for row in truth["demand"]:
            if row["yyyyww"] == truth["calendar"][80]["yyyyww"]:
                row["demand_qty"] = "40"
        data["training_request"] = reseal_training(truth)
        shock = evaluate(data)
        self.assertEqual(
            base["decision_evidence"][0],
            shock["decision_evidence"][0]
            | {"world_ledger_row": base["decision_evidence"][0]["world_ledger_row"]},
        )
        self.assertEqual(
            shock["decision_evidence"][1]["observation"]["state"]["backorder_qty"], "20"
        )
        self.assertEqual(shock["decision_evidence"][1]["validation"]["accepted_order_qty"], "40")
        self.assertEqual(shock["world_result"]["total_cost"], "472")
        self.assertEqual(base["mathematical_policy_report"], shock["mathematical_policy_report"])
        for decision in shock["decision_evidence"]:
            obs = canonical_json(decision["observation"])
            for forbidden in (
                "actual_due",
                "actual_demand",
                "delay_weeks",
                "world_result",
                "cost_profile",
            ):
                self.assertNotIn(forbidden, obs)

    def test_future_delay_hidden_and_overdue_supply_retained(self):
        data = fixture()
        base = evaluate(data)
        training = data["training_request"]
        training["scenarios"][0].update(delay_kind="FIXED_DELAY", delay_weeks=2)
        data["training_request"] = reseal_training(training)
        delay = evaluate(data)
        for key in ("observation", "observation_hash", "proposal", "validation"):
            self.assertEqual(base["decision_evidence"][0][key], delay["decision_evidence"][0][key])
        pending = delay["decision_evidence"][1]["observation"]["pending_supply"]
        self.assertEqual(len(pending), 2)
        self.assertLess(
            min(r["due_date"] for r in pending),
            delay["decision_evidence"][1]["observation"]["decision_date"],
        )

    def test_period_policy_and_override_are_not_reinterpreted_by_world(self):
        data = override(fixture(), start_index=1)
        source = data["mathematical_request"]["recommendation"]["canonical_input"]
        policies = source["snapshots"]["policies"]["rows"]
        second = copy.deepcopy(policies[0])
        boundary = sorted(source["snapshots"]["calendar"]["rows"], key=lambda r: r["seq"])[1][
            "start_date"
        ]
        policies[0]["effective_to"] = boundary
        second.update(
            policy_id="PERIOD2", effective_from=boundary, lead_time_days=0, order_multiple="7"
        )
        policies.append(second)
        result = evaluate(reseal_evaluation(data))
        decision = result["decision_evidence"][1]
        self.assertEqual(decision["validation"]["accepted_order_qty"], "35")
        self.assertEqual(decision["world_ledger_row"]["receipt_qty"], "45")
        self.assertEqual(decision["world_action"]["planned_due_index"], 81)
        self.assertEqual(result["world_result"]["orders"][0]["planned_due_index"], 81)
        self.assertEqual(
            result["mathematical_policy_report"]["policy_evidence"][1]["effective_policy_source"],
            "APPROVED_OVERRIDE",
        )

    def test_approved_controls_and_missing_history_fallback(self):
        data = override(fixture(), kind="SOURCE_FALLBACK")
        data["mathematical_request"]["policy_input"]["history"] = []
        data["mathematical_request"]["recommendation"]["execution"]["item_controls"][0][
            "max_order_qty"
        ] = "5"
        result = evaluate(reseal_evaluation(data))
        self.assertEqual(result["decision_evidence"][0]["validation"]["accepted_order_qty"], "5")
        self.assertEqual(
            result["mathematical_policy_report"]["policy_evidence"][0]["effective_policy_source"],
            "SOURCE_FALLBACK",
        )
        data["mathematical_request"]["policy_input"]["adjustments"] = []
        result = evaluate(reseal_evaluation(data))
        self.assertTrue(
            all(d["proposal"]["action_type"] == "HOLD" for d in result["decision_evidence"])
        )

    def test_non_order_date_preserved(self):
        data = fixture()
        control = data["mathematical_request"]["recommendation"]["execution"]["item_controls"][0]
        control["order_dates"] = control["order_dates"][:1]
        result = evaluate(data)
        self.assertEqual([r["order_qty"] for r in result["world_result"]["ledger"]], ["0"] * 4)
        self.assertIn(
            "ORDER_CALENDAR_CLOSED", result["decision_evidence"][1]["validation"]["reason_codes"]
        )

    def test_overdue_capacity_and_future_cap_reservation(self):
        from dsio_inventory_engine.recommend_replenishment.application.guard import (
            capacity_headroom,
        )

        obs = evaluate(fixture())["decision_evidence"][1]["observation"]
        obs["policy_schedule"][0]["physical_max_capacity"] = "40"
        obs["pending_supply"].append(
            {"supply_id": "OVERDUE", "quantity": "15", "receipt_bucket": None}
        )
        self.assertEqual(capacity_headroom(obs, obs["calendar"][-1]), Decimal(5))

    def test_strict_contract_and_development_only(self):
        data = fixture()
        for key, value in (
            ("split", "RANDOM"),
            ("forecast_mode", "LATEST"),
            ("delay_scope", "INCLUDING_WARMUP"),
            ("extra", True),
        ):
            with self.subTest(key=key), self.assertRaises(InventoryInputError):
                ProductionEvaluationRequest.from_dict({**data, key: value})
        with self.assertRaisesRegex(InventoryInputError, "LOCAL_EVALUATION_ONLY"):
            EvaluateMathematicalStrategyUseCase(DeploymentScope("DSE", "PRODUCTION")).execute(
                ProductionEvaluationRequest.from_dict(data)
            )
        invalid = copy.deepcopy(data)
        invalid["training_request"]["content_hash"] = "0" * 64
        with self.assertRaisesRegex(InventoryInputError, "TRAINING_INPUT_HASH_MISMATCH"):
            evaluate(invalid)

    def test_cross_input_tampering_rejected(self):
        for kind in (
            "context",
            "boh",
            "pipeline",
            "forecast",
            "history",
            "policy",
            "lineage",
            "calendar",
        ):
            with self.subTest(kind=kind):
                data = fixture()
                source = data["mathematical_request"]["recommendation"]["canonical_input"]
                snaps = source["snapshots"]
                if kind == "context":
                    data["training_request"]["context"]["configuration_revision"] = "DIFFERENT"
                    data["training_request"] = reseal_training(data["training_request"])
                elif kind == "boh":
                    snaps["inventory"]["rows"][0].update(on_hand_qty="11", available_qty="11")
                    snaps["prior_inventory"]["rows"][0]["eoh_qty"] = "11"
                elif kind == "pipeline":
                    snaps["receipts"]["rows"][0]["due_qty"] = "11"
                elif kind == "forecast":
                    snaps["forecast"]["rows"][0]["forecast_qty"] = "11"
                elif kind == "history":
                    data["mathematical_request"]["policy_input"]["history"][0]["demand_qty"] = "11"
                elif kind == "policy":
                    snaps["policies"]["rows"][0]["moq"] = "2"
                elif kind == "lineage":
                    for name in ("inventory", "prior_inventory", "receipts"):
                        snaps[name]["metadata"]["generator_version"] = "DIFFERENT"
                else:
                    data["split"] = "TRAIN"
                with self.assertRaisesRegex(InventoryInputError, "EVALUATION_"):
                    evaluate(reseal_evaluation(data))

    def test_all_demo_scenarios_and_splits_deterministic(self):
        training = build_demo_request()
        for split in ("TRAIN", "VALIDATION", "TEST"):
            for scenario in training.to_dict()["scenarios"]:
                with self.subTest(split=split, scenario=scenario["scenario_id"]):
                    request = build_evaluation_request(
                        training, DEV, split=split, scenario_id=scenario["scenario_id"]
                    )
                    result = evaluate(request.to_dict())
                    self.assertEqual(
                        len(result["decision_evidence"]), (26 if split == "TRAIN" else 13) * 5
                    )
                    self.assertEqual(
                        result["content_hash"],
                        digest({k: v for k, v in result.items() if k != "content_hash"}),
                    )
                    self.assertFalse(result["database_writes"])
        data = fixture()
        self.assertEqual(evaluate(data), evaluate(data))

    def test_world_admitted_transport_rejects_stale_and_invalid_atomically(self):
        training = TrainingRequest.from_dict(training_fixture())
        world = ReferenceEpisode(training, DEV, "TEST", "BASE")
        before = world.observe()
        hold = {
            "A": {"quantity": "0", "order_id": None, "due_date": None, "planned_due_index": None}
        }
        for change in (
            {"quantity": "0.5"},
            {"order_id": "NONZERO"},
            {"quantity": "1", "order_id": "O1", "due_date": "bad", "planned_due_index": 81},
            {"quantity": "1", "order_id": "O1", "due_date": "2020-01-01", "planned_due_index": 81},
            {"quantity": "1", "order_id": "O1", "due_date": "2026-08-01", "planned_due_index": 100},
        ):
            with self.assertRaises(InventoryInputError):
                world.step_admitted({"A": {**hold["A"], **change}}, observation_hash=digest(before))
            self.assertEqual(world.observe(), before)
        with self.assertRaisesRegex(InventoryInputError, "STALE_WORLD_OBSERVATION"):
            world.step_admitted(hold, observation_hash="0" * 64)
        world.step_admitted(hold, observation_hash=digest(before))
        with self.assertRaisesRegex(InventoryInputError, "STALE_WORLD_OBSERVATION"):
            world.step_admitted(hold, observation_hash=digest(before))

    def test_reference_defaults_remain_distinct(self):
        training = TrainingRequest.from_dict(training_fixture())
        world = ReferenceEpisode(training, DEV, "TEST", "BASE")
        while not world.done:
            world.step({"A": "0"})
        self.assertEqual(world.result()["total_cost"], "170")
        self.assertNotEqual(
            world.result()["total_cost"], evaluate(fixture())["world_result"]["total_cost"]
        )
        from dsio_inventory_engine.training.prepare import run_reference_benchmark

        reference = run_reference_benchmark(training, DEV, "TEST", "BASE", "REFERENCE_R_S")
        self.assertEqual(reference["benchmark"], "REFERENCE_R_S")
        self.assertFalse(reference["production_strategy_executed"])
        self.assertNotEqual([r["order_qty"] for r in reference["ledger"]], ["0", "10", "0", "0"])

    def test_selected_production_order_delay_and_original_due_date(self):
        data = fixture()
        base = evaluate(data)
        order = base["world_result"]["orders"][0]
        training = data["training_request"]
        training["scenarios"][0].update(
            delay_kind="SELECTED_ORDER_DELAY", delay_weeks=1, selected_order_ids=[order["order_id"]]
        )
        data["training_request"] = reseal_training(training)
        delayed = evaluate(data)["world_result"]
        self.assertEqual(delayed["orders"][0]["due_date"], order["due_date"])
        self.assertEqual(delayed["orders"][0]["actual_due_index"], order["planned_due_index"] + 1)
        self.assertEqual(delayed["terminal_open_orders"][0]["order_id"], order["order_id"])

    def test_period_capacity_constraint_and_actual_excess_diagnostic(self):
        data = override(fixture(), start_index=1)
        source = data["mathematical_request"]["recommendation"]["canonical_input"]
        policies = source["snapshots"]["policies"]["rows"]
        boundary = sorted(source["snapshots"]["calendar"]["rows"], key=lambda r: r["seq"])[1][
            "start_date"
        ]
        later = {
            **policies[0],
            "policy_id": "LATER",
            "effective_from": boundary,
            "lead_time_days": 0,
            "order_multiple": "7",
            "physical_max_capacity": "35",
        }
        policies[0]["effective_to"] = boundary
        policies.append(later)
        result = evaluate(reseal_evaluation(data))
        self.assertEqual(result["decision_evidence"][1]["validation"]["accepted_order_qty"], "14")
        self.assertTrue(
            all(d["physical_capacity_excess_qty"] == "0" for d in result["decision_evidence"])
        )
        later["physical_max_capacity"] = "5"
        result = evaluate(reseal_evaluation(data))
        self.assertEqual(result["decision_evidence"][1]["validation"]["accepted_order_qty"], "0")
        self.assertEqual(result["decision_evidence"][1]["physical_capacity_excess_qty"], "15")

    def test_fractional_uom_and_posm(self):
        training = training_fixture()
        training["context"]["plan_type"] = "POSM"
        training["items"][0].update(uom="KG", quantity_step="0.1", lot_multiple="0.1")
        for row in training["demand"]:
            row.update(uom="KG", demand_qty="1.1")
        result = evaluate(fixture(reseal_training(training)))
        self.assertEqual(
            [r["order_qty"] for r in result["world_result"]["ledger"]], ["0", "1.1", "0", "0"]
        )
        self.assertEqual(result["prepared_input"]["context"]["plan_type"], "POSM")

    def test_input_cutoff_orphans_and_invalid_synthetic_recipe_rejected(self):
        for change, code in (("unsealed", "UNSEALED"), ("orphan", "ORPHAN"), ("cutoff", "EOH")):
            data = fixture()
            snaps = data["mathematical_request"]["recommendation"]["canonical_input"]["snapshots"]
            if change == "unsealed":
                snaps["inventory"]["status"] = "COLLECTING"
            elif change == "orphan":
                snaps["forecast"]["rows"][0]["item_id"] = "MISSING"
            else:
                snaps["prior_inventory"]["rows"][0]["eoh_qty"] = "0"
            with self.subTest(change=change), self.assertRaises(InventoryInputError):
                evaluate(reseal_evaluation(data))
        training = training_fixture()
        training["items"][0]["quantity_step"] = "0.5"
        with self.assertRaisesRegex(InventoryInputError, "EVALUATION_UOM_STEP_UNSUPPORTED"):
            fixture(reseal_training(training))
        with self.assertRaisesRegex(
            InventoryInputError, "EVALUATION_INITIAL_SUPPLY_OUTSIDE_HORIZON"
        ):
            build_evaluation_request(
                TrainingRequest.from_dict(training_fixture()), DEV, split="TRAIN"
            )

    def test_world_multi_item_invalid_transport_has_no_partial_mutation(self):
        world = ReferenceEpisode(build_demo_request(), DEV, "TEST", "BASE")
        before = world.observe()
        actions = {
            i["item_id"]: {
                "quantity": "0",
                "order_id": None,
                "due_date": None,
                "planned_due_index": None,
            }
            for i in before["items"]
        }
        key = before["items"][-1]["item_id"]
        actions[key]["quantity"] = "-1"
        with self.assertRaises(InventoryInputError):
            world.step_admitted(actions, observation_hash=digest(before))
        self.assertEqual(world.observe(), before)

    def test_external_decimal_context_does_not_change_result(self):
        from decimal import localcontext, ROUND_FLOOR

        request = fixture()
        expected = evaluate(request)
        with localcontext() as context:
            context.prec = 12
            context.rounding = ROUND_FLOOR
            self.assertEqual(evaluate(request), expected)


if __name__ == "__main__":
    unittest.main()

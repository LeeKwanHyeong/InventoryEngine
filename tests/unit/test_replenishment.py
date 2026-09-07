"""Independent numeric expectations and adversarial strategy-boundary tests."""

import copy
import sys
import unittest
from decimal import Decimal, localcontext
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from fixtures import GOLDEN, build_request
from strategy_fixtures import ContractProbe, binding, canonical, request

from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.replenishment import (
    RecommendationRequest,
    ReplenishmentProposal,
    STRATEGY_TYPES,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, canonical_json
from dsio_inventory_engine.recommend_replenishment.application.run import (
    RunRecommendedPsiUseCase,
    validate_controls,
)
from dsio_inventory_engine.simulate_inventory.application.baseline import RunPsiSimulationUseCase
from dsio_inventory_engine.simulate_inventory.application.step import InventoryState, advance_bucket

DEPLOYMENT = DeploymentScope("DSE", "DEVELOPMENT")


def run(req=None, probe=None):
    return RunRecommendedPsiUseCase(DEPLOYMENT).execute(req or request(), probe or ContractProbe())


def changed(req, callback):
    data = req.to_dict()
    callback(data)
    return RecommendationRequest.from_dict(data)


class StrategyExecutionTests(unittest.TestCase):
    def test_all_three_families_use_the_same_protocol_and_pending_ledger(self):
        for family in STRATEGY_TYPES:
            with self.subTest(family=family):
                probe = ContractProbe(family)
                output = run(request(family=family, lead_time_days=14), probe)
                self.assertEqual([r["eoh_qty"] for r in output["psi_rows"]], ["10", "10", "100"])
                self.assertEqual(
                    [r["recommended_receipt_qty"] for r in output["psi_rows"]], ["0", "0", "90"]
                )
                self.assertEqual(len(output["recommended_orders"]), 1)
                self.assertEqual(output["recommended_orders"][0]["quantity"], "90")
                self.assertEqual(
                    probe.observations[1].to_dict()["state"]["inventory_position_qty"], "100"
                )
                self.assertEqual(
                    probe.observations[1].to_dict()["pending_supply"][0]["supply_kind"],
                    "RECOMMENDED",
                )
                self.assertEqual(
                    [e["validation"]["status"] for e in output["decision_evidence"]],
                    ["ACCEPTED", "NO_ORDER", "NO_ORDER"],
                )
                self.assertFalse(
                    output["database_writes"]
                    or output["artifact_sealed"]
                    or output["run_claimed"]
                    or output["evidence_persisted"]
                )

    def test_hold_preserves_every_existing_golden_baseline_quantity(self):
        for case in GOLDEN["cases"]:
            with self.subTest(case=case["id"]):
                source = build_request(case)
                baseline = RunPsiSimulationUseCase(DEPLOYMENT).execute(
                    CanonicalInputRequest.from_dict(source)
                )
                output = run(request(source), ContractProbe(action="HOLD", quantity="0"))
                for base, recommended in zip(baseline["psi_rows"], output["psi_rows"], strict=True):
                    self.assertEqual(
                        {k: v for k, v in base.items() if k != "psi_scenario_type"},
                        {
                            k: v
                            for k, v in recommended.items()
                            if k
                            not in (
                                "psi_scenario_type",
                                "strategy_type",
                                "physical_capacity_excess_qty",
                            )
                        },
                    )

    def test_lead_time_mapping_zero_exact_midweek_and_horizon(self):
        for days, receipt, expected in (
            (0, "2026-09-28", ["100", "100", "100"]),
            (1, "2026-10-05", ["10", "100", "100"]),
            (7, "2026-10-05", ["10", "100", "100"]),
            (8, "2026-10-12", ["10", "10", "100"]),
            (15, None, ["10", "10", "10"]),
        ):
            with self.subTest(days=days):
                output = run(request(lead_time_days=days))
                self.assertEqual([r["eoh_qty"] for r in output["psi_rows"]], expected)
                first = output["decision_evidence"][0]["validation"]
                self.assertEqual(first["receipt_date"], receipt)
                if receipt is None:
                    self.assertEqual(first["reason_codes"], ["ARRIVAL_OUTSIDE_PLAN_HORIZON"])
                if days == 1:
                    self.assertEqual(first["due_date"], "2026-09-29")
                    self.assertIn("ARRIVAL_ROUNDED_FORWARD", first["reason_codes"])

    def test_moq_multiple_capacity_joint_feasibility(self):
        output = run(
            request(
                canonical(boh="80"), moq="100", order_multiple="50", physical_max_capacity="200"
            ),
            ContractProbe(quantity="217"),
        )
        first = output["decision_evidence"][0]["validation"]
        self.assertEqual(
            (
                first["raw_order_qty"],
                first["lot_rounded_qty"],
                first["capacity_headroom_qty"],
                first["accepted_order_qty"],
                first["uncovered_order_qty"],
            ),
            ("137", "150", "120", "100", "37"),
        )
        self.assertEqual([r["eoh_qty"] for r in output["psi_rows"]], ["80", "180", "180"])

    def test_capacity_below_moq_rejects_without_discarding_inventory(self):
        output = run(
            request(
                canonical(boh="180"), moq="100", order_multiple="50", physical_max_capacity="200"
            ),
            ContractProbe(quantity="300"),
        )
        self.assertEqual(output["recommended_orders"], [])
        self.assertEqual(
            output["decision_evidence"][0]["validation"]["reason_codes"],
            ["NO_FEASIBLE_ORDER_QUANTITY"],
        )
        self.assertEqual(output["psi_rows"][0]["eoh_qty"], "180")

    def test_reserved_is_in_capacity_but_not_available_position(self):
        output = run(
            request(canonical(boh="180", reserved="80"), physical_max_capacity="200"),
            ContractProbe(quantity="200"),
        )
        first = output["decision_evidence"][0]
        self.assertEqual(first["observation"]["state"]["inventory_position_qty"], "100")
        self.assertEqual(first["validation"]["accepted_order_qty"], "20")
        self.assertEqual(output["psi_rows"][1]["on_hand_eoh_qty"], "200")

    def test_capacity_reserves_future_committed_supply_without_demand_credit(self):
        source = canonical(
            boh="80",
            forecast=["80", "0", "0"],
            receipts=[{"week": 2, "qty": "100", "status": "CONFIRMED"}],
        )
        output = run(
            request(source, physical_max_capacity="200"),
            ContractProbe(action="ORDER_QTY", quantity="100"),
        )
        self.assertEqual(
            output["decision_evidence"][0]["validation"]["capacity_headroom_qty"], "20"
        )
        self.assertEqual(output["decision_evidence"][0]["validation"]["accepted_order_qty"], "20")

    def test_future_capacity_revision_and_current_policy_selection(self):
        source = canonical(boh="80")
        first = source["snapshots"]["policies"]["rows"][0]
        first["effective_to"] = "2026-10-05"
        second = {
            **first,
            "policy_id": "POLICY-2",
            "effective_from": "2026-10-05",
            "effective_to": "2026-10-19",
            "physical_max_capacity": "100",
            "lead_time_days": 0,
        }
        source["snapshots"]["policies"]["rows"].append(second)
        output = run(request(source), ContractProbe(quantity="200"))
        self.assertEqual(output["decision_evidence"][0]["validation"]["accepted_order_qty"], "20")
        self.assertEqual(
            output["decision_evidence"][1]["validation"]["source_policy_id"], "POLICY-2"
        )

    def test_preexisting_capacity_excess_is_diagnosed_not_clipped(self):
        output = run(
            request(canonical(boh="210"), physical_max_capacity="200"),
            ContractProbe(action="HOLD", quantity="0"),
        )
        self.assertEqual(output["psi_rows"][0]["eoh_qty"], "210")
        self.assertEqual(output["psi_rows"][0]["physical_capacity_excess_qty"], "10")

    def test_closed_order_calendar_and_approved_target_bounds(self):
        req = changed(
            request(), lambda d: d["execution"]["item_controls"][0].update(order_dates=[])
        )
        self.assertEqual(
            run(req)["decision_evidence"][0]["validation"]["reason_codes"],
            ["ORDER_CALENDAR_CLOSED"],
        )
        req = changed(
            request(), lambda d: d["execution"]["item_controls"][0].update(max_target_qty="50")
        )
        self.assertEqual(
            run(req)["decision_evidence"][0]["validation"]["reason_codes"],
            ["TARGET_OUTSIDE_APPROVED_BOUNDS"],
        )

    def test_direct_orders_can_be_disabled_for_learned_strategies(self):
        req = changed(
            request(family="DEEP_RL"),
            lambda d: d["execution"].update(allowed_action_types=["HOLD", "ORDER_UP_TO"]),
        )
        output = run(req, ContractProbe("DEEP_RL", "ORDER_QTY", "10"))
        self.assertEqual(output["recommended_orders"], [])
        self.assertEqual(
            output["decision_evidence"][0]["validation"]["reason_codes"], ["ACTION_NOT_APPROVED"]
        )

    def test_approved_order_limit_and_zero_requirement_do_not_force_moq(self):
        req = changed(
            request(moq="10", order_multiple="10"),
            lambda d: d["execution"]["item_controls"][0].update(max_order_qty="25"),
        )
        self.assertEqual(run(req)["decision_evidence"][0]["validation"]["accepted_order_qty"], "20")
        output = run(request(moq="100"), ContractProbe(quantity="10"))
        self.assertEqual(output["recommended_orders"], [])

    def test_existing_firm_supply_prevents_duplicate_and_unverified_does_not(self):
        for status, count in (
            ("CONFIRMED", 0),
            ("IN_TRANSIT", 0),
            ("ORDERED", 1),
            ("UNVERIFIED_DUE_IN", 1),
            ("RECEIVED", 1),
        ):
            with self.subTest(status=status):
                source = canonical(receipts=[{"week": 1, "qty": "90", "status": status}])
                output = run(request(source))
                self.assertEqual(len(output["recommended_orders"]), count)
                self.assertEqual(output["prepared_input"]["receipt_decisions"][0]["due_qty"], "90")

    def test_actual_backorder_carries_but_forecast_shortage_does_not(self):
        source = canonical(boh="0", forecast=["20", "0", "0"], backorder="5")
        output = run(request(source), ContractProbe(quantity="30"))
        rows = output["psi_rows"]
        self.assertEqual(
            (rows[0]["backorder_close_qty"], rows[0]["forecast_shortage_qty"]), ("5", "20")
        )
        self.assertEqual((rows[1]["fulfilled_backorder_qty"], rows[1]["eoh_qty"]), ("5", "30"))
        self.assertEqual(rows[2]["backorder_open_qty"], "0")

    def test_each_accepted_order_is_received_once_and_stock_conserved(self):
        output = run(
            request(canonical(boh="20", forecast=["10", "10", "10"])), ContractProbe(quantity="40")
        )
        rows = output["psi_rows"]
        self.assertEqual([r["eoh_qty"] for r in rows], ["10", "20", "20"])
        self.assertEqual([o["quantity"] for o in output["recommended_orders"]], ["20", "10"])
        self.assertEqual(sum(Decimal(r["recommended_receipt_qty"]) for r in rows), Decimal(30))
        for index, row in enumerate(rows):
            self.assertEqual(
                Decimal(row["boh_qty"])
                + Decimal(row["confirmed_supplier_receipt_qty"])
                + Decimal(row["recommended_receipt_qty"]),
                Decimal(row["eoh_qty"])
                + sum(
                    Decimal(row[k])
                    for k in (
                        "fulfilled_backorder_qty",
                        "fulfilled_confirmed_customer_order_qty",
                        "fulfilled_forecast_qty",
                    )
                ),
            )
            if index:
                self.assertEqual(row["boh_qty"], rows[index - 1]["eoh_qty"])

    def test_run_state_is_not_shared_and_retry_preserves_input_hash(self):
        req = request()
        first = run(req)
        retry = changed(
            req, lambda d: d["canonical_input"]["context"].update(engine_run_id="IO-RETRY")
        )
        self.assertEqual(req.input_hash, retry.input_hash)
        self.assertEqual(first["psi_content_hash"], run(retry)["psi_content_hash"])
        self.assertEqual(first["orders_content_hash"], run(req)["orders_content_hash"])
        changed_model = request(family="DEEP_RL")
        other = changed(
            changed_model,
            lambda d: d["execution"]["strategy"]["model"].update(content_hash="b" * 64),
        )
        self.assertNotEqual(changed_model.input_hash, other.input_hash)

    def test_decimal_context_does_not_change_result(self):
        expected = run(request(canonical(boh="0.1", uom="KG")), ContractProbe(quantity="0.3"))[
            "psi_content_hash"
        ]
        with localcontext() as ctx:
            ctx.prec = 2
            self.assertEqual(
                run(request(canonical(boh="0.1", uom="KG")), ContractProbe(quantity="0.3"))[
                    "psi_content_hash"
                ],
                expected,
            )


class BoundaryTests(unittest.TestCase):
    def test_canonical_rejection_before_strategy_call(self):
        probe = ContractProbe()
        data = request().to_dict()
        data["canonical_input"]["snapshots"]["inventory"]["status"] = "REJECTED"
        data["execution"]["canonical_input_hash"] = CanonicalInputRequest.from_dict(
            data["canonical_input"]
        ).input_hash
        req = RecommendationRequest.from_dict(data)
        with self.assertRaisesRegex(InventoryInputError, "UNSEALED_INPUT"):
            run(req, probe)
        self.assertEqual(probe.observations, [])

    def test_other_scope_and_changed_binding_rejected(self):
        data = request().to_dict()
        data["canonical_input"]["context"]["site_cd"] = "OTHER"
        with self.assertRaisesRegex(InventoryInputError, "INPUT_BINDING_MISMATCH"):
            RecommendationRequest.from_dict(data)
        with self.assertRaisesRegex(InventoryInputError, "DEPLOYMENT_COMPANY_MISMATCH"):
            RunRecommendedPsiUseCase(DeploymentScope("OTHER", "DEVELOPMENT")).execute(
                request(), ContractProbe()
            )

    def test_unknown_codes_fields_model_and_configuration(self):
        changes = [
            (lambda d: d["execution"].update(psi_scenario_type="BASELINE"), "INVALID_CODE"),
            (lambda d: d["execution"].update(execution_mode="PRODUCTION"), "INVALID_CODE"),
            (lambda d: d["execution"].update(capacity_mode="IGNORE"), "INVALID_CODE"),
            (
                lambda d: d["execution"].update(configuration_revision="CHANGED"),
                "CONFIGURATION_MISMATCH",
            ),
            (
                lambda d: d["execution"]["strategy"].update(strategy_type="DEEP_RL"),
                "MODEL_BINDING_REQUIRED",
            ),
            (lambda d: d["execution"].update(unexpected=True), "CONTRACT_FIELDS"),
        ]
        for change, code in changes:
            with self.subTest(code=code), self.assertRaisesRegex(InventoryInputError, code):
                changed(request(), change)

    def test_production_and_wrong_strategy_adapter_rejected(self):
        with self.assertRaisesRegex(InventoryInputError, "LOCAL_RECOMMENDATION_ONLY"):
            RunRecommendedPsiUseCase(DeploymentScope("DSE", "PRODUCTION")).execute(
                request(), ContractProbe()
            )
        with self.assertRaisesRegex(InventoryInputError, "STRATEGY_BINDING_MISMATCH"):
            run(request(), ContractProbe("DEEP_RL"))

    def test_controls_rejected_before_callback(self):
        changes = [
            (lambda d: d["execution"].update(item_controls=[]), "CONTROL_UNIVERSE_MISMATCH"),
            (lambda d: d["execution"]["item_controls"][0].update(uom="KG"), "CONTROL_UOM_MISMATCH"),
            (
                lambda d: d["execution"]["item_controls"][0].update(max_order_qty="0.1"),
                "CONTROL_UOM_PRECISION",
            ),
            (
                lambda d: d["execution"]["item_controls"][0].update(order_dates=["2026-09-29"]),
                "ORDER_DATE_NOT_BUCKET_START",
            ),
        ]
        for change, code in changes:
            probe = ContractProbe()
            with self.subTest(code=code), self.assertRaisesRegex(InventoryInputError, code):
                run(changed(request(), change), probe)
            self.assertEqual(probe.observations, [])
        # Test the expansion guard independently of Canonical Calendar validation:
        # both checks must reject before allocating repeated observation Evidence.
        output = run()
        for count, code in (
            (700, "RECOMMENDATION_OBSERVATION_LIMIT"),
            (600, "RECOMMENDATION_EVIDENCE_LIMIT"),
        ):
            prepared = copy.deepcopy(output["prepared_input"])
            prepared["calendar"] = [prepared["calendar"][0]] * count
            with self.subTest(count=count), self.assertRaisesRegex(InventoryInputError, code):
                validate_controls(prepared, output["execution"])

    def test_duplicate_control_dates_bounds_and_lead_time(self):
        changes = [
            (
                lambda d: d["execution"]["item_controls"].append(
                    copy.deepcopy(d["execution"]["item_controls"][0])
                ),
                "DUPLICATE_ITEM_CONTROL",
            ),
            (
                lambda d: d["execution"]["item_controls"][0].update(order_dates=["2026-09-28"] * 2),
                "DUPLICATE_ORDER_DATE",
            ),
            (
                lambda d: d["execution"]["item_controls"][0].update(min_target_qty="1001"),
                "INVALID_TARGET_BOUNDS",
            ),
        ]
        for change, code in changes:
            with self.subTest(code=code), self.assertRaisesRegex(InventoryInputError, code):
                changed(request(), change)
        with self.assertRaisesRegex(InventoryInputError, "LEAD_TIME_RANGE"):
            run(request(lead_time_days=3661))

    def test_stale_or_forged_strategy_results(self):
        for key, value, code in (
            ("decision_id", "DEC-OLD", "STALE_STRATEGY_DECISION"),
            ("observation_hash", "0" * 64, "STALE_STRATEGY_DECISION"),
            ("strategy", binding("DEEP_RL"), "STRATEGY_BINDING_MISMATCH"),
        ):

            class Forged(ContractProbe):
                def decide(self, observation):
                    result = super().decide(observation).to_dict()
                    result[key] = value
                    return ReplenishmentProposal.from_dict(result)

            with self.subTest(key=key), self.assertRaisesRegex(InventoryInputError, code):
                run(probe=Forged())

    def test_direct_dataclass_construction_cannot_bypass_validation(self):
        class Invalid(ContractProbe):
            def decide(self, observation):
                value = super().decide(observation).to_dict()
                value["quantity"] = "NaN"
                return ReplenishmentProposal(canonical_json(value))

        with self.assertRaisesRegex(InventoryInputError, "NON_FINITE_QUANTITY"):
            run(probe=Invalid())
        with self.assertRaisesRegex(InventoryInputError, "PROPOSAL_UOM_PRECISION"):
            run(probe=ContractProbe(quantity="0.1"))

    def test_adapter_failure_does_not_expose_exception_or_silently_fallback(self):
        probe = ContractProbe()
        with patch.object(probe, "decide", side_effect=RuntimeError("secret-placeholder")):
            with self.assertRaisesRegex(
                InventoryInputError, "STRATEGY_EXECUTION_FAILED"
            ) as failure:
                run(probe=probe)
        self.assertNotIn("secret-placeholder", failure.exception.evidence_json)
        with patch.object(probe, "decide", return_value={}):
            with self.assertRaisesRegex(InventoryInputError, "INVALID_STRATEGY_RESULT"):
                run(probe=probe)

    def test_observation_mutation_does_not_change_engine_state(self):
        class Mutating(ContractProbe):
            def decide(self, observation):
                copied = observation.to_dict()
                copied["state"]["available_qty"] = "999999"
                copied["pending_supply"].clear()
                return super().decide(observation)

        self.assertEqual(run(probe=Mutating())["psi_content_hash"], run()["psi_content_hash"])

    def test_step_rejects_negative_nonfinite_or_nondecimal_inputs(self):
        for invalid in (Decimal(-1), Decimal("NaN"), 1, True):
            with (
                self.subTest(invalid=invalid),
                self.assertRaisesRegex(InventoryInputError, "INVALID_PSI_STEP_QUANTITY"),
            ):
                advance_bucket(
                    InventoryState(invalid, Decimal(0), Decimal(0)),
                    confirmed_customer_order_qty=Decimal(0),
                    net_forecast_qty=Decimal(0),
                    confirmed_supplier_receipt_qty=Decimal(0),
                )


if __name__ == "__main__":
    unittest.main()

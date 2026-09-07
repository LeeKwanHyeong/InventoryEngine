"""Literal policy/PSI goldens plus time, scope, rounding and precedence boundaries."""

import copy
import sys
import unittest
from decimal import Decimal, Inexact, ROUND_FLOOR, localcontext
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from fixtures import reseal
from math_fixtures import GOLDEN_MATH, adjustment, build_math, reseal_math
from strategy_fixtures import ContractProbe

from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.replenishment import RecommendationRequest
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, canonical_json
from dsio_inventory_engine.recommend_replenishment.application.mathematical import (
    RunMathematicalReplenishmentUseCase,
)
from dsio_inventory_engine.recommend_replenishment.application.run import RunRecommendedPsiUseCase

DEPLOYMENT = DeploymentScope("DSE", "DEVELOPMENT")


def execute(data=None):
    return RunMathematicalReplenishmentUseCase(DEPLOYMENT).execute(
        MathematicalPolicyRequest.from_dict(data or build_math())
    )


def rebind(data):
    canonical = CanonicalInputRequest.from_dict(reseal(data["recommendation"]["canonical_input"]))
    data["recommendation"]["canonical_input"] = canonical.to_dict()
    data["recommendation"]["execution"]["canonical_input_hash"] = canonical.input_hash
    data["policy_input"]["context"]["canonical_input_hash"] = canonical.input_hash
    return reseal_math(data)


def report(result):
    return result["mathematical_policy_report"]["policy_evidence"]


class MathematicalGoldenTests(unittest.TestCase):
    def test_all_literal_policy_and_recommended_psi_goldens(self):
        for case in GOLDEN_MATH["cases"]:
            with self.subTest(case=case["id"]):
                result = execute(build_math(case))
                self.assertEqual(
                    list(report(result)[0]["python_calculated_policy"].values()),
                    case["expected_policy"],
                )
                self.assertEqual(
                    [r["quantity"] for r in result["recommended_orders"]], case["expected_orders"]
                )
                self.assertEqual([r["eoh_qty"] for r in result["psi_rows"]], case["expected_eoh"])
                self.assertEqual(result["policy_coverage"], "COMPLETE")
                self.assertFalse(
                    result["database_writes"]
                    or result["evidence_persisted"]
                    or result["approval_authenticated"]
                )
                for row in result["psi_rows"]:
                    self.assertEqual(
                        Decimal(row["boh_qty"])
                        + Decimal(row["confirmed_supplier_receipt_qty"])
                        + Decimal(row["recommended_receipt_qty"]),
                        sum(
                            Decimal(row[k])
                            for k in (
                                "eoh_qty",
                                "fulfilled_backorder_qty",
                                "fulfilled_confirmed_customer_order_qty",
                                "fulfilled_forecast_qty",
                            )
                        ),
                    )

    def test_sample_variance_factor_and_legacy_comparison_are_preserved(self):
        result = execute()
        stats = result["mathematical_policy_report"]["history_statistics"][0]
        self.assertEqual(
            (stats["mean_weekly_demand"], stats["variance"], stats["stddev"]), ("10", "4", "2")
        )
        first = report(result)[0]
        self.assertEqual(first["calculation_trace"]["service_level_factor"], "1.644853626951")
        self.assertEqual(
            first["source_comparison"], {"rop_qty": "-6", "target_inventory_qty": "-76"}
        )
        self.assertEqual(first["source_policy"]["source_target_inventory_qty"], "100")
        self.assertEqual(first["effective_policy_source"], "PYTHON_CALCULATED")

    def test_26_weeks_and_population_stddev(self):
        case = {**GOLDEN_MATH["cases"][0], "profile": {"lookback_weeks": 26, "stddev_ddof": 0}}
        output = execute(build_math(case))
        self.assertEqual(
            output["mathematical_policy_report"]["history_statistics"][0]["observation_count"], 26
        )
        self.assertEqual(
            report(output)[0]["effective_policy"],
            {"safety_stock_qty": "0", "rop_qty": "10", "target_inventory_qty": "20"},
        )
        data = build_math()
        data["policy_input"]["profile"]["stddev_ddof"] = 0
        stats = execute(reseal_math(data))["mathematical_policy_report"]["history_statistics"][0]
        self.assertAlmostEqual(float(stats["variance"]), 48 / 13, places=12)

    def test_profile_rounding_independent_of_callers_decimal_context(self):
        expected = execute()
        with localcontext() as ctx:
            ctx.prec = 8
            ctx.rounding = ROUND_FLOOR
            ctx.traps[Inexact] = True
            result = execute()
        self.assertEqual(result, expected)

    def test_service_half_has_no_normal_safety_stock(self):
        data = build_math({**GOLDEN_MATH["cases"][1], "policy": {"approved_service_level": "0.5"}})
        self.assertEqual(
            report(execute(data))[0]["python_calculated_policy"]["safety_stock_qty"], "0"
        )

    def test_intermittent_history_is_counted_without_dropping_zero_weeks(self):
        data = build_math({**GOLDEN_MATH["cases"][1], "history": "intermittent"})
        stats = execute(data)["mathematical_policy_report"]["history_statistics"][0]
        self.assertEqual(
            (
                stats["observation_count"],
                stats["positive_demand_weeks"],
                stats["mean_weekly_demand"],
                stats["variance"],
            ),
            (13, 1, "1", "13"),
        )

    def test_override_precedes_python_with_expiry_and_full_lineage(self):
        data = build_math()
        data["policy_input"]["adjustments"] = [adjustment(effective_to="2026-10-05")]
        result = execute(reseal_math(data))
        self.assertEqual(
            [r["effective_policy_source"] for r in report(result)],
            ["APPROVED_OVERRIDE", "PYTHON_CALCULATED", "PYTHON_CALCULATED"],
        )
        self.assertEqual(
            report(result)[0]["python_calculated_policy"]["target_inventory_qty"], "24"
        )
        self.assertEqual(report(result)[0]["effective_policy"]["target_inventory_qty"], "40")
        self.assertEqual(result["recommended_orders"][0]["quantity"], "30")
        self.assertEqual(report(result)[0]["adjustment_id"], "ADJ-APPROVED_OVERRIDE")

    def test_override_cannot_bypass_source_capacity_moq_or_control(self):
        data = build_math(
            {**GOLDEN_MATH["cases"][1], "policy": {"physical_max_capacity": "15", "moq": "20"}}
        )
        data["policy_input"]["adjustments"] = [adjustment()]
        result = execute(reseal_math(data))
        self.assertEqual(result["recommended_orders"], [])
        self.assertEqual(
            result["decision_evidence"][0]["validation"]["reason_codes"],
            ["NO_FEASIBLE_ORDER_QUANTITY"],
        )
        data = build_math()
        data["policy_input"]["adjustments"] = [adjustment()]
        data["recommendation"]["execution"]["item_controls"][0]["max_target_qty"] = "30"
        result = execute(reseal_math(data))
        self.assertEqual(
            result["decision_evidence"][0]["validation"]["reason_codes"],
            ["TARGET_OUTSIDE_APPROVED_BOUNDS"],
        )

    def test_missing_history_is_not_zero_and_unapproved_legacy_is_not_fallback(self):
        data = build_math()
        missing = data["policy_input"]["history"].pop()["yyyyww"]
        result = execute(reseal_math(data))
        self.assertEqual(
            (result["policy_coverage"], result["excluded_policy_decisions"]), ("NONE", 3)
        )
        self.assertEqual(result["recommended_orders"], [])
        stats = result["mathematical_policy_report"]["history_statistics"][0]
        self.assertEqual(stats["missing_weeks"], [missing])
        self.assertIsNone(stats["mean_weekly_demand"])
        self.assertIn("POLICY_EXCLUDED", result["decision_evidence"][0]["proposal"]["reason_codes"])

    def test_source_fallback_only_when_insufficient_and_partial_expiry(self):
        data = build_math()
        data["policy_input"]["adjustments"] = [
            adjustment("SOURCE_FALLBACK", effective_to="2026-10-05")
        ]
        self.assertEqual(
            report(execute(reseal_math(data)))[0]["effective_policy_source"], "PYTHON_CALCULATED"
        )
        data["policy_input"]["history"].pop()
        result = execute(reseal_math(data))
        self.assertEqual(result["policy_coverage"], "PARTIAL")
        self.assertEqual(
            [r["effective_policy_source"] for r in report(result)],
            ["SOURCE_FALLBACK", "EXCLUDED", "EXCLUDED"],
        )
        self.assertIsNone(report(result)[0]["python_calculated_policy"]["rop_qty"])

    def test_explicit_legacy_fallback_after_source_and_override(self):
        data = build_math()
        data["policy_input"]["history"] = []
        data["policy_input"]["profile"]["allow_legacy_fallback"] = True
        data["policy_input"]["adjustments"] = [adjustment("LEGACY_FALLBACK")]
        self.assertEqual(
            report(execute(reseal_math(data)))[0]["effective_policy_source"], "LEGACY_FALLBACK"
        )
        data["policy_input"]["adjustments"].append(adjustment("SOURCE_FALLBACK"))
        self.assertEqual(
            report(execute(reseal_math(data)))[0]["effective_policy_source"], "SOURCE_FALLBACK"
        )
        data["policy_input"]["adjustments"].append(adjustment())
        self.assertEqual(
            report(execute(reseal_math(data)))[0]["effective_policy_source"], "APPROVED_OVERRIDE"
        )

    def test_policy_revision_changes_per_week_with_frozen_history(self):
        data = build_math()
        policies = data["recommendation"]["canonical_input"]["snapshots"]["policies"]["rows"]
        policies[0]["effective_to"] = "2026-10-05"
        policies.append(
            {
                **policies[0],
                "policy_id": "SECOND",
                "effective_from": "2026-10-05",
                "effective_to": "2026-10-19",
                "lead_time_days": 8,
            }
        )
        result = execute(rebind(data))
        self.assertEqual(
            [r["effective_policy"]["rop_qty"] for r in report(result)], ["14", "25", "25"]
        )
        self.assertEqual(len(result["mathematical_policy_report"]["history_statistics"]), 1)

    def test_retry_is_stable_but_history_profile_changes_change_input_hash(self):
        data = build_math()
        first = execute(data)
        data["recommendation"]["canonical_input"]["context"]["engine_run_id"] = "RETRY-2"
        retry = execute(data)
        for key in (
            "input_content_hash",
            "psi_content_hash",
            "mathematical_policy_content_hash",
            "orders_content_hash",
        ):
            self.assertEqual(first[key], retry[key])
        data["policy_input"]["history"][0]["demand_qty"] = "9"
        self.assertNotEqual(
            first["input_content_hash"], execute(reseal_math(data))["input_content_hash"]
        )
        data = build_math()
        data["policy_input"]["profile"]["replenishment_cycle_weeks"] = 2
        self.assertNotEqual(
            first["input_content_hash"], execute(reseal_math(data))["input_content_hash"]
        )

    def test_forecast_changes_psi_not_frozen_history_statistics(self):
        data = build_math()
        data["recommendation"]["canonical_input"]["snapshots"]["forecast"]["rows"][0][
            "forecast_qty"
        ] = "50"
        result = execute(rebind(data))
        original = execute()
        self.assertEqual(
            result["mathematical_policy_report"]["history_statistics"],
            original["mathematical_policy_report"]["history_statistics"],
        )
        self.assertEqual(
            report(result)[0]["python_calculated_policy"],
            report(original)[0]["python_calculated_policy"],
        )
        self.assertNotEqual(result["psi_content_hash"], original["psi_content_hash"])

    def test_existing_supply_and_zero_policy_backorder_actions(self):
        data = build_math()
        source = data["recommendation"]["canonical_input"]
        source["snapshots"]["receipts"]["rows"].append(
            {
                **{k: source["context"][k] for k in ("company_cd", "subs_cd", "site_cd")},
                "item_id": "ITEM-A",
                "uom": "EA",
                "receipt_id": "EXISTING-PO",
                "due_date": "2026-10-05",
                "due_qty": "20",
                "status": "CONFIRMED",
                "supply_type": "PURCHASE_ORDER",
            }
        )
        result = execute(rebind(data))
        self.assertEqual(result["recommended_orders"], [])
        self.assertEqual([r["eoh_qty"] for r in result["psi_rows"]], ["0", "10", "0"])
        data = build_math({**GOLDEN_MATH["cases"][7], "policy": {"lead_time_days": 0}})
        data["recommendation"]["canonical_input"]["snapshots"]["inventory"]["rows"][0][
            "backorder_qty"
        ] = "5"
        result = execute(rebind(data))
        self.assertEqual([r["quantity"] for r in result["recommended_orders"]], ["5"])
        self.assertEqual(result["psi_rows"][0]["fulfilled_backorder_qty"], "5")


class MathematicalBoundaryTests(unittest.TestCase):
    def assert_rejected(self, data, code):
        with self.assertRaisesRegex(InventoryInputError, code):
            execute(data)

    def test_seal_hash_and_binding_checks(self):
        data = build_math()
        data["policy_input"]["history"][0]["demand_qty"] = "11"
        self.assert_rejected(data, "POLICY_INPUT_HASH_MISMATCH")
        for name, value, code in (
            ("status", "COLLECTING", "UNSEALED_POLICY_INPUT"),
            ("history_row_count", 0, "HISTORY_ROW_COUNT_MISMATCH"),
        ):
            data = build_math()
            data["policy_input"][name] = value
            self.assert_rejected(data, code)
        data = build_math()
        data["recommendation"]["execution"]["strategy_input_binding"]["content_hash"] = "0" * 64
        self.assert_rejected(data, "POLICY_INPUT_BINDING_MISMATCH")

    def test_snapshot_context_future_and_censored_data_fail_closed(self):
        for key, value, code in (
            ("site_cd", "OTHER", "POLICY_SCOPE_MISMATCH"),
            ("canonical_input_hash", "0" * 64, "POLICY_CANONICAL_BINDING_MISMATCH"),
            ("configuration_revision", "OTHER", "POLICY_CONFIGURATION_MISMATCH"),
            ("as_of_date", "2026-09-28", "HISTORY_AS_OF_MISMATCH"),
            ("available_at", "2026-09-27T14:59:59.100000Z", "FUTURE_POLICY_INFORMATION"),
            ("quantity_semantics", "FULFILLED_SALES", "CENSORED_HISTORY_NOT_SUPPORTED"),
        ):
            with self.subTest(key=key):
                data = build_math()
                data["policy_input"]["context"][key] = value
                self.assert_rejected(reseal_math(data), code)

    def test_invalid_profile_parameters(self):
        for key, value, code in (
            ("lookback_weeks", 12, "INVALID_POLICY_LOOKBACK"),
            ("stddev_ddof", 2, "INVALID_STDDEV_DDOF"),
            ("replenishment_cycle_weeks", 0, "INVALID_REPLENISHMENT_CYCLE"),
            ("formula", "GUESS", "INVALID_CODE"),
        ):
            data = build_math()
            data["policy_input"]["profile"][key] = value
            self.assert_rejected(reseal_math(data), code)

    def test_history_calendar_and_universe_errors(self):
        changes = [
            (lambda s: s["calendar"].pop(), "HISTORY_CALENDAR_SIZE"),
            (
                lambda s: s["calendar"][0].update(start_date="2026-01-01"),
                "HISTORY_CALENDAR_GAP_OR_OVERLAP",
            ),
            (
                lambda s: s["calendar"][0].update(yyyyww=s["calendar"][1]["yyyyww"]),
                "DUPLICATE_HISTORY_WEEK",
            ),
            (lambda s: s["calendar"][0].update(yyyyww="202640"), "HISTORY_FUTURE_WEEK"),
            (
                lambda s: s["history"].append(copy.deepcopy(s["history"][0])),
                "DUPLICATE_HISTORY_ITEM_WEEK",
            ),
            (lambda s: s["history"][0].update(item_id="ORPHAN"), "ORPHAN_ITEM"),
            (lambda s: s["history"][0].update(uom="KG"), "UOM_MISMATCH"),
            (lambda s: s["history"][0].update(yyyyww="202640"), "HISTORY_OUTSIDE_LOOKBACK"),
            (lambda s: s["history"][0].update(demand_qty="0.1"), "UOM_QUANTITY_PRECISION"),
        ]
        for mutate, code in changes:
            with self.subTest(code=code):
                data = build_math()
                mutate(data["policy_input"])
                self.assert_rejected(reseal_math(data), code)

    def test_bad_adjustment_approval_range_precedence_and_fields(self):
        for changes, code in (
            ({"approved_at": "2026-09-27T14:59:59.100000Z"}, "ADJUSTMENT_NOT_KNOWN_AT_CUTOFF"),
            ({"effective_to": "2026-09-28"}, "ADJUSTMENT_EFFECTIVE_RANGE"),
            (
                {
                    "values": {
                        "safety_stock_qty": "30",
                        "rop_qty": "20",
                        "target_inventory_qty": "40",
                    }
                },
                "INVALID_EFFECTIVE_POLICY_ORDER",
            ),
            ({"kind": "LEGACY_FALLBACK"}, "LEGACY_FALLBACK_NOT_ALLOWED"),
            ({"approved_by": ""}, "INVALID_IDENTIFIER"),
        ):
            data = build_math()
            data["policy_input"]["adjustments"] = [adjustment(**changes)]
            self.assert_rejected(reseal_math(data), code)
        data = build_math()
        data["policy_input"]["adjustments"] = [adjustment(), adjustment(adjustment_id="SECOND")]
        self.assert_rejected(reseal_math(data), "OVERLAPPING_POLICY_ADJUSTMENTS")

    def test_cutoff_is_checked_before_math_and_production_is_forbidden(self):
        data = build_math()
        data["recommendation"]["canonical_input"]["snapshots"]["inventory"]["status"] = "REJECTED"
        with patch(
            "dsio_inventory_engine.recommend_replenishment.application.mathematical.build_policy_report"
        ) as calculate:
            self.assert_rejected(rebind(data), "UNSEALED_INPUT")
            calculate.assert_not_called()
        with self.assertRaisesRegex(InventoryInputError, "LOCAL_RECOMMENDATION_ONLY"):
            RunMathematicalReplenishmentUseCase(DeploymentScope("DSE", "PRODUCTION")).execute(
                MathematicalPolicyRequest.from_dict(build_math())
            )

    def test_invalid_service_level_and_policy_numeric_range(self):
        self.assert_rejected(
            build_math({**GOLDEN_MATH["cases"][1], "policy": {"approved_service_level": "0.49"}}),
            "MATH_SERVICE_LEVEL_RANGE",
        )
        data = build_math()
        for row in data["policy_input"]["history"]:
            row["demand_qty"] = "1000000000000"
        self.assert_rejected(reseal_math(data), "CALCULATED_POLICY_RANGE")

    def test_direct_dto_construction_and_strategy_input_binding_cannot_bypass(self):
        data = build_math()
        data["policy_input"]["profile"]["lookback_weeks"] = 1
        invalid = MathematicalPolicyRequest(canonical_json(reseal_math(data)))
        with self.assertRaisesRegex(InventoryInputError, "INVALID_POLICY_LOOKBACK"):
            RunMathematicalReplenishmentUseCase(DEPLOYMENT).execute(invalid)
        data = build_math()
        rec = RecommendationRequest.from_dict(data["recommendation"])
        probe = ContractProbe()
        probe.descriptor = data["recommendation"]["execution"]["strategy"]
        with self.assertRaisesRegex(InventoryInputError, "STRATEGY_INPUT_BINDING_MISMATCH"):
            RunRecommendedPsiUseCase(DEPLOYMENT).execute(rec, probe)

    def test_partial_week_profile_is_rejected_explicitly(self):
        data = build_math()
        canonical = data["recommendation"]["canonical_input"]
        canonical["context"]["plan_end_date"] = "2026-10-17"
        canonical["snapshots"]["calendar"]["rows"][-1]["end_date"] = "2026-10-17"
        self.assert_rejected(rebind(data), "MATH_REQUIRES_FULL_WEEK_BUCKETS")

    def test_multiple_items_keep_independent_history_and_exclusion(self):
        data = build_math()
        canonical = data["recommendation"]["canonical_input"]
        for name, snapshot in canonical["snapshots"].items():
            if name == "calendar":
                continue
            for row in list(snapshot["rows"]):
                other = {**row, "item_id": "ITEM-B"}
                if "policy_id" in other:
                    other["policy_id"] = "POLICY-B"
                snapshot["rows"].append(other)
        controls = data["recommendation"]["execution"]["item_controls"]
        controls.append({**controls[0], "item_id": "ITEM-B"})
        result = execute(rebind(data))
        rows = report(result)
        self.assertEqual(result["policy_coverage"], "PARTIAL")
        self.assertEqual({r["item_id"] for r in result["recommended_orders"]}, {"ITEM-A"})
        self.assertTrue(
            all(
                r["effective_policy_source"] == "EXCLUDED" for r in rows if r["item_id"] == "ITEM-B"
            )
        )


if __name__ == "__main__":
    unittest.main()

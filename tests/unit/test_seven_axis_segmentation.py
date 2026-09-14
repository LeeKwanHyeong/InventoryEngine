"""Closed deterministic checks for the optional segmentation axes."""

import copy
import unittest
from dataclasses import replace
from decimal import Decimal

from dsio_inventory_engine.classify_inventory.application import (
    CONFIG_SCHEMA_V2_HASH,
    CONFIG_SCHEMA_V2_ID,
    CONFIG_SCHEMA_V2_VERSION,
    ClassificationInputs,
    InventoryScope,
    ItemMetric,
    build_snapshot,
    config_hash,
)
from dsio_inventory_engine.inventory_contracts.seven_axis import (
    classify_fsn,
    classify_hml,
    classify_plc,
    classify_sde,
    display_segment_code,
    hml_thresholds,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError


def config_values() -> dict:
    segments = ("AX", "AY", "AZ", "BX", "BY", "BZ", "CX", "CY", "CZ")
    return {
        "segmentation": {
            "mode": "ABC_XYZ",
            "abc": {
                "enabled": True,
                "basis": "REVENUE",
                "lookback_weeks": 52,
                "a_cumulative_share": 0.8,
                "b_cumulative_share": 0.95,
                "currency": "USD",
            },
            "xyz": {
                "enabled": True,
                "metric": "DEMAND_CV2",
                "lookback_weeks": 52,
                "x_max_cv2": 0.49,
                "y_max_cv2": 1.0,
            },
            "ved": {
                "enabled": False,
                "default_class": "D",
                "assignment_snapshot_id": None,
                "assignment_content_hash": None,
            },
        },
        "policy_matrix": {
            "service_level_type": "CYCLE_SERVICE_LEVEL",
            "default_strategy": "MATHEMATICAL",
            "cells": [
                {
                    "segment_key": segment,
                    "target_service_level": 0.95,
                    "review_cycle_weeks": 1,
                    "strategy": "MATHEMATICAL",
                }
                for segment in segments
            ],
            "ved_service_level_floor": {"V": 0.99, "E": 0.97, "D": 0.9},
        },
    }


def inputs() -> ClassificationInputs:
    values = config_values()
    metrics = tuple(
        ItemMetric(
            item_id=item_id,
            source_row_count=52,
            invalid_row_count=0,
            observed_week_count=52,
            total_demand=Decimal(demand),
            demand_square_sum=Decimal(square_sum),
            revenue=Decimal(revenue),
        )
        for item_id, demand, square_sum, revenue in (
            ("A", "520", "5200", "800"),
            ("B", "260", "3900", "150"),
            ("C", "52", "2704", "50"),
        )
    )
    return ClassificationInputs(
        tenant_id="default",
        scope=InventoryScope("project-a", "DSE", "C100", "V100", "V100"),
        config_id="00000000-0000-0000-0000-000000000001",
        config_revision_id="00000000-0000-0000-0000-000000000002",
        config_schema_id="urn:dsai:inventory-engine-config-values:1.1.0",
        config_schema_version="1.1.0",
        config_schema_hash="b7841bc8cbe996903a7a9b2c1bb70a0a2dc4f283019fdde5931b54c788627b04",
        config_hash=config_hash(values),
        config_values=values,
        actual_yyyyww="202601",
        closure_revision_no=1,
        publication_id="00000000-0000-0000-0000-000000000003",
        source_relation="dsdm.tb_dyn_demand_dtl",
        source_manifest_sha256="a" * 64,
        metrics=metrics,
        actual_close_window_start_yyyyww="202502",
        actual_close_history_hash="b" * 64,
    )


def v2_values() -> dict:
    values = config_values()
    values["segmentation"] = {
        **values["segmentation"],
        "mode": "SEVEN_AXIS",
        "fsn": {
            "enabled": True,
            "application_mode": "OPERATIONAL",
            "source_contract_key": "CLOSED_DEMAND_MOVEMENT_V1",
            "lookback_weeks": 52,
            "fast_min_active_week_ratio": 0.5,
            "non_moving_weeks": 26,
        },
        "sde": {
            "enabled": True,
            "application_mode": "SHADOW",
            "source_contract_key": "SUPPLIER_LEAD_TIME_V1",
            "lookback_weeks": 52,
            "difficult_min_lead_time_days": 28,
            "scarce_min_lead_time_days": 84,
            "scarce_max_supplier_count": 1,
            "scarce_max_on_time_rate": 0.8,
            "difficult_max_on_time_rate": 0.95,
            "min_receipt_sample_count": 8,
        },
        "hml": {
            "enabled": True,
            "application_mode": "SHADOW",
            "source_contract_key": "INVENTORY_UNIT_COST_V1",
            "basis": "UNIT_COST",
            "threshold_mode": "PERCENTILE",
            "medium_min_percentile": 0.5,
            "high_min_percentile": 0.8,
            "currency": "USD",
            "max_source_age_weeks": 13,
        },
        "plc": {
            "enabled": True,
            "application_mode": "SHADOW",
            "source_contract_key": "SITE_PART_LIFECYCLE_V1",
            "introduction_weeks": 13,
            "growth_weeks": 52,
            "decline_horizon_weeks": 26,
        },
    }
    values["policy_overlays"] = {
        "fsn_order_action": {"F": "ALLOW", "S": "ALLOW", "N": "REVIEW"},
        "sde_lead_time_basis": {"S": "P90", "D": "P90", "E": "P50"},
        "hml_approval_level": {"H": "HIGH_VALUE", "M": "STANDARD", "L": "AUTO"},
        "plc_order_action": {
            "PRE_LAUNCH": "REVIEW",
            "INTRODUCTION": "ALLOW",
            "GROWTH": "ALLOW",
            "MATURE": "ALLOW",
            "DECLINE": "REVIEW",
            "SERVICE_ONLY": "REVIEW",
            "DISCONTINUED": "BLOCK",
        },
    }
    return values


def v2_metric(row: ItemMetric, **changes: object) -> ItemMetric:
    explicit = replace(
        row,
        abc_source_row_count=row.source_row_count,
        abc_invalid_row_count=row.invalid_row_count,
        abc_observed_week_count=row.observed_week_count,
        abc_revenue=row.revenue,
        xyz_source_row_count=row.source_row_count,
        xyz_invalid_row_count=row.invalid_row_count,
        xyz_observed_week_count=row.observed_week_count,
        xyz_total_demand=row.total_demand,
        xyz_demand_square_sum=row.demand_square_sum,
        fsn_source_row_count=row.source_row_count,
        fsn_invalid_row_count=0,
        fsn_source_status="VERIFIED",
    )
    return replace(explicit, **changes)


class SevenAxisContractTests(unittest.TestCase):
    def test_each_axis_is_deterministic_and_keeps_shadow_separate(self):
        fsn_rules = v2_values()["segmentation"]["fsn"]
        fsn = classify_fsn(
            source_row_count=52,
            invalid_row_count=0,
            positive_week_count=30,
            last_positive_demand_yyyyww="202601",
            source_status="VERIFIED",
            as_of_yyyyww="202601",
            rules=fsn_rules,
        )
        self.assertEqual(fsn["class_code"], "F")
        self.assertTrue(fsn["policy_effective"])

        sde_rules = v2_values()["segmentation"]["sde"]
        sde = classify_sde(
            supplier_count=1,
            planning_lead_time_days=Decimal("14"),
            p50_lead_time_days=Decimal("14"),
            p90_lead_time_days=Decimal("21"),
            on_time_delivery_rate=Decimal("0.99"),
            receipt_sample_count=10,
            source_status="VERIFIED",
            rules=sde_rules,
        )
        self.assertEqual(sde["class_code"], "S")
        self.assertFalse(sde["policy_effective"])
        self.assertEqual(sde["evidence"]["p90_lead_time_days"], "21")

        hml_rules = v2_values()["segmentation"]["hml"]
        thresholds = hml_thresholds(
            [Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4"), Decimal("5")],
            medium_percentile=Decimal("0.5"),
            high_percentile=Decimal("0.8"),
        )
        self.assertEqual(thresholds, (Decimal("3"), Decimal("4")))
        hml = classify_hml(
            unit_cost=Decimal("4"),
            unit_cost_currency="USD",
            thresholds=thresholds,
            source_status="VERIFIED",
            rules=hml_rules,
            unit_cost_as_of_yyyyww="202601",
            as_of_yyyyww="202601",
        )
        self.assertEqual(hml["class_code"], "H")

        plc = classify_plc(
            introduced_yyyyww="202501",
            production_end_yyyyww="202752",
            service_end_yyyyww="202752",
            lifecycle_status_cd="ACTIVE",
            source_status="VERIFIED",
            as_of_yyyyww="202601",
            rules=v2_values()["segmentation"]["plc"],
        )
        self.assertEqual(plc["class_code"], "MATURE")

        synthetic_plc = classify_plc(
            introduced_yyyyww="202501",
            production_end_yyyyww="202752",
            service_end_yyyyww="202752",
            lifecycle_status_cd="ACTIVE",
            source_status="SYNTHETIC",
            as_of_yyyyww="202601",
            rules=v2_values()["segmentation"]["plc"],
            source_profile_hash="f" * 64,
        )
        self.assertEqual(synthetic_plc["status"], "SYNTHETIC")
        self.assertEqual(synthetic_plc["class_code"], "MATURE")
        self.assertFalse(synthetic_plc["policy_effective"])
        self.assertEqual(synthetic_plc["evidence"]["source_profile_hash"], "f" * 64)

    def test_missing_verified_sources_are_not_guessed(self):
        values = v2_values()
        fsn = classify_fsn(
            source_row_count=0,
            invalid_row_count=0,
            positive_week_count=0,
            last_positive_demand_yyyyww=None,
            source_status="UNVERIFIED",
            as_of_yyyyww="202601",
            rules=values["segmentation"]["fsn"],
        )
        sde = classify_sde(
            supplier_count=1,
            planning_lead_time_days=Decimal("14"),
            on_time_delivery_rate=None,
            receipt_sample_count=0,
            source_status="UNVERIFIED",
            rules=values["segmentation"]["sde"],
        )
        hml = classify_hml(
            unit_cost=None,
            unit_cost_currency=None,
            thresholds=None,
            source_status="UNVERIFIED",
            rules=values["segmentation"]["hml"],
        )
        self.assertEqual(fsn["status"], "UNVERIFIED")
        self.assertEqual(fsn["reason_code"], "MOVEMENT_EVIDENCE_INCOMPLETE")
        self.assertEqual(sde["status"], "UNVERIFIED")
        self.assertEqual(hml["status"], "UNVERIFIED")

    def test_fsn_verified_status_still_requires_explicit_complete_rows(self):
        result = classify_fsn(
            source_row_count=None,
            invalid_row_count=None,
            positive_week_count=0,
            last_positive_demand_yyyyww=None,
            source_status="VERIFIED",
            as_of_yyyyww="202601",
            rules=v2_values()["segmentation"]["fsn"],
        )

        self.assertEqual(result["status"], "UNVERIFIED")
        self.assertEqual(result["reason_code"], "MOVEMENT_EVIDENCE_INCOMPLETE")

    def test_sde_rejects_invalid_lead_time_distribution(self):
        rules = v2_values()["segmentation"]["sde"]

        result = classify_sde(
            supplier_count=2,
            planning_lead_time_days=Decimal("14"),
            p50_lead_time_days=Decimal("21"),
            p90_lead_time_days=Decimal("14"),
            on_time_delivery_rate=Decimal("0.99"),
            receipt_sample_count=10,
            source_status="VERIFIED",
            rules=rules,
        )

        self.assertEqual(result["status"], "UNVERIFIED")
        self.assertEqual(result["reason_code"], "SUPPLIER_LEAD_TIME_EVIDENCE_INCOMPLETE")

    def test_hml_source_age_accepts_boundary_and_rejects_stale_or_future_cost(self):
        rules = v2_values()["segmentation"]["hml"]
        thresholds = (Decimal("10"), Decimal("20"))

        boundary = classify_hml(
            unit_cost=Decimal("20"),
            unit_cost_currency="USD",
            thresholds=thresholds,
            source_status="VERIFIED",
            rules=rules,
            unit_cost_as_of_yyyyww="202601",
            as_of_yyyyww="202614",
        )
        stale = classify_hml(
            unit_cost=Decimal("20"),
            unit_cost_currency="USD",
            thresholds=thresholds,
            source_status="VERIFIED",
            rules=rules,
            unit_cost_as_of_yyyyww="202552",
            as_of_yyyyww="202614",
        )
        future = classify_hml(
            unit_cost=Decimal("20"),
            unit_cost_currency="USD",
            thresholds=thresholds,
            source_status="VERIFIED",
            rules=rules,
            unit_cost_as_of_yyyyww="202615",
            as_of_yyyyww="202614",
        )

        self.assertEqual(boundary["status"], "CLASSIFIED")
        self.assertEqual(boundary["evidence"]["source_age_weeks"], 13)
        self.assertEqual(stale["reason_code"], "INVENTORY_UNIT_COST_STALE")
        self.assertEqual(future["reason_code"], "INVENTORY_UNIT_COST_FROM_FUTURE")

    def test_display_code_keeps_abc_xyz_together(self):
        self.assertEqual(
            display_segment_code(
                [
                    {"axis": "ABC", "class_code": "A"},
                    {"axis": "XYZ", "class_code": "X"},
                    {"axis": "VED", "class_code": "V"},
                    {"axis": "FSN", "class_code": "F"},
                    {"axis": "SDE", "class_code": None},
                    {"axis": "HML", "class_code": "H"},
                    {"axis": "PLC", "class_code": "MATURE"},
                ]
            ),
            "AX-V-F-H-MATURE",
        )

    def test_v2_rejects_unimplemented_abc_basis_at_validation_boundary(self):
        original = inputs()
        values = v2_values()
        values["segmentation"]["abc"]["basis"] = "CONTRIBUTION_MARGIN"
        changed = replace(
            original,
            config_schema_id=CONFIG_SCHEMA_V2_ID,
            config_schema_version=CONFIG_SCHEMA_V2_VERSION,
            config_schema_hash=CONFIG_SCHEMA_V2_HASH,
            config_values=values,
            config_hash=config_hash(values),
        )

        with self.assertRaisesRegex(InventoryInputError, "CLASSIFICATION_ABC_BASIS_UNSUPPORTED"):
            build_snapshot(changed)

    def test_v2_rejects_operational_plc_while_source_contract_is_synthetic(self):
        original = inputs()
        values = v2_values()
        values["segmentation"]["plc"]["application_mode"] = "OPERATIONAL"
        changed = replace(
            original,
            config_schema_id=CONFIG_SCHEMA_V2_ID,
            config_schema_version=CONFIG_SCHEMA_V2_VERSION,
            config_schema_hash=CONFIG_SCHEMA_V2_HASH,
            config_values=values,
            config_hash=config_hash(values),
        )

        with self.assertRaisesRegex(InventoryInputError, "CLASSIFICATION_CONFIG_INVALID"):
            build_snapshot(changed)

    def test_v2_rejects_plc_introduction_outside_contract_or_not_before_growth(self):
        original = inputs()
        invalid_boundaries = (
            {"introduction_weeks": 53, "growth_weeks": 54},
            {"introduction_weeks": 52, "growth_weeks": 52},
        )

        for plc_values in invalid_boundaries:
            with self.subTest(**plc_values):
                values = v2_values()
                values["segmentation"]["plc"].update(plc_values)
                changed = replace(
                    original,
                    config_schema_id=CONFIG_SCHEMA_V2_ID,
                    config_schema_version=CONFIG_SCHEMA_V2_VERSION,
                    config_schema_hash=CONFIG_SCHEMA_V2_HASH,
                    config_values=values,
                    config_hash=config_hash(values),
                )

                with self.assertRaisesRegex(InventoryInputError, "CLASSIFICATION_CONFIG_INVALID"):
                    build_snapshot(changed)

    def test_schema_identity_is_bound_to_segmentation_mode(self):
        original = inputs()
        values = v2_values()
        mismatched = replace(
            original,
            config_values=values,
            config_hash=config_hash(values),
        )

        with self.assertRaisesRegex(InventoryInputError, "CLASSIFICATION_CONFIG_SCHEMA_MISMATCH"):
            build_snapshot(mismatched)

    def test_stale_hml_cost_is_excluded_from_percentile_cohort(self):
        original = inputs()
        values = v2_values()
        metrics = tuple(
            v2_metric(
                row,
                positive_week_count=30,
                last_positive_demand_yyyyww="202601",
                unit_cost=Decimal(cost),
                unit_cost_currency="USD",
                unit_cost_as_of_yyyyww=("202539" if row.item_id == "C" else "202601"),
                hml_source_status="VERIFIED",
                introduced_yyyyww="202401",
                service_end_yyyyww="203052",
                lifecycle_status_cd="ACTIVE",
                plc_source_status="SYNTHETIC",
                plc_source_profile_hash="f" * 64,
            )
            for row, cost in zip(original.metrics, ("1", "2", "1000"), strict=True)
        )
        changed = replace(
            original,
            config_schema_id=CONFIG_SCHEMA_V2_ID,
            config_schema_version=CONFIG_SCHEMA_V2_VERSION,
            config_schema_hash=CONFIG_SCHEMA_V2_HASH,
            config_values=values,
            config_hash=config_hash(values),
            metrics=metrics,
        )

        snapshot = build_snapshot(changed)
        by_item = {item["item_id"]: item for item in snapshot["items"]}
        hml_by_item = {
            item_id: next(axis for axis in item["axis_results"] if axis["axis"] == "HML")
            for item_id, item in by_item.items()
        }

        self.assertEqual(hml_by_item["B"]["class_code"], "H")
        self.assertEqual(hml_by_item["C"]["reason_code"], "INVENTORY_UNIT_COST_STALE")

    def test_v2_snapshot_contains_all_axes_and_is_replayable(self):
        original = inputs()
        values = v2_values()
        metrics: tuple[ItemMetric, ...] = tuple(
            v2_metric(
                row,
                positive_week_count=30,
                last_positive_demand_yyyyww="202601",
                introduced_yyyyww="202401",
                service_end_yyyyww="203052",
                lifecycle_status_cd="ACTIVE",
                plc_source_status="SYNTHETIC",
                plc_source_profile_hash="f" * 64,
            )
            for row in original.metrics
        )
        changed = replace(
            original,
            config_schema_id=CONFIG_SCHEMA_V2_ID,
            config_schema_version=CONFIG_SCHEMA_V2_VERSION,
            config_schema_hash=CONFIG_SCHEMA_V2_HASH,
            config_values=values,
            config_hash=config_hash(values),
            metrics=metrics,
        )

        first = build_snapshot(changed)
        replay = build_snapshot(copy.deepcopy(changed))

        self.assertEqual(first, replay)
        self.assertEqual(first["axis_result_contract_version"], "2.0.0")
        item = first["items"][0]
        self.assertEqual(
            [axis["axis"] for axis in item["axis_results"]],
            ["ABC", "XYZ", "VED", "FSN", "SDE", "HML", "PLC"],
        )
        self.assertNotIn("SDE", item["policy_effective_axes"])
        self.assertNotIn("HML", item["policy_effective_axes"])
        self.assertTrue(item["seven_axis_operational_eligible"])
        self.assertEqual(item["display_segment_code"], "AX-F-MATURE")
        plc = next(axis for axis in item["axis_results"] if axis["axis"] == "PLC")
        self.assertEqual(plc["status"], "SYNTHETIC")
        self.assertEqual(plc["evidence"]["source_profile_hash"], "f" * 64)
        self.assertEqual(first["segmentation_type"], "SEVEN_AXIS")
        self.assertEqual(first["axis_windows"]["ABC"]["lookback_weeks"], 52)
        self.assertEqual(first["actual_close_window_start_yyyyww"], "202502")
        self.assertEqual(first["actual_close_history_hash"], "b" * 64)
        self.assertIn("ACTUAL-CLOSE-WINDOW-202502-202601-", first["source_revision"])
        self.assertEqual(first["effective_policy_contract_version"], "2.0.0")
        self.assertEqual(item["effective_policy_contract_version"], "2.0.0")
        self.assertEqual(item["effective_order_action"], "ALLOW")
        self.assertTrue(item["recommendation_calculation_allowed"])
        self.assertTrue(item["evidence_storage_allowed"])
        self.assertTrue(item["automatic_publish_allowed"])
        self.assertTrue(item["automatic_order_allowed"])
        self.assertEqual(
            [axis["axis"] for axis in item["effective_axis_projection"]],
            ["ABC", "XYZ", "FSN"],
        )

        corrected_history = build_snapshot(replace(changed, actual_close_history_hash="c" * 64))
        self.assertNotEqual(first["source_revision"], corrected_history["source_revision"])
        self.assertNotEqual(first["content_hash"], corrected_history["content_hash"])

    def test_shadow_axis_change_does_not_change_snapshot_item_policy_hash(self):
        original = inputs()
        values = v2_values()
        metrics = tuple(
            v2_metric(
                row,
                positive_week_count=30,
                last_positive_demand_yyyyww="202601",
                supplier_count=1,
                planning_lead_time_days=Decimal("14"),
                p50_lead_time_days=Decimal("14"),
                p90_lead_time_days=Decimal("21"),
                on_time_delivery_rate=Decimal("0.99"),
                receipt_sample_count=10,
                sde_source_status="VERIFIED",
                introduced_yyyyww="202401",
                service_end_yyyyww="203052",
                lifecycle_status_cd="ACTIVE",
                plc_source_status="SYNTHETIC",
                plc_source_profile_hash="f" * 64,
            )
            for row in original.metrics
        )
        first_inputs = replace(
            original,
            config_schema_id=CONFIG_SCHEMA_V2_ID,
            config_schema_version=CONFIG_SCHEMA_V2_VERSION,
            config_schema_hash=CONFIG_SCHEMA_V2_HASH,
            config_values=values,
            config_hash=config_hash(values),
            metrics=metrics,
        )
        changed_values = copy.deepcopy(values)
        changed_values["policy_overlays"]["sde_lead_time_basis"] = {
            "S": "P50",
            "D": "P50",
            "E": "P90",
        }
        changed_inputs = replace(
            first_inputs,
            config_values=changed_values,
            config_hash=config_hash(changed_values),
        )

        first = build_snapshot(first_inputs)
        changed = build_snapshot(changed_inputs)
        changed_profile = build_snapshot(
            replace(
                first_inputs,
                metrics=tuple(
                    replace(row, plc_source_profile_hash="e" * 64) for row in first_inputs.metrics
                ),
            )
        )

        self.assertNotEqual(first["content_hash"], changed["content_hash"])
        self.assertEqual(
            [item["effective_policy_hash"] for item in first["items"]],
            [item["effective_policy_hash"] for item in changed["items"]],
        )
        self.assertEqual(
            first["effective_policy_content_hash"],
            changed["effective_policy_content_hash"],
        )
        self.assertNotEqual(first["content_hash"], changed_profile["content_hash"])
        self.assertEqual(
            [item["effective_policy_hash"] for item in first["items"]],
            [item["effective_policy_hash"] for item in changed_profile["items"]],
        )

    def test_snapshot_records_each_axis_window_and_widest_source_window(self):
        original = inputs()
        values = v2_values()
        values["segmentation"]["xyz"]["lookback_weeks"] = 26
        values["segmentation"]["fsn"]["lookback_weeks"] = 13
        values["segmentation"]["fsn"]["non_moving_weeks"] = 13
        values["segmentation"]["sde"]["lookback_weeks"] = 104
        metrics = tuple(
            v2_metric(
                row,
                positive_week_count=10,
                last_positive_demand_yyyyww="202601",
                introduced_yyyyww="202401",
                service_end_yyyyww="203052",
                lifecycle_status_cd="ACTIVE",
                plc_source_status="VERIFIED",
            )
            for row in original.metrics
        )
        changed = replace(
            original,
            config_schema_id=CONFIG_SCHEMA_V2_ID,
            config_schema_version=CONFIG_SCHEMA_V2_VERSION,
            config_schema_hash=CONFIG_SCHEMA_V2_HASH,
            config_values=values,
            config_hash=config_hash(values),
            metrics=metrics,
        )

        snapshot = build_snapshot(changed)

        self.assertEqual(
            {axis: window["lookback_weeks"] for axis, window in snapshot["axis_windows"].items()},
            {"ABC": 52, "XYZ": 26, "FSN": 13, "SDE": 104},
        )
        self.assertEqual(snapshot["window_start"], snapshot["axis_windows"]["ABC"]["window_start"])
        self.assertNotEqual(
            snapshot["window_start"], snapshot["axis_windows"]["SDE"]["window_start"]
        )
        self.assertEqual(snapshot["actual_close_window_start_yyyyww"], "202502")

    def test_v2_rejects_implicit_v1_axis_aggregate_fallback(self):
        original = inputs()
        values = v2_values()
        changed = replace(
            original,
            config_schema_id=CONFIG_SCHEMA_V2_ID,
            config_schema_version=CONFIG_SCHEMA_V2_VERSION,
            config_schema_hash=CONFIG_SCHEMA_V2_HASH,
            config_values=values,
            config_hash=config_hash(values),
        )

        with self.assertRaisesRegex(
            InventoryInputError, "CLASSIFICATION_V2_AXIS_METRICS_INCOMPLETE"
        ):
            build_snapshot(changed)


if __name__ == "__main__":
    unittest.main()

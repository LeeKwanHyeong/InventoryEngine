"""Inventory classification lifecycle contract checks without database access."""

import copy
import unittest
from dataclasses import replace
from decimal import Decimal

from dsio_inventory_engine.classify_inventory.application import (
    CONFIG_SCHEMA_HASH,
    CONFIG_SCHEMA_ID,
    CONFIG_SCHEMA_VERSION,
    ClassificationInputs,
    InventoryClassificationLifecycleUseCase,
    InventoryScope,
    ItemMetric,
    VedAssignment,
    build_snapshot,
    classify_items,
    config_hash,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError


def config_values() -> dict:
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
                for segment in ("AX", "AY", "AZ", "BX", "BY", "BZ", "CX", "CY", "CZ")
            ],
            "ved_service_level_floor": {"V": 0.99, "E": 0.97, "D": 0.9},
        },
    }


def metric(
    item_id: str,
    demand: str,
    square_sum: str,
    revenue: str,
    *,
    source_rows: int = 52,
    observed_weeks: int = 52,
    invalid_rows: int = 0,
) -> ItemMetric:
    return ItemMetric(
        item_id=item_id,
        source_row_count=source_rows,
        invalid_row_count=invalid_rows,
        observed_week_count=observed_weeks,
        total_demand=Decimal(demand),
        demand_square_sum=Decimal(square_sum),
        revenue=Decimal(revenue),
    )


def inputs() -> ClassificationInputs:
    values = config_values()
    return ClassificationInputs(
        tenant_id="default",
        scope=InventoryScope("project-a", "DSE", "C100", "V100", "V100"),
        config_id="00000000-0000-0000-0000-000000000001",
        config_revision_id="00000000-0000-0000-0000-000000000002",
        config_schema_id=CONFIG_SCHEMA_ID,
        config_schema_version=CONFIG_SCHEMA_VERSION,
        config_schema_hash=CONFIG_SCHEMA_HASH,
        config_hash=config_hash(values),
        config_values=values,
        actual_yyyyww="202601",
        closure_revision_no=1,
        publication_id="00000000-0000-0000-0000-000000000003",
        source_relation="dsdm.tb_dyn_demand_dtl",
        source_manifest_sha256="a" * 64,
        metrics=(
            metric("A", "520", "5200", "800"),
            metric("B", "260", "3900", "150"),
            metric("C", "52", "2704", "50"),
        ),
    )


class FakeSource:
    def __init__(self, value: ClassificationInputs):
        self.value = value
        self.scopes = []

    async def load_inputs(self, scope: InventoryScope) -> ClassificationInputs:
        self.scopes.append(scope)
        return self.value


class FakePublisher:
    def __init__(self):
        self.calls = []

    async def publish(self, snapshot, *, approved_by):
        self.calls.append((snapshot, approved_by))
        return {
            "publication_status": "exact_replay",
            "classification_snapshot_id": snapshot["classification_snapshot_id"],
            "snapshot_revision": 1,
        }


class ClassificationSnapshotTests(unittest.IsolatedAsyncioTestCase):
    def test_source_business_item_id_is_preserved_through_v1_snapshot(self):
        original = inputs()
        changed = replace(
            original,
            metrics=(
                replace(original.metrics[0], item_id="ITEM/01"),
                *original.metrics[1:],
            ),
        )

        snapshot = build_snapshot(changed)

        self.assertIn("ITEM/01", {item["item_id"] for item in snapshot["items"]})

    def test_v1_source_content_hash_keeps_published_projection(self):
        snapshot = build_snapshot(inputs())

        self.assertEqual(
            snapshot["source_content_hash"],
            "fa68b90d53aaa4b3b94423c8fc57843bb1ebad53efa5f831d18f924258aeb1f3",
        )

    async def test_dry_run_is_deterministic_and_never_calls_publisher(self):
        source = FakeSource(inputs())
        publisher = FakePublisher()
        lifecycle = InventoryClassificationLifecycleUseCase(source, publisher)

        first = await lifecycle.execute(source.value.scope, approved_by="admin")
        replay = await lifecycle.execute(source.value.scope, approved_by="admin")

        self.assertEqual(first, replay)
        self.assertEqual(first["publication_status"], "dry_run")
        self.assertFalse(first["database_writes"] or first["run_claimed"])
        self.assertEqual(first["eligible_sku_count"], 3)
        self.assertEqual(len(first["segments"]), 9)
        self.assertEqual(publisher.calls, [])

    async def test_apply_delegates_once_and_reports_exact_replay(self):
        source = FakeSource(inputs())
        publisher = FakePublisher()

        receipt = await InventoryClassificationLifecycleUseCase(source, publisher).execute(
            source.value.scope, approved_by="admin", publish=True
        )

        self.assertEqual(receipt["publication_status"], "exact_replay")
        self.assertTrue(receipt["exact_replay"])
        self.assertFalse(receipt["database_writes"] or receipt["run_claimed"])
        self.assertEqual(len(publisher.calls), 1)

    async def test_snapshot_binds_ved_version_and_publishes_item_results(self):
        original = inputs()
        values = copy.deepcopy(original.config_values)
        values["segmentation"]["ved"] = {
            "enabled": True,
            "default_class": "D",
            "assignment_snapshot_id": "00000000-0000-0000-0000-000000000010",
            "assignment_content_hash": "e" * 64,
        }
        changed = replace(
            original,
            config_values=values,
            config_hash=config_hash(values),
            ved_assignments=(VedAssignment("A", "V", "critical"),),
        )
        publisher = FakePublisher()

        await InventoryClassificationLifecycleUseCase(FakeSource(changed), publisher).execute(
            changed.scope, approved_by="admin", publish=True
        )

        snapshot = publisher.calls[0][0]
        self.assertEqual(snapshot["item_result_contract_version"], "1.1.0")
        self.assertEqual(snapshot["effective_policy_contract_version"], "1.0.0")
        self.assertEqual(
            snapshot["ved_assignment_snapshot_id"],
            "00000000-0000-0000-0000-000000000010",
        )
        self.assertEqual(snapshot["ved_assignment_content_hash"], "e" * 64)
        self.assertEqual(len(snapshot["items"]), snapshot["eligible_sku_count"])
        self.assertEqual(
            next(item for item in snapshot["items"] if item["item_id"] == "A")["final_segment_key"],
            "AX-V",
        )
        effective = next(item for item in snapshot["items"] if item["item_id"] == "A")
        self.assertEqual(effective["effective_target_service_level"], "0.99")
        self.assertEqual(
            effective["policy_adjustment_reason"],
            "VED_SERVICE_LEVEL_FLOOR_APPLIED",
        )
        self.assertTrue(effective["operational_io_eligible"])

    async def test_config_hash_drift_blocks_before_publish(self):
        original = inputs()
        values = copy.deepcopy(original.config_values)
        values["segmentation"]["xyz"]["x_max_cv2"] = 0.3
        changed = replace(original, config_values=values)
        publisher = FakePublisher()

        with self.assertRaisesRegex(InventoryInputError, "CLASSIFICATION_CONFIG_HASH_MISMATCH"):
            await InventoryClassificationLifecycleUseCase(FakeSource(changed), publisher).execute(
                changed.scope, approved_by="admin", publish=True
            )
        self.assertEqual(publisher.calls, [])

    async def test_unimplemented_abc_source_basis_fails_closed(self):
        original = inputs()
        values = copy.deepcopy(original.config_values)
        values["segmentation"]["abc"]["basis"] = "CONTRIBUTION_MARGIN"
        changed = replace(
            original,
            config_values=values,
            config_hash=config_hash(values),
        )

        with self.assertRaisesRegex(InventoryInputError, "CLASSIFICATION_ABC_BASIS_UNSUPPORTED"):
            await InventoryClassificationLifecycleUseCase(FakeSource(changed)).execute(
                changed.scope, approved_by="admin"
            )

    def test_missing_evidence_is_unclassified_instead_of_guessed(self):
        rows = [
            metric("VALID", "520", "5200", "100"),
            metric("NO_HISTORY", "0", "0", "0", source_rows=0, observed_weeks=0),
            metric("ZERO", "0", "0", "10"),
            metric("NO_REVENUE", "10", "100", "0"),
            metric("INVALID", "10", "100", "10", invalid_rows=1),
        ]

        result = classify_items(
            rows,
            abc_a_cumulative_share=Decimal("0.8"),
            abc_b_cumulative_share=Decimal("0.95"),
            xyz_x_max_cv2=Decimal("0.49"),
            xyz_y_max_cv2=Decimal("1"),
            lookback_weeks=52,
        )

        self.assertEqual(result.classified_sku_count, 1)
        self.assertEqual(result.unclassified_sku_count, 4)
        self.assertEqual(len(result.items), 5)
        self.assertEqual(
            {
                row["item_id"]: row["unclassified_reason_code"]
                for row in result.items
                if row["classification_status"] == "UNCLASSIFIED"
            },
            {
                "NO_HISTORY": "INSUFFICIENT_DEMAND_HISTORY",
                "ZERO": "ZERO_MEAN_DEMAND",
                "NO_REVENUE": "MISSING_REVENUE",
                "INVALID": "INVALID_SOURCE_RECORD",
            },
        )
        self.assertEqual(
            {row["reason_code"] for row in result.unclassified_reasons},
            {
                "INSUFFICIENT_DEMAND_HISTORY",
                "INVALID_SOURCE_RECORD",
                "MISSING_REVENUE",
                "ZERO_MEAN_DEMAND",
            },
        )

    def test_ved_assignment_and_default_are_preserved_per_item(self):
        result = classify_items(
            [
                metric("ASSIGNED", "520", "5200", "800"),
                metric("DEFAULT", "260", "3900", "150"),
                metric("NO_HISTORY", "0", "0", "0", source_rows=0, observed_weeks=0),
            ],
            abc_a_cumulative_share=Decimal("0.8"),
            abc_b_cumulative_share=Decimal("0.95"),
            xyz_x_max_cv2=Decimal("0.49"),
            xyz_y_max_cv2=Decimal("1"),
            lookback_weeks=52,
            ved_enabled=True,
            ved_default_class="D",
            ved_assignments=(VedAssignment("ASSIGNED", "V", "critical"),),
        )

        by_item = {row["item_id"]: row for row in result.items}
        self.assertEqual(by_item["ASSIGNED"]["ved_class"], "V")
        self.assertEqual(by_item["ASSIGNED"]["ved_assignment_source"], "ASSIGNMENT_SNAPSHOT")
        self.assertEqual(by_item["ASSIGNED"]["ved_assignment_reason"], "critical")
        self.assertTrue(by_item["ASSIGNED"]["final_segment_key"].endswith("-V"))
        self.assertEqual(by_item["DEFAULT"]["ved_class"], "D")
        self.assertEqual(by_item["DEFAULT"]["ved_assignment_source"], "DEFAULT_CLASS")
        self.assertIsNone(by_item["DEFAULT"]["ved_assignment_reason"])
        self.assertIsNone(by_item["NO_HISTORY"]["final_segment_key"])
        self.assertEqual(
            by_item["NO_HISTORY"]["unclassified_reason_code"],
            "INSUFFICIENT_DEMAND_HISTORY",
        )

    def test_orphan_ved_assignment_fails_closed(self):
        with self.assertRaisesRegex(InventoryInputError, "CLASSIFICATION_VED_ORPHAN_ITEM"):
            classify_items(
                [metric("KNOWN", "520", "5200", "100")],
                abc_a_cumulative_share=Decimal("0.8"),
                abc_b_cumulative_share=Decimal("0.95"),
                xyz_x_max_cv2=Decimal("0.49"),
                xyz_y_max_cv2=Decimal("1"),
                lookback_weeks=52,
                ved_enabled=True,
                ved_assignments=(VedAssignment("ORPHAN", "V"),),
            )

    def test_abc_and_xyz_use_their_own_lookback_aggregates_and_divisors(self):
        row = metric("ITEM", "520", "5200", "999")
        row = replace(
            row,
            abc_source_row_count=52,
            abc_invalid_row_count=0,
            abc_observed_week_count=52,
            abc_revenue=Decimal("200"),
            xyz_source_row_count=13,
            xyz_invalid_row_count=0,
            xyz_observed_week_count=13,
            xyz_total_demand=Decimal("130"),
            xyz_demand_square_sum=Decimal("1300"),
        )

        result = classify_items(
            [row],
            abc_a_cumulative_share=Decimal("0.8"),
            abc_b_cumulative_share=Decimal("0.95"),
            xyz_x_max_cv2=Decimal("0.49"),
            xyz_y_max_cv2=Decimal("1"),
            abc_lookback_weeks=52,
            xyz_lookback_weeks=13,
        )

        item = result.items[0]
        self.assertEqual(item["revenue"], "200")
        self.assertEqual(item["demand_cv2"], "0.000000000000")
        self.assertEqual(item["segment_key"], "AX")

    def test_semantically_equal_axis_fallbacks_have_same_source_hash(self):
        fallback = metric("ITEM", "520", "5200", "200")
        explicit = replace(
            fallback,
            abc_source_row_count=52,
            abc_invalid_row_count=0,
            abc_observed_week_count=52,
            abc_revenue=Decimal("200"),
            xyz_source_row_count=52,
            xyz_invalid_row_count=0,
            xyz_observed_week_count=52,
            xyz_total_demand=Decimal("520"),
            xyz_demand_square_sum=Decimal("5200"),
        )

        def classify(row: ItemMetric):
            return classify_items(
                [row],
                abc_a_cumulative_share=Decimal("0.8"),
                abc_b_cumulative_share=Decimal("0.95"),
                xyz_x_max_cv2=Decimal("0.49"),
                xyz_y_max_cv2=Decimal("1"),
                abc_lookback_weeks=52,
                xyz_lookback_weeks=52,
            )

        self.assertEqual(
            classify(fallback).source_content_hash,
            classify(explicit).source_content_hash,
        )

    def test_one_axis_missing_history_is_reported_explicitly(self):
        row = replace(
            metric("ITEM", "130", "1300", "200"),
            abc_source_row_count=52,
            abc_observed_week_count=52,
            xyz_source_row_count=0,
            xyz_observed_week_count=0,
        )

        result = classify_items(
            [row, metric("VALID", "130", "1300", "100")],
            abc_a_cumulative_share=Decimal("0.8"),
            abc_b_cumulative_share=Decimal("0.95"),
            xyz_x_max_cv2=Decimal("0.49"),
            xyz_y_max_cv2=Decimal("1"),
            abc_lookback_weeks=52,
            xyz_lookback_weeks=13,
        )

        item = next(item for item in result.items if item["item_id"] == "ITEM")
        self.assertEqual(
            item["unclassified_reason_code"],
            "INSUFFICIENT_XYZ_HISTORY",
        )

    def test_price_invalidity_excludes_abc_but_keeps_xyz_evidence_valid(self):
        price_invalid = replace(
            metric("PRICE_INVALID", "130", "1300", "200"),
            abc_invalid_row_count=1,
            xyz_invalid_row_count=0,
            xyz_source_row_count=13,
            xyz_observed_week_count=13,
            xyz_total_demand=Decimal("130"),
            xyz_demand_square_sum=Decimal("1300"),
        )

        result = classify_items(
            [price_invalid, metric("VALID", "130", "1300", "100")],
            abc_a_cumulative_share=Decimal("0.8"),
            abc_b_cumulative_share=Decimal("0.95"),
            xyz_x_max_cv2=Decimal("0.49"),
            xyz_y_max_cv2=Decimal("1"),
            abc_lookback_weeks=52,
            xyz_lookback_weeks=13,
        )

        item = next(item for item in result.items if item["item_id"] == "PRICE_INVALID")
        self.assertEqual(item["classification_status"], "UNCLASSIFIED")
        self.assertEqual(item["abc_classification_status"], "UNCLASSIFIED")
        self.assertEqual(item["abc_unclassified_reason_code"], "INVALID_ABC_SOURCE_RECORD")
        self.assertEqual(item["xyz_classification_status"], "CLASSIFIED")
        self.assertEqual(item["xyz_class"], "X")
        self.assertEqual(item["demand_cv2"], "0.000000000000")


if __name__ == "__main__":
    unittest.main()

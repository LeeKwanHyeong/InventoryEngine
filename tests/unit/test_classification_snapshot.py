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
        self.assertEqual(
            {row["reason_code"] for row in result.unclassified_reasons},
            {
                "INSUFFICIENT_DEMAND_HISTORY",
                "INVALID_SOURCE_RECORD",
                "MISSING_REVENUE",
                "ZERO_MEAN_DEMAND",
            },
        )


if __name__ == "__main__":
    unittest.main()

"""VED binding checks in the PostgreSQL classification source adapter."""

import unittest

from dsio_inventory_engine.classify_inventory.application import (
    CONFIG_SCHEMA_HASH,
    CONFIG_SCHEMA_ID,
    CONFIG_SCHEMA_VERSION,
    InventoryScope,
    config_hash,
)
from dsio_inventory_engine.infrastructure.postgresql.classification_snapshot import (
    ACTIVE_CONFIG_SQL,
    ACTUAL_CLOSE_SQL,
    ITEM_METRICS_SQL,
    VED_ASSIGNMENT_HEADER_SQL,
    VED_ASSIGNMENT_ITEMS_SQL,
    PostgresClassificationSource,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError


class Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


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
                "enabled": True,
                "default_class": "D",
                "assignment_snapshot_id": "00000000-0000-0000-0000-000000000010",
                "assignment_content_hash": "e" * 64,
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


class Connection:
    def __init__(self, *, ved_hash: str = "e" * 64):
        self.values = config_values()
        self.ved_hash = ved_hash
        self.calls = []

    def transaction(self, **kwargs):
        self.calls.append(("transaction", kwargs))
        return Transaction()

    async def execute(self, query, *args):
        self.calls.append((query, args))

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        if query == ACTIVE_CONFIG_SQL:
            return {
                "config_id": "00000000-0000-0000-0000-000000000001",
                "tenant_id": "default",
                "project_id": "project-a",
                "active_config_revision_id": "00000000-0000-0000-0000-000000000002",
                "config_hash": config_hash(self.values),
                "config_schema_id": CONFIG_SCHEMA_ID,
                "config_schema_version": CONFIG_SCHEMA_VERSION,
                "config_schema_hash": CONFIG_SCHEMA_HASH,
                "config_values": self.values,
            }
        if query == ACTUAL_CLOSE_SQL:
            return {
                "actual_yyyyww": "202601",
                "closure_revision_no": 1,
                "source_relation": "dsdm.tb_dyn_demand_dtl",
                "source_manifest_sha256": "a" * 64,
                "publication_id": "00000000-0000-0000-0000-000000000003",
            }
        if query == VED_ASSIGNMENT_HEADER_SQL:
            return {
                "assignment_snapshot_id": self.values["segmentation"]["ved"][
                    "assignment_snapshot_id"
                ],
                "assignment_content_hash": self.ved_hash,
                "status": "approved",
            }
        raise AssertionError(query)

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        if query == ITEM_METRICS_SQL:
            return [
                {
                    "item_id": "ITEM/01",
                    "source_row_count": 52,
                    "invalid_row_count": 0,
                    "observed_week_count": 52,
                    "total_demand": 520,
                    "demand_square_sum": 5200,
                    "revenue": 1000,
                }
            ]
        if query == VED_ASSIGNMENT_ITEMS_SQL:
            return [
                {
                    "item_id": "ITEM/01",
                    "ved_class": "V",
                    "assignment_reason": "critical",
                }
            ]
        raise AssertionError(query)


class ClassificationSourcePostgresTests(unittest.IsolatedAsyncioTestCase):
    async def test_approved_scope_bound_ved_assignment_is_loaded(self):
        connection = Connection()
        scope = InventoryScope("project-a", "DSE", "C100", "V100", "V100")

        result = await PostgresClassificationSource(connection).load_inputs(scope)

        self.assertEqual(len(result.ved_assignments), 1)
        self.assertEqual(result.ved_assignments[0].item_id, "ITEM/01")
        self.assertEqual(result.ved_assignments[0].ved_class, "V")
        header_call = next(call for call in connection.calls if call[0] == VED_ASSIGNMENT_HEADER_SQL)
        self.assertEqual(header_call[1][1:], ("default", "project-a", "DSE", "C100", "V100", "V100"))

    async def test_changed_ved_content_hash_fails_closed(self):
        connection = Connection(ved_hash="f" * 64)
        scope = InventoryScope("project-a", "DSE", "C100", "V100", "V100")

        with self.assertRaisesRegex(
            InventoryInputError,
            "CLASSIFICATION_VED_SNAPSHOT_MISMATCH",
        ):
            await PostgresClassificationSource(connection).load_inputs(scope)


if __name__ == "__main__":
    unittest.main()

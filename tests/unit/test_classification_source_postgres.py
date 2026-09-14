"""VED binding checks in the PostgreSQL classification source adapter."""

import unittest
from datetime import timedelta

from dsio_inventory_engine.classify_inventory.application import (
    CONFIG_SCHEMA_HASH,
    CONFIG_SCHEMA_ID,
    CONFIG_SCHEMA_VERSION,
    CONFIG_SCHEMA_V2_HASH,
    CONFIG_SCHEMA_V2_ID,
    CONFIG_SCHEMA_V2_VERSION,
    InventoryScope,
    build_snapshot,
    classification_window,
    config_hash,
)
from dsio_inventory_engine.infrastructure.postgresql.classification_snapshot import (
    ACTIVE_CONFIG_SQL,
    ACTUAL_CLOSE_HISTORY_SQL,
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


def actual_close_history(anchor: str = "202601", *, weeks: int = 52) -> list[dict]:
    start, _ = classification_window(anchor, weeks)
    return [
        {
            "actual_yyyyww": (start + timedelta(weeks=offset)).strftime("%G%V"),
            "closure_revision_no": 1,
            "status_cd": "CLOSED",
            "source_relation": "dsdm.tb_dyn_demand_dtl",
            "source_manifest_sha256": "a" * 64,
            "publication_id": "00000000-0000-0000-0000-000000000003",
        }
        for offset in range(weeks)
    ]


class Connection:
    def __init__(
        self,
        *,
        ved_hash: str = "e" * 64,
        authority_status: str = "CLOSED",
        authority_publication_id: str = "00000000-0000-0000-0000-000000000003",
        authority_history: list[dict] | None = None,
        values: dict | None = None,
        item_id: str = "ITEM/01",
    ):
        self.values = config_values() if values is None else values
        self.ved_hash = ved_hash
        self.authority_status = authority_status
        self.authority_publication_id = authority_publication_id
        self.authority_history = (
            actual_close_history() if authority_history is None else authority_history
        )
        self.item_id = item_id
        self.calls = []

    def transaction(self, **kwargs):
        self.calls.append(("transaction", kwargs))
        return Transaction()

    async def execute(self, query, *args):
        self.calls.append((query, args))

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        if query == ACTIVE_CONFIG_SQL:
            is_v2 = self.values["segmentation"]["mode"] == "SEVEN_AXIS"
            return {
                "config_id": "00000000-0000-0000-0000-000000000001",
                "tenant_id": "default",
                "project_id": "project-a",
                "active_config_revision_id": "00000000-0000-0000-0000-000000000002",
                "config_hash": config_hash(self.values),
                "config_schema_id": CONFIG_SCHEMA_V2_ID if is_v2 else CONFIG_SCHEMA_ID,
                "config_schema_version": (
                    CONFIG_SCHEMA_V2_VERSION if is_v2 else CONFIG_SCHEMA_VERSION
                ),
                "config_schema_hash": (CONFIG_SCHEMA_V2_HASH if is_v2 else CONFIG_SCHEMA_HASH),
                "config_values": self.values,
            }
        if query == ACTUAL_CLOSE_SQL:
            return {
                "actual_yyyyww": "202601",
                "closure_revision_no": 1,
                "status_cd": self.authority_status,
                "source_relation": "dsdm.tb_dyn_demand_dtl",
                "source_manifest_sha256": "a" * 64,
                "publication_id": self.authority_publication_id,
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
        if query == ACTUAL_CLOSE_HISTORY_SQL:
            return self.authority_history
        if query == ITEM_METRICS_SQL:
            return [
                {
                    "item_id": self.item_id,
                    "source_row_count": 52,
                    "invalid_row_count": 0,
                    "observed_week_count": 52,
                    "total_demand": 520,
                    "demand_square_sum": 5200,
                    "revenue": 1000,
                    "abc_source_row_count": 26,
                    "abc_invalid_row_count": 0,
                    "abc_observed_week_count": 26,
                    "abc_revenue": 600,
                    "xyz_source_row_count": 13,
                    "xyz_invalid_row_count": 0,
                    "xyz_observed_week_count": 13,
                    "xyz_total_demand": 130,
                    "xyz_demand_square_sum": 1300,
                    "fsn_source_row_count": 26,
                    "fsn_invalid_row_count": 0,
                    "positive_week_count": 13,
                    "last_positive_demand_yyyyww": "202601",
                    "introduced_yyyyww": "202401",
                    "production_end_yyyyww": None,
                    "service_end_yyyyww": "203052",
                    "lifecycle_status_cd": "ACTIVE",
                    "source_profile_hash": "f" * 64,
                }
            ]
        if query == VED_ASSIGNMENT_ITEMS_SQL:
            return [
                {
                    "item_id": self.item_id,
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
        self.assertEqual(result.metrics[0].abc_revenue, 600)
        self.assertEqual(result.metrics[0].xyz_total_demand, 130)
        self.assertEqual(result.metrics[0].fsn_source_status, "UNVERIFIED")
        self.assertEqual(result.metrics[0].plc_source_status, "SYNTHETIC")
        self.assertEqual(result.metrics[0].plc_source_profile_hash, "f" * 64)
        self.assertEqual(result.actual_close_window_start_yyyyww, "202502")
        self.assertEqual(
            result.actual_close_history_hash,
            "c97b88e2b01da426ac3cbc29d86e1a531444cd1adf31872e0004eae9674509fb",
        )
        self.assertIn(
            ("transaction", {"isolation": "repeatable_read", "readonly": True}),
            connection.calls,
        )
        self.assertNotIn("status_cd IN", ACTUAL_CLOSE_SQL)
        history_call = next(
            call for call in connection.calls if call[0] == ACTUAL_CLOSE_HISTORY_SQL
        )
        self.assertEqual(history_call[1][-2:], ("202502", "202601"))
        metric_call = next(call for call in connection.calls if call[0] == ITEM_METRICS_SQL)
        self.assertEqual(len(metric_call[1]), 8)
        self.assertIn("AS abc_invalid_row_count", ITEM_METRICS_SQL)
        self.assertIn("AS demand_invalid_row_count", ITEM_METRICS_SQL)
        self.assertIn("SUM(weekly.demand_invalid_row_count) FILTER", ITEM_METRICS_SQL)
        header_call = next(
            call for call in connection.calls if call[0] == VED_ASSIGNMENT_HEADER_SQL
        )
        self.assertEqual(
            header_call[1][1:], ("default", "project-a", "DSE", "C100", "V100", "V100")
        )

    async def test_changed_ved_content_hash_fails_closed(self):
        connection = Connection(ved_hash="f" * 64)
        scope = InventoryScope("project-a", "DSE", "C100", "V100", "V100")

        with self.assertRaisesRegex(
            InventoryInputError,
            "CLASSIFICATION_VED_SNAPSHOT_MISMATCH",
        ):
            await PostgresClassificationSource(connection).load_inputs(scope)

    async def test_latest_non_terminal_actual_close_fails_without_fallback(self):
        connection = Connection(authority_status="OPEN")
        scope = InventoryScope("project-a", "DSE", "C100", "V100", "V100")

        with self.assertRaisesRegex(
            InventoryInputError,
            "CLASSIFICATION_ACTUAL_CLOSE_NOT_TERMINAL",
        ):
            await PostgresClassificationSource(connection).load_inputs(scope)

    async def test_missing_week_in_actual_lookback_fails_closed(self):
        history = actual_close_history()
        del history[10]
        connection = Connection(authority_history=history)
        scope = InventoryScope("project-a", "DSE", "C100", "V100", "V100")

        with self.assertRaisesRegex(
            InventoryInputError,
            "CLASSIFICATION_ACTUAL_LOOKBACK_NOT_FULLY_SEALED",
        ):
            await PostgresClassificationSource(connection).load_inputs(scope)

    async def test_blank_actual_close_publication_id_fails_closed(self):
        connection = Connection(authority_publication_id="")
        scope = InventoryScope("project-a", "DSE", "C100", "V100", "V100")

        with self.assertRaisesRegex(
            InventoryInputError,
            "CLASSIFICATION_ACTUAL_CLOSE_INVALID",
        ):
            await PostgresClassificationSource(connection).load_inputs(scope)

    async def test_blank_historical_publication_id_breaks_seal(self):
        history = actual_close_history()
        history[10]["publication_id"] = ""
        connection = Connection(authority_history=history)
        scope = InventoryScope("project-a", "DSE", "C100", "V100", "V100")

        with self.assertRaisesRegex(
            InventoryInputError,
            "CLASSIFICATION_ACTUAL_LOOKBACK_NOT_FULLY_SEALED",
        ):
            await PostgresClassificationSource(connection).load_inputs(scope)

    async def test_historical_revision_changes_window_lineage_hash(self):
        first_history = actual_close_history()
        changed_history = actual_close_history()
        changed_history[10] = {
            **changed_history[10],
            "closure_revision_no": 2,
            "status_cd": "REVISED",
            "source_manifest_sha256": "b" * 64,
        }
        scope = InventoryScope("project-a", "DSE", "C100", "V100", "V100")

        first = await PostgresClassificationSource(
            Connection(authority_history=first_history)
        ).load_inputs(scope)
        changed = await PostgresClassificationSource(
            Connection(authority_history=changed_history)
        ).load_inputs(scope)

        self.assertNotEqual(
            first.actual_close_history_hash,
            changed.actual_close_history_hash,
        )

    async def test_sde_lookback_does_not_expand_demand_actual_lineage(self):
        from tests.unit.test_seven_axis_segmentation import v2_values

        values = v2_values()
        values["segmentation"]["abc"]["lookback_weeks"] = 26
        values["segmentation"]["xyz"]["lookback_weeks"] = 13
        values["segmentation"]["fsn"]["lookback_weeks"] = 13
        values["segmentation"]["fsn"]["non_moving_weeks"] = 13
        values["segmentation"]["sde"]["lookback_weeks"] = 104
        connection = Connection(
            values=values,
            authority_history=actual_close_history(weeks=26),
            item_id="ITEM-01",
        )
        scope = InventoryScope("project-a", "DSE", "C100", "V100", "V100")

        result = await PostgresClassificationSource(connection).load_inputs(scope)

        snapshot = build_snapshot(result)

        self.assertEqual(result.actual_close_window_start_yyyyww, "202528")
        self.assertEqual(snapshot["actual_close_window_start_yyyyww"], "202528")
        history_call = next(
            call for call in connection.calls if call[0] == ACTUAL_CLOSE_HISTORY_SQL
        )
        self.assertEqual(history_call[1][-2:], ("202528", "202601"))

    async def test_unsealed_shadow_fsn_is_unverified_without_blocking_core_axes(self):
        from tests.unit.test_seven_axis_segmentation import v2_values

        values = v2_values()
        values["segmentation"]["abc"]["lookback_weeks"] = 26
        values["segmentation"]["xyz"]["lookback_weeks"] = 13
        values["segmentation"]["fsn"]["lookback_weeks"] = 52
        values["segmentation"]["fsn"]["application_mode"] = "SHADOW"
        connection = Connection(
            values=values,
            authority_history=actual_close_history(weeks=26),
            item_id="ITEM-01",
        )
        scope = InventoryScope("project-a", "DSE", "C100", "V100", "V100")

        result = await PostgresClassificationSource(connection).load_inputs(scope)

        snapshot = build_snapshot(result)

        self.assertEqual(result.actual_close_window_start_yyyyww, "202528")
        self.assertEqual(result.metrics[0].fsn_source_status, "UNVERIFIED")
        fsn = next(axis for axis in snapshot["items"][0]["axis_results"] if axis["axis"] == "FSN")
        self.assertEqual(fsn["status"], "UNVERIFIED")


if __name__ == "__main__":
    unittest.main()

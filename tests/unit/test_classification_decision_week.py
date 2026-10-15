"""A Plan's decision week binds the exact prior close, never the latest week."""

import copy
import unittest
from dataclasses import replace

from tests.unit.test_classification_source_postgres import (
    Connection,
    actual_close_history,
)
from tests.unit.test_seven_axis_segmentation import inputs, v2_metric, v2_values
from tests.unit.test_classification_snapshot import FakePublisher, FakeSource
from dsio_inventory_engine.classify_inventory.application import (
    CONFIG_SCHEMA_V2_HASH,
    CONFIG_SCHEMA_V2_ID,
    CONFIG_SCHEMA_V2_VERSION,
    InventoryScope,
    InventoryClassificationLifecycleUseCase,
    build_snapshot,
    config_hash,
)
from dsio_inventory_engine.infrastructure.postgresql.classification_snapshot import (
    ACTUAL_CLOSE_SQL,
    ACTUAL_CLOSE_HISTORY_SQL,
    ITEM_METRICS_SQL,
    PostgresClassificationSource,
    _validate_publishable_snapshot,
    _validate_v2_header,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError


def values():
    value = v2_values()
    for axis in ("abc", "xyz", "fsn"):
        value["segmentation"][axis]["lookback_weeks"] = 26
    value["segmentation"]["fsn"]["non_moving_weeks"] = 13
    return value


def decision_inputs():
    value = values()
    original = inputs()
    return replace(
        original,
        config_values=value,
        config_hash=config_hash(value),
        config_schema_id=CONFIG_SCHEMA_V2_ID,
        config_schema_version=CONFIG_SCHEMA_V2_VERSION,
        config_schema_hash=CONFIG_SCHEMA_V2_HASH,
        actual_yyyyww="202511",
        decision_yyyyww="202512",
        actual_close_window_start_yyyyww="202438",
        metrics=tuple(
            v2_metric(row, positive_week_count=20, last_positive_demand_yyyyww="202511")
            for row in original.metrics
        ),
    )


class DecisionConnection(Connection):
    def __init__(self, *, anchor="202511", **kwargs):
        self.anchor = anchor
        super().__init__(
            values=values(), authority_history=actual_close_history(anchor, weeks=26), **kwargs
        )

    async def fetchrow(self, query, *args):
        if "FROM dsdm.ctl_actual_week_close" in query:
            self.calls.append((query, args))
            # A future close exists. Only an exact parameter may retrieve the prior week.
            if len(args) == 6:
                self.asserted_week = args[-1]
                if args[-1] != self.anchor:
                    return None
                return {**self.authority_history[-1], "status_cd": self.authority_status}
            return {**self.authority_history[-1], "actual_yyyyww": "202601"}
        return await super().fetchrow(query, *args)

    async def fetch(self, query, *args):
        rows = await super().fetch(query, *args)
        if query == ITEM_METRICS_SQL:
            return [{**row, "last_positive_demand_yyyyww": self.anchor} for row in rows]
        return rows


class DecisionSnapshotTests(unittest.TestCase):
    def test_closed_history_accepts_next_decision_week_without_moving_window(self):
        snapshot = build_snapshot(decision_inputs())
        self.assertEqual(snapshot["as_of_yyyyww"], "202512")
        self.assertEqual(snapshot["window_start"], "2024-09-16")
        self.assertEqual(snapshot["window_end_exclusive"], "2025-03-17")
        self.assertTrue(
            snapshot["source_revision"].startswith("ACTUAL-CLOSE-DECISION-WINDOW-202438-202511-")
        )
        self.assertEqual(snapshot, build_snapshot(decision_inputs()))
        _validate_publishable_snapshot(snapshot)

    def test_wrong_history_week_is_not_replaced_by_any_available_close(self):
        for week in ("202510", "202512", "202601"):
            with (
                self.subTest(week=week),
                self.assertRaisesRegex(
                    InventoryInputError, "CLASSIFICATION_HISTORY_MUST_PRECEDE_DECISION"
                ),
            ):
                build_snapshot(replace(decision_inputs(), actual_yyyyww=week))

    def test_source_cannot_be_relabelled_as_authoritative_close(self):
        for changes in (
            {"source_relation": "DEVELOPMENT_PINNED_FIXTURE"},
            {"closure_revision_no": 0},
        ):
            with (
                self.subTest(changes=changes),
                self.assertRaisesRegex(InventoryInputError, "CLASSIFICATION_ACTUAL_CLOSE_INVALID"),
            ):
                build_snapshot(replace(decision_inputs(), **changes))

    def test_header_rejects_changed_decision_lineage_and_window(self):
        original = build_snapshot(decision_inputs())
        for changes in (
            {"source_revision": original["source_revision"].replace("202511-", "202510-")},
            {"source_revision": original["source_revision"][:-64] + "f" * 64},
            {"source_revision": original["source_revision"].replace("202438-", "202439-")},
            {"window_end_exclusive": "2025-03-24"},
            {"source_relation": "DEVELOPMENT_PINNED_FIXTURE"},
        ):
            with self.subTest(changes=changes), self.assertRaises(InventoryInputError):
                _validate_v2_header({**copy.deepcopy(original), **changes})


class DecisionSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_plan_mismatch_is_rejected_before_any_snapshot_publication(self):
        publisher = FakePublisher()
        lifecycle = InventoryClassificationLifecycleUseCase(
            FakeSource(decision_inputs()), publisher
        )
        with self.assertRaisesRegex(InventoryInputError, "CLASSIFICATION_DECISION_WEEK_MISMATCH"):
            await lifecycle.execute(
                decision_inputs().scope,
                approved_by="admin",
                publish=True,
                decision_yyyyww="202513",
            )
        self.assertEqual(publisher.calls, [])

    async def test_plan_uses_exact_prior_close_despite_a_later_close(self):
        connection = DecisionConnection()
        source = PostgresClassificationSource(connection, decision_yyyyww="202512")
        result = await source.load_inputs(
            InventoryScope("project-a", "DSE", "C100", "V100", "V100")
        )
        self.assertEqual(result.actual_yyyyww, "202511")
        self.assertEqual(result.decision_yyyyww, "202512")
        self.assertEqual(connection.asserted_week, "202511")
        self.assertFalse(any(query == ACTUAL_CLOSE_SQL for query, _ in connection.calls))
        history = next(
            args for query, args in connection.calls if query == ACTUAL_CLOSE_HISTORY_SQL
        )
        self.assertEqual(history[-2:], ("202438", "202511"))
        metrics = next(args for query, args in connection.calls if query == ITEM_METRICS_SQL)
        self.assertEqual(metrics[3:5], ("20240916", "20250317"))
        _validate_publishable_snapshot(build_snapshot(result))

    async def test_missing_exact_week_fails_without_latest_fallback(self):
        connection = DecisionConnection(anchor="202510")
        with self.assertRaisesRegex(InventoryInputError, "CLASSIFICATION_ACTUAL_CLOSE_MISSING"):
            await PostgresClassificationSource(connection, decision_yyyyww="202512").load_inputs(
                InventoryScope("project-a", "DSE", "C100", "V100", "V100")
            )
        self.assertFalse(any(query == ACTUAL_CLOSE_SQL for query, _ in connection.calls))

    async def test_open_latest_revision_of_exact_week_is_not_skipped(self):
        connection = DecisionConnection(authority_status="OPEN")
        with self.assertRaisesRegex(
            InventoryInputError, "CLASSIFICATION_ACTUAL_CLOSE_NOT_TERMINAL"
        ):
            await PostgresClassificationSource(connection, decision_yyyyww="202512").load_inputs(
                InventoryScope("project-a", "DSE", "C100", "V100", "V100")
            )

    async def test_calendar_year_boundary_uses_previous_iso_week(self):
        connection = DecisionConnection(anchor="202053")
        result = await PostgresClassificationSource(
            connection, decision_yyyyww="202101"
        ).load_inputs(InventoryScope("project-a", "DSE", "C100", "V100", "V100"))
        self.assertEqual(result.actual_yyyyww, "202053")
        self.assertEqual(connection.asserted_week, "202053")

    def test_invalid_decision_week_fails_before_database_access(self):
        for week in ("202553", "20251", "", 202512):
            with self.subTest(week=week), self.assertRaises(InventoryInputError):
                PostgresClassificationSource(DecisionConnection(), decision_yyyyww=week)


if __name__ == "__main__":
    unittest.main()

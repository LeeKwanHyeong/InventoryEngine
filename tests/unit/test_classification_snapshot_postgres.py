"""PostgreSQL publication behavior with connection-injected fakes."""

import hashlib
import json
import unittest
import uuid
from unittest.mock import AsyncMock

from dsio_inventory_engine.classify_inventory.application import SNAPSHOT_NAMESPACE
from dsio_inventory_engine.infrastructure.postgresql.classification_snapshot import (
    EXISTING_SNAPSHOT_SQL,
    PostgresClassificationPublisher,
)
from dsio_inventory_engine.inventory_contracts.classification import (
    derive_effective_item_policy,
)
from dsio_inventory_engine.inventory_contracts.effective_policy_v2 import (
    derive_effective_item_policy_v2,
    effective_policy_content_hash,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError


class Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class Connection:
    def __init__(self, existing):
        self.existing = existing
        self.transactions = []
        self.execute = AsyncMock()
        self.executemany = AsyncMock()
        self.fetchval = AsyncMock(return_value=2)
        self.fetchrow = AsyncMock(side_effect=self._fetchrow)

    def transaction(self, **kwargs):
        self.transactions.append(kwargs)
        return Transaction()

    async def _fetchrow(self, query, *args):
        if query == EXISTING_SNAPSHOT_SQL:
            return self.existing
        raise AssertionError(query)


def snapshot() -> dict:
    value = {
        "classification_snapshot_id": "00000000-0000-0000-0000-000000000001",
        "tenant_id": "default",
        "project_id": "project-a",
        "scope": {
            "company_cd": "DSE",
            "subs_cd": "C100",
            "plant_cd": "V100",
            "site_cd": "V100",
        },
        "config_id": "00000000-0000-0000-0000-000000000002",
        "config_revision_id": "00000000-0000-0000-0000-000000000003",
        "config_hash": "a" * 64,
        "source_revision": "ACTUAL-CLOSE-202601-R1-publication-a",
        "source_content_hash": "b" * 64,
        "source_manifest_sha256": "c" * 64,
        "source_relation": "dsdm.tb_dyn_demand_dtl",
        "content_hash": "0" * 64,
        "as_of_yyyyww": "202601",
        "window_start": "2025-01-06",
        "window_end_exclusive": "2026-01-05",
        "segmentation_type": "ABC_XYZ",
        "abc_basis": "REVENUE",
        "abc_lookback_weeks": 52,
        "xyz_metric": "DEMAND_CV2",
        "xyz_lookback_weeks": 52,
        "service_level_type": "CYCLE_SERVICE_LEVEL",
        "item_result_contract_version": "1.1.0",
        "effective_policy_contract_version": "1.0.0",
        "effective_policy_content_hash": "d" * 64,
        "ved_assignment_snapshot_id": None,
        "ved_assignment_content_hash": None,
        "eligible_sku_count": 1,
        "classified_sku_count": 1,
        "unclassified_sku_count": 0,
        "segments": tuple(
            {
                "segment_key": key,
                "sku_count": 1 if key == "AX" else 0,
                "revenue_share": "1.000000000000" if key == "AX" else "0.000000000000",
            }
            for key in ("AX", "AY", "AZ", "BX", "BY", "BZ", "CX", "CY", "CZ")
        ),
        "unclassified_reasons": (),
        "items": (
            {
                "item_id": "A",
                "classification_status": "CLASSIFIED",
                "abc_class": "A",
                "xyz_class": "X",
                "ved_class": None,
                "segment_key": "AX",
                "final_segment_key": "AX",
                "ved_assignment_source": "DISABLED",
                "ved_assignment_reason": None,
                "unclassified_reason_code": None,
                "demand_cv2": "0.000000000000",
                "revenue": "100",
                "cumulative_revenue_share_before": "0.000000000000",
            },
        ),
    }
    value["items"][0].update(
        derive_effective_item_policy(
            item_id="A",
            classification_status="CLASSIFIED",
            segment_key="AX",
            ved_class=None,
            unclassified_reason_code=None,
            policy_cell={
                "segment_key": "AX",
                "target_service_level": 0.95,
                "review_cycle_weeks": 1,
                "strategy": "MATHEMATICAL",
            },
            ved_service_level_floor={"V": 0.99, "E": 0.97, "D": 0.9},
            config_hash=value["config_hash"],
        )
    )
    value["effective_policy_content_hash"] = hashlib.sha256(
        json.dumps(
            [
                {
                    "item_id": "A",
                    "effective_policy_hash": value["items"][0]["effective_policy_hash"],
                }
            ],
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    body = {
        key: item
        for key, item in value.items()
        if key not in {"classification_snapshot_id", "content_hash"}
    }
    value["content_hash"] = hashlib.sha256(
        json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    value["classification_snapshot_id"] = str(uuid.uuid5(SNAPSHOT_NAMESPACE, value["content_hash"]))
    return value


def reseal(value: dict) -> None:
    body = {
        key: item
        for key, item in value.items()
        if key not in {"classification_snapshot_id", "content_hash"}
    }
    value["content_hash"] = hashlib.sha256(
        json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    value["classification_snapshot_id"] = str(uuid.uuid5(SNAPSHOT_NAMESPACE, value["content_hash"]))


def v2_snapshot() -> dict:
    value = snapshot()
    axis_results = [
        {
            "axis": "ABC",
            "status": "CLASSIFIED",
            "class_code": "A",
            "application_mode": "OPERATIONAL",
            "policy_effective": True,
            "source_contract_key": "CLOSED_DEMAND_VALUE_V1",
            "evidence": {},
            "reason_code": None,
        },
        {
            "axis": "XYZ",
            "status": "CLASSIFIED",
            "class_code": "X",
            "application_mode": "OPERATIONAL",
            "policy_effective": True,
            "source_contract_key": "CLOSED_DEMAND_VARIABILITY_V1",
            "evidence": {},
            "reason_code": None,
        },
        {
            "axis": "VED",
            "status": "NOT_APPLICABLE",
            "class_code": None,
            "application_mode": "SHADOW",
            "policy_effective": False,
            "source_contract_key": "APPROVED_VED_ASSIGNMENT_V1",
            "evidence": {},
            "reason_code": "AXIS_DISABLED",
        },
        {
            "axis": "FSN",
            "status": "CLASSIFIED",
            "class_code": "N",
            "application_mode": "OPERATIONAL",
            "policy_effective": True,
            "source_contract_key": "CLOSED_DEMAND_MOVEMENT_V1",
            "evidence": {"weeks_since_movement": 30},
            "reason_code": None,
        },
        {
            "axis": "SDE",
            "status": "CLASSIFIED",
            "class_code": "D",
            "application_mode": "OPERATIONAL",
            "policy_effective": True,
            "source_contract_key": "SUPPLIER_LEAD_TIME_V1",
            "evidence": {
                "p50_lead_time_days": "14",
                "p90_lead_time_days": "21",
            },
            "reason_code": None,
        },
        {
            "axis": "HML",
            "status": "CLASSIFIED",
            "class_code": "H",
            "application_mode": "OPERATIONAL",
            "policy_effective": True,
            "source_contract_key": "INVENTORY_UNIT_COST_V1",
            "evidence": {"unit_cost": "100"},
            "reason_code": None,
        },
        {
            "axis": "PLC",
            "status": "SYNTHETIC",
            "class_code": "MATURE",
            "application_mode": "SHADOW",
            "policy_effective": False,
            "source_contract_key": "SITE_PART_LIFECYCLE_V1",
            "evidence": {"source_profile_hash": "e" * 64},
            "reason_code": "SYNTHETIC_SOURCE_SHADOW_ONLY",
        },
    ]
    item = {
        "item_id": "A",
        "classification_status": "CLASSIFIED",
        "abc_classification_status": "CLASSIFIED",
        "xyz_classification_status": "CLASSIFIED",
        "abc_class": "A",
        "xyz_class": "X",
        "abc_unclassified_reason_code": None,
        "xyz_unclassified_reason_code": None,
        "ved_class": None,
        "segment_key": "AX",
        "final_segment_key": "AX",
        "ved_assignment_source": "DISABLED",
        "ved_assignment_reason": None,
        "unclassified_reason_code": None,
        "demand_cv2": "0.000000000000",
        "revenue": "100",
        "cumulative_revenue_share_before": "0.000000000000",
        "axis_results": axis_results,
        "display_segment_code": "AX-N-D-H-MATURE",
        "seven_axis_operational_eligible": True,
        "policy_effective_axes": ["ABC", "XYZ", "FSN", "SDE", "HML"],
    }
    item.update(
        derive_effective_item_policy_v2(
            item_id="A",
            classification_config_hash=value["config_hash"],
            classification_status="CLASSIFIED",
            segment_key="AX",
            unclassified_reason_code=None,
            policy_cell={
                "segment_key": "AX",
                "target_service_level": 0.95,
                "review_cycle_weeks": 1,
                "strategy": "MATHEMATICAL",
            },
            ved_service_level_floor={"V": 0.99, "E": 0.97, "D": 0.9},
            axis_results=axis_results,
            policy_overlays={
                "fsn_order_action": {"F": "ALLOW", "S": "ALLOW", "N": "REVIEW"},
                "sde_lead_time_basis": {"S": "P90", "D": "P90", "E": "P50"},
                "hml_approval_level": {
                    "H": "HIGH_VALUE",
                    "M": "STANDARD",
                    "L": "AUTO",
                },
                "plc_order_action": {
                    "PRE_LAUNCH": "REVIEW",
                    "INTRODUCTION": "ALLOW",
                    "GROWTH": "ALLOW",
                    "MATURE": "ALLOW",
                    "DECLINE": "REVIEW",
                    "SERVICE_ONLY": "REVIEW",
                    "DISCONTINUED": "BLOCK",
                },
            },
        )
    )
    value.update(
        {
            "segmentation_type": "SEVEN_AXIS",
            "item_result_contract_version": "2.0.0",
            "effective_policy_contract_version": "2.0.0",
            "axis_result_contract_version": "2.0.0",
            "actual_close_window_start_yyyyww": "202502",
            "actual_close_history_hash": "f" * 64,
            "source_revision": f"ACTUAL-CLOSE-WINDOW-202502-202601-{'f' * 64}",
            "axis_windows": {
                axis: {
                    "lookback_weeks": 52,
                    "window_start": "2025-01-06",
                    "window_end_exclusive": "2026-01-05",
                }
                for axis in ("ABC", "XYZ", "FSN", "SDE")
            },
            "items": (item,),
        }
    )
    value["effective_policy_content_hash"] = effective_policy_content_hash(value["items"])
    reseal(value)
    return value


class PostgresClassificationPublisherTests(unittest.IsolatedAsyncioTestCase):
    async def test_v2_projection_writes_closed_review_evidence(self):
        value = v2_snapshot()
        connection = Connection(None)

        result = await PostgresClassificationPublisher(connection).publish(
            value, approved_by="admin"
        )

        self.assertEqual(result["publication_status"], "published")
        self.assertEqual(connection.transactions, [{"isolation": "serializable"}])
        self.assertEqual(connection.execute.await_count, 3)
        self.assertEqual(connection.executemany.await_count, 5)
        segment_call, item_call, window_call, axis_call, policy_reason_call = (
            connection.executemany.await_args_list
        )
        self.assertEqual(len(segment_call.args[1]), 9)
        self.assertEqual(len(item_call.args[1]), 1)
        self.assertEqual(len(window_call.args[1]), 4)
        self.assertEqual(len(axis_call.args[1]), 7)
        axis_rows = {row[2]: row for row in axis_call.args[1]}
        self.assertEqual(axis_rows["FSN"][10], '{"order_action":"REVIEW"}')
        self.assertEqual(
            axis_rows["SDE"][10],
            '{"lead_time_basis":"P90","lead_time_days":"21"}',
        )
        self.assertEqual(axis_rows["HML"][10], '{"approval_level":"HIGH_VALUE"}')
        self.assertIsNone(axis_rows["PLC"][10])
        self.assertEqual(
            axis_rows["PLC"][8],
            '{"source_profile_hash":"' + "e" * 64 + '"}',
        )
        self.assertEqual(
            policy_reason_call.args[1],
            [
                (
                    value["classification_snapshot_id"],
                    "A",
                    "ADJUSTMENT",
                    1,
                    "FSN_ORDER_ACTION_REVIEW",
                ),
                (
                    value["classification_snapshot_id"],
                    "A",
                    "ADJUSTMENT",
                    2,
                    "SDE_PROTECTION_LEAD_TIME_P90",
                ),
                (
                    value["classification_snapshot_id"],
                    "A",
                    "ADJUSTMENT",
                    3,
                    "HML_APPROVAL_HIGH_VALUE",
                ),
                (
                    value["classification_snapshot_id"],
                    "A",
                    "GATE",
                    1,
                    "ORDER_ACTION_REVIEW",
                ),
                (
                    value["classification_snapshot_id"],
                    "A",
                    "GATE",
                    2,
                    "HML_APPROVAL_HIGH_VALUE",
                ),
            ],
        )
        v2_item_row = item_call.args[1][0]
        self.assertTrue(v2_item_row[27])
        self.assertEqual(v2_item_row[29], "REVIEW")
        self.assertFalse(v2_item_row[35])
        self.assertFalse(v2_item_row[36])

    async def test_v2_exact_replay_performs_no_insert(self):
        value = v2_snapshot()
        connection = Connection(
            {
                "classification_snapshot_id": value["classification_snapshot_id"],
                "snapshot_revision": 4,
            }
        )

        result = await PostgresClassificationPublisher(connection).publish(
            value, approved_by="admin"
        )

        self.assertEqual(result["publication_status"], "exact_replay")
        self.assertEqual(result["snapshot_revision"], 4)
        connection.fetchval.assert_not_awaited()
        connection.executemany.assert_not_awaited()
        self.assertEqual(connection.execute.await_count, 2)

    async def test_v2_missing_axis_fails_before_transaction(self):
        value = v2_snapshot()
        value["items"][0]["axis_results"].pop()
        reseal(value)
        connection = Connection(None)

        with self.assertRaisesRegex(InventoryInputError, "CLASSIFICATION_V2_AXIS_RESULTS_INVALID"):
            await PostgresClassificationPublisher(connection).publish(value, approved_by="admin")

        self.assertEqual(connection.transactions, [])

    async def test_v2_axis_policy_projection_drift_fails_before_transaction(self):
        value = v2_snapshot()
        fsn = next(
            result for result in value["items"][0]["axis_results"] if result["axis"] == "FSN"
        )
        fsn["class_code"] = "F"
        reseal(value)
        connection = Connection(None)

        with self.assertRaisesRegex(
            InventoryInputError, "CLASSIFICATION_V2_AXIS_PROJECTION_MISMATCH"
        ):
            await PostgresClassificationPublisher(connection).publish(value, approved_by="admin")

        self.assertEqual(connection.transactions, [])

    async def test_v2_effective_policy_hash_tampering_fails_before_transaction(self):
        value = v2_snapshot()
        value["items"][0]["effective_policy_hash"] = "1" * 64
        reseal(value)
        connection = Connection(None)

        with self.assertRaisesRegex(InventoryInputError, "ITEM_POLICY_HASH_MISMATCH"):
            await PostgresClassificationPublisher(connection).publish(value, approved_by="admin")

        self.assertEqual(connection.transactions, [])

    async def test_header_downgrade_cannot_publish_v2_item_shape(self):
        value = snapshot()
        value["items"][0]["axis_results"] = []
        connection = Connection(None)

        with self.assertRaisesRegex(
            InventoryInputError, "CLASSIFICATION_V2_PROJECTION_MIGRATION_REQUIRED"
        ):
            await PostgresClassificationPublisher(connection).publish(value, approved_by="admin")

        self.assertEqual(connection.transactions, [])

    async def test_resealed_unknown_item_field_cannot_be_silently_dropped(self):
        value = snapshot()
        value["items"][0]["effective_order_action"] = "ALLOW"
        reseal(value)
        connection = Connection(None)

        with self.assertRaisesRegex(
            InventoryInputError, "CLASSIFICATION_V2_PROJECTION_MIGRATION_REQUIRED"
        ):
            await PostgresClassificationPublisher(connection).publish(value, approved_by="admin")

        self.assertEqual(connection.transactions, [])

    async def test_resealed_item_policy_tampering_fails_before_transaction(self):
        value = snapshot()
        value["items"][0]["effective_policy_hash"] = "f" * 64
        value["effective_policy_content_hash"] = hashlib.sha256(
            json.dumps(
                [
                    {
                        "item_id": "A",
                        "effective_policy_hash": "f" * 64,
                    }
                ],
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        reseal(value)
        connection = Connection(None)

        with self.assertRaisesRegex(InventoryInputError, "ITEM_POLICY_HASH_MISMATCH"):
            await PostgresClassificationPublisher(connection).publish(value, approved_by="admin")

        self.assertEqual(connection.transactions, [])

    async def test_resealed_segment_redistribution_fails_before_transaction(self):
        value = snapshot()
        value["segments"][0].update({"sku_count": 0, "revenue_share": "0.000000000000"})
        value["segments"][1].update({"sku_count": 1, "revenue_share": "1.000000000000"})
        reseal(value)
        connection = Connection(None)

        with self.assertRaisesRegex(
            InventoryInputError, "CLASSIFICATION_SNAPSHOT_SEGMENT_AGGREGATE_MISMATCH"
        ):
            await PostgresClassificationPublisher(connection).publish(value, approved_by="admin")

        self.assertEqual(connection.transactions, [])

    async def test_resealed_spurious_reason_fails_before_transaction(self):
        value = snapshot()
        value["unclassified_reasons"] = ({"reason_code": "SPURIOUS_REASON", "sku_count": 0},)
        reseal(value)
        connection = Connection(None)

        with self.assertRaisesRegex(
            InventoryInputError, "CLASSIFICATION_SNAPSHOT_REASON_AGGREGATE_MISMATCH"
        ):
            await PostgresClassificationPublisher(connection).publish(value, approved_by="admin")

        self.assertEqual(connection.transactions, [])

    async def test_tampered_snapshot_content_is_rejected_before_transaction(self):
        value = snapshot()
        value["source_relation"] = "dsdm.tampered_source"
        connection = Connection(None)

        with self.assertRaisesRegex(
            InventoryInputError, "CLASSIFICATION_SNAPSHOT_CONTENT_HASH_MISMATCH"
        ):
            await PostgresClassificationPublisher(connection).publish(value, approved_by="admin")

        self.assertEqual(connection.transactions, [])

    async def test_exact_replay_performs_no_insert(self):
        value = snapshot()
        connection = Connection(
            {
                "classification_snapshot_id": value["classification_snapshot_id"],
                "snapshot_revision": 1,
            }
        )

        result = await PostgresClassificationPublisher(connection).publish(
            value, approved_by="admin"
        )

        self.assertEqual(result["publication_status"], "exact_replay")
        self.assertEqual(connection.transactions, [{"isolation": "serializable"}])
        connection.fetchval.assert_not_awaited()
        connection.executemany.assert_not_awaited()
        self.assertEqual(connection.execute.await_count, 2)

    async def test_new_content_writes_header_and_exactly_nine_segments(self):
        value = snapshot()
        connection = Connection(None)

        result = await PostgresClassificationPublisher(connection).publish(
            value, approved_by="admin"
        )

        self.assertEqual(result["publication_status"], "published")
        self.assertEqual(result["snapshot_revision"], 2)
        self.assertEqual(connection.execute.await_count, 3)
        self.assertEqual(connection.executemany.await_count, 2)
        segment_call, item_call = connection.executemany.await_args_list
        self.assertEqual(len(segment_call.args[1]), 9)
        self.assertEqual(len(item_call.args[1]), 1)


if __name__ == "__main__":
    unittest.main()

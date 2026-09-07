"""PostgreSQL publication behavior with connection-injected fakes."""

import unittest
from unittest.mock import AsyncMock

from dsio_inventory_engine.infrastructure.postgresql.classification_snapshot import (
    EXISTING_SNAPSHOT_SQL,
    PostgresClassificationPublisher,
)


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
    return {
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
        "content_hash": "c" * 64,
        "as_of_yyyyww": "202601",
        "segmentation_type": "ABC_XYZ",
        "abc_basis": "REVENUE",
        "abc_lookback_weeks": 52,
        "xyz_metric": "DEMAND_CV2",
        "xyz_lookback_weeks": 52,
        "service_level_type": "CYCLE_SERVICE_LEVEL",
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
    }


class PostgresClassificationPublisherTests(unittest.IsolatedAsyncioTestCase):
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
        connection.executemany.assert_awaited_once()
        self.assertEqual(len(connection.executemany.call_args.args[1]), 9)


if __name__ == "__main__":
    unittest.main()

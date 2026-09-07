"""PostgreSQL Event adapter contract checks without a database."""

import unittest

from dsio_inventory_engine.classify_inventory.application import InventoryScope
from dsio_inventory_engine.infrastructure.postgresql.run_events import (
    PostgresInventoryRunEventRecorder,
)
from dsio_inventory_engine.run_inventory.classification_stage import (
    InventoryRunContext,
    InventoryRunEvent,
)


class Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class Connection:
    def __init__(self):
        self.fetchrow_calls = []
        self.execute_calls = []

    def transaction(self):
        return Transaction()

    async def fetchrow(self, sql, *args):
        self.fetchrow_calls.append((sql, args))
        if "SELECT status_projection" in sql:
            return {"status_projection": "running"}
        return {"event_id": "event-a", "event_seq": 2}

    async def fetchval(self, sql, *args):
        return 2

    async def execute(self, sql, *args):
        self.execute_calls.append((sql, args))
        return "UPDATE 1"


class InventoryRunEventPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def test_event_is_append_only_and_projection_stays_non_terminal(self):
        connection = Connection()
        context = InventoryRunContext(
            engine_run_id="00000000-0000-0000-0000-000000000010",
            tenant_id="default",
            project_id="project-a",
            scope=InventoryScope("project-a", "DSE", "C100", "V100", "V100"),
        )
        event = InventoryRunEvent(
            event_type="inventory.classification",
            stage="classification",
            status="succeeded",
            message_code="INVENTORY_CLASSIFICATION_SNAPSHOT_PUBLISHED",
            progress_percent=100,
            payload_redacted={
                "classification_snapshot_id": "00000000-0000-0000-0000-000000000011",
                "snapshot_revision": 2,
                "content_hash": "a" * 64,
                "eligible_sku_count": 7000,
                "classified_sku_count": 6009,
                "unclassified_sku_count": 991,
                "exact_replay": False,
            },
        )

        result = await PostgresInventoryRunEventRecorder(connection).append(context, event)

        self.assertEqual(result["publication_status"], "published")
        insert_sql = connection.fetchrow_calls[1][0]
        self.assertIn("INSERT INTO dsai.engine_runtime_run_events", insert_sql)
        projection_sql = connection.execute_calls[0][0]
        self.assertNotIn("status_projection = 'succeeded'", projection_sql)
        self.assertIn("status_projection = 'running'", projection_sql)


if __name__ == "__main__":
    unittest.main()

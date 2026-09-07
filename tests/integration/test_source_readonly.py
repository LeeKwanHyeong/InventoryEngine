"""Opt-in, pinned development diagnostic. No source sealing or DB writes."""

import json
import os
import unittest
from pathlib import Path

from dsio_inventory_engine.infrastructure.postgresql.source_reader import (
    PostgresInventorySourceReader,
)
from dsio_inventory_engine.inventory_contracts.values import digest, InventoryInputError

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(
    os.environ.get("IO_SOURCE_READONLY_INTEGRATION") == "1",
    "explicit read-only source integration not enabled",
)
class SourceReadonlyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import asyncpg

        self.evidence = json.loads(
            (
                ROOT / "docs/architecture/evidence/source-readonly-verification-20260903.json"
            ).read_text()
        )
        self.connection = await asyncpg.connect(
            os.environ["IO_POSTGRES_DSN"],
            timeout=10,
            command_timeout=15,
            server_settings={
                "default_transaction_read_only": "on",
                "application_name": "io_source_readonly_test",
            },
        )
        self.reader = PostgresInventorySourceReader(self.connection)

    async def asyncTearDown(self):
        await self.connection.close()

    async def test_exact_run_three_week_hash_and_negative_scope(self):
        first = await self.reader.read_forecast_rows(self.evidence["selector"])
        second = await self.reader.read_forecast_rows(self.evidence["selector"])
        self.assertEqual(digest(first), self.evidence["raw_forecast_rows_hash"])
        self.assertEqual(digest(first), digest(second))
        self.assertEqual(len(first), self.evidence["forecast_rows"])
        with self.assertRaisesRegex(InventoryInputError, "DEMAND_RUN_SCOPE_MISMATCH"):
            await self.reader.read_forecast_rows({**self.evidence["selector"], "site_cd": "V999"})

    async def test_current_master_is_readiness_only(self):
        result = await self.reader.inspect_site("DSE", "C100", "V100", "2025-03-03", "2025-03-23")
        self.assertFalse(result["canonical_ready"] or result["database_writes"])
        self.assertEqual(result["source_status"], "COLLECTING")
        self.assertIn("INVENTORY_CUTOFF_WATERMARK_RESERVED_REQUIRED", result["blockers"])

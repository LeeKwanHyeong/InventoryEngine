"""Reject local input before network access; corroborate the exact admitted snapshot."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from source_fixtures import mapped, seal, wrap
from dsio_inventory_engine.entrypoints.source_input import prepare_source
from dsio_inventory_engine.infrastructure.postgresql.source_reader import (
    PostgresInventorySourceReader,
)
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.source import SourceInputRequest
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError

DEV = DeploymentScope("DSE", "DEVELOPMENT")


def write_sources(root, sources):
    for source in sources.values():
        (root / (source["snapshot_id"] + ".json")).write_text(json.dumps(source))


class SourcePreflightTests(unittest.IsolatedAsyncioTestCase):
    async def assert_local_failure(self, request, sources, code):
        connection = SimpleNamespace(close=AsyncMock())
        connect = AsyncMock(return_value=connection)
        verify = AsyncMock(side_effect=lambda snapshot: snapshot)
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(sys.modules, {"asyncpg": SimpleNamespace(connect=connect)}),
            patch.object(PostgresInventorySourceReader, "verify_snapshot", verify),
        ):
            root = Path(directory)
            write_sources(root, sources)
            with self.assertRaisesRegex(InventoryInputError, code):
                await prepare_source(
                    SourceInputRequest.from_dict(request),
                    DEV,
                    root,
                    read_postgres=True,
                    dsn="TEST-ONLY-NOT-A-CONNECTION",
                )
        connect.assert_not_awaited()
        verify.assert_not_awaited()
        connection.close.assert_not_awaited()

    async def test_request_hash_mismatch_rejected_before_connect(self):
        req, sources = mapped()
        req["source_bindings"]["forecast"]["content_hash"] = "f" * 64
        await self.assert_local_failure(req, sources, "PINNED_SOURCE_MISMATCH")

    async def test_all_local_errors_precede_connect(self):
        cases = (
            (
                "forecast",
                lambda s: s["metadata"].update(
                    demand_run_id="00000000-0000-4000-8000-000000000001"
                ),
                "FORECAST_RUN_MISMATCH",
            ),
            ("forecast", lambda s: s.update(status="COLLECTING"), "UNSEALED_SOURCE"),
            ("forecast", lambda s: s["metadata"].update(site_cd="V999"), "SOURCE_SCOPE_MISMATCH"),
            ("forecast", lambda s: s["rows"].pop(), "MISSING_FORECAST_BUCKET"),
            (
                "inventory",
                lambda s: s.update(origin_type="UNKNOWN_SOURCE"),
                "UNKNOWN_SOURCE_ORIGIN",
            ),
            ("inventory", lambda s: s["rows"][0].update(oper_part_no="ORPHAN"), "ORPHAN_ITEM"),
            (
                "inventory",
                lambda s: s.update(source_as_of_date="2026-09-29"),
                "SOURCE_POSITION_AS_OF_MISMATCH",
            ),
            ("inventory", lambda s: s.update(rows=[]), "MISSING_POSITION"),
            (
                "inventory",
                lambda s: s["rows"][0].update(boh_qty="101", available_qty="101"),
                "EOH_BOH_RECONCILIATION_FAILED",
            ),
            (
                "inventory",
                lambda s: s["metadata"].update(complete_through="2026-09-27T00:00:00Z"),
                "SOURCE_WATERMARK_INCOMPLETE",
            ),
            (
                "inventory",
                lambda s: s["metadata"].update(source_watermark="WRONG"),
                "CUTOFF_BINDING_MISMATCH",
            ),
            (
                "inventory",
                lambda s: (
                    s["rows"][0].update(reserved_qty="10", available_qty="90"),
                    s["metadata"].update(reserved_treatment="UNKNOWN"),
                ),
                "RESERVED_SEMANTICS_UNSUPPORTED",
            ),
            (
                "customer_orders",
                lambda s: s["metadata"].update(coverage="UNAVAILABLE"),
                "SOURCE_COVERAGE_UNAVAILABLE",
            ),
            ("policies", lambda s: s["rows"][0].update(min_po_qty=None), "DECIMAL_STRING_REQUIRED"),
        )
        for kind, mutate, code in cases:
            req, sources = mapped()
            mutate(sources[kind])
            with self.subTest(code=code):
                await self.assert_local_failure(seal(req, sources), sources, code)

    async def test_missing_file_rejected_before_connect(self):
        req, sources = mapped()
        sources.pop("inventory")
        await self.assert_local_failure(req, sources, "SOURCE_SNAPSHOT_NOT_FOUND")

    async def test_non_db_forecast_adapter_rejected_before_connect(self):
        req, sources = wrap()
        await self.assert_local_failure(req, sources, "POSTGRES_FORECAST_ADAPTER_REQUIRED")

    async def test_db_corroboration_keeps_exact_prepared_output_without_rereading(self):
        req, sources = mapped()
        connection = SimpleNamespace(close=AsyncMock())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_sources(root, sources)
            request = SourceInputRequest.from_dict(req)
            offline = await prepare_source(request, DEV, root)

            async def connect_after_preflight(*args, **kwargs):
                # A producer changes the file after admission. Do not consume or reseal it.
                forecast = sources["forecast"]
                (root / (forecast["snapshot_id"] + ".json")).write_text("{}")
                return connection

            connect = AsyncMock(side_effect=connect_after_preflight)
            verify = AsyncMock(side_effect=lambda snapshot: snapshot)
            with (
                patch.dict(sys.modules, {"asyncpg": SimpleNamespace(connect=connect)}),
                patch.object(PostgresInventorySourceReader, "verify_snapshot", verify),
            ):
                online = await prepare_source(
                    request, DEV, root, read_postgres=True, dsn="TEST-ONLY"
                )
        self.assertEqual(online, offline)
        verify.assert_awaited_once()
        self.assertEqual(
            verify.call_args.args[0].to_dict(), offline["source_snapshots"]["forecast"]
        )
        connect.assert_awaited_once()
        self.assertEqual(
            connect.call_args.kwargs["server_settings"]["default_transaction_read_only"], "on"
        )
        connection.close.assert_awaited_once()

    async def test_db_mismatch_rejects_and_closes_without_new_snapshot(self):
        req, sources = mapped()
        connection = SimpleNamespace(close=AsyncMock())
        connect = AsyncMock(return_value=connection)
        verify = AsyncMock(side_effect=InventoryInputError("SOURCE_HASH_MISMATCH"))
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(sys.modules, {"asyncpg": SimpleNamespace(connect=connect)}),
            patch.object(PostgresInventorySourceReader, "verify_snapshot", verify),
        ):
            root = Path(directory)
            write_sources(root, sources)
            with self.assertRaisesRegex(InventoryInputError, "SOURCE_HASH_MISMATCH"):
                await prepare_source(
                    SourceInputRequest.from_dict(req),
                    DEV,
                    root,
                    read_postgres=True,
                    dsn="TEST-ONLY",
                )
        connect.assert_awaited_once()
        verify.assert_awaited_once()
        connection.close.assert_awaited_once()

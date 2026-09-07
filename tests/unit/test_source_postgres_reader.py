"""Connection-injected reader checks with no database access."""

import copy
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from source_fixtures import mapped
from dsio_inventory_engine.infrastructure.postgresql.source_reader import (
    CALENDAR_SQL,
    CATALOG_SQL,
    FORECAST_SQL,
    MASTER_SQL,
    RUN_SQL,
    SITE_SQL,
    PostgresInventorySourceReader,
    scalar,
)
from dsio_inventory_engine.inventory_contracts.source import (
    SourceSnapshot,
    forecast_selector,
    source_hash,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class Connection:
    def __init__(self):
        _, sources = mapped()
        self.source = sources["forecast"]
        self.selector = self.source["semantics"]["selector"]
        self.run = {
            k: self.selector[k]
            for k in (
                "demand_run_id",
                "tenant_id",
                "project_id",
                "company_cd",
                "subs_cd",
                "plant_cd",
                "site_cd",
                "plan_id",
            )
        }
        self.run.update(engine_key="demand", status_projection="succeeded")
        self.rows = copy.deepcopy(self.source["rows"])
        self.tx = []
        self.execute = AsyncMock()
        self.fetchval = AsyncMock(return_value="on")
        self.fetchrow = AsyncMock(side_effect=lambda *a: self.run)
        self.fetch = AsyncMock(side_effect=self._fetch)

    def transaction(self, **kwargs):
        self.tx.append(kwargs)
        return FakeTransaction()

    async def _fetch(self, query, *args):
        if query == SITE_SQL:
            return [{"site_cd": self.selector["site_cd"], "subs_cd": self.selector["subs_cd"]}]
        if query == FORECAST_SQL:
            return self.rows
        if query == CATALOG_SQL:
            return [
                {
                    "table_name": "tb_mst_oper_part",
                    "column_name": "use_flag",
                    "data_type": "character varying",
                }
            ]
        if query == MASTER_SQL:
            return [
                {
                    "oper_part_no": "ITEM-A",
                    "use_flag": "Y",
                    "stock_flag": "Y",
                    "min_po_qty": None,
                    "po_lot_qty": None,
                    "stock_lt": None,
                    "svc_lv": None,
                }
            ]
        if query == CALENDAR_SQL:
            return [
                {
                    "yyyyww": "202640",
                    "week_start_dt": date(2026, 9, 28),
                    "week_end_dt": date(2026, 10, 4),
                }
            ]
        raise AssertionError(query)


class PostgresSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_record_iteration_is_values_not_keys(self):
        class RecordLike(dict):
            def __iter__(self):
                return iter(self.values())

        c = Connection()
        c.run = RecordLike({**c.run, "site_cd": "V999"})
        with self.assertRaisesRegex(InventoryInputError, "DEMAND_RUN_SCOPE_MISMATCH"):
            await PostgresInventorySourceReader(c).read_forecast_rows(c.selector)
        c.fetch.assert_not_awaited()

    async def test_exact_parameters_and_readonly_transaction(self):
        c = Connection()
        source = await PostgresInventorySourceReader(c).verify_snapshot(
            SourceSnapshot.from_dict(c.source)
        )
        self.assertEqual(source.to_dict()["content_hash"], c.source["content_hash"])
        self.assertEqual(c.tx, [{"isolation": "repeatable_read", "readonly": True}])
        c.fetchrow.assert_awaited_once()
        query, args_run, tenant, project = c.fetchrow.call_args.args
        self.assertEqual(query, RUN_SQL)
        self.assertEqual(
            (str(args_run), tenant, project),
            (c.selector["demand_run_id"], "default", "TEST-PROJECT"),
        )
        self.assertNotIn(c.selector["plan_id"], FORECAST_SQL)
        self.assertNotIn("latest", FORECAST_SQL.lower())
        self.assertIn("LIMIT 100001", FORECAST_SQL)

    async def test_site_inspection_reports_inventory_scope_and_fixed_ea(self):
        c = Connection()

        inspected = await PostgresInventorySourceReader(c).inspect_site(
            "DSE",
            "C100",
            "V101",
            "2026-09-28",
            "2026-10-04",
        )

        scope = inspected["inventory_master_scope"]
        self.assertEqual(scope["source_relation"], "dsdm.tb_mst_oper_part")
        self.assertEqual(scope["eligibility_rule"], "USE_FLAG_Y_AND_STOCK_FLAG_Y_V1")
        self.assertEqual(scope["eligible_item_count"], 1)
        self.assertEqual(scope["uom_rule"], "FIXED_EA_V1")

    async def test_db_mutation_never_resealed(self):
        c = Connection()
        c.rows[0]["fcst_qty"] = Decimal("999")
        with self.assertRaisesRegex(InventoryInputError, "SOURCE_HASH_MISMATCH"):
            await PostgresInventorySourceReader(c).verify_snapshot(
                SourceSnapshot.from_dict(c.source)
            )

    async def test_run_not_found_failed_or_other_scope_block_before_forecast_query(self):
        cases = [
            (None, "DEMAND_RUN_NOT_FOUND"),
            ({"status_projection": "failed"}, "DEMAND_RUN_NOT_SUCCEEDED"),
            ({"engine_key": "inventory"}, "DEMAND_RUN_NOT_SUCCEEDED"),
            ({"site_cd": "V999"}, "DEMAND_RUN_SCOPE_MISMATCH"),
        ]
        for update, code in cases:
            c = Connection()
            if update is None:
                c.run = None
            else:
                c.run.update(update)
            with self.subTest(code=code), self.assertRaisesRegex(InventoryInputError, code):
                await PostgresInventorySourceReader(c).read_forecast_rows(c.selector)
            c.fetch.assert_not_awaited()

    async def test_empty_limit_failed_model_and_mapping(self):
        for kind, code in (
            ("empty", "FORECAST_SOURCE_EMPTY_OR_LIMIT"),
            ("large", "FORECAST_SOURCE_EMPTY_OR_LIMIT"),
            ("failed", "FORECAST_MODEL_NOT_SUCCEEDED"),
            ("site", "SITE_SUBS_MAPPING_MISMATCH"),
        ):
            c = Connection()
            if kind == "empty":
                c.rows = []
            elif kind == "large":
                c.rows = [c.rows[0]] * 100001
            elif kind == "failed":
                c.rows[0]["model_run_status"] = "FAILED"
            else:
                c.fetch.side_effect = lambda *a: []
            with self.subTest(kind=kind), self.assertRaisesRegex(InventoryInputError, code):
                await PostgresInventorySourceReader(c).read_forecast_rows(c.selector)

    async def test_readonly_required(self):
        c = Connection()
        c.fetchval.return_value = "off"
        with self.assertRaisesRegex(InventoryInputError, "READONLY_TRANSACTION_REQUIRED"):
            await PostgresInventorySourceReader(c).read_forecast_rows(c.selector)

    async def test_invalid_uuid_rejected_before_io(self):
        c = Connection()
        for run in ("latest", "bad' OR 1=1", "91930A93-8601-4B71-9736-1D404F44C6FF"):
            selector = {**c.selector, "demand_run_id": run}
            with self.subTest(run=run), self.assertRaises(InventoryInputError):
                await PostgresInventorySourceReader(c).read_forecast_rows(selector)
        self.assertEqual(c.tx, [])

    async def test_live_inspection_does_not_produce_seal_or_default_policy(self):
        c = Connection()
        report = await PostgresInventorySourceReader(c).inspect_site(
            "DSE", "C100", "V101", "2026-09-28", "2026-10-18"
        )
        self.assertFalse(report["canonical_ready"] or report["database_writes"])
        self.assertEqual(report["source_status"], "COLLECTING")
        self.assertIn("tb_pln_inv_boh", report["missing_dsdm_tables"])
        self.assertEqual(report["policy_null_counts"]["min_po_qty"], 1)

    async def test_run_metadata_mismatch_and_unsealed_rejected(self):
        for kind, code in (("run", "FORECAST_RUN_MISMATCH"), ("seal", "UNSEALED_SOURCE")):
            c = Connection()
            if kind == "run":
                c.source["metadata"]["demand_run_id"] = "OTHER"
            else:
                c.source["status"] = "COLLECTING"
            c.source["content_hash"] = source_hash(c.source)
            with self.subTest(kind=kind), self.assertRaisesRegex(InventoryInputError, code):
                await PostgresInventorySourceReader(c).verify_snapshot(
                    SourceSnapshot.from_dict(c.source)
                )
            self.assertEqual(c.tx, [])

    def test_safe_scalar_preserves_decimal_and_rejects_float_nan(self):
        self.assertEqual(scalar(Decimal("1.230000")), "1.23")
        self.assertEqual(scalar(date(2026, 9, 3)), "2026-09-03")
        for value in (1.23, Decimal("NaN"), Decimal("Infinity")):
            with self.assertRaises(InventoryInputError):
                scalar(value)
        with self.assertRaises(InventoryInputError):
            forecast_selector({})

"""Bounded, read-only DSDM Source inspection and exact-run Forecast verification."""

from datetime import date
from decimal import Decimal
from uuid import UUID

from dsio_inventory_engine.inventory_contracts.source import SourceSnapshot, forecast_selector
from dsio_inventory_engine.inventory_contracts.values import (
    day,
    identifier,
    quantity_text,
    require,
)

RUN_SQL = """
SELECT engine_run_id::text AS demand_run_id, tenant_id, project_id, engine_key,
       company_cd, subs_cd, plant_cd, site_cd, plan_id, status_projection
FROM dsai.engine_runtime_runs
WHERE engine_run_id=$1 AND tenant_id=$2 AND project_id=$3
"""
FORECAST_SQL = """
SELECT r.result_id::text, r.model_run_id, r.company_cd, r.subs_cd, r.plant_cd,
       r.site_cd, r.plan_id, r.plan_yyyyww, r.fcst_w0_yyyyww, r.oper_part_no,
       r.target_cd, r.bukt_cd, r.snrio_id, r.snrio_grp, r.regul_type,
       r.forecast_stat_cd, r.fcst_yyyyww, r.fcst_qty,
       r.source_profile_sha256, r.input_manifest_sha256,
       m.run_status_cd AS model_run_status
FROM dsdm.tb_sum_fcst_weekly_res r
JOIN dsdm.tb_fcst_model_run m ON m.model_run_id=r.model_run_id
 AND m.company_cd=r.company_cd AND m.subs_cd=r.subs_cd
 AND m.plant_cd=r.plant_cd AND m.site_cd=r.site_cd AND m.plan_id=r.plan_id
WHERE m.engine_run_id=$1 AND m.tenant_id=$2
 AND r.company_cd=$3 AND r.subs_cd=$4 AND r.plant_cd=$5 AND r.site_cd=$6
 AND r.plan_id=$7 AND r.plan_yyyyww=$8 AND r.fcst_w0_yyyyww=$9
 AND r.target_cd=$10 AND r.bukt_cd=$11 AND r.snrio_id=$12
 AND r.snrio_grp=$13 AND r.regul_type=$14 AND r.forecast_stat_cd=$15
 AND r.fcst_yyyyww BETWEEN $16 AND $17
ORDER BY r.oper_part_no,r.fcst_yyyyww,r.result_id
LIMIT 100001
"""
MASTER_SQL = """
SELECT company_cd,subs_cd,site_cd,oper_part_no,use_flag,stock_flag,
       min_po_qty,po_lot_qty,stock_lt,mst_stock_lt,svc_lv,max_qty,rop_qty
FROM dsdm.tb_mst_oper_part
WHERE company_cd=$1 AND subs_cd=$2 AND site_cd=$3
ORDER BY oper_part_no LIMIT 100001
"""
SITE_SQL = """
SELECT site_cd,subs_cd FROM dsdm.tb_mst_site_country WHERE site_cd=$1 LIMIT 2
"""
CALENDAR_SQL = """
SELECT yyyyww,week_start_dt,week_end_dt FROM dsdm.calendar_week
WHERE week_end_dt >= $1 AND week_start_dt <= $2
ORDER BY week_start_dt LIMIT 521
"""
SOURCE_TABLES = (
    "tb_mst_site_country",
    "tb_mst_oper_part",
    "calendar_week",
    "tb_sum_fcst_weekly_res",
    "tb_pln_version",
    "tb_pln_inv_boh",
    "tb_pln_inv_duein",
    "tb_pln_inv_bo",
    "tb_pln_po_policy_qty",
    "tb_pln_inv_dmd",
)
CATALOG_SQL = """
SELECT table_name,column_name,data_type FROM information_schema.columns
WHERE table_schema='dsdm' AND table_name=ANY($1::text[])
ORDER BY table_name,ordinal_position LIMIT 501
"""


def scalar(value):
    if isinstance(value, Decimal):
        require(value.is_finite(), "NON_FINITE_SOURCE_QUANTITY")
        return quantity_text(value)
    if isinstance(value, date):
        return value.isoformat()
    require(value is None or type(value) in (str, int, bool), "UNSUPPORTED_SOURCE_VALUE")
    return value


class PostgresInventorySourceReader:
    def __init__(self, connection):
        self.connection = connection

    async def _readonly(self):
        await self.connection.execute("SET LOCAL statement_timeout = '10s'")
        require(
            await self.connection.fetchval("SHOW transaction_read_only") == "on",
            "READONLY_TRANSACTION_REQUIRED",
        )

    async def _site(self, subs: str, site: str):
        rows = await self.connection.fetch(SITE_SQL, site)
        require(len(rows) == 1 and rows[0]["subs_cd"] == subs, "SITE_SUBS_MAPPING_MISMATCH")

    async def read_forecast_rows(self, selector: dict) -> list[dict]:
        """Diagnostic extraction only; callers MUST NOT interpret these rows as a seal."""
        s = forecast_selector(selector)
        async with self.connection.transaction(isolation="repeatable_read", readonly=True):
            await self._readonly()
            run = await self.connection.fetchrow(
                RUN_SQL, UUID(s["demand_run_id"]), s["tenant_id"], s["project_id"]
            )
            require(run is not None, "DEMAND_RUN_NOT_FOUND")
            require(
                run["engine_key"] == "demand" and run["status_projection"] == "succeeded",
                "DEMAND_RUN_NOT_SUCCEEDED",
            )
            require(
                all(
                    run[k] == s[k]
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
                ),
                "DEMAND_RUN_SCOPE_MISMATCH",
            )
            await self._site(s["subs_cd"], s["site_cd"])
            keys = (
                "company_cd",
                "subs_cd",
                "plant_cd",
                "site_cd",
                "plan_id",
                "plan_yyyyww",
                "fcst_w0_yyyyww",
                "target_cd",
                "bukt_cd",
                "snrio_id",
                "snrio_grp",
                "regul_type",
                "forecast_stat_cd",
                "forecast_from_yyyyww",
                "forecast_to_yyyyww",
            )
            rows = await self.connection.fetch(
                FORECAST_SQL, UUID(s["demand_run_id"]), s["tenant_id"], *(s[k] for k in keys)
            )
            require(0 < len(rows) <= 100_000, "FORECAST_SOURCE_EMPTY_OR_LIMIT")
            require(
                all(r["model_run_status"] == "SUCCEEDED" for r in rows),
                "FORECAST_MODEL_NOT_SUCCEEDED",
            )
            return [{k: scalar(v) for k, v in dict(row).items()} for row in rows]

    async def verify_snapshot(self, snapshot: SourceSnapshot) -> SourceSnapshot:
        data = SourceSnapshot.from_dict(snapshot.to_dict()).to_dict()
        require(
            data["adapter"] == "DSDM_FORECAST_V1" and data["snapshot_type"] == "forecast",
            "POSTGRES_FORECAST_ADAPTER_REQUIRED",
        )
        require(data["status"] == "SEALED", "UNSEALED_SOURCE")
        selector = forecast_selector(data["semantics"]["selector"])
        require(
            data["metadata"]["demand_run_id"] == selector["demand_run_id"], "FORECAST_RUN_MISMATCH"
        )
        data["rows"] = await self.read_forecast_rows(selector)
        # Preserve the externally pinned ID/hash/count/attestation. Never reseal current rows.
        return SourceSnapshot.from_dict(data)

    async def inspect_site(
        self, company_cd: str, subs_cd: str, site_cd: str, plan_start_date: str, plan_end_date: str
    ) -> dict:
        """Inspect current Master only. It is NOT a historical Master Snapshot."""
        company, subs, site = map(identifier, (company_cd, subs_cd, site_cd))
        start, end = (
            date.fromisoformat(day(plan_start_date)),
            date.fromisoformat(day(plan_end_date)),
        )
        require(0 <= (end - start).days < 3640, "SOURCE_PLAN_RANGE_LIMIT")
        async with self.connection.transaction(isolation="repeatable_read", readonly=True):
            await self._readonly()
            await self._site(subs, site)
            catalog = await self.connection.fetch(CATALOG_SQL, list(SOURCE_TABLES))
            require(len(catalog) <= 500, "SOURCE_CATALOG_LIMIT")
            master = await self.connection.fetch(MASTER_SQL, company, subs, site)
            calendar = await self.connection.fetch(CALENDAR_SQL, start, end)
            require(len(master) <= 100_000 and len(calendar) <= 520, "SOURCE_ROW_LIMIT")
            tables = {r["table_name"] for r in catalog}
            missing = sorted(set(SOURCE_TABLES) - tables)
            policy_nulls = {
                key: sum(r[key] is None for r in master)
                for key in ("min_po_qty", "po_lot_qty", "stock_lt", "svc_lv")
            }
            eligible_items = sum(
                row["use_flag"] == "Y" and row["stock_flag"] == "Y" for row in master
            )
            inactive_items = sum(row["use_flag"] != "Y" for row in master)
            active_non_stock_items = sum(
                row["use_flag"] == "Y" and row["stock_flag"] != "Y" for row in master
            )
            return {
                "status": "SOURCE_INSPECTED_NOT_ADMITTED",
                "source_status": "COLLECTING",
                "canonical_ready": False,
                "database_writes": False,
                "missing_dsdm_tables": missing,
                "policy_null_counts": policy_nulls,
                "inventory_master_scope": {
                    "contract_id": "io-inventory-master-scope-v1",
                    "source_relation": "dsdm.tb_mst_oper_part",
                    "eligibility_rule": "USE_FLAG_Y_AND_STOCK_FLAG_Y_V1",
                    "uom_rule": "FIXED_EA_V1",
                    "source_item_count": len(master),
                    "eligible_item_count": eligible_items,
                    "excluded_inactive_item_count": inactive_items,
                    "excluded_active_non_stock_item_count": active_non_stock_items,
                },
                "catalog": [dict(r) for r in catalog],
                "master_rows": [{k: scalar(v) for k, v in dict(r).items()} for r in master],
                "calendar_rows": [{k: scalar(v) for k, v in dict(r).items()} for r in calendar],
                "blockers": [
                    "DEMAND_FORECAST_EXPORT_SEAL_REQUIRED",
                    "MASTER_AS_OF_SNAPSHOT_REQUIRED",
                    "CUSTOMER_ORDER_COVERAGE_REQUIRED",
                    "INVENTORY_CUTOFF_WATERMARK_RESERVED_REQUIRED",
                    "POLICY_SEMANTICS_AND_APPROVAL_REQUIRED",
                    "CALENDAR_BASE_MONTH_BINDING_REQUIRED",
                ],
                "origin_type": "UNKNOWN_SOURCE",
            }

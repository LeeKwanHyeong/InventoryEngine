"""PostgreSQL adapters for one Inventory classification publication lifecycle."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Mapping

from dsio_inventory_engine.classify_inventory.application import (
    ClassificationInputs,
    InventoryScope,
    ItemMetric,
    classification_window,
)
from dsio_inventory_engine.inventory_contracts.values import require


ACTIVE_CONFIG_SQL = """
SELECT config.config_id::text, config.tenant_id, config.project_id,
       config.active_config_revision_id::text, revision.config_hash,
       revision.config_schema_id, revision.config_schema_version,
       revision.config_schema_hash, revision.config_values
FROM dsai.demand_engine_config AS config
JOIN dsai.demand_engine_config_revision AS revision
  ON revision.config_revision_id = config.active_config_revision_id
 AND revision.config_id = config.config_id
WHERE config.project_id = $1
  AND config.engine_key = 'inventory'
  AND config.scope_type = 'site'
  AND config.company_cd = $2
  AND config.subs_cd = $3
  AND config.plant_cd = $4
  AND config.site_cd = $5
  AND config.status = 'active'
  AND revision.revision_status = 'published'
"""

ACTUAL_CLOSE_SQL = """
SELECT actual_yyyyww, closure_revision_no, source_relation,
       source_manifest_sha256, publication_id
FROM dsdm.ctl_actual_week_close
WHERE tenant_id = $1
  AND company_cd = $2
  AND subs_cd = $3
  AND plant_cd = $4
  AND site_cd = $5
  AND status_cd IN ('CLOSED', 'REVISED')
ORDER BY actual_yyyyww DESC, closure_revision_no DESC
LIMIT 1
"""

ITEM_METRICS_SQL = """
WITH eligible AS (
    SELECT DISTINCT part.oper_part_no AS item_id
    FROM dsdm.tb_mst_oper_part AS part
    WHERE part.company_cd = $1
      AND part.subs_cd = $2
      AND part.site_cd = $3
      AND part.use_flag = 'Y'
      AND part.stock_flag = 'Y'
),
weekly AS (
    SELECT demand.part_no AS item_id,
           to_char(
               date_trunc('week', to_date(demand.order_dt, 'YYYYMMDD')),
               'IYYYIW'
           ) AS yyyyww,
           COUNT(*) AS source_row_count,
           COUNT(*) FILTER (
               WHERE demand.order_qty IS NULL
                  OR demand.order_qty < 0
                  OR demand.unit_price IS NULL
                  OR demand.unit_price < 0
           ) AS invalid_row_count,
           SUM(COALESCE(demand.order_qty, 0)) AS demand_qty,
           SUM(
               COALESCE(demand.order_qty, 0)
               * COALESCE(demand.unit_price, 0)
           ) AS revenue
    FROM dsdm.tb_dyn_demand_dtl AS demand
    JOIN eligible ON eligible.item_id = demand.part_no
    WHERE demand.company_cd = $1
      AND demand.subs_cd = $2
      AND demand.site_cd = $3
      AND demand.order_dt >= $4
      AND demand.order_dt < $5
      AND demand.close_flag = 'Y'
      AND demand.del_flag = 'N'
    GROUP BY demand.part_no, yyyyww
)
SELECT eligible.item_id,
       COALESCE(SUM(weekly.source_row_count), 0)::bigint AS source_row_count,
       COALESCE(SUM(weekly.invalid_row_count), 0)::bigint AS invalid_row_count,
       COUNT(weekly.yyyyww)::integer AS observed_week_count,
       COALESCE(SUM(weekly.demand_qty), 0)::numeric AS total_demand,
       COALESCE(SUM(weekly.demand_qty * weekly.demand_qty), 0)::numeric
           AS demand_square_sum,
       COALESCE(SUM(weekly.revenue), 0)::numeric AS revenue
FROM eligible
LEFT JOIN weekly ON weekly.item_id = eligible.item_id
GROUP BY eligible.item_id
ORDER BY eligible.item_id
"""

EXISTING_SNAPSHOT_SQL = """
SELECT classification_snapshot_id::text, snapshot_revision
FROM dsai.inventory_classification_snapshots
WHERE tenant_id = $1 AND project_id = $2
  AND company_cd = $3 AND subs_cd = $4
  AND plant_cd = $5 AND site_cd = $6
  AND content_hash = $7
"""

NEXT_REVISION_SQL = """
SELECT COALESCE(MAX(snapshot_revision), 0) + 1
FROM dsai.inventory_classification_snapshots
WHERE tenant_id = $1 AND project_id = $2
  AND company_cd = $3 AND subs_cd = $4
  AND plant_cd = $5 AND site_cd = $6
"""

INSERT_SNAPSHOT_SQL = """
INSERT INTO dsai.inventory_classification_snapshots (
    classification_snapshot_id, tenant_id, project_id,
    company_cd, subs_cd, plant_cd, site_cd, snapshot_revision,
    config_id, config_revision_id, config_hash, source_revision,
    source_content_hash, content_hash, as_of_yyyyww,
    segmentation_type, abc_basis, abc_lookback_weeks,
    xyz_metric, xyz_lookback_weeks, service_level_type,
    eligible_sku_count, classified_sku_count,
    unclassified_sku_count, approved_by, approved_at
) VALUES (
    $1::uuid, $2, $3, $4, $5, $6, $7, $8,
    $9::uuid, $10::uuid, $11, $12, $13, $14, $15,
    $16, $17, $18, $19, $20, $21, $22, $23, $24, $25, NOW()
)
"""

INSERT_SEGMENT_SQL = """
INSERT INTO dsai.inventory_classification_snapshot_segments (
    classification_snapshot_id, segment_key, sku_count, revenue_share
) VALUES ($1::uuid, $2, $3, $4::numeric)
"""

INSERT_REASON_SQL = """
INSERT INTO dsai.inventory_classification_snapshot_unclassified_reasons (
    classification_snapshot_id, reason_code, sku_count
) VALUES ($1::uuid, $2, $3)
"""


class PostgresClassificationSource:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    async def load_inputs(self, scope: InventoryScope) -> ClassificationInputs:
        async with self.connection.transaction(isolation="repeatable_read", readonly=True):
            await self.connection.execute("SET LOCAL statement_timeout = '120s'")
            config = await self.connection.fetchrow(
                ACTIVE_CONFIG_SQL,
                scope.project_id,
                scope.company_cd,
                scope.subs_cd,
                scope.plant_cd,
                scope.site_cd,
            )
            require(config is not None, "CLASSIFICATION_ACTIVE_CONFIG_MISSING")
            require(str(config["project_id"]) == scope.project_id, "CLASSIFICATION_SCOPE_MISMATCH")

            authority = await self.connection.fetchrow(
                ACTUAL_CLOSE_SQL,
                config["tenant_id"],
                scope.company_cd,
                scope.subs_cd,
                scope.plant_cd,
                scope.site_cd,
            )
            require(authority is not None, "CLASSIFICATION_ACTUAL_CLOSE_MISSING")

            config_values = config["config_values"]
            if isinstance(config_values, str):
                config_values = json.loads(config_values)
            abc = config_values.get("segmentation", {}).get("abc", {})
            xyz = config_values.get("segmentation", {}).get("xyz", {})
            try:
                lookback_weeks = max(int(abc["lookback_weeks"]), int(xyz["lookback_weeks"]))
                start, end_exclusive = classification_window(
                    str(authority["actual_yyyyww"]), lookback_weeks
                )
            except (KeyError, TypeError, ValueError):
                require(False, "CLASSIFICATION_CONFIG_INVALID")
            rows = await self.connection.fetch(
                ITEM_METRICS_SQL,
                scope.company_cd,
                scope.subs_cd,
                scope.site_cd,
                start.strftime("%Y%m%d"),
                end_exclusive.strftime("%Y%m%d"),
            )

        metrics = tuple(
            ItemMetric(
                item_id=str(row["item_id"]),
                source_row_count=int(row["source_row_count"]),
                invalid_row_count=int(row["invalid_row_count"]),
                observed_week_count=int(row["observed_week_count"]),
                total_demand=Decimal(row["total_demand"]),
                demand_square_sum=Decimal(row["demand_square_sum"]),
                revenue=Decimal(row["revenue"]),
            )
            for row in rows
        )
        return ClassificationInputs(
            tenant_id=str(config["tenant_id"]),
            scope=scope,
            config_id=str(config["config_id"]),
            config_revision_id=str(config["active_config_revision_id"]),
            config_schema_id=str(config["config_schema_id"]),
            config_schema_version=str(config["config_schema_version"]),
            config_schema_hash=str(config["config_schema_hash"]),
            config_hash=str(config["config_hash"]),
            config_values=config_values,
            actual_yyyyww=str(authority["actual_yyyyww"]),
            closure_revision_no=int(authority["closure_revision_no"]),
            publication_id=str(authority["publication_id"]),
            source_relation=str(authority["source_relation"]),
            source_manifest_sha256=str(authority["source_manifest_sha256"]),
            metrics=metrics,
        )


class PostgresClassificationPublisher:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    async def publish(self, snapshot: Mapping[str, Any], *, approved_by: str) -> Mapping[str, Any]:
        scope = snapshot["scope"]
        lock_key = ":".join(
            (
                str(snapshot["tenant_id"]),
                str(snapshot["project_id"]),
                str(scope["company_cd"]),
                str(scope["subs_cd"]),
                str(scope["plant_cd"]),
                str(scope["site_cd"]),
            )
        )
        async with self.connection.transaction(isolation="serializable"):
            await self.connection.execute("SET LOCAL statement_timeout = '120s'")
            await self.connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", lock_key
            )
            existing = await self.connection.fetchrow(
                EXISTING_SNAPSHOT_SQL,
                snapshot["tenant_id"],
                snapshot["project_id"],
                scope["company_cd"],
                scope["subs_cd"],
                scope["plant_cd"],
                scope["site_cd"],
                snapshot["content_hash"],
            )
            if existing is not None:
                require(
                    str(existing["classification_snapshot_id"])
                    == snapshot["classification_snapshot_id"],
                    "CLASSIFICATION_EXACT_REPLAY_ID_MISMATCH",
                )
                return {
                    "publication_status": "exact_replay",
                    "classification_snapshot_id": str(existing["classification_snapshot_id"]),
                    "snapshot_revision": int(existing["snapshot_revision"]),
                }

            revision = await self.connection.fetchval(
                NEXT_REVISION_SQL,
                snapshot["tenant_id"],
                snapshot["project_id"],
                scope["company_cd"],
                scope["subs_cd"],
                scope["plant_cd"],
                scope["site_cd"],
            )
            await self.connection.execute(
                INSERT_SNAPSHOT_SQL,
                snapshot["classification_snapshot_id"],
                snapshot["tenant_id"],
                snapshot["project_id"],
                scope["company_cd"],
                scope["subs_cd"],
                scope["plant_cd"],
                scope["site_cd"],
                revision,
                snapshot["config_id"],
                snapshot["config_revision_id"],
                snapshot["config_hash"],
                snapshot["source_revision"],
                snapshot["source_content_hash"],
                snapshot["content_hash"],
                snapshot["as_of_yyyyww"],
                snapshot["segmentation_type"],
                snapshot["abc_basis"],
                snapshot["abc_lookback_weeks"],
                snapshot["xyz_metric"],
                snapshot["xyz_lookback_weeks"],
                snapshot["service_level_type"],
                snapshot["eligible_sku_count"],
                snapshot["classified_sku_count"],
                snapshot["unclassified_sku_count"],
                approved_by,
            )
            await self.connection.executemany(
                INSERT_SEGMENT_SQL,
                [
                    (
                        snapshot["classification_snapshot_id"],
                        segment["segment_key"],
                        segment["sku_count"],
                        segment["revenue_share"],
                    )
                    for segment in snapshot["segments"]
                ],
            )
            if snapshot["unclassified_reasons"]:
                await self.connection.executemany(
                    INSERT_REASON_SQL,
                    [
                        (
                            snapshot["classification_snapshot_id"],
                            reason["reason_code"],
                            reason["sku_count"],
                        )
                        for reason in snapshot["unclassified_reasons"]
                    ],
                )
        return {
            "publication_status": "published",
            "classification_snapshot_id": snapshot["classification_snapshot_id"],
            "snapshot_revision": int(revision),
        }

"""PostgreSQL adapters for one Inventory classification publication lifecycle."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Mapping

from dsio_inventory_engine.classify_inventory.application import (
    ClassificationInputs,
    InventoryScope,
    ItemMetric,
    VedAssignment,
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

VED_ASSIGNMENT_HEADER_SQL = """
SELECT assignment_snapshot_id::text, content_hash AS assignment_content_hash, status
FROM dsai.inventory_ved_assignment_snapshots
WHERE assignment_snapshot_id = $1::uuid
  AND tenant_id = $2
  AND project_id = $3
  AND company_cd = $4
  AND subs_cd = $5
  AND plant_cd = $6
  AND site_cd = $7
"""

VED_ASSIGNMENT_ITEMS_SQL = """
SELECT item_id, ved_class, assignment_reason
FROM dsai.inventory_ved_assignment_snapshot_items
WHERE assignment_snapshot_id = $1::uuid
ORDER BY item_id
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
    item_result_contract_version, ved_assignment_snapshot_id,
    ved_assignment_content_hash, effective_policy_contract_version,
    effective_policy_content_hash,
    eligible_sku_count, classified_sku_count,
    unclassified_sku_count, approved_by, approved_at
) VALUES (
    $1::uuid, $2, $3, $4, $5, $6, $7, $8,
    $9::uuid, $10::uuid, $11, $12, $13, $14, $15,
    $16, $17, $18, $19, $20, $21, $22, $23::uuid, $24, $25, $26,
    $27, $28, $29, $30, NOW()
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

INSERT_ITEM_SQL = """
INSERT INTO dsai.inventory_classification_snapshot_items (
    classification_snapshot_id, item_id, classification_status,
    abc_class, xyz_class, ved_class, segment_key, final_segment_key,
    ved_assignment_source, ved_assignment_reason, unclassified_reason_code,
    demand_cv2, revenue_value, cumulative_revenue_share_before,
    effective_target_service_level, effective_review_cycle_weeks,
    effective_strategy, policy_source, policy_adjustment_reason,
    operational_io_eligible, effective_policy_hash
) VALUES (
    $1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
    $12::numeric, $13::numeric, $14::numeric, $15::numeric, $16,
    $17, $18, $19, $20, $21
)
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
            ved_assignments: tuple[VedAssignment, ...] = ()
            ved = config_values.get("segmentation", {}).get("ved", {})
            if ved.get("enabled") is True:
                assignment_snapshot_id = ved.get("assignment_snapshot_id")
                assignment_content_hash = ved.get("assignment_content_hash")
                require(
                    bool(assignment_snapshot_id) and bool(assignment_content_hash),
                    "CLASSIFICATION_VED_BINDING_MISSING",
                )
                header = await self.connection.fetchrow(
                    VED_ASSIGNMENT_HEADER_SQL,
                    assignment_snapshot_id,
                    config["tenant_id"],
                    scope.project_id,
                    scope.company_cd,
                    scope.subs_cd,
                    scope.plant_cd,
                    scope.site_cd,
                )
                require(header is not None, "CLASSIFICATION_VED_SNAPSHOT_NOT_FOUND")
                require(
                    str(header["status"]).lower() == "approved"
                    and str(header["assignment_content_hash"]) == assignment_content_hash,
                    "CLASSIFICATION_VED_SNAPSHOT_MISMATCH",
                )
                assignment_rows = await self.connection.fetch(
                    VED_ASSIGNMENT_ITEMS_SQL,
                    assignment_snapshot_id,
                )
                ved_assignments = tuple(
                    VedAssignment(
                        item_id=str(row["item_id"]),
                        ved_class=str(row["ved_class"]),
                        reason=(
                            str(row["assignment_reason"])
                            if row["assignment_reason"] is not None
                            else None
                        ),
                    )
                    for row in assignment_rows
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
            ved_assignments=ved_assignments,
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
                snapshot["item_result_contract_version"],
                snapshot["ved_assignment_snapshot_id"],
                snapshot["ved_assignment_content_hash"],
                snapshot["effective_policy_contract_version"],
                snapshot["effective_policy_content_hash"],
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
            await self.connection.executemany(
                INSERT_ITEM_SQL,
                [
                    (
                        snapshot["classification_snapshot_id"],
                        item["item_id"],
                        item["classification_status"],
                        item["abc_class"],
                        item["xyz_class"],
                        item["ved_class"],
                        item["segment_key"],
                        item["final_segment_key"],
                        item["ved_assignment_source"],
                        item["ved_assignment_reason"],
                        item["unclassified_reason_code"],
                        item["demand_cv2"],
                        item["revenue"],
                        item["cumulative_revenue_share_before"],
                        item["effective_target_service_level"],
                        item["effective_review_cycle_weeks"],
                        item["effective_strategy"],
                        item["policy_source"],
                        item["policy_adjustment_reason"],
                        item["operational_io_eligible"],
                        item["effective_policy_hash"],
                    )
                    for item in snapshot["items"]
                ],
            )
        return {
            "publication_status": "published",
            "classification_snapshot_id": snapshot["classification_snapshot_id"],
            "snapshot_revision": int(revision),
        }

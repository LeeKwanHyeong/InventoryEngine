"""PostgreSQL adapters for one Inventory classification publication lifecycle."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from dsio_inventory_engine.classify_inventory.application import (
    SNAPSHOT_NAMESPACE,
    ClassificationInputs,
    InventoryScope,
    ItemMetric,
    VedAssignment,
    classification_window,
)
from dsio_inventory_engine.inventory_contracts.classification import (
    validate_effective_item_policy,
)
from dsio_inventory_engine.inventory_contracts.effective_policy_v2 import (
    AXIS_ORDER,
    EFFECTIVE_POLICY_V2_FIELDS,
    effective_policy_content_hash,
    validate_effective_item_policy_v2,
)
from dsio_inventory_engine.inventory_contracts.seven_axis import display_segment_code
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    canonical_json,
    hash_value,
    identifier,
    item_identifier,
    require,
    yyyyww,
)


V1_SNAPSHOT_FIELDS = frozenset(
    {
        "classification_snapshot_id",
        "tenant_id",
        "project_id",
        "scope",
        "config_id",
        "config_revision_id",
        "config_hash",
        "source_revision",
        "source_content_hash",
        "source_manifest_sha256",
        "source_relation",
        "as_of_yyyyww",
        "window_start",
        "window_end_exclusive",
        "segmentation_type",
        "abc_basis",
        "abc_lookback_weeks",
        "xyz_metric",
        "xyz_lookback_weeks",
        "service_level_type",
        "item_result_contract_version",
        "effective_policy_contract_version",
        "effective_policy_content_hash",
        "ved_assignment_snapshot_id",
        "ved_assignment_content_hash",
        "eligible_sku_count",
        "classified_sku_count",
        "unclassified_sku_count",
        "segments",
        "unclassified_reasons",
        "items",
        "content_hash",
    }
)
V1_ITEM_FIELDS = frozenset(
    {
        "item_id",
        "classification_status",
        "abc_class",
        "xyz_class",
        "ved_class",
        "segment_key",
        "final_segment_key",
        "ved_assignment_source",
        "ved_assignment_reason",
        "unclassified_reason_code",
        "demand_cv2",
        "revenue",
        "cumulative_revenue_share_before",
        "effective_target_service_level",
        "effective_review_cycle_weeks",
        "effective_strategy",
        "policy_source",
        "policy_adjustment_reason",
        "operational_io_eligible",
        "effective_policy_hash",
    }
)
V1_SEGMENTS = ("AX", "AY", "AZ", "BX", "BY", "BZ", "CX", "CY", "CZ")
V2_SNAPSHOT_FIELDS = V1_SNAPSHOT_FIELDS | frozenset(
    {
        "axis_windows",
        "axis_result_contract_version",
        "actual_close_window_start_yyyyww",
        "actual_close_history_hash",
    }
)
V2_ITEM_FIELDS = (
    V1_ITEM_FIELDS
    | frozenset(EFFECTIVE_POLICY_V2_FIELDS)
    | frozenset(
        {
            "abc_classification_status",
            "xyz_classification_status",
            "abc_unclassified_reason_code",
            "xyz_unclassified_reason_code",
            "axis_results",
            "display_segment_code",
            "seven_axis_operational_eligible",
            "policy_effective_axes",
        }
    )
)
V2_AXIS_FIELDS = frozenset(
    {
        "axis",
        "status",
        "class_code",
        "application_mode",
        "policy_effective",
        "source_contract_key",
        "evidence",
        "reason_code",
    }
)
V2_AXIS_WINDOW_FIELDS = frozenset({"lookback_weeks", "window_start", "window_end_exclusive"})
V2_WINDOW_AXES = ("ABC", "XYZ", "FSN", "SDE")
V2_AXIS_CLASSES = {
    "ABC": {"A", "B", "C"},
    "XYZ": {"X", "Y", "Z"},
    "VED": {"V", "E", "D"},
    "FSN": {"F", "S", "N"},
    "SDE": {"S", "D", "E"},
    "HML": {"H", "M", "L"},
    "PLC": {
        "PRE_LAUNCH",
        "INTRODUCTION",
        "GROWTH",
        "MATURE",
        "DECLINE",
        "SERVICE_ONLY",
        "DISCONTINUED",
    },
}
V2_AXIS_STATUSES = {
    "CLASSIFIED",
    "UNCLASSIFIED",
    "UNVERIFIED",
    "SYNTHETIC",
    "NOT_APPLICABLE",
}


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
SELECT actual_yyyyww, closure_revision_no, status_cd, source_relation,
       source_manifest_sha256, publication_id
FROM dsdm.ctl_actual_week_close
WHERE tenant_id = $1
  AND company_cd = $2
  AND subs_cd = $3
  AND plant_cd = $4
  AND site_cd = $5
ORDER BY actual_yyyyww DESC, closure_revision_no DESC
LIMIT 1
"""

ACTUAL_CLOSE_HISTORY_SQL = """
SELECT DISTINCT ON (actual_yyyyww)
       actual_yyyyww, closure_revision_no, status_cd, source_relation,
       source_manifest_sha256, publication_id
FROM dsdm.ctl_actual_week_close
WHERE tenant_id = $1
  AND company_cd = $2
  AND subs_cd = $3
  AND plant_cd = $4
  AND site_cd = $5
  AND actual_yyyyww BETWEEN $6 AND $7
ORDER BY actual_yyyyww, closure_revision_no DESC
"""

ITEM_METRICS_SQL = """
WITH eligible AS (
    SELECT DISTINCT part.oper_part_no AS item_id,
           lifecycle.introduced_yyyyww,
           lifecycle.production_end_yyyyww,
           lifecycle.service_end_yyyyww,
           lifecycle.lifecycle_status_cd,
           lifecycle.source_profile_hash
    FROM dsdm.tb_mst_oper_part AS part
    LEFT JOIN dsdm.tb_map_site_part_lifecycle AS lifecycle
      ON lifecycle.company_cd = part.company_cd
     AND lifecycle.subs_cd = part.subs_cd
     AND lifecycle.site_cd = part.site_cd
     AND lifecycle.part_no = part.oper_part_no
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
           date_trunc('week', to_date(demand.order_dt, 'YYYYMMDD'))::date
               AS week_start,
           COUNT(*) AS source_row_count,
           COUNT(*) FILTER (
               WHERE demand.order_qty IS NULL
                  OR demand.order_qty < 0
                  OR demand.unit_price IS NULL
                  OR demand.unit_price < 0
           ) AS invalid_row_count,
           COUNT(*) FILTER (
               WHERE demand.order_qty IS NULL
                  OR demand.order_qty < 0
                  OR demand.unit_price IS NULL
                  OR demand.unit_price < 0
           ) AS abc_invalid_row_count,
           COUNT(*) FILTER (
               WHERE demand.order_qty IS NULL
                  OR demand.order_qty < 0
           ) AS demand_invalid_row_count,
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
    GROUP BY demand.part_no, yyyyww, week_start
)
SELECT eligible.item_id,
       COALESCE(SUM(weekly.source_row_count), 0)::bigint AS source_row_count,
       COALESCE(SUM(weekly.invalid_row_count), 0)::bigint AS invalid_row_count,
       COUNT(weekly.yyyyww)::integer AS observed_week_count,
       COALESCE(SUM(weekly.source_row_count) FILTER (
           WHERE weekly.week_start >= to_date($6, 'YYYYMMDD')
       ), 0)::bigint AS abc_source_row_count,
       COALESCE(SUM(weekly.abc_invalid_row_count) FILTER (
           WHERE weekly.week_start >= to_date($6, 'YYYYMMDD')
       ), 0)::bigint AS abc_invalid_row_count,
       COUNT(weekly.yyyyww) FILTER (
           WHERE weekly.week_start >= to_date($6, 'YYYYMMDD')
       )::integer AS abc_observed_week_count,
       COALESCE(SUM(weekly.revenue) FILTER (
           WHERE weekly.week_start >= to_date($6, 'YYYYMMDD')
       ), 0)::numeric AS abc_revenue,
       COALESCE(SUM(weekly.source_row_count) FILTER (
           WHERE weekly.week_start >= to_date($7, 'YYYYMMDD')
       ), 0)::bigint AS xyz_source_row_count,
       COALESCE(SUM(weekly.demand_invalid_row_count) FILTER (
           WHERE weekly.week_start >= to_date($7, 'YYYYMMDD')
       ), 0)::bigint AS xyz_invalid_row_count,
       COUNT(weekly.yyyyww) FILTER (
           WHERE weekly.week_start >= to_date($7, 'YYYYMMDD')
       )::integer AS xyz_observed_week_count,
       COALESCE(SUM(weekly.demand_qty) FILTER (
           WHERE weekly.week_start >= to_date($7, 'YYYYMMDD')
       ), 0)::numeric AS xyz_total_demand,
       COALESCE(SUM(weekly.demand_qty * weekly.demand_qty) FILTER (
           WHERE weekly.week_start >= to_date($7, 'YYYYMMDD')
       ), 0)::numeric AS xyz_demand_square_sum,
       COUNT(weekly.yyyyww) FILTER (
           WHERE weekly.demand_qty > 0
             AND weekly.week_start >= to_date($8, 'YYYYMMDD')
       )::integer AS positive_week_count,
       COALESCE(SUM(weekly.source_row_count) FILTER (
           WHERE weekly.week_start >= to_date($8, 'YYYYMMDD')
       ), 0)::bigint AS fsn_source_row_count,
       COALESCE(SUM(weekly.demand_invalid_row_count) FILTER (
           WHERE weekly.week_start >= to_date($8, 'YYYYMMDD')
       ), 0)::bigint AS fsn_invalid_row_count,
       MAX(weekly.yyyyww) FILTER (
           WHERE weekly.demand_qty > 0
             AND weekly.week_start >= to_date($8, 'YYYYMMDD')
       ) AS last_positive_demand_yyyyww,
       COALESCE(SUM(weekly.demand_qty), 0)::numeric AS total_demand,
       COALESCE(SUM(weekly.demand_qty * weekly.demand_qty), 0)::numeric
           AS demand_square_sum,
       COALESCE(SUM(weekly.revenue), 0)::numeric AS revenue,
       eligible.introduced_yyyyww,
       eligible.production_end_yyyyww,
       eligible.service_end_yyyyww,
       eligible.lifecycle_status_cd,
       eligible.source_profile_hash
FROM eligible
LEFT JOIN weekly ON weekly.item_id = eligible.item_id
GROUP BY eligible.item_id, eligible.introduced_yyyyww,
         eligible.production_end_yyyyww, eligible.service_end_yyyyww,
         eligible.lifecycle_status_cd, eligible.source_profile_hash
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

INSERT_SNAPSHOT_V2_SQL = """
INSERT INTO dsai.inventory_classification_snapshots (
    classification_snapshot_id, tenant_id, project_id,
    company_cd, subs_cd, plant_cd, site_cd, snapshot_revision,
    config_id, config_revision_id, config_hash, source_revision,
    source_content_hash, source_manifest_sha256, source_relation,
    content_hash, as_of_yyyyww, window_start, window_end_exclusive,
    segmentation_type, abc_basis, abc_lookback_weeks,
    xyz_metric, xyz_lookback_weeks, service_level_type,
    item_result_contract_version, ved_assignment_snapshot_id,
    ved_assignment_content_hash, effective_policy_contract_version,
    effective_policy_content_hash, axis_result_contract_version,
    actual_close_window_start_yyyyww, actual_close_history_hash,
    eligible_sku_count, classified_sku_count,
    unclassified_sku_count, approved_by, approved_at
) VALUES (
    $1::uuid, $2, $3, $4, $5, $6, $7, $8,
    $9::uuid, $10::uuid, $11, $12, $13, $14, $15,
    $16, $17, $18::date, $19::date, $20, $21, $22,
    $23, $24, $25, $26, $27::uuid, $28, $29, $30,
    $31, $32, $33, $34, $35, $36, $37, NOW()
)
"""

INSERT_ITEM_V2_SQL = """
INSERT INTO dsai.inventory_classification_snapshot_items (
    classification_snapshot_id, item_id, classification_status,
    abc_class, xyz_class, ved_class, segment_key, final_segment_key,
    ved_assignment_source, ved_assignment_reason, unclassified_reason_code,
    demand_cv2, revenue_value, cumulative_revenue_share_before,
    effective_target_service_level, effective_review_cycle_weeks,
    effective_strategy, policy_source, policy_adjustment_reason,
    operational_io_eligible, effective_policy_hash,
    item_result_contract_version, effective_policy_contract_version,
    classification_config_hash, classification_config_binding_hash,
    display_segment_code, seven_axis_operational_eligible,
    recommendation_calculation_allowed, evidence_storage_allowed,
    effective_order_action, order_execution_allowed, no_order_gate,
    effective_protection_lead_time_basis,
    effective_protection_lead_time_days, effective_approval_level,
    automatic_publish_allowed, automatic_order_allowed
) VALUES (
    $1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
    $12::numeric, $13::numeric, $14::numeric, $15::numeric, $16,
    $17, $18, $19, $20, $21, $22, $23, $24, $25, $26, $27,
    $28, $29, $30, $31, $32, $33, $34::numeric, $35, $36, $37
)
"""

INSERT_AXIS_WINDOW_V2_SQL = """
INSERT INTO dsai.inventory_classification_snapshot_axis_windows (
    classification_snapshot_id, axis, lookback_weeks,
    window_start, window_end_exclusive
) VALUES ($1::uuid, $2, $3, $4::date, $5::date)
"""

INSERT_ITEM_AXIS_V2_SQL = """
INSERT INTO dsai.inventory_classification_snapshot_item_axes (
    classification_snapshot_id, item_id, axis, status, class_code,
    application_mode, policy_effective, source_contract_key,
    evidence, reason_code, effective_policy_effect
) VALUES (
    $1::uuid, $2, $3, $4, $5, $6, $7, $8,
    $9::jsonb, $10, $11::jsonb
)
"""

INSERT_ITEM_POLICY_REASON_V2_SQL = """
INSERT INTO dsai.inventory_classification_snapshot_item_policy_reasons (
    classification_snapshot_id, item_id, reason_kind, ordinal, reason_code
) VALUES ($1::uuid, $2, $3, $4, $5)
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
            require(
                str(authority["status_cd"]).strip().upper() in {"CLOSED", "REVISED"},
                "CLASSIFICATION_ACTUAL_CLOSE_NOT_TERMINAL",
            )
            require(
                _valid_actual_close(authority),
                "CLASSIFICATION_ACTUAL_CLOSE_INVALID",
            )

            config_values = config["config_values"]
            if isinstance(config_values, str):
                config_values = json.loads(config_values)
            abc = config_values.get("segmentation", {}).get("abc", {})
            xyz = config_values.get("segmentation", {}).get("xyz", {})
            try:
                segmentation = config_values.get("segmentation", {})
                fsn = segmentation.get("fsn", {})
                fsn_enabled = fsn.get("enabled") is True
                demand_lookbacks = [
                    int(abc["lookback_weeks"]),
                    int(xyz["lookback_weeks"]),
                ]
                if fsn_enabled:
                    demand_lookbacks.append(int(fsn["lookback_weeks"]))
                lookback_weeks = max(demand_lookbacks)
                start, end_exclusive = classification_window(
                    str(authority["actual_yyyyww"]), lookback_weeks
                )
                abc_start, _ = classification_window(
                    str(authority["actual_yyyyww"]), int(abc["lookback_weeks"])
                )
                xyz_start, _ = classification_window(
                    str(authority["actual_yyyyww"]), int(xyz["lookback_weeks"])
                )
                fsn_lookback = int(fsn.get("lookback_weeks", lookback_weeks))
                fsn_start, _ = classification_window(str(authority["actual_yyyyww"]), fsn_lookback)
            except (KeyError, TypeError, ValueError):
                require(False, "CLASSIFICATION_CONFIG_INVALID")
            expected_actual_weeks = tuple(
                (start + timedelta(weeks=offset)).strftime("%G%V")
                for offset in range(lookback_weeks)
            )
            authority_history = await self.connection.fetch(
                ACTUAL_CLOSE_HISTORY_SQL,
                config["tenant_id"],
                scope.company_cd,
                scope.subs_cd,
                scope.plant_cd,
                scope.site_cd,
                expected_actual_weeks[0],
                expected_actual_weeks[-1],
            )
            actual_close_by_week = {str(row["actual_yyyyww"]): row for row in authority_history}
            history_anchor = actual_close_by_week.get(str(authority["actual_yyyyww"]))
            abc_weeks = expected_actual_weeks[-int(abc["lookback_weeks"]) :]
            xyz_weeks = expected_actual_weeks[-int(xyz["lookback_weeks"]) :]
            fsn_weeks = expected_actual_weeks[-fsn_lookback:] if fsn_enabled else ()
            abc_actual_sealed = _actual_close_window_sealed(actual_close_by_week, abc_weeks)
            xyz_actual_sealed = _actual_close_window_sealed(actual_close_by_week, xyz_weeks)
            fsn_actual_sealed = bool(
                fsn_enabled and _actual_close_window_sealed(actual_close_by_week, fsn_weeks)
            )
            require(
                abc_actual_sealed and xyz_actual_sealed,
                "CLASSIFICATION_ACTUAL_LOOKBACK_NOT_FULLY_SEALED",
            )
            require(
                not (
                    fsn_enabled
                    and fsn.get("application_mode") == "OPERATIONAL"
                    and not fsn_actual_sealed
                ),
                "CLASSIFICATION_ACTUAL_LOOKBACK_NOT_FULLY_SEALED",
            )
            require(
                history_anchor is not None
                and int(history_anchor["closure_revision_no"])
                == int(authority["closure_revision_no"])
                and str(history_anchor["status_cd"]).strip().upper()
                == str(authority["status_cd"]).strip().upper()
                and str(history_anchor["source_relation"]) == str(authority["source_relation"])
                and str(history_anchor["source_manifest_sha256"])
                == str(authority["source_manifest_sha256"])
                and str(history_anchor["publication_id"]) == str(authority["publication_id"]),
                "CLASSIFICATION_ACTUAL_LOOKBACK_LINEAGE_MISMATCH",
            )
            lineage_windows = [abc_weeks, xyz_weeks]
            if fsn_enabled and fsn_actual_sealed:
                lineage_windows.append(fsn_weeks)
            lineage_weeks = max(lineage_windows, key=len)
            actual_close_history = [
                {
                    "actual_yyyyww": week,
                    "closure_revision_no": int(actual_close_by_week[week]["closure_revision_no"]),
                    "publication_id": str(actual_close_by_week[week]["publication_id"]).strip(),
                    "source_manifest_sha256": str(
                        actual_close_by_week[week]["source_manifest_sha256"]
                    )
                    .strip()
                    .lower(),
                    "source_relation": str(actual_close_by_week[week]["source_relation"]).strip(),
                }
                for week in lineage_weeks
            ]
            actual_close_history_hash = _document_hash(actual_close_history)
            rows = await self.connection.fetch(
                ITEM_METRICS_SQL,
                scope.company_cd,
                scope.subs_cd,
                scope.site_cd,
                start.strftime("%Y%m%d"),
                end_exclusive.strftime("%Y%m%d"),
                abc_start.strftime("%Y%m%d"),
                xyz_start.strftime("%Y%m%d"),
                fsn_start.strftime("%Y%m%d"),
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
                abc_source_row_count=int(row.get("abc_source_row_count", row["source_row_count"])),
                abc_invalid_row_count=int(
                    row.get("abc_invalid_row_count", row["invalid_row_count"])
                ),
                abc_observed_week_count=int(
                    row.get("abc_observed_week_count", row["observed_week_count"])
                ),
                abc_revenue=Decimal(row.get("abc_revenue", row["revenue"])),
                xyz_source_row_count=int(row.get("xyz_source_row_count", row["source_row_count"])),
                xyz_invalid_row_count=int(
                    row.get("xyz_invalid_row_count", row["invalid_row_count"])
                ),
                xyz_observed_week_count=int(
                    row.get("xyz_observed_week_count", row["observed_week_count"])
                ),
                xyz_total_demand=Decimal(row.get("xyz_total_demand", row["total_demand"])),
                xyz_demand_square_sum=Decimal(
                    row.get("xyz_demand_square_sum", row["demand_square_sum"])
                ),
                fsn_source_row_count=int(row.get("fsn_source_row_count") or 0),
                fsn_invalid_row_count=int(row.get("fsn_invalid_row_count") or 0),
                positive_week_count=int(row.get("positive_week_count") or 0),
                last_positive_demand_yyyyww=(
                    str(row["last_positive_demand_yyyyww"])
                    if row.get("last_positive_demand_yyyyww") is not None
                    else None
                ),
                fsn_source_status=(
                    "VERIFIED"
                    if fsn_actual_sealed
                    and int(row.get("fsn_source_row_count") or 0) > 0
                    and int(row.get("fsn_invalid_row_count") or 0) == 0
                    else "UNVERIFIED"
                ),
                introduced_yyyyww=(
                    str(row["introduced_yyyyww"])
                    if row.get("introduced_yyyyww") is not None
                    else None
                ),
                production_end_yyyyww=(
                    str(row["production_end_yyyyww"])
                    if row.get("production_end_yyyyww") is not None
                    else None
                ),
                service_end_yyyyww=(
                    str(row["service_end_yyyyww"])
                    if row.get("service_end_yyyyww") is not None
                    else None
                ),
                lifecycle_status_cd=(
                    str(row["lifecycle_status_cd"])
                    if row.get("lifecycle_status_cd") is not None
                    else None
                ),
                plc_source_status=(
                    "SYNTHETIC" if _valid_sha256(row.get("source_profile_hash")) else "UNVERIFIED"
                ),
                plc_source_profile_hash=(
                    str(row["source_profile_hash"]).strip().lower()
                    if _valid_sha256(row.get("source_profile_hash"))
                    else None
                ),
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
            actual_close_window_start_yyyyww=lineage_weeks[0],
            actual_close_history_hash=actual_close_history_hash,
        )


class PostgresClassificationPublisher:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    async def publish(self, snapshot: Mapping[str, Any], *, approved_by: str) -> Mapping[str, Any]:
        contract_version = _validate_publishable_snapshot(snapshot)
        require(
            isinstance(approved_by, str) and bool(approved_by.strip()) and len(approved_by) <= 64,
            "CLASSIFICATION_APPROVER_INVALID",
        )
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
            if contract_version == "2.0.0":
                await self.connection.execute(
                    INSERT_SNAPSHOT_V2_SQL,
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
                    snapshot["source_manifest_sha256"],
                    snapshot["source_relation"],
                    snapshot["content_hash"],
                    snapshot["as_of_yyyyww"],
                    snapshot["window_start"],
                    snapshot["window_end_exclusive"],
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
                    snapshot["axis_result_contract_version"],
                    snapshot["actual_close_window_start_yyyyww"],
                    snapshot["actual_close_history_hash"],
                    snapshot["eligible_sku_count"],
                    snapshot["classified_sku_count"],
                    snapshot["unclassified_sku_count"],
                    approved_by,
                )
            else:
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
            if contract_version == "2.0.0":
                await self._publish_v2_projection(snapshot)
            else:
                await self.connection.executemany(
                    INSERT_ITEM_SQL,
                    [
                        _v1_item_row(snapshot["classification_snapshot_id"], item)
                        for item in snapshot["items"]
                    ],
                )
        return {
            "publication_status": "published",
            "classification_snapshot_id": snapshot["classification_snapshot_id"],
            "snapshot_revision": int(revision),
        }

    async def _publish_v2_projection(self, snapshot: Mapping[str, Any]) -> None:
        snapshot_id = snapshot["classification_snapshot_id"]
        await self.connection.executemany(
            INSERT_ITEM_V2_SQL,
            [_v2_item_row(snapshot_id, item) for item in snapshot["items"]],
        )
        await self.connection.executemany(
            INSERT_AXIS_WINDOW_V2_SQL,
            [
                (
                    snapshot_id,
                    axis,
                    snapshot["axis_windows"][axis]["lookback_weeks"],
                    snapshot["axis_windows"][axis]["window_start"],
                    snapshot["axis_windows"][axis]["window_end_exclusive"],
                )
                for axis in V2_WINDOW_AXES
                if axis in snapshot["axis_windows"]
            ],
        )
        await self.connection.executemany(
            INSERT_ITEM_AXIS_V2_SQL,
            [
                _v2_axis_row(snapshot_id, item, axis_result)
                for item in snapshot["items"]
                for axis_result in item["axis_results"]
            ],
        )
        policy_reason_rows = [
            (
                snapshot_id,
                item["item_id"],
                reason_kind,
                ordinal,
                reason_code,
            )
            for item in snapshot["items"]
            for reason_kind, reason_codes in (
                ("ADJUSTMENT", item["policy_adjustment_reasons"]),
                ("GATE", item["policy_gate_reason_codes"]),
            )
            for ordinal, reason_code in enumerate(reason_codes, start=1)
        ]
        if policy_reason_rows:
            await self.connection.executemany(
                INSERT_ITEM_POLICY_REASON_V2_SQL,
                policy_reason_rows,
            )


def _validate_publishable_snapshot(snapshot: Mapping[str, Any]) -> str:
    require(type(snapshot) is dict, "CLASSIFICATION_SNAPSHOT_CONTRACT_MISMATCH")
    identity = (
        snapshot.get("segmentation_type"),
        snapshot.get("item_result_contract_version"),
        snapshot.get("effective_policy_contract_version"),
    )
    if identity == ("ABC_XYZ", "1.1.0", "1.0.0"):
        _validate_publishable_v1_snapshot(snapshot)
        return "1.0.0"
    if identity == ("SEVEN_AXIS", "2.0.0", "2.0.0"):
        _validate_publishable_v2_snapshot(snapshot)
        return "2.0.0"
    raise InventoryInputError("CLASSIFICATION_SNAPSHOT_CONTRACT_MISMATCH")


def _v1_item_row(snapshot_id: str, item: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        snapshot_id,
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


def _v2_item_row(snapshot_id: str, item: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        *_v1_item_row(snapshot_id, item),
        "2.0.0",
        item["effective_policy_contract_version"],
        item["classification_config_hash"],
        item["classification_config_binding_hash"],
        item["display_segment_code"],
        item["seven_axis_operational_eligible"],
        item["recommendation_calculation_allowed"],
        item["evidence_storage_allowed"],
        item["effective_order_action"],
        item["order_execution_allowed"],
        item["no_order_gate"],
        item["effective_protection_lead_time_basis"],
        item["effective_protection_lead_time_days"],
        item["effective_approval_level"],
        item["automatic_publish_allowed"],
        item["automatic_order_allowed"],
    )


def _v2_axis_row(
    snapshot_id: str,
    item: Mapping[str, Any],
    axis_result: Mapping[str, Any],
) -> tuple[Any, ...]:
    projection_by_axis = {
        row["axis"]: row["policy_effect"] for row in item["effective_axis_projection"]
    }
    policy_effect = projection_by_axis.get(axis_result["axis"])
    return (
        snapshot_id,
        item["item_id"],
        axis_result["axis"],
        axis_result["status"],
        axis_result["class_code"],
        axis_result["application_mode"],
        axis_result["policy_effective"],
        axis_result["source_contract_key"],
        canonical_json(axis_result["evidence"]),
        axis_result["reason_code"],
        canonical_json(policy_effect) if policy_effect is not None else None,
    )


def _validate_publishable_v1_snapshot(snapshot: Mapping[str, Any]) -> None:
    require(
        set(snapshot) == V1_SNAPSHOT_FIELDS
        and snapshot.get("segmentation_type") == "ABC_XYZ"
        and snapshot.get("item_result_contract_version") == "1.1.0"
        and snapshot.get("effective_policy_contract_version") == "1.0.0"
        and type(snapshot.get("scope")) is dict
        and set(snapshot["scope"]) == {"company_cd", "subs_cd", "plant_cd", "site_cd"},
        "CLASSIFICATION_V2_PROJECTION_MIGRATION_REQUIRED",
    )
    items = snapshot["items"]
    require(
        isinstance(items, (list, tuple))
        and all(type(item) is dict and set(item) == V1_ITEM_FIELDS for item in items),
        "CLASSIFICATION_V2_PROJECTION_MIGRATION_REQUIRED",
    )
    item_ids = [item["item_id"] for item in items]
    require(
        item_ids == sorted(item_ids) and len(item_ids) == len(set(item_ids)),
        "CLASSIFICATION_SNAPSHOT_ITEM_AGGREGATE_MISMATCH",
    )
    for item in items:
        validate_effective_item_policy(item, config_hash=snapshot["config_hash"])
    expected_policy_hash = _document_hash(
        [
            {
                "item_id": item["item_id"],
                "effective_policy_hash": item["effective_policy_hash"],
            }
            for item in items
        ]
    )
    require(
        snapshot["effective_policy_content_hash"] == expected_policy_hash,
        "CLASSIFICATION_EFFECTIVE_POLICY_CONTENT_HASH_MISMATCH",
    )
    segments = snapshot["segments"]
    require(
        isinstance(segments, (list, tuple))
        and len(segments) == len(V1_SEGMENTS)
        and all(
            type(segment) is dict and set(segment) == {"segment_key", "sku_count", "revenue_share"}
            for segment in segments
        )
        and tuple(segment["segment_key"] for segment in segments) == V1_SEGMENTS,
        "CLASSIFICATION_SNAPSHOT_SEGMENT_AGGREGATE_MISMATCH",
    )
    classified_count = sum(item["classification_status"] == "CLASSIFIED" for item in items)
    unclassified_count = len(items) - classified_count
    classified_items = [item for item in items if item["classification_status"] == "CLASSIFIED"]
    total_revenue = sum(
        (_publisher_decimal(item["revenue"]) for item in classified_items),
        Decimal("0"),
    )
    expected_segments = tuple(
        {
            "segment_key": segment_key,
            "sku_count": sum(item["segment_key"] == segment_key for item in classified_items),
            "revenue_share": _fixed_decimal_text(
                (
                    sum(
                        (
                            _publisher_decimal(item["revenue"])
                            for item in classified_items
                            if item["segment_key"] == segment_key
                        ),
                        Decimal("0"),
                    )
                    / total_revenue
                    if total_revenue > 0
                    else Decimal("0")
                ),
                places=12,
            ),
        }
        for segment_key in V1_SEGMENTS
    )
    require(
        tuple(segments) == expected_segments,
        "CLASSIFICATION_SNAPSHOT_SEGMENT_AGGREGATE_MISMATCH",
    )
    reasons = snapshot["unclassified_reasons"]
    require(
        isinstance(reasons, (list, tuple))
        and all(
            type(reason) is dict and set(reason) == {"reason_code", "sku_count"}
            for reason in reasons
        )
        and len({reason["reason_code"] for reason in reasons}) == len(reasons),
        "CLASSIFICATION_SNAPSHOT_REASON_AGGREGATE_MISMATCH",
    )
    reason_counts = Counter(
        item["unclassified_reason_code"]
        for item in items
        if item["classification_status"] == "UNCLASSIFIED"
    )
    expected_reasons = tuple(
        {"reason_code": reason_code, "sku_count": reason_counts[reason_code]}
        for reason_code in sorted(reason_counts)
    )
    require(
        tuple(reasons) == expected_reasons,
        "CLASSIFICATION_SNAPSHOT_REASON_AGGREGATE_MISMATCH",
    )
    require(
        snapshot["eligible_sku_count"] == len(items)
        and snapshot["classified_sku_count"] == classified_count
        and snapshot["unclassified_sku_count"] == unclassified_count
        and sum(segment["sku_count"] for segment in segments) == classified_count,
        "CLASSIFICATION_SNAPSHOT_ITEM_AGGREGATE_MISMATCH",
    )
    require(
        sum(reason["sku_count"] for reason in reasons) == unclassified_count,
        "CLASSIFICATION_SNAPSHOT_REASON_AGGREGATE_MISMATCH",
    )
    body = {
        key: value
        for key, value in snapshot.items()
        if key not in {"classification_snapshot_id", "content_hash"}
    }
    expected_hash = _document_hash(body)
    require(
        snapshot.get("content_hash") == expected_hash,
        "CLASSIFICATION_SNAPSHOT_CONTENT_HASH_MISMATCH",
    )
    require(
        snapshot.get("classification_snapshot_id")
        == str(uuid.uuid5(SNAPSHOT_NAMESPACE, expected_hash)),
        "CLASSIFICATION_SNAPSHOT_ID_MISMATCH",
    )


def _validate_publishable_v2_snapshot(snapshot: Mapping[str, Any]) -> None:
    require(
        set(snapshot) == V2_SNAPSHOT_FIELDS
        and snapshot.get("segmentation_type") == "SEVEN_AXIS"
        and snapshot.get("item_result_contract_version") == "2.0.0"
        and snapshot.get("effective_policy_contract_version") == "2.0.0"
        and snapshot.get("axis_result_contract_version") == "2.0.0"
        and type(snapshot.get("scope")) is dict
        and set(snapshot["scope"]) == {"company_cd", "subs_cd", "plant_cd", "site_cd"},
        "CLASSIFICATION_V2_CONTRACT_MISMATCH",
    )
    _validate_v2_header(snapshot)
    items = snapshot["items"]
    require(
        isinstance(items, (list, tuple))
        and all(type(item) is dict and set(item) == V2_ITEM_FIELDS for item in items),
        "CLASSIFICATION_V2_ITEM_CONTRACT_MISMATCH",
    )
    item_ids = [item["item_id"] for item in items]
    require(
        item_ids == sorted(item_ids) and len(item_ids) == len(set(item_ids)),
        "CLASSIFICATION_SNAPSHOT_ITEM_AGGREGATE_MISMATCH",
    )
    for item in items:
        _validate_v2_item(item, config_hash=snapshot["config_hash"])
    for axis in AXIS_ORDER:
        axis_rows = [
            next(result for result in item["axis_results"] if result["axis"] == axis)
            for item in items
        ]
        require(
            len({result["application_mode"] for result in axis_rows}) == 1
            and not (
                any(result["status"] == "NOT_APPLICABLE" for result in axis_rows)
                and any(result["status"] != "NOT_APPLICABLE" for result in axis_rows)
            ),
            "CLASSIFICATION_V2_AXIS_RESULTS_INVALID",
        )
    require(
        snapshot["effective_policy_content_hash"] == effective_policy_content_hash(items),
        "CLASSIFICATION_EFFECTIVE_POLICY_CONTENT_HASH_MISMATCH",
    )
    _validate_snapshot_aggregates(snapshot, items)
    _validate_v2_axis_windows(snapshot, items)
    _validate_snapshot_seal(snapshot)


def _validate_v2_header(snapshot: Mapping[str, Any]) -> None:
    scope = snapshot["scope"]
    require(
        all(
            isinstance(value, str)
            and bool(value.strip())
            and value == value.strip()
            and len(value) <= 64
            for value in (
                snapshot["tenant_id"],
                snapshot["project_id"],
                scope["company_cd"],
                scope["subs_cd"],
                scope["plant_cd"],
                scope["site_cd"],
            )
        )
        and isinstance(snapshot["source_revision"], str)
        and bool(snapshot["source_revision"].strip())
        and len(snapshot["source_revision"]) <= 255
        and isinstance(snapshot["source_relation"], str)
        and bool(snapshot["source_relation"].strip())
        and len(snapshot["source_relation"]) <= 255,
        "CLASSIFICATION_V2_HEADER_INVALID",
    )
    _require_uuid(snapshot["classification_snapshot_id"], "CLASSIFICATION_V2_HEADER_INVALID")
    _require_uuid(snapshot["config_id"], "CLASSIFICATION_V2_HEADER_INVALID")
    _require_uuid(snapshot["config_revision_id"], "CLASSIFICATION_V2_HEADER_INVALID")
    if snapshot["ved_assignment_snapshot_id"] is not None:
        _require_uuid(
            snapshot["ved_assignment_snapshot_id"],
            "CLASSIFICATION_V2_HEADER_INVALID",
        )
    require(
        (snapshot["ved_assignment_snapshot_id"] is None)
        == (snapshot["ved_assignment_content_hash"] is None),
        "CLASSIFICATION_V2_HEADER_INVALID",
    )
    for field in (
        "config_hash",
        "source_content_hash",
        "source_manifest_sha256",
        "content_hash",
        "effective_policy_content_hash",
        "actual_close_history_hash",
    ):
        hash_value(snapshot[field])
    if snapshot["ved_assignment_content_hash"] is not None:
        hash_value(snapshot["ved_assignment_content_hash"])
    as_of_monday = _iso_week_monday(snapshot["as_of_yyyyww"], "CLASSIFICATION_V2_HEADER_INVALID")
    lineage_start = _iso_week_monday(
        snapshot["actual_close_window_start_yyyyww"],
        "CLASSIFICATION_V2_HEADER_INVALID",
    )
    window_start = _iso_date(snapshot["window_start"], "CLASSIFICATION_V2_HEADER_INVALID")
    window_end = _iso_date(snapshot["window_end_exclusive"], "CLASSIFICATION_V2_HEADER_INVALID")
    require(
        window_start < window_end
        and window_start.weekday() == 0
        and window_end.weekday() == 0
        and window_end - timedelta(days=7) == as_of_monday
        and snapshot["abc_basis"] == "REVENUE"
        and snapshot["xyz_metric"] == "DEMAND_CV2"
        and snapshot["service_level_type"] == "CYCLE_SERVICE_LEVEL"
        and type(snapshot["abc_lookback_weeks"]) is int
        and type(snapshot["xyz_lookback_weeks"]) is int
        and 13 <= snapshot["abc_lookback_weeks"] <= 156
        and 13 <= snapshot["xyz_lookback_weeks"] <= 156
        and window_start <= lineage_start < window_end,
        "CLASSIFICATION_V2_HEADER_INVALID",
    )


def _validate_v2_axis_windows(
    snapshot: Mapping[str, Any], items: Sequence[Mapping[str, Any]]
) -> None:
    windows = snapshot["axis_windows"]
    require(
        type(windows) is dict and {"ABC", "XYZ"} <= set(windows) <= set(V2_WINDOW_AXES),
        "CLASSIFICATION_V2_AXIS_WINDOWS_INVALID",
    )
    header_end = _iso_date(
        snapshot["window_end_exclusive"], "CLASSIFICATION_V2_AXIS_WINDOWS_INVALID"
    )
    parsed_starts: dict[str, date] = {}
    for axis in V2_WINDOW_AXES:
        if axis not in windows:
            continue
        window = windows[axis]
        require(
            type(window) is dict
            and set(window) == V2_AXIS_WINDOW_FIELDS
            and type(window["lookback_weeks"]) is int
            and 13 <= window["lookback_weeks"] <= 156,
            "CLASSIFICATION_V2_AXIS_WINDOWS_INVALID",
        )
        start = _iso_date(window["window_start"], "CLASSIFICATION_V2_AXIS_WINDOWS_INVALID")
        end = _iso_date(
            window["window_end_exclusive"],
            "CLASSIFICATION_V2_AXIS_WINDOWS_INVALID",
        )
        require(
            start.weekday() == 0
            and end == header_end
            and end == start + timedelta(weeks=window["lookback_weeks"]),
            "CLASSIFICATION_V2_AXIS_WINDOWS_INVALID",
        )
        parsed_starts[axis] = start
    require(
        windows["ABC"]["lookback_weeks"] == snapshot["abc_lookback_weeks"]
        and windows["XYZ"]["lookback_weeks"] == snapshot["xyz_lookback_weeks"]
        and _iso_date(snapshot["window_start"], "CLASSIFICATION_V2_AXIS_WINDOWS_INVALID")
        == min(parsed_starts[axis] for axis in parsed_starts if axis != "SDE"),
        "CLASSIFICATION_V2_AXIS_WINDOWS_INVALID",
    )
    by_axis = {
        axis: {
            next(result for result in item["axis_results"] if result["axis"] == axis)["status"]
            != "NOT_APPLICABLE"
            for item in items
        }
        for axis in ("FSN", "SDE")
    }
    require(
        all(len(enabled) <= 1 for enabled in by_axis.values())
        and all(
            (bool(enabled) and True in enabled) == (axis in windows)
            for axis, enabled in by_axis.items()
        ),
        "CLASSIFICATION_V2_AXIS_WINDOWS_INVALID",
    )


def _validate_v2_item(item: Mapping[str, Any], *, config_hash: str) -> None:
    item_identifier(item["item_id"])
    normalized_policy = validate_effective_item_policy_v2(item)
    require(
        all(item[key] == normalized_policy[key] for key in EFFECTIVE_POLICY_V2_FIELDS)
        and item["classification_config_hash"] == config_hash,
        "CLASSIFICATION_V2_POLICY_BINDING_MISMATCH",
    )
    axis_results = item["axis_results"]
    require(
        type(axis_results) is list
        and len(axis_results) == len(AXIS_ORDER)
        and all(type(result) is dict and set(result) == V2_AXIS_FIELDS for result in axis_results)
        and tuple(result["axis"] for result in axis_results) == AXIS_ORDER,
        "CLASSIFICATION_V2_AXIS_RESULTS_INVALID",
    )
    for axis_result in axis_results:
        _validate_v2_axis_result(axis_result)
    axis_by_name = {result["axis"]: result for result in axis_results}
    projection_by_axis = {
        result["axis"]: result for result in normalized_policy["effective_axis_projection"]
    }
    expected_effective_axes = [
        axis for axis in AXIS_ORDER if axis_by_name[axis]["policy_effective"]
    ]
    require(
        item["policy_effective_axes"] == expected_effective_axes
        and list(projection_by_axis) == expected_effective_axes,
        "CLASSIFICATION_V2_AXIS_PROJECTION_MISMATCH",
    )
    for axis in expected_effective_axes:
        axis_result = axis_by_name[axis]
        projection = projection_by_axis[axis]
        require(
            projection["class_code"] == axis_result["class_code"]
            and projection["source_contract_key"] == axis_result["source_contract_key"],
            "CLASSIFICATION_V2_AXIS_PROJECTION_MISMATCH",
        )
    operational_eligible = all(
        result["status"] == "CLASSIFIED"
        for result in axis_results
        if result["application_mode"] == "OPERATIONAL"
    )
    require(
        type(item["seven_axis_operational_eligible"]) is bool
        and item["seven_axis_operational_eligible"] == operational_eligible
        and isinstance(item["display_segment_code"], str)
        and len(item["display_segment_code"]) <= 128
        and item["display_segment_code"] == display_segment_code(axis_results),
        "CLASSIFICATION_V2_AXIS_PROJECTION_MISMATCH",
    )
    _validate_v2_base_classification(item, axis_by_name)


def _validate_v2_axis_result(axis_result: Mapping[str, Any]) -> None:
    axis = axis_result["axis"]
    status = axis_result["status"]
    class_code = axis_result["class_code"]
    mode = axis_result["application_mode"]
    require(
        axis in AXIS_ORDER
        and status in V2_AXIS_STATUSES
        and mode in {"SHADOW", "OPERATIONAL"}
        and type(axis_result["policy_effective"]) is bool
        and axis_result["policy_effective"] == (status == "CLASSIFIED" and mode == "OPERATIONAL")
        and not (status in {"SYNTHETIC", "NOT_APPLICABLE"} and mode != "SHADOW")
        and not (axis in {"ABC", "XYZ"} and mode != "OPERATIONAL")
        and not (axis == "PLC" and mode != "SHADOW"),
        "CLASSIFICATION_V2_AXIS_RESULTS_INVALID",
    )
    require(
        (status == "CLASSIFIED" and class_code in V2_AXIS_CLASSES[axis])
        or (
            status == "SYNTHETIC"
            and (
                (axis == "PLC" and class_code in V2_AXIS_CLASSES[axis])
                or (axis != "PLC" and class_code is None)
            )
        )
        or (status in {"UNCLASSIFIED", "UNVERIFIED", "NOT_APPLICABLE"} and class_code is None),
        "CLASSIFICATION_V2_AXIS_RESULTS_INVALID",
    )
    require(
        isinstance(axis_result["source_contract_key"], str)
        and identifier(axis_result["source_contract_key"]) == axis_result["source_contract_key"]
        and type(axis_result["evidence"]) is dict
        and (
            (status == "CLASSIFIED" and axis_result["reason_code"] is None)
            or (
                status != "CLASSIFIED"
                and _valid_reason_code(axis_result["reason_code"], max_length=64)
            )
        ),
        "CLASSIFICATION_V2_AXIS_RESULTS_INVALID",
    )
    try:
        canonical_json(axis_result["evidence"])
    except (TypeError, ValueError, OverflowError):
        raise InventoryInputError("CLASSIFICATION_V2_AXIS_RESULTS_INVALID") from None


def _validate_v2_base_classification(
    item: Mapping[str, Any], axis_by_name: Mapping[str, Mapping[str, Any]]
) -> None:
    abc = axis_by_name["ABC"]
    xyz = axis_by_name["XYZ"]
    ved = axis_by_name["VED"]
    require(
        abc["status"] in {"CLASSIFIED", "UNCLASSIFIED"}
        and xyz["status"] in {"CLASSIFIED", "UNCLASSIFIED"}
        and item["abc_classification_status"] == abc["status"]
        and item["xyz_classification_status"] == xyz["status"]
        and item["abc_class"] == abc["class_code"]
        and item["xyz_class"] == xyz["class_code"]
        and item["abc_unclassified_reason_code"] == abc["reason_code"]
        and item["xyz_unclassified_reason_code"] == xyz["reason_code"]
        and item["ved_class"] == (ved["class_code"] if ved["status"] == "CLASSIFIED" else None),
        "CLASSIFICATION_V2_BASE_CLASSIFICATION_MISMATCH",
    )
    classified = abc["status"] == "CLASSIFIED" and xyz["status"] == "CLASSIFIED"
    expected_reason = None
    if not classified:
        if (
            abc["reason_code"] == "INVALID_ABC_SOURCE_RECORD"
            and xyz["reason_code"] == "INVALID_XYZ_SOURCE_RECORD"
        ):
            expected_reason = "INVALID_SOURCE_RECORD"
        elif (
            abc["reason_code"] == "INSUFFICIENT_ABC_HISTORY"
            and xyz["reason_code"] == "INSUFFICIENT_XYZ_HISTORY"
        ):
            expected_reason = "INSUFFICIENT_DEMAND_HISTORY"
        else:
            expected_reason = (
                abc["reason_code"] or xyz["reason_code"] or "CLASSIFICATION_SOURCE_INVALID"
            )
    require(
        item["classification_status"] == ("CLASSIFIED" if classified else "UNCLASSIFIED")
        and item["unclassified_reason_code"] == expected_reason
        and (
            (classified and item["segment_key"] == f"{item['abc_class']}{item['xyz_class']}")
            or (not classified and item["segment_key"] is None)
        ),
        "CLASSIFICATION_V2_BASE_CLASSIFICATION_MISMATCH",
    )
    if classified:
        expected_final = (
            item["segment_key"]
            if item["ved_class"] is None
            else f"{item['segment_key']}-{item['ved_class']}"
        )
        require(
            item["final_segment_key"] == expected_final
            and item["unclassified_reason_code"] is None
            and item["demand_cv2"] is not None
            and item["cumulative_revenue_share_before"] is not None,
            "CLASSIFICATION_V2_BASE_CLASSIFICATION_MISMATCH",
        )
    else:
        require(
            item["final_segment_key"] is None
            and _valid_reason_code(item["unclassified_reason_code"], max_length=64),
            "CLASSIFICATION_V2_BASE_CLASSIFICATION_MISMATCH",
        )
    require(
        item["ved_assignment_source"] in {"DISABLED", "DEFAULT_CLASS", "ASSIGNMENT_SNAPSHOT"}
        and (
            (
                item["ved_assignment_source"] == "DISABLED"
                and ved["status"] == "NOT_APPLICABLE"
                and item["ved_assignment_reason"] is None
            )
            or (
                item["ved_assignment_source"] in {"DEFAULT_CLASS", "ASSIGNMENT_SNAPSHOT"}
                and ved["status"] == "CLASSIFIED"
                and (
                    item["ved_assignment_source"] == "ASSIGNMENT_SNAPSHOT"
                    or item["ved_assignment_reason"] is None
                )
            )
        )
        and (
            item["ved_assignment_reason"] is None
            or (
                isinstance(item["ved_assignment_reason"], str)
                and bool(item["ved_assignment_reason"].strip())
                and len(item["ved_assignment_reason"]) <= 500
            )
        ),
        "CLASSIFICATION_V2_BASE_CLASSIFICATION_MISMATCH",
    )
    revenue = _publisher_decimal(item["revenue"])
    demand_cv2 = _publisher_decimal(item["demand_cv2"]) if item["demand_cv2"] is not None else None
    cumulative = (
        _publisher_decimal(item["cumulative_revenue_share_before"])
        if item["cumulative_revenue_share_before"] is not None
        else None
    )
    require(
        revenue >= 0
        and (demand_cv2 is None or demand_cv2 >= 0)
        and (cumulative is None or Decimal("0") <= cumulative <= Decimal("1")),
        "CLASSIFICATION_V2_BASE_CLASSIFICATION_MISMATCH",
    )


def _validate_snapshot_aggregates(
    snapshot: Mapping[str, Any], items: Sequence[Mapping[str, Any]]
) -> None:
    segments = snapshot["segments"]
    require(
        isinstance(segments, (list, tuple))
        and len(segments) == len(V1_SEGMENTS)
        and all(
            type(segment) is dict and set(segment) == {"segment_key", "sku_count", "revenue_share"}
            for segment in segments
        )
        and tuple(segment["segment_key"] for segment in segments) == V1_SEGMENTS,
        "CLASSIFICATION_SNAPSHOT_SEGMENT_AGGREGATE_MISMATCH",
    )
    classified_items = [item for item in items if item["classification_status"] == "CLASSIFIED"]
    classified_count = len(classified_items)
    unclassified_count = len(items) - classified_count
    total_revenue = sum(
        (_publisher_decimal(item["revenue"]) for item in classified_items),
        Decimal("0"),
    )
    expected_segments = tuple(
        {
            "segment_key": segment_key,
            "sku_count": sum(item["segment_key"] == segment_key for item in classified_items),
            "revenue_share": _fixed_decimal_text(
                (
                    sum(
                        (
                            _publisher_decimal(item["revenue"])
                            for item in classified_items
                            if item["segment_key"] == segment_key
                        ),
                        Decimal("0"),
                    )
                    / total_revenue
                    if total_revenue > 0
                    else Decimal("0")
                ),
                places=12,
            ),
        }
        for segment_key in V1_SEGMENTS
    )
    require(
        tuple(segments) == expected_segments,
        "CLASSIFICATION_SNAPSHOT_SEGMENT_AGGREGATE_MISMATCH",
    )
    reasons = snapshot["unclassified_reasons"]
    require(
        isinstance(reasons, (list, tuple))
        and all(
            type(reason) is dict and set(reason) == {"reason_code", "sku_count"}
            for reason in reasons
        )
        and len({reason["reason_code"] for reason in reasons}) == len(reasons),
        "CLASSIFICATION_SNAPSHOT_REASON_AGGREGATE_MISMATCH",
    )
    reason_counts = Counter(
        item["unclassified_reason_code"]
        for item in items
        if item["classification_status"] == "UNCLASSIFIED"
    )
    expected_reasons = tuple(
        {"reason_code": reason_code, "sku_count": reason_counts[reason_code]}
        for reason_code in sorted(reason_counts)
    )
    require(
        tuple(reasons) == expected_reasons
        and len(items) > 0
        and type(snapshot["eligible_sku_count"]) is int
        and type(snapshot["classified_sku_count"]) is int
        and type(snapshot["unclassified_sku_count"]) is int
        and snapshot["eligible_sku_count"] == len(items)
        and snapshot["classified_sku_count"] == classified_count
        and snapshot["unclassified_sku_count"] == unclassified_count
        and sum(segment["sku_count"] for segment in segments) == classified_count
        and sum(reason["sku_count"] for reason in reasons) == unclassified_count,
        "CLASSIFICATION_SNAPSHOT_ITEM_AGGREGATE_MISMATCH",
    )


def _validate_snapshot_seal(snapshot: Mapping[str, Any]) -> None:
    body = {
        key: value
        for key, value in snapshot.items()
        if key not in {"classification_snapshot_id", "content_hash"}
    }
    expected_hash = _document_hash(body)
    require(
        snapshot.get("content_hash") == expected_hash,
        "CLASSIFICATION_SNAPSHOT_CONTENT_HASH_MISMATCH",
    )
    require(
        snapshot.get("classification_snapshot_id")
        == str(uuid.uuid5(SNAPSHOT_NAMESPACE, expected_hash)),
        "CLASSIFICATION_SNAPSHOT_ID_MISMATCH",
    )


def _require_uuid(value: Any, code: str) -> None:
    try:
        require(isinstance(value, str) and str(uuid.UUID(value)) == value.lower(), code)
    except (ValueError, AttributeError, TypeError):
        raise InventoryInputError(code) from None


def _iso_date(value: Any, code: str) -> date:
    try:
        require(isinstance(value, str) and date.fromisoformat(value).isoformat() == value, code)
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise InventoryInputError(code) from None


def _iso_week_monday(value: Any, code: str) -> date:
    try:
        normalized = yyyyww(value)
        return date.fromisocalendar(int(normalized[:4]), int(normalized[4:]), 1)
    except (InventoryInputError, ValueError, TypeError):
        raise InventoryInputError(code) from None


def _valid_reason_code(value: Any, *, max_length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= max_length
        and re.fullmatch(r"[A-Z][A-Z0-9_]*", value) is not None
    )


def _document_hash(value: Any) -> str:
    document = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(document.encode("utf-8")).hexdigest()


def _valid_actual_close(row: Mapping[str, Any]) -> bool:
    return (
        str(row.get("source_relation") or "").strip() == "dsdm.tb_dyn_demand_dtl"
        and _valid_sha256(row.get("source_manifest_sha256"))
        and bool(str(row.get("publication_id") or "").strip())
    )


def _actual_close_window_sealed(
    by_week: Mapping[str, Mapping[str, Any]], weeks: tuple[str, ...]
) -> bool:
    return bool(weeks) and all(
        week in by_week
        and str(by_week[week].get("status_cd") or "").strip().upper() in {"CLOSED", "REVISED"}
        and _valid_actual_close(by_week[week])
        for week in weeks
    )


def _valid_sha256(value: Any) -> bool:
    normalized = str(value or "").strip().lower()
    return len(normalized) == 64 and all(
        character in "0123456789abcdef" for character in normalized
    )


def _publisher_decimal(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
        require(result.is_finite() and result >= 0, "CLASSIFICATION_SNAPSHOT_REVENUE_INVALID")
        return result
    except (InvalidOperation, TypeError, ValueError):
        raise InventoryInputError("CLASSIFICATION_SNAPSHOT_REVENUE_INVALID") from None


def _fixed_decimal_text(value: Decimal, *, places: int) -> str:
    return format(value.quantize(Decimal(1).scaleb(-places)), "f")

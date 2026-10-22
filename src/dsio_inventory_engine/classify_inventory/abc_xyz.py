"""Pure ABC-XYZ calculation, preserving the original classification result projection.

Optional VED annotations are compatibility metadata, not seven-axis policy overlays.
This class cannot access a database, publish results, or change operational gates.
"""

from collections import Counter
from decimal import Decimal
from typing import Any, Sequence

from dsio_inventory_engine.classify_inventory.models import (
    ClassificationResult,
    ItemMetric,
    VedAssignment,
)
from dsio_inventory_engine.classify_inventory._serialization import (
    _axis_count,
    _combined_axis_reason,
    _decimal_text,
    _optional_decimal_text,
    _snapshot_hash,
    _ved_result,
)
from dsio_inventory_engine.inventory_contracts.values import item_identifier as _item_identifier
from dsio_inventory_engine.inventory_contracts.values import require

SEGMENTS = ("AX", "AY", "AZ", "BX", "BY", "BZ", "CX", "CY", "CZ")


class AbcXyzSegmentation:
    """Existing deterministic algorithm; method identity is not operational approval."""

    method_id = "ABC_XYZ"
    implementation_version = "1.0.0"

    def classify(
        self,
        rows: Sequence[ItemMetric],
        *,
        abc_a_cumulative_share: Decimal,
        abc_b_cumulative_share: Decimal,
        xyz_x_max_cv2: Decimal,
        xyz_y_max_cv2: Decimal,
        lookback_weeks: int | None = None,
        abc_lookback_weeks: int | None = None,
        xyz_lookback_weeks: int | None = None,
        ved_enabled: bool = False,
        ved_default_class: str = "D",
        ved_assignments: Sequence[VedAssignment] = (),
    ) -> ClassificationResult:
        """Classify eligible item metrics without inventing missing evidence."""

        abc_lookback = abc_lookback_weeks or lookback_weeks
        xyz_lookback = xyz_lookback_weeks or lookback_weeks
        require(
            bool(rows)
            and type(abc_lookback) is int
            and abc_lookback >= 1
            and type(xyz_lookback) is int
            and xyz_lookback >= 1,
            "CLASSIFICATION_SOURCE_EMPTY",
        )
        require(
            Decimal("0") < abc_a_cumulative_share < abc_b_cumulative_share < Decimal("1"),
            "CLASSIFICATION_ABC_THRESHOLD_INVALID",
        )
        require(
            Decimal("0") <= xyz_x_max_cv2 < xyz_y_max_cv2,
            "CLASSIFICATION_XYZ_THRESHOLD_INVALID",
        )
        require(ved_default_class in {"V", "E", "D"}, "CLASSIFICATION_VED_CLASS_INVALID")

        seen: set[str] = set()
        abc_candidates: list[tuple[ItemMetric, Decimal]] = []
        xyz_cv2_by_item: dict[str, Decimal] = {}
        abc_reason_by_item: dict[str, str | None] = {}
        xyz_reason_by_item: dict[str, str | None] = {}
        abc_revenue_by_item: dict[str, Decimal] = {}
        reasons: Counter[str] = Counter()
        reason_by_item: dict[str, str] = {}
        for row in sorted(rows, key=lambda item: item.item_id):
            _item_identifier(row.item_id)
            require(row.item_id not in seen, "CLASSIFICATION_SOURCE_ITEM_INVALID")
            seen.add(row.item_id)
            abc_source_row_count = _axis_count(row.abc_source_row_count, row.source_row_count)
            abc_invalid_row_count = _axis_count(row.abc_invalid_row_count, row.invalid_row_count)
            abc_observed_week_count = _axis_count(
                row.abc_observed_week_count, row.observed_week_count
            )
            xyz_source_row_count = _axis_count(row.xyz_source_row_count, row.source_row_count)
            xyz_invalid_row_count = _axis_count(row.xyz_invalid_row_count, row.invalid_row_count)
            xyz_observed_week_count = _axis_count(
                row.xyz_observed_week_count, row.observed_week_count
            )
            abc_revenue = row.abc_revenue if row.abc_revenue is not None else row.revenue
            xyz_total_demand = (
                row.xyz_total_demand if row.xyz_total_demand is not None else row.total_demand
            )
            xyz_demand_square_sum = (
                row.xyz_demand_square_sum
                if row.xyz_demand_square_sum is not None
                else row.demand_square_sum
            )
            require(
                min(
                    row.source_row_count,
                    row.invalid_row_count,
                    row.observed_week_count,
                    abc_source_row_count,
                    abc_invalid_row_count,
                    abc_observed_week_count,
                    xyz_source_row_count,
                    xyz_invalid_row_count,
                    xyz_observed_week_count,
                )
                >= 0,
                "CLASSIFICATION_SOURCE_COUNT_INVALID",
            )
            require(
                all(
                    value.is_finite()
                    for value in (
                        row.total_demand,
                        row.demand_square_sum,
                        row.revenue,
                        abc_revenue,
                        xyz_total_demand,
                        xyz_demand_square_sum,
                    )
                ),
                "CLASSIFICATION_SOURCE_VALUE_INVALID",
            )
            abc_history_missing = abc_source_row_count == 0 or abc_observed_week_count == 0
            xyz_history_missing = xyz_source_row_count == 0 or xyz_observed_week_count == 0
            abc_reason = (
                "INVALID_ABC_SOURCE_RECORD"
                if abc_invalid_row_count > 0
                else "INSUFFICIENT_ABC_HISTORY"
                if abc_history_missing
                else "MISSING_REVENUE"
                if abc_revenue <= 0
                else None
            )
            xyz_reason = (
                "INVALID_XYZ_SOURCE_RECORD"
                if xyz_invalid_row_count > 0
                else "INSUFFICIENT_XYZ_HISTORY"
                if xyz_history_missing
                else "ZERO_MEAN_DEMAND"
                if xyz_total_demand <= 0
                else None
            )
            abc_reason_by_item[row.item_id] = abc_reason
            xyz_reason_by_item[row.item_id] = xyz_reason
            abc_revenue_by_item[row.item_id] = abc_revenue
            if abc_reason is None:
                abc_candidates.append((row, abc_revenue))
            if xyz_reason is None:
                mean = xyz_total_demand / Decimal(xyz_lookback)
                variance = max(
                    Decimal("0"),
                    xyz_demand_square_sum / Decimal(xyz_lookback) - mean * mean,
                )
                xyz_cv2_by_item[row.item_id] = variance / (mean * mean)
            if abc_reason is not None or xyz_reason is not None:
                reason_by_item[row.item_id] = _combined_axis_reason(abc_reason, xyz_reason)

        reasons.update(reason_by_item.values())
        assignment_by_item: dict[str, VedAssignment] = {}
        for assignment in ved_assignments:
            require(
                assignment.item_id not in assignment_by_item,
                "CLASSIFICATION_VED_ASSIGNMENT_DUPLICATE",
            )
            assignment_by_item[assignment.item_id] = assignment
        require(
            set(assignment_by_item) <= seen,
            "CLASSIFICATION_VED_ORPHAN_ITEM",
        )
        require(
            ved_enabled or not assignment_by_item,
            "CLASSIFICATION_VED_ASSIGNMENT_UNEXPECTED",
        )

        abc_total_revenue = sum((revenue for _, revenue in abc_candidates), Decimal("0"))
        require(abc_total_revenue > 0, "CLASSIFICATION_REVENUE_EMPTY")
        abc_class_by_item: dict[str, str] = {}
        cumulative_share_by_item: dict[str, Decimal] = {}
        cumulative_revenue = Decimal("0")
        for row, abc_revenue in sorted(
            abc_candidates, key=lambda item: (-item[1], item[0].item_id)
        ):
            share_before_item = cumulative_revenue / abc_total_revenue
            abc_class_by_item[row.item_id] = (
                "A"
                if share_before_item < abc_a_cumulative_share
                else "B"
                if share_before_item < abc_b_cumulative_share
                else "C"
            )
            cumulative_share_by_item[row.item_id] = share_before_item
            cumulative_revenue += abc_revenue

        complete_items = [
            row
            for row in rows
            if row.item_id in abc_class_by_item and row.item_id in xyz_cv2_by_item
        ]
        total_revenue = sum(
            (abc_revenue_by_item[row.item_id] for row in complete_items),
            Decimal("0"),
        )

        segment_counts = Counter({segment: 0 for segment in SEGMENTS})
        segment_revenue = {segment: Decimal("0") for segment in SEGMENTS}
        item_results: list[dict[str, Any]] = []
        for row in sorted(
            complete_items,
            key=lambda item: (-abc_revenue_by_item[item.item_id], item.item_id),
        ):
            cv2 = xyz_cv2_by_item[row.item_id]
            abc_revenue = abc_revenue_by_item[row.item_id]
            abc_class = abc_class_by_item[row.item_id]
            share_before_item = cumulative_share_by_item[row.item_id]
            xyz_class = "X" if cv2 <= xyz_x_max_cv2 else "Y" if cv2 <= xyz_y_max_cv2 else "Z"
            segment = f"{abc_class}{xyz_class}"
            segment_counts[segment] += 1
            segment_revenue[segment] += abc_revenue
            ved_class, ved_source, ved_reason = _ved_result(
                row.item_id,
                enabled=ved_enabled,
                default_class=ved_default_class,
                assignments=assignment_by_item,
            )
            item_results.append(
                {
                    "item_id": row.item_id,
                    "classification_status": "CLASSIFIED",
                    "abc_classification_status": "CLASSIFIED",
                    "xyz_classification_status": "CLASSIFIED",
                    "abc_class": abc_class,
                    "xyz_class": xyz_class,
                    "abc_unclassified_reason_code": None,
                    "xyz_unclassified_reason_code": None,
                    "ved_class": ved_class,
                    "segment_key": segment,
                    "final_segment_key": (
                        f"{segment}-{ved_class}" if ved_class is not None else segment
                    ),
                    "ved_assignment_source": ved_source,
                    "ved_assignment_reason": ved_reason,
                    "unclassified_reason_code": None,
                    "demand_cv2": _decimal_text(cv2, places=12),
                    "revenue": _decimal_text(abc_revenue),
                    "cumulative_revenue_share_before": _decimal_text(
                        share_before_item,
                        places=12,
                    ),
                }
            )

        metric_by_item = {row.item_id: row for row in rows}
        for item_id, reason in sorted(reason_by_item.items()):
            row = metric_by_item[item_id]
            abc_class = abc_class_by_item.get(item_id)
            cv2 = xyz_cv2_by_item.get(item_id)
            xyz_class = (
                None
                if cv2 is None
                else "X"
                if cv2 <= xyz_x_max_cv2
                else "Y"
                if cv2 <= xyz_y_max_cv2
                else "Z"
            )
            ved_class, ved_source, ved_reason = _ved_result(
                item_id,
                enabled=ved_enabled,
                default_class=ved_default_class,
                assignments=assignment_by_item,
            )
            item_results.append(
                {
                    "item_id": item_id,
                    "classification_status": "UNCLASSIFIED",
                    "abc_classification_status": (
                        "CLASSIFIED" if abc_class is not None else "UNCLASSIFIED"
                    ),
                    "xyz_classification_status": (
                        "CLASSIFIED" if xyz_class is not None else "UNCLASSIFIED"
                    ),
                    "abc_class": abc_class,
                    "xyz_class": xyz_class,
                    "abc_unclassified_reason_code": abc_reason_by_item[item_id],
                    "xyz_unclassified_reason_code": xyz_reason_by_item[item_id],
                    "ved_class": ved_class,
                    "segment_key": None,
                    "final_segment_key": None,
                    "ved_assignment_source": ved_source,
                    "ved_assignment_reason": ved_reason,
                    "unclassified_reason_code": reason,
                    "demand_cv2": (_decimal_text(cv2, places=12) if cv2 is not None else None),
                    "revenue": _decimal_text(
                        row.abc_revenue if row.abc_revenue is not None else row.revenue
                    ),
                    "cumulative_revenue_share_before": (
                        _decimal_text(cumulative_share_by_item[item_id], places=12)
                        if item_id in cumulative_share_by_item
                        else None
                    ),
                }
            )

        segments = tuple(
            {
                "segment_key": segment,
                "sku_count": segment_counts[segment],
                "revenue_share": _decimal_text(
                    (
                        segment_revenue[segment] / total_revenue
                        if total_revenue > 0
                        else Decimal("0")
                    ),
                    places=12,
                ),
            }
            for segment in SEGMENTS
        )
        unclassified = tuple(
            {"reason_code": code, "sku_count": reasons[code]}
            for code in sorted(reasons)
            if reasons[code] > 0
        )
        source_rows = [
            {
                "item_id": row.item_id,
                "source_row_count": row.source_row_count,
                "invalid_row_count": row.invalid_row_count,
                "observed_week_count": row.observed_week_count,
                "total_demand": _decimal_text(row.total_demand),
                "demand_square_sum": _decimal_text(row.demand_square_sum),
                "revenue": _decimal_text(row.revenue),
                "abc_source_row_count": _axis_count(row.abc_source_row_count, row.source_row_count),
                "abc_invalid_row_count": _axis_count(
                    row.abc_invalid_row_count, row.invalid_row_count
                ),
                "abc_observed_week_count": _axis_count(
                    row.abc_observed_week_count, row.observed_week_count
                ),
                "abc_revenue": _decimal_text(
                    row.abc_revenue if row.abc_revenue is not None else row.revenue
                ),
                "xyz_source_row_count": _axis_count(row.xyz_source_row_count, row.source_row_count),
                "xyz_invalid_row_count": _axis_count(
                    row.xyz_invalid_row_count, row.invalid_row_count
                ),
                "xyz_observed_week_count": _axis_count(
                    row.xyz_observed_week_count, row.observed_week_count
                ),
                "xyz_total_demand": _decimal_text(
                    row.xyz_total_demand if row.xyz_total_demand is not None else row.total_demand
                ),
                "xyz_demand_square_sum": _decimal_text(
                    row.xyz_demand_square_sum
                    if row.xyz_demand_square_sum is not None
                    else row.demand_square_sum
                ),
                "fsn_source_row_count": row.fsn_source_row_count,
                "fsn_invalid_row_count": row.fsn_invalid_row_count,
                "positive_week_count": row.positive_week_count,
                "last_positive_demand_yyyyww": row.last_positive_demand_yyyyww,
                "fsn_source_status": row.fsn_source_status,
                "supplier_count": row.supplier_count,
                "planning_lead_time_days": _optional_decimal_text(row.planning_lead_time_days),
                "p50_lead_time_days": _optional_decimal_text(row.p50_lead_time_days),
                "p90_lead_time_days": _optional_decimal_text(row.p90_lead_time_days),
                "on_time_delivery_rate": _optional_decimal_text(row.on_time_delivery_rate),
                "receipt_sample_count": row.receipt_sample_count,
                "sde_source_status": row.sde_source_status,
                "unit_cost": _optional_decimal_text(row.unit_cost),
                "unit_cost_currency": row.unit_cost_currency,
                "unit_cost_as_of_yyyyww": row.unit_cost_as_of_yyyyww,
                "hml_source_status": row.hml_source_status,
                "introduced_yyyyww": row.introduced_yyyyww,
                "production_end_yyyyww": row.production_end_yyyyww,
                "service_end_yyyyww": row.service_end_yyyyww,
                "lifecycle_status_cd": row.lifecycle_status_cd,
                "plc_source_status": row.plc_source_status,
                "plc_source_profile_hash": row.plc_source_profile_hash,
            }
            for row in sorted(rows, key=lambda item: item.item_id)
        ]
        return ClassificationResult(
            eligible_sku_count=len(rows),
            classified_sku_count=len(complete_items),
            unclassified_sku_count=sum(reasons.values()),
            segments=segments,
            unclassified_reasons=unclassified,
            items=tuple(sorted(item_results, key=lambda item: item["item_id"])),
            source_content_hash=_snapshot_hash(source_rows),
        )

"""Deterministic ABC-XYZ classification and append-only publication orchestration."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Mapping, Protocol, Sequence

from dsio_inventory_engine.inventory_contracts.classification import (
    EFFECTIVE_POLICY_CONTRACT_VERSION,
    derive_effective_item_policy,
)
from dsio_inventory_engine.inventory_contracts.effective_policy_v2 import (
    EFFECTIVE_POLICY_V2_CONTRACT_VERSION,
    derive_effective_item_policy_v2,
    effective_policy_content_hash,
)
from dsio_inventory_engine.inventory_contracts.seven_axis import (
    AXIS_RESULT_CONTRACT_VERSION,
    classify_fsn,
    classify_hml,
    classify_plc,
    classify_sde,
    display_segment_code,
    hml_thresholds,
)
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    canonical_json,
    hash_value,
    identifier,
    item_identifier,
    require,
    yyyyww,
)


SEGMENTS = ("AX", "AY", "AZ", "BX", "BY", "BZ", "CX", "CY", "CZ")
SNAPSHOT_NAMESPACE = uuid.UUID("f9821dd0-652d-4fb1-bc75-f88c2b68fe8b")
CONFIG_SCHEMA_ID = "urn:dsai:inventory-engine-config-values:1.1.0"
CONFIG_SCHEMA_VERSION = "1.1.0"
CONFIG_SCHEMA_HASH = "b7841bc8cbe996903a7a9b2c1bb70a0a2dc4f283019fdde5931b54c788627b04"
CONFIG_SCHEMA_V2_ID = "urn:dsai:inventory-engine-config-values:2.0.0"
CONFIG_SCHEMA_V2_VERSION = "2.0.0"
CONFIG_SCHEMA_V2_HASH = "77a1ccc984d3a6296c963e09460d8ff2e252debc0e2ca6179ec2b2fcacd67e9e"


@dataclass(frozen=True, slots=True)
class InventoryScope:
    project_id: str
    company_cd: str
    subs_cd: str
    plant_cd: str
    site_cd: str

    def __post_init__(self) -> None:
        for value in (
            self.project_id,
            self.company_cd,
            self.subs_cd,
            self.plant_cd,
            self.site_cd,
        ):
            identifier(value)

    def to_dict(self) -> dict[str, str]:
        return {
            "company_cd": self.company_cd,
            "subs_cd": self.subs_cd,
            "plant_cd": self.plant_cd,
            "site_cd": self.site_cd,
        }


@dataclass(frozen=True, slots=True)
class ItemMetric:
    item_id: str
    source_row_count: int
    invalid_row_count: int
    observed_week_count: int
    total_demand: Decimal
    demand_square_sum: Decimal
    revenue: Decimal
    abc_source_row_count: int | None = None
    abc_invalid_row_count: int | None = None
    abc_observed_week_count: int | None = None
    abc_revenue: Decimal | None = None
    xyz_source_row_count: int | None = None
    xyz_invalid_row_count: int | None = None
    xyz_observed_week_count: int | None = None
    xyz_total_demand: Decimal | None = None
    xyz_demand_square_sum: Decimal | None = None
    fsn_source_row_count: int | None = None
    fsn_invalid_row_count: int | None = None
    positive_week_count: int = 0
    last_positive_demand_yyyyww: str | None = None
    fsn_source_status: str = "UNVERIFIED"
    supplier_count: int | None = None
    planning_lead_time_days: Decimal | None = None
    p50_lead_time_days: Decimal | None = None
    p90_lead_time_days: Decimal | None = None
    on_time_delivery_rate: Decimal | None = None
    receipt_sample_count: int = 0
    sde_source_status: str = "UNVERIFIED"
    unit_cost: Decimal | None = None
    unit_cost_currency: str | None = None
    unit_cost_as_of_yyyyww: str | None = None
    hml_source_status: str = "UNVERIFIED"
    introduced_yyyyww: str | None = None
    production_end_yyyyww: str | None = None
    service_end_yyyyww: str | None = None
    lifecycle_status_cd: str | None = None
    plc_source_status: str = "UNVERIFIED"
    plc_source_profile_hash: str | None = None


@dataclass(frozen=True, slots=True)
class VedAssignment:
    item_id: str
    ved_class: str
    reason: str | None = None

    def __post_init__(self) -> None:
        _item_identifier(self.item_id)
        require(self.ved_class in {"V", "E", "D"}, "CLASSIFICATION_VED_CLASS_INVALID")
        require(
            self.reason is None
            or (
                isinstance(self.reason, str)
                and bool(self.reason.strip())
                and len(self.reason) <= 500
            ),
            "CLASSIFICATION_VED_REASON_INVALID",
        )


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    eligible_sku_count: int
    classified_sku_count: int
    unclassified_sku_count: int
    segments: tuple[dict[str, Any], ...]
    unclassified_reasons: tuple[dict[str, Any], ...]
    items: tuple[dict[str, Any], ...]
    source_content_hash: str


@dataclass(frozen=True, slots=True)
class ClassificationInputs:
    tenant_id: str
    scope: InventoryScope
    config_id: str
    config_revision_id: str
    config_schema_id: str
    config_schema_version: str
    config_schema_hash: str
    config_hash: str
    config_values: Mapping[str, Any]
    actual_yyyyww: str
    closure_revision_no: int
    publication_id: str
    source_relation: str
    source_manifest_sha256: str
    metrics: tuple[ItemMetric, ...]
    ved_assignments: tuple[VedAssignment, ...] = ()
    actual_close_window_start_yyyyww: str | None = None
    actual_close_history_hash: str | None = None


class ClassificationSource(Protocol):
    async def load_inputs(self, scope: InventoryScope) -> ClassificationInputs: ...


class ClassificationPublisher(Protocol):
    async def publish(
        self, snapshot: Mapping[str, Any], *, approved_by: str
    ) -> Mapping[str, Any]: ...


class InventoryClassificationLifecycleUseCase:
    """Run one bounded classification lifecycle; publishing is explicit."""

    def __init__(
        self,
        source: ClassificationSource,
        publisher: ClassificationPublisher | None = None,
    ) -> None:
        self.source = source
        self.publisher = publisher

    async def execute(
        self,
        scope: InventoryScope,
        *,
        approved_by: str,
        publish: bool = False,
    ) -> dict[str, Any]:
        identifier(approved_by)
        inputs = await self.source.load_inputs(scope)
        snapshot = build_snapshot(inputs)
        publication: Mapping[str, Any] = {"publication_status": "dry_run"}
        if publish:
            require(self.publisher is not None, "CLASSIFICATION_PUBLISHER_REQUIRED")
            publication = await self.publisher.publish(snapshot, approved_by=approved_by)
        return publication_receipt(snapshot, publication)


def classify_items(
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
        abc_observed_week_count = _axis_count(row.abc_observed_week_count, row.observed_week_count)
        xyz_source_row_count = _axis_count(row.xyz_source_row_count, row.source_row_count)
        xyz_invalid_row_count = _axis_count(row.xyz_invalid_row_count, row.invalid_row_count)
        xyz_observed_week_count = _axis_count(row.xyz_observed_week_count, row.observed_week_count)
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
    for row, abc_revenue in sorted(abc_candidates, key=lambda item: (-item[1], item[0].item_id)):
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
        row for row in rows if row.item_id in abc_class_by_item and row.item_id in xyz_cv2_by_item
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
                (segment_revenue[segment] / total_revenue if total_revenue > 0 else Decimal("0")),
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
            "abc_invalid_row_count": _axis_count(row.abc_invalid_row_count, row.invalid_row_count),
            "abc_observed_week_count": _axis_count(
                row.abc_observed_week_count, row.observed_week_count
            ),
            "abc_revenue": _decimal_text(
                row.abc_revenue if row.abc_revenue is not None else row.revenue
            ),
            "xyz_source_row_count": _axis_count(row.xyz_source_row_count, row.source_row_count),
            "xyz_invalid_row_count": _axis_count(row.xyz_invalid_row_count, row.invalid_row_count),
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


def build_snapshot(inputs: ClassificationInputs) -> dict[str, Any]:
    schema_identity = (
        inputs.config_schema_id,
        inputs.config_schema_version,
        inputs.config_schema_hash,
    )
    require(
        schema_identity
        in {
            (CONFIG_SCHEMA_ID, CONFIG_SCHEMA_VERSION, CONFIG_SCHEMA_HASH),
            (CONFIG_SCHEMA_V2_ID, CONFIG_SCHEMA_V2_VERSION, CONFIG_SCHEMA_V2_HASH),
        },
        "CLASSIFICATION_CONFIG_SCHEMA_MISMATCH",
    )
    config = validate_config(inputs.config_values)
    seven_axis_mode = config["segmentation"]["mode"] == "SEVEN_AXIS"
    expected_schema_identity = (
        (CONFIG_SCHEMA_V2_ID, CONFIG_SCHEMA_V2_VERSION, CONFIG_SCHEMA_V2_HASH)
        if seven_axis_mode
        else (CONFIG_SCHEMA_ID, CONFIG_SCHEMA_VERSION, CONFIG_SCHEMA_HASH)
    )
    require(
        schema_identity == expected_schema_identity,
        "CLASSIFICATION_CONFIG_SCHEMA_MISMATCH",
    )
    require(config_hash(config) == inputs.config_hash, "CLASSIFICATION_CONFIG_HASH_MISMATCH")
    hash_value(inputs.source_manifest_sha256)
    as_of_yyyyww = yyyyww(inputs.actual_yyyyww)

    abc = config["segmentation"]["abc"]
    xyz = config["segmentation"]["xyz"]
    ved = config["segmentation"]["ved"]
    require(abc["basis"] == "REVENUE", "CLASSIFICATION_ABC_BASIS_UNSUPPORTED")
    if seven_axis_mode:
        _validate_v2_metric_contract(inputs.metrics, config)
    demand_lookbacks = [int(abc["lookback_weeks"]), int(xyz["lookback_weeks"])]
    lineage_lookbacks = list(demand_lookbacks)
    if seven_axis_mode:
        fsn = config["segmentation"]["fsn"]
        if fsn["enabled"]:
            demand_lookbacks.append(int(fsn["lookback_weeks"]))
            if fsn["application_mode"] == "OPERATIONAL" or any(
                metric.fsn_source_status == "VERIFIED" for metric in inputs.metrics
            ):
                lineage_lookbacks.append(int(fsn["lookback_weeks"]))
    lookback_weeks = max(demand_lookbacks)
    lineage_lookback_weeks = max(lineage_lookbacks)
    start_monday, end_monday = classification_window(as_of_yyyyww, lookback_weeks)
    if seven_axis_mode:
        lineage_start, _ = classification_window(as_of_yyyyww, lineage_lookback_weeks)
        require(
            inputs.actual_close_window_start_yyyyww is not None
            and yyyyww(inputs.actual_close_window_start_yyyyww) == lineage_start.strftime("%G%V"),
            "CLASSIFICATION_ACTUAL_LOOKBACK_LINEAGE_MISMATCH",
        )
        require(
            inputs.actual_close_history_hash is not None,
            "CLASSIFICATION_ACTUAL_LOOKBACK_LINEAGE_MISSING",
        )
        hash_value(inputs.actual_close_history_hash)
    result = classify_items(
        inputs.metrics,
        abc_a_cumulative_share=Decimal(str(abc["a_cumulative_share"])),
        abc_b_cumulative_share=Decimal(str(abc["b_cumulative_share"])),
        xyz_x_max_cv2=Decimal(str(xyz["x_max_cv2"])),
        xyz_y_max_cv2=Decimal(str(xyz["y_max_cv2"])),
        abc_lookback_weeks=int(abc["lookback_weeks"]),
        xyz_lookback_weeks=int(xyz["lookback_weeks"]),
        ved_enabled=ved["enabled"],
        ved_default_class=ved["default_class"],
        ved_assignments=inputs.ved_assignments,
    )
    classified_items = result.items
    if config["segmentation"]["mode"] == "SEVEN_AXIS":
        classified_items = _apply_seven_axis_results(
            result.items,
            inputs.metrics,
            config=config,
            as_of_yyyyww=as_of_yyyyww,
        )
    else:
        classified_items = tuple(
            {
                key: value
                for key, value in item.items()
                if key
                not in {
                    "abc_classification_status",
                    "xyz_classification_status",
                    "abc_unclassified_reason_code",
                    "xyz_unclassified_reason_code",
                }
            }
            for item in classified_items
        )
    cells = {cell["segment_key"]: cell for cell in config["policy_matrix"]["cells"]}
    effective_items: tuple[dict[str, Any], ...]
    if seven_axis_mode:
        effective_items = tuple(
            {
                **item,
                **derive_effective_item_policy_v2(
                    item_id=item["item_id"],
                    classification_config_hash=inputs.config_hash,
                    classification_status=item["classification_status"],
                    segment_key=item["segment_key"],
                    unclassified_reason_code=item["unclassified_reason_code"],
                    policy_cell=(cells[item["segment_key"]] if item["segment_key"] else None),
                    ved_service_level_floor=config["policy_matrix"]["ved_service_level_floor"],
                    axis_results=item["axis_results"],
                    policy_overlays=config["policy_overlays"],
                ),
            }
            for item in classified_items
        )
    else:
        effective_items = tuple(
            {
                **item,
                **derive_effective_item_policy(
                    item_id=item["item_id"],
                    classification_status=item["classification_status"],
                    segment_key=item["segment_key"],
                    ved_class=item["ved_class"],
                    unclassified_reason_code=item["unclassified_reason_code"],
                    policy_cell=(cells[item["segment_key"]] if item["segment_key"] else None),
                    ved_service_level_floor=config["policy_matrix"]["ved_service_level_floor"],
                    config_hash=inputs.config_hash,
                ),
            }
            for item in classified_items
        )
    axis_windows = _axis_window_evidence(config, as_of_yyyyww)
    source_revision = (
        "ACTUAL-CLOSE-WINDOW-"
        f"{inputs.actual_close_window_start_yyyyww}-{as_of_yyyyww}-"
        f"{inputs.actual_close_history_hash}"
        if seven_axis_mode
        else f"ACTUAL-CLOSE-{as_of_yyyyww}-R{inputs.closure_revision_no}-{inputs.publication_id}"
    )
    body = {
        "tenant_id": inputs.tenant_id,
        "project_id": inputs.scope.project_id,
        "scope": inputs.scope.to_dict(),
        "config_id": inputs.config_id,
        "config_revision_id": inputs.config_revision_id,
        "config_hash": inputs.config_hash,
        "source_revision": source_revision,
        "source_content_hash": (
            result.source_content_hash
            if seven_axis_mode
            else _v1_source_content_hash(inputs.metrics)
        ),
        "source_manifest_sha256": inputs.source_manifest_sha256,
        "source_relation": inputs.source_relation,
        "as_of_yyyyww": as_of_yyyyww,
        "window_start": start_monday.isoformat(),
        "window_end_exclusive": end_monday.isoformat(),
        "segmentation_type": config["segmentation"]["mode"],
        "abc_basis": abc["basis"],
        "abc_lookback_weeks": int(abc["lookback_weeks"]),
        "xyz_metric": xyz["metric"],
        "xyz_lookback_weeks": int(xyz["lookback_weeks"]),
        "service_level_type": config["policy_matrix"]["service_level_type"],
        "item_result_contract_version": (
            AXIS_RESULT_CONTRACT_VERSION
            if config["segmentation"]["mode"] == "SEVEN_AXIS"
            else "1.1.0"
        ),
        "effective_policy_contract_version": (
            EFFECTIVE_POLICY_V2_CONTRACT_VERSION
            if seven_axis_mode
            else EFFECTIVE_POLICY_CONTRACT_VERSION
        ),
        "effective_policy_content_hash": (
            effective_policy_content_hash(effective_items)
            if seven_axis_mode
            else _snapshot_hash(
                [
                    {
                        "item_id": item["item_id"],
                        "effective_policy_hash": item["effective_policy_hash"],
                    }
                    for item in effective_items
                ]
            )
        ),
        "ved_assignment_snapshot_id": (ved["assignment_snapshot_id"] if ved["enabled"] else None),
        "ved_assignment_content_hash": (ved["assignment_content_hash"] if ved["enabled"] else None),
        "eligible_sku_count": result.eligible_sku_count,
        "classified_sku_count": result.classified_sku_count,
        "unclassified_sku_count": result.unclassified_sku_count,
        "segments": result.segments,
        "unclassified_reasons": result.unclassified_reasons,
        "items": effective_items,
    }
    if config["segmentation"]["mode"] == "SEVEN_AXIS":
        body["axis_windows"] = axis_windows
        body["axis_result_contract_version"] = AXIS_RESULT_CONTRACT_VERSION
        body["actual_close_window_start_yyyyww"] = inputs.actual_close_window_start_yyyyww
        body["actual_close_history_hash"] = inputs.actual_close_history_hash
    body["content_hash"] = _snapshot_hash(body)
    body["classification_snapshot_id"] = str(uuid.uuid5(SNAPSHOT_NAMESPACE, body["content_hash"]))
    return body


def validate_config(value: Mapping[str, Any]) -> dict[str, Any]:
    require(type(value) is dict, "CLASSIFICATION_CONFIG_INVALID")
    segmentation = value.get("segmentation")
    require(type(segmentation) is dict, "CLASSIFICATION_CONFIG_INVALID")
    if segmentation.get("mode") == "ABC_XYZ":
        return _validate_v1_config(value)
    if segmentation.get("mode") == "SEVEN_AXIS":
        return _validate_v2_config(value)
    raise InventoryInputError("CLASSIFICATION_CONFIG_INVALID")


def _validate_v1_config(value: Mapping[str, Any]) -> dict[str, Any]:
    require(type(value) is dict, "CLASSIFICATION_CONFIG_INVALID")
    require(set(value) == {"segmentation", "policy_matrix"}, "CLASSIFICATION_CONFIG_INVALID")
    segmentation = value["segmentation"]
    policy = value["policy_matrix"]
    require(type(segmentation) is dict and type(policy) is dict, "CLASSIFICATION_CONFIG_INVALID")
    require(
        set(segmentation) == {"mode", "abc", "xyz", "ved"} and segmentation["mode"] == "ABC_XYZ",
        "CLASSIFICATION_CONFIG_INVALID",
    )
    abc, xyz, ved = segmentation["abc"], segmentation["xyz"], segmentation["ved"]
    require(
        type(abc) is dict
        and set(abc)
        == {
            "enabled",
            "basis",
            "lookback_weeks",
            "a_cumulative_share",
            "b_cumulative_share",
            "currency",
        },
        "CLASSIFICATION_CONFIG_INVALID",
    )
    require(
        abc["enabled"] is True
        and abc["basis"] in {"REVENUE", "CONTRIBUTION_MARGIN", "ANNUAL_USAGE_VALUE"}
        and type(abc["lookback_weeks"]) is int
        and 13 <= abc["lookback_weeks"] <= 156
        and type(abc["currency"]) is str
        and len(abc["currency"]) == 3
        and abc["currency"].isupper(),
        "CLASSIFICATION_CONFIG_INVALID",
    )
    try:
        a_share = Decimal(str(abc["a_cumulative_share"]))
        b_share = Decimal(str(abc["b_cumulative_share"]))
    except Exception as exc:
        raise InventoryInputError("CLASSIFICATION_CONFIG_INVALID") from exc
    require(Decimal("0") < a_share < b_share < Decimal("1"), "CLASSIFICATION_CONFIG_INVALID")
    require(
        type(xyz) is dict
        and set(xyz) == {"enabled", "metric", "lookback_weeks", "x_max_cv2", "y_max_cv2"},
        "CLASSIFICATION_CONFIG_INVALID",
    )
    try:
        x_max = Decimal(str(xyz["x_max_cv2"]))
        y_max = Decimal(str(xyz["y_max_cv2"]))
    except Exception as exc:
        raise InventoryInputError("CLASSIFICATION_CONFIG_INVALID") from exc
    require(
        xyz["enabled"] is True
        and xyz["metric"] == "DEMAND_CV2"
        and type(xyz["lookback_weeks"]) is int
        and 13 <= xyz["lookback_weeks"] <= 156
        and Decimal("0") <= x_max < y_max,
        "CLASSIFICATION_CONFIG_INVALID",
    )
    require(
        type(ved) is dict
        and set(ved)
        == {"enabled", "default_class", "assignment_snapshot_id", "assignment_content_hash"}
        and type(ved["enabled"]) is bool
        and ved["default_class"] in {"V", "E", "D"},
        "CLASSIFICATION_CONFIG_INVALID",
    )
    bound = bool(ved["assignment_snapshot_id"]) and bool(ved["assignment_content_hash"])
    require(
        (not ved["enabled"] or bound)
        and bool(ved["assignment_snapshot_id"]) == bool(ved["assignment_content_hash"]),
        "CLASSIFICATION_CONFIG_INVALID",
    )
    if ved["assignment_content_hash"] is not None:
        hash_value(ved["assignment_content_hash"])

    require(
        set(policy)
        == {"service_level_type", "default_strategy", "cells", "ved_service_level_floor"}
        and policy["service_level_type"] == "CYCLE_SERVICE_LEVEL"
        and policy["default_strategy"] in {"MATHEMATICAL", "PREDICTIVE_ML", "DEEP_RL"}
        and type(policy["cells"]) is list
        and len(policy["cells"]) == 9,
        "CLASSIFICATION_CONFIG_INVALID",
    )
    segment_keys: list[str] = []
    for cell in policy["cells"]:
        require(
            type(cell) is dict
            and set(cell)
            == {"segment_key", "target_service_level", "review_cycle_weeks", "strategy"},
            "CLASSIFICATION_CONFIG_INVALID",
        )
        segment_keys.append(cell["segment_key"])
        require(
            cell["segment_key"] in SEGMENTS
            and type(cell["review_cycle_weeks"]) is int
            and 1 <= cell["review_cycle_weeks"] <= 13
            and cell["strategy"] in {"MATHEMATICAL", "PREDICTIVE_ML", "DEEP_RL"}
            and Decimal("0.5") <= Decimal(str(cell["target_service_level"])) <= Decimal("0.9999"),
            "CLASSIFICATION_CONFIG_INVALID",
        )
    require(set(segment_keys) == set(SEGMENTS), "CLASSIFICATION_CONFIG_INVALID")
    floors = policy["ved_service_level_floor"]
    require(
        type(floors) is dict and set(floors) == {"V", "E", "D"}, "CLASSIFICATION_CONFIG_INVALID"
    )
    try:
        floor_values = [Decimal(str(floors[key])) for key in ("V", "E", "D")]
    except Exception as exc:
        raise InventoryInputError("CLASSIFICATION_CONFIG_INVALID") from exc
    require(
        all(Decimal("0.5") <= level <= Decimal("0.9999") for level in floor_values)
        and floor_values[0] >= floor_values[1] >= floor_values[2],
        "CLASSIFICATION_CONFIG_INVALID",
    )
    return dict(value)


def _validate_v2_config(value: Mapping[str, Any]) -> dict[str, Any]:
    require(
        set(value) == {"segmentation", "policy_matrix", "policy_overlays"},
        "CLASSIFICATION_CONFIG_INVALID",
    )
    segmentation = value["segmentation"]
    require(
        type(segmentation) is dict
        and set(segmentation) == {"mode", "abc", "xyz", "ved", "fsn", "sde", "hml", "plc"}
        and segmentation["mode"] == "SEVEN_AXIS",
        "CLASSIFICATION_CONFIG_INVALID",
    )
    _validate_v1_config(
        {
            "segmentation": {
                "mode": "ABC_XYZ",
                "abc": segmentation["abc"],
                "xyz": segmentation["xyz"],
                "ved": segmentation["ved"],
            },
            "policy_matrix": value["policy_matrix"],
        }
    )
    require(
        segmentation["abc"]["basis"] == "REVENUE",
        "CLASSIFICATION_ABC_BASIS_UNSUPPORTED",
    )
    fsn = segmentation["fsn"]
    require(
        type(fsn) is dict
        and set(fsn)
        == {
            "enabled",
            "application_mode",
            "source_contract_key",
            "lookback_weeks",
            "fast_min_active_week_ratio",
            "non_moving_weeks",
        }
        and _axis_switch_valid(fsn)
        and fsn["source_contract_key"] == "CLOSED_DEMAND_MOVEMENT_V1"
        and type(fsn["lookback_weeks"]) is int
        and 13 <= fsn["lookback_weeks"] <= 156
        and Decimal("0") < Decimal(str(fsn["fast_min_active_week_ratio"])) <= Decimal("1")
        and type(fsn["non_moving_weeks"]) is int
        and 4 <= fsn["non_moving_weeks"] <= fsn["lookback_weeks"],
        "CLASSIFICATION_CONFIG_INVALID",
    )
    sde = segmentation["sde"]
    require(
        type(sde) is dict
        and set(sde)
        == {
            "enabled",
            "application_mode",
            "source_contract_key",
            "lookback_weeks",
            "difficult_min_lead_time_days",
            "scarce_min_lead_time_days",
            "scarce_max_supplier_count",
            "scarce_max_on_time_rate",
            "difficult_max_on_time_rate",
            "min_receipt_sample_count",
        }
        and _axis_switch_valid(sde)
        and sde["source_contract_key"] == "SUPPLIER_LEAD_TIME_V1"
        and type(sde["lookback_weeks"]) is int
        and 13 <= sde["lookback_weeks"] <= 156
        and Decimal("0")
        <= Decimal(str(sde["difficult_min_lead_time_days"]))
        < Decimal(str(sde["scarce_min_lead_time_days"]))
        and type(sde["scarce_max_supplier_count"]) is int
        and 1 <= sde["scarce_max_supplier_count"] <= 100
        and Decimal("0")
        <= Decimal(str(sde["scarce_max_on_time_rate"]))
        < Decimal(str(sde["difficult_max_on_time_rate"]))
        <= Decimal("1")
        and type(sde["min_receipt_sample_count"]) is int
        and 1 <= sde["min_receipt_sample_count"] <= 10_000,
        "CLASSIFICATION_CONFIG_INVALID",
    )
    hml = segmentation["hml"]
    require(
        type(hml) is dict
        and set(hml)
        == {
            "enabled",
            "application_mode",
            "source_contract_key",
            "basis",
            "threshold_mode",
            "medium_min_percentile",
            "high_min_percentile",
            "currency",
            "max_source_age_weeks",
        }
        and _axis_switch_valid(hml)
        and hml["source_contract_key"] == "INVENTORY_UNIT_COST_V1"
        and hml["basis"] == "UNIT_COST"
        and hml["threshold_mode"] == "PERCENTILE"
        and Decimal("0")
        < Decimal(str(hml["medium_min_percentile"]))
        < Decimal(str(hml["high_min_percentile"]))
        < Decimal("1")
        and type(hml["currency"]) is str
        and len(hml["currency"]) == 3
        and hml["currency"].isupper()
        and type(hml["max_source_age_weeks"]) is int
        and 1 <= hml["max_source_age_weeks"] <= 156,
        "CLASSIFICATION_CONFIG_INVALID",
    )
    plc = segmentation["plc"]
    require(
        type(plc) is dict
        and set(plc)
        == {
            "enabled",
            "application_mode",
            "source_contract_key",
            "introduction_weeks",
            "growth_weeks",
            "decline_horizon_weeks",
        }
        and _axis_switch_valid(plc)
        and plc["application_mode"] == "SHADOW"
        and plc["source_contract_key"] == "SITE_PART_LIFECYCLE_V1"
        and type(plc["introduction_weeks"]) is int
        and type(plc["growth_weeks"]) is int
        and 1 <= plc["introduction_weeks"] <= 52
        and plc["introduction_weeks"] < plc["growth_weeks"] <= 156
        and type(plc["decline_horizon_weeks"]) is int
        and 1 <= plc["decline_horizon_weeks"] <= 104,
        "CLASSIFICATION_CONFIG_INVALID",
    )
    overlays = value["policy_overlays"]
    require(
        type(overlays) is dict
        and set(overlays)
        == {"fsn_order_action", "sde_lead_time_basis", "hml_approval_level", "plc_order_action"},
        "CLASSIFICATION_CONFIG_INVALID",
    )
    require(
        _closed_choices(overlays["fsn_order_action"], {"F", "S", "N"}, {"ALLOW", "REVIEW", "BLOCK"})
        and _closed_choices(overlays["sde_lead_time_basis"], {"S", "D", "E"}, {"P50", "P90"})
        and _closed_choices(
            overlays["hml_approval_level"], {"H", "M", "L"}, {"AUTO", "STANDARD", "HIGH_VALUE"}
        )
        and _closed_choices(
            overlays["plc_order_action"],
            {
                "PRE_LAUNCH",
                "INTRODUCTION",
                "GROWTH",
                "MATURE",
                "DECLINE",
                "SERVICE_ONLY",
                "DISCONTINUED",
            },
            {"ALLOW", "REVIEW", "BLOCK"},
        ),
        "CLASSIFICATION_CONFIG_INVALID",
    )
    return dict(value)


def _axis_switch_valid(value: Mapping[str, Any]) -> bool:
    return (
        type(value.get("enabled")) is bool
        and value.get("application_mode") in {"SHADOW", "OPERATIONAL"}
        and (value.get("enabled") is True or value.get("application_mode") == "SHADOW")
    )


def _closed_choices(value: Any, keys: set[str], choices: set[str]) -> bool:
    return (
        type(value) is dict
        and set(value) == keys
        and all(item in choices for item in value.values())
    )


def _validate_v2_metric_contract(metrics: Sequence[ItemMetric], config: Mapping[str, Any]) -> None:
    """Require explicit axis aggregates instead of reusing the widest V1 window."""

    statuses = {"VERIFIED", "UNVERIFIED", "SYNTHETIC"}
    fsn_enabled = bool(config["segmentation"]["fsn"]["enabled"])
    for metric in metrics:
        require(
            all(
                value is not None
                for value in (
                    metric.abc_source_row_count,
                    metric.abc_invalid_row_count,
                    metric.abc_observed_week_count,
                    metric.abc_revenue,
                    metric.xyz_source_row_count,
                    metric.xyz_invalid_row_count,
                    metric.xyz_observed_week_count,
                    metric.xyz_total_demand,
                    metric.xyz_demand_square_sum,
                )
            ),
            "CLASSIFICATION_V2_AXIS_METRICS_INCOMPLETE",
        )
        require(
            metric.fsn_source_status in statuses
            and metric.sde_source_status in statuses
            and metric.hml_source_status in statuses
            and metric.plc_source_status in statuses,
            "CLASSIFICATION_V2_AXIS_SOURCE_STATUS_INVALID",
        )
        if fsn_enabled:
            require(
                metric.fsn_source_row_count is not None
                and metric.fsn_invalid_row_count is not None,
                "CLASSIFICATION_V2_AXIS_METRICS_INCOMPLETE",
            )
        if metric.plc_source_status == "SYNTHETIC":
            require(
                metric.plc_source_profile_hash is not None,
                "CLASSIFICATION_PLC_PROFILE_HASH_MISSING",
            )
            hash_value(metric.plc_source_profile_hash)


def _apply_seven_axis_results(
    items: Sequence[Mapping[str, Any]],
    metrics: Sequence[ItemMetric],
    *,
    config: Mapping[str, Any],
    as_of_yyyyww: str,
) -> tuple[dict[str, Any], ...]:
    segmentation = config["segmentation"]
    metric_by_item = {metric.item_id: metric for metric in metrics}
    hml_rules = segmentation["hml"]
    verified_costs = [
        metric.unit_cost
        for metric in metrics
        if metric.hml_source_status == "VERIFIED"
        and metric.unit_cost is not None
        and metric.unit_cost.is_finite()
        and metric.unit_cost > 0
        and metric.unit_cost_currency == hml_rules["currency"]
        and _hml_source_age_is_eligible(
            metric.unit_cost_as_of_yyyyww,
            as_of_yyyyww,
            max_source_age_weeks=int(hml_rules["max_source_age_weeks"]),
        )
    ]
    thresholds = hml_thresholds(
        verified_costs,
        medium_percentile=Decimal(str(hml_rules["medium_min_percentile"])),
        high_percentile=Decimal(str(hml_rules["high_min_percentile"])),
    )
    results: list[dict[str, Any]] = []
    for item in items:
        metric = metric_by_item[str(item["item_id"])]
        axis_results = [
            _base_axis_result(
                "ABC",
                item.get("abc_class"),
                classified=item.get("abc_classification_status") == "CLASSIFIED",
                source_contract_key="CLOSED_DEMAND_VALUE_V1",
                reason_code=item.get("abc_unclassified_reason_code"),
            ),
            _base_axis_result(
                "XYZ",
                item.get("xyz_class"),
                classified=item.get("xyz_classification_status") == "CLASSIFIED",
                source_contract_key="CLOSED_DEMAND_VARIABILITY_V1",
                reason_code=item.get("xyz_unclassified_reason_code"),
            ),
            _base_axis_result(
                "VED",
                item.get("ved_class"),
                classified=item.get("ved_class") is not None,
                source_contract_key="APPROVED_VED_ASSIGNMENT_V1",
                reason_code=None,
                enabled=segmentation["ved"]["enabled"],
            ),
            classify_fsn(
                source_row_count=metric.fsn_source_row_count,
                invalid_row_count=metric.fsn_invalid_row_count,
                positive_week_count=metric.positive_week_count,
                last_positive_demand_yyyyww=metric.last_positive_demand_yyyyww,
                source_status=metric.fsn_source_status,
                as_of_yyyyww=as_of_yyyyww,
                rules=segmentation["fsn"],
            ),
            classify_sde(
                supplier_count=metric.supplier_count,
                planning_lead_time_days=metric.planning_lead_time_days,
                p50_lead_time_days=metric.p50_lead_time_days,
                p90_lead_time_days=metric.p90_lead_time_days,
                on_time_delivery_rate=metric.on_time_delivery_rate,
                receipt_sample_count=metric.receipt_sample_count,
                source_status=metric.sde_source_status,
                rules=segmentation["sde"],
            ),
            classify_hml(
                unit_cost=metric.unit_cost,
                unit_cost_currency=metric.unit_cost_currency,
                thresholds=thresholds,
                source_status=metric.hml_source_status,
                rules=hml_rules,
                unit_cost_as_of_yyyyww=metric.unit_cost_as_of_yyyyww,
                as_of_yyyyww=as_of_yyyyww,
            ),
            classify_plc(
                introduced_yyyyww=metric.introduced_yyyyww,
                production_end_yyyyww=metric.production_end_yyyyww,
                service_end_yyyyww=metric.service_end_yyyyww,
                lifecycle_status_cd=metric.lifecycle_status_cd,
                source_status=metric.plc_source_status,
                as_of_yyyyww=as_of_yyyyww,
                rules=segmentation["plc"],
                source_profile_hash=metric.plc_source_profile_hash,
            ),
        ]
        operational = [
            result for result in axis_results if result["application_mode"] == "OPERATIONAL"
        ]
        results.append(
            {
                **item,
                "axis_results": axis_results,
                "display_segment_code": display_segment_code(axis_results),
                "seven_axis_operational_eligible": all(
                    result["status"] == "CLASSIFIED" for result in operational
                ),
                "policy_effective_axes": [
                    result["axis"] for result in operational if result["policy_effective"]
                ],
            }
        )
    return tuple(results)


def _base_axis_result(
    axis: str,
    class_code: str | None,
    *,
    classified: bool,
    source_contract_key: str,
    reason_code: str | None,
    enabled: bool = True,
) -> dict[str, Any]:
    if not enabled:
        return {
            "axis": axis,
            "status": "NOT_APPLICABLE",
            "class_code": None,
            "application_mode": "SHADOW",
            "policy_effective": False,
            "source_contract_key": source_contract_key,
            "evidence": {},
            "reason_code": "AXIS_DISABLED",
        }
    return {
        "axis": axis,
        "status": "CLASSIFIED" if classified else "UNCLASSIFIED",
        "class_code": class_code,
        "application_mode": "OPERATIONAL",
        "policy_effective": classified,
        "source_contract_key": source_contract_key,
        "evidence": {},
        "reason_code": None if classified else reason_code,
    }


def config_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def publication_receipt(
    snapshot: Mapping[str, Any], publication: Mapping[str, Any]
) -> dict[str, Any]:
    receipt = {
        "contract_id": "inventory-classification-snapshot-publication-receipt-v1",
        "publication_status": publication["publication_status"],
        "classification_snapshot_id": publication.get(
            "classification_snapshot_id", snapshot["classification_snapshot_id"]
        ),
        "snapshot_revision": publication.get("snapshot_revision"),
        "scope": snapshot["scope"],
        "as_of_yyyyww": snapshot["as_of_yyyyww"],
        "window_start": snapshot["window_start"],
        "window_end_exclusive": snapshot["window_end_exclusive"],
        "config_revision_id": snapshot["config_revision_id"],
        "config_hash": snapshot["config_hash"],
        "source_revision": snapshot["source_revision"],
        "source_content_hash": snapshot["source_content_hash"],
        "content_hash": snapshot["content_hash"],
        "item_result_contract_version": snapshot["item_result_contract_version"],
        "effective_policy_contract_version": snapshot["effective_policy_contract_version"],
        "effective_policy_content_hash": snapshot["effective_policy_content_hash"],
        "item_result_count": len(snapshot["items"]),
        "ved_assignment_snapshot_id": snapshot["ved_assignment_snapshot_id"],
        "ved_assignment_content_hash": snapshot["ved_assignment_content_hash"],
        "eligible_sku_count": snapshot["eligible_sku_count"],
        "classified_sku_count": snapshot["classified_sku_count"],
        "unclassified_sku_count": snapshot["unclassified_sku_count"],
        "segments": snapshot["segments"],
        "unclassified_reasons": snapshot["unclassified_reasons"],
        "database_writes": publication["publication_status"] == "published",
        "exact_replay": publication["publication_status"] == "exact_replay",
        "run_claimed": False,
    }
    if "actual_close_history_hash" in snapshot:
        receipt["axis_windows"] = snapshot["axis_windows"]
        receipt["actual_close_window_start_yyyyww"] = snapshot["actual_close_window_start_yyyyww"]
        receipt["actual_close_history_hash"] = snapshot["actual_close_history_hash"]
    return receipt


def classification_window(value: str, lookback_weeks: int) -> tuple[date, date]:
    require(lookback_weeks >= 1, "CLASSIFICATION_LOOKBACK_INVALID")
    try:
        end_exclusive = date.fromisocalendar(int(value[:4]), int(value[4:]), 1) + timedelta(weeks=1)
        return end_exclusive - timedelta(weeks=lookback_weeks), end_exclusive
    except ValueError as exc:
        raise InventoryInputError("CLASSIFICATION_ACTUAL_WEEK_INVALID") from exc


def _snapshot_hash(value: Any) -> str:
    document = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(document.encode("utf-8")).hexdigest()


def _v1_source_content_hash(rows: Sequence[ItemMetric]) -> str:
    """Preserve the published 1.1.0 source-hash projection exactly."""

    return _snapshot_hash(
        [
            {
                "item_id": row.item_id,
                "source_row_count": row.source_row_count,
                "invalid_row_count": row.invalid_row_count,
                "observed_week_count": row.observed_week_count,
                "total_demand": _decimal_text(row.total_demand),
                "demand_square_sum": _decimal_text(row.demand_square_sum),
                "revenue": _decimal_text(row.revenue),
            }
            for row in sorted(rows, key=lambda item: item.item_id)
        ]
    )


def _decimal_text(value: Decimal, *, places: int | None = None) -> str:
    normalized = value.quantize(Decimal(1).scaleb(-places)) if places is not None else value
    return format(normalized, "f")


def _optional_decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else _decimal_text(value)


def _axis_count(value: int | None, fallback: int) -> int:
    return fallback if value is None else value


def _combined_axis_reason(abc_reason: str | None, xyz_reason: str | None) -> str:
    if abc_reason == "INVALID_ABC_SOURCE_RECORD" and xyz_reason == "INVALID_XYZ_SOURCE_RECORD":
        return "INVALID_SOURCE_RECORD"
    if abc_reason == "INSUFFICIENT_ABC_HISTORY" and xyz_reason == "INSUFFICIENT_XYZ_HISTORY":
        return "INSUFFICIENT_DEMAND_HISTORY"
    return abc_reason or xyz_reason or "CLASSIFICATION_SOURCE_INVALID"


def _axis_window_evidence(
    config: Mapping[str, Any], as_of_yyyyww: str
) -> dict[str, dict[str, Any]]:
    segmentation = config["segmentation"]
    axes: list[tuple[str, int]] = [
        ("ABC", int(segmentation["abc"]["lookback_weeks"])),
        ("XYZ", int(segmentation["xyz"]["lookback_weeks"])),
    ]
    if segmentation["mode"] == "SEVEN_AXIS":
        axes.extend(
            (axis.upper(), int(segmentation[axis]["lookback_weeks"]))
            for axis in ("fsn", "sde")
            if segmentation[axis]["enabled"]
        )
    result: dict[str, dict[str, Any]] = {}
    for axis, lookback_weeks in axes:
        start, end_exclusive = classification_window(as_of_yyyyww, lookback_weeks)
        result[axis] = {
            "lookback_weeks": lookback_weeks,
            "window_start": start.isoformat(),
            "window_end_exclusive": end_exclusive.isoformat(),
        }
    return result


def _hml_source_age_is_eligible(
    source_as_of_yyyyww: str | None,
    as_of_yyyyww: str,
    *,
    max_source_age_weeks: int,
) -> bool:
    if source_as_of_yyyyww is None:
        return False
    try:
        source_week = yyyyww(source_as_of_yyyyww)
        target_week = yyyyww(as_of_yyyyww)
        source_monday = date.fromisocalendar(int(source_week[:4]), int(source_week[4:]), 1)
        target_monday = date.fromisocalendar(int(target_week[:4]), int(target_week[4:]), 1)
    except (InventoryInputError, ValueError):
        return False
    age_weeks = (target_monday - source_monday).days // 7
    return 0 <= age_weeks <= max_source_age_weeks


def _ved_result(
    item_id: str,
    *,
    enabled: bool,
    default_class: str,
    assignments: Mapping[str, VedAssignment],
) -> tuple[str | None, str, str | None]:
    if not enabled:
        return None, "DISABLED", None
    assignment = assignments.get(item_id)
    if assignment is None:
        return default_class, "DEFAULT_CLASS", None
    return assignment.ved_class, "ASSIGNMENT_SNAPSHOT", assignment.reason


def _item_identifier(value: Any) -> str:
    return item_identifier(value)

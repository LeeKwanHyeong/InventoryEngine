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

from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    canonical_json,
    hash_value,
    identifier,
    require,
    yyyyww,
)


SEGMENTS = ("AX", "AY", "AZ", "BX", "BY", "BZ", "CX", "CY", "CZ")
SNAPSHOT_NAMESPACE = uuid.UUID("f9821dd0-652d-4fb1-bc75-f88c2b68fe8b")
CONFIG_SCHEMA_ID = "urn:dsai:inventory-engine-config-values:1.1.0"
CONFIG_SCHEMA_VERSION = "1.1.0"
CONFIG_SCHEMA_HASH = "b7841bc8cbe996903a7a9b2c1bb70a0a2dc4f283019fdde5931b54c788627b04"


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
    lookback_weeks: int,
    ved_enabled: bool = False,
    ved_default_class: str = "D",
    ved_assignments: Sequence[VedAssignment] = (),
) -> ClassificationResult:
    """Classify eligible item metrics without inventing missing evidence."""

    require(bool(rows) and lookback_weeks >= 1, "CLASSIFICATION_SOURCE_EMPTY")
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
    valid: list[tuple[ItemMetric, Decimal]] = []
    reasons: Counter[str] = Counter()
    reason_by_item: dict[str, str] = {}
    for row in sorted(rows, key=lambda item: item.item_id):
        _item_identifier(row.item_id)
        require(row.item_id not in seen, "CLASSIFICATION_SOURCE_ITEM_INVALID")
        seen.add(row.item_id)
        require(
            min(
                row.source_row_count,
                row.invalid_row_count,
                row.observed_week_count,
            )
            >= 0,
            "CLASSIFICATION_SOURCE_COUNT_INVALID",
        )
        require(
            all(
                value.is_finite()
                for value in (row.total_demand, row.demand_square_sum, row.revenue)
            ),
            "CLASSIFICATION_SOURCE_VALUE_INVALID",
        )
        if row.invalid_row_count > 0:
            reason_by_item[row.item_id] = "INVALID_SOURCE_RECORD"
            continue
        if row.source_row_count == 0 or row.observed_week_count == 0:
            reason_by_item[row.item_id] = "INSUFFICIENT_DEMAND_HISTORY"
            continue
        if row.total_demand <= 0:
            reason_by_item[row.item_id] = "ZERO_MEAN_DEMAND"
            continue
        if row.revenue <= 0:
            reason_by_item[row.item_id] = "MISSING_REVENUE"
            continue

        mean = row.total_demand / Decimal(lookback_weeks)
        variance = max(
            Decimal("0"),
            row.demand_square_sum / Decimal(lookback_weeks) - mean * mean,
        )
        valid.append((row, variance / (mean * mean)))

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

    total_revenue = sum((row.revenue for row, _ in valid), Decimal("0"))
    require(total_revenue > 0, "CLASSIFICATION_REVENUE_EMPTY")

    segment_counts = Counter({segment: 0 for segment in SEGMENTS})
    segment_revenue = {segment: Decimal("0") for segment in SEGMENTS}
    item_results: list[dict[str, Any]] = []
    cumulative_revenue = Decimal("0")
    for row, cv2 in sorted(valid, key=lambda item: (-item[0].revenue, item[0].item_id)):
        share_before_item = cumulative_revenue / total_revenue
        abc_class = (
            "A"
            if share_before_item < abc_a_cumulative_share
            else "B"
            if share_before_item < abc_b_cumulative_share
            else "C"
        )
        cumulative_revenue += row.revenue
        xyz_class = "X" if cv2 <= xyz_x_max_cv2 else "Y" if cv2 <= xyz_y_max_cv2 else "Z"
        segment = f"{abc_class}{xyz_class}"
        segment_counts[segment] += 1
        segment_revenue[segment] += row.revenue
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
                "abc_class": abc_class,
                "xyz_class": xyz_class,
                "ved_class": ved_class,
                "segment_key": segment,
                "final_segment_key": (
                    f"{segment}-{ved_class}" if ved_class is not None else segment
                ),
                "ved_assignment_source": ved_source,
                "ved_assignment_reason": ved_reason,
                "unclassified_reason_code": None,
                "demand_cv2": _decimal_text(cv2, places=12),
                "revenue": _decimal_text(row.revenue),
                "cumulative_revenue_share_before": _decimal_text(
                    share_before_item,
                    places=12,
                ),
            }
        )

    metric_by_item = {row.item_id: row for row in rows}
    for item_id, reason in sorted(reason_by_item.items()):
        row = metric_by_item[item_id]
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
                "abc_class": None,
                "xyz_class": None,
                "ved_class": ved_class,
                "segment_key": None,
                "final_segment_key": None,
                "ved_assignment_source": ved_source,
                "ved_assignment_reason": ved_reason,
                "unclassified_reason_code": reason,
                "demand_cv2": None,
                "revenue": _decimal_text(row.revenue),
                "cumulative_revenue_share_before": None,
            }
        )

    segments = tuple(
        {
            "segment_key": segment,
            "sku_count": segment_counts[segment],
            "revenue_share": _decimal_text(
                segment_revenue[segment] / total_revenue,
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
        }
        for row in sorted(rows, key=lambda item: item.item_id)
    ]
    return ClassificationResult(
        eligible_sku_count=len(rows),
        classified_sku_count=len(valid),
        unclassified_sku_count=sum(reasons.values()),
        segments=segments,
        unclassified_reasons=unclassified,
        items=tuple(sorted(item_results, key=lambda item: item["item_id"])),
        source_content_hash=_snapshot_hash(source_rows),
    )


def build_snapshot(inputs: ClassificationInputs) -> dict[str, Any]:
    require(
        (
            inputs.config_schema_id,
            inputs.config_schema_version,
            inputs.config_schema_hash,
        )
        == (CONFIG_SCHEMA_ID, CONFIG_SCHEMA_VERSION, CONFIG_SCHEMA_HASH),
        "CLASSIFICATION_CONFIG_SCHEMA_MISMATCH",
    )
    config = validate_config(inputs.config_values)
    require(config_hash(config) == inputs.config_hash, "CLASSIFICATION_CONFIG_HASH_MISMATCH")
    hash_value(inputs.source_manifest_sha256)
    as_of_yyyyww = yyyyww(inputs.actual_yyyyww)

    abc = config["segmentation"]["abc"]
    xyz = config["segmentation"]["xyz"]
    ved = config["segmentation"]["ved"]
    require(abc["basis"] == "REVENUE", "CLASSIFICATION_ABC_BASIS_UNSUPPORTED")
    lookback_weeks = max(int(abc["lookback_weeks"]), int(xyz["lookback_weeks"]))
    result = classify_items(
        inputs.metrics,
        abc_a_cumulative_share=Decimal(str(abc["a_cumulative_share"])),
        abc_b_cumulative_share=Decimal(str(abc["b_cumulative_share"])),
        xyz_x_max_cv2=Decimal(str(xyz["x_max_cv2"])),
        xyz_y_max_cv2=Decimal(str(xyz["y_max_cv2"])),
        lookback_weeks=lookback_weeks,
        ved_enabled=ved["enabled"],
        ved_default_class=ved["default_class"],
        ved_assignments=inputs.ved_assignments,
    )
    start_monday, end_monday = classification_window(as_of_yyyyww, lookback_weeks)
    source_revision = (
        f"ACTUAL-CLOSE-{as_of_yyyyww}-R{inputs.closure_revision_no}-{inputs.publication_id}"
    )
    body = {
        "tenant_id": inputs.tenant_id,
        "project_id": inputs.scope.project_id,
        "scope": inputs.scope.to_dict(),
        "config_id": inputs.config_id,
        "config_revision_id": inputs.config_revision_id,
        "config_hash": inputs.config_hash,
        "source_revision": source_revision,
        "source_content_hash": result.source_content_hash,
        "source_manifest_sha256": inputs.source_manifest_sha256,
        "source_relation": inputs.source_relation,
        "as_of_yyyyww": as_of_yyyyww,
        "window_start": start_monday.isoformat(),
        "window_end_exclusive": end_monday.isoformat(),
        "segmentation_type": "ABC_XYZ",
        "abc_basis": abc["basis"],
        "abc_lookback_weeks": int(abc["lookback_weeks"]),
        "xyz_metric": xyz["metric"],
        "xyz_lookback_weeks": int(xyz["lookback_weeks"]),
        "service_level_type": config["policy_matrix"]["service_level_type"],
        "item_result_contract_version": "1.0.0",
        "ved_assignment_snapshot_id": (
            ved["assignment_snapshot_id"] if ved["enabled"] else None
        ),
        "ved_assignment_content_hash": (
            ved["assignment_content_hash"] if ved["enabled"] else None
        ),
        "eligible_sku_count": result.eligible_sku_count,
        "classified_sku_count": result.classified_sku_count,
        "unclassified_sku_count": result.unclassified_sku_count,
        "segments": result.segments,
        "unclassified_reasons": result.unclassified_reasons,
        "items": result.items,
    }
    body["content_hash"] = _snapshot_hash(body)
    body["classification_snapshot_id"] = str(uuid.uuid5(SNAPSHOT_NAMESPACE, body["content_hash"]))
    return body


def validate_config(value: Mapping[str, Any]) -> dict[str, Any]:
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


def config_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def publication_receipt(
    snapshot: Mapping[str, Any], publication: Mapping[str, Any]
) -> dict[str, Any]:
    return {
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


def _decimal_text(value: Decimal, *, places: int | None = None) -> str:
    normalized = value.quantize(Decimal(1).scaleb(-places)) if places is not None else value
    return format(normalized, "f")


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
    require(
        isinstance(value, str)
        and bool(value.strip())
        and value == value.strip()
        and len(value) <= 160
        and "\x00" not in value,
        "CLASSIFICATION_SOURCE_ITEM_INVALID",
    )
    return value

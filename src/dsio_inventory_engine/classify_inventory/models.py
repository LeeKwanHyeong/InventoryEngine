"""Classification metrics and compatibility result types; no Source or policy access."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from dsio_inventory_engine.inventory_contracts.values import item_identifier, require


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
        item_identifier(self.item_id)
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

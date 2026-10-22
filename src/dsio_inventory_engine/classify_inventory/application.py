"""Deterministic ABC-XYZ classification and append-only publication orchestration."""

from __future__ import annotations

import hashlib
import uuid
import copy
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Mapping, Protocol, Sequence

from dsio_inventory_engine.classify_inventory.abc_xyz import AbcXyzSegmentation
from dsio_inventory_engine.classify_inventory.models import (
    ClassificationResult,
    ItemMetric,
    VedAssignment,
)
from dsio_inventory_engine.classify_inventory._serialization import (
    _decimal_text,
    _snapshot_hash,
)

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
from dsio_inventory_engine.inventory_contracts.policy_search import normalize_policy_search


SEGMENTS = ("AX", "AY", "AZ", "BX", "BY", "BZ", "CX", "CY", "CZ")
SNAPSHOT_NAMESPACE = uuid.UUID("f9821dd0-652d-4fb1-bc75-f88c2b68fe8b")
CONFIG_SCHEMA_ID = "urn:dsai:inventory-engine-config-values:1.1.0"
CONFIG_SCHEMA_VERSION = "1.1.0"
CONFIG_SCHEMA_HASH = "b7841bc8cbe996903a7a9b2c1bb70a0a2dc4f283019fdde5931b54c788627b04"
CONFIG_SCHEMA_V2_ID = "urn:dsai:inventory-engine-config-values:2.0.0"
CONFIG_SCHEMA_V2_VERSION = "2.0.0"
CONFIG_SCHEMA_V2_HASH = "77a1ccc984d3a6296c963e09460d8ff2e252debc0e2ca6179ec2b2fcacd67e9e"
CONFIG_SCHEMA_V21_ID = "urn:dsai:inventory-engine-config-values:2.1.0"
CONFIG_SCHEMA_V21_VERSION = "2.1.0"
CONFIG_SCHEMA_V21_HASH = "f39e51436cc6efdec2a4c9cb5a9f1508db7aa269f09a3b0d05c0680f51723dae"


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
    decision_yyyyww: str | None = None
    history_source_type: str = "ACTUAL_CLOSE"


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
        decision_yyyyww: str | None = None,
    ) -> dict[str, Any]:
        identifier(approved_by)
        if decision_yyyyww is not None:
            previous_actual_week(decision_yyyyww)
        inputs = await self.source.load_inputs(scope)
        if decision_yyyyww is not None:
            require(
                inputs.scope == scope and inputs.decision_yyyyww == decision_yyyyww,
                "CLASSIFICATION_DECISION_WEEK_MISMATCH",
            )
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
    """Compatibility entrypoint; Snapshot/FSN/effective policy orchestration stays here."""
    return AbcXyzSegmentation().classify(
        rows,
        abc_a_cumulative_share=abc_a_cumulative_share,
        abc_b_cumulative_share=abc_b_cumulative_share,
        xyz_x_max_cv2=xyz_x_max_cv2,
        xyz_y_max_cv2=xyz_y_max_cv2,
        lookback_weeks=lookback_weeks,
        abc_lookback_weeks=abc_lookback_weeks,
        xyz_lookback_weeks=xyz_lookback_weeks,
        ved_enabled=ved_enabled,
        ved_default_class=ved_default_class,
        ved_assignments=ved_assignments,
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
            (CONFIG_SCHEMA_V21_ID, CONFIG_SCHEMA_V21_VERSION, CONFIG_SCHEMA_V21_HASH),
        },
        "CLASSIFICATION_CONFIG_SCHEMA_MISMATCH",
    )
    config = validate_config(inputs.config_values)
    seven_axis_mode = config["segmentation"]["mode"] == "SEVEN_AXIS"
    expected_schema_identity = (
        (CONFIG_SCHEMA_V21_ID, CONFIG_SCHEMA_V21_VERSION, CONFIG_SCHEMA_V21_HASH)
        if "policy_search" in config
        else (CONFIG_SCHEMA_V2_ID, CONFIG_SCHEMA_V2_VERSION, CONFIG_SCHEMA_V2_HASH)
        if seven_axis_mode
        else (CONFIG_SCHEMA_ID, CONFIG_SCHEMA_VERSION, CONFIG_SCHEMA_HASH)
    )
    require(
        schema_identity == expected_schema_identity,
        "CLASSIFICATION_CONFIG_SCHEMA_MISMATCH",
    )
    require(config_hash(config) == inputs.config_hash, "CLASSIFICATION_CONFIG_HASH_MISMATCH")
    hash_value(inputs.source_manifest_sha256)
    history_yyyyww = yyyyww(inputs.actual_yyyyww)
    as_of_yyyyww = (
        yyyyww(inputs.decision_yyyyww) if inputs.decision_yyyyww is not None else history_yyyyww
    )
    if inputs.decision_yyyyww is not None:
        require(
            history_yyyyww == previous_actual_week(as_of_yyyyww),
            "CLASSIFICATION_HISTORY_MUST_PRECEDE_DECISION",
        )
    require(
        inputs.history_source_type in {"ACTUAL_CLOSE", "DEVELOPMENT_PINNED"},
        "CLASSIFICATION_HISTORY_SOURCE_TYPE",
    )
    if inputs.history_source_type == "DEVELOPMENT_PINNED":
        require(
            seven_axis_mode
            and inputs.decision_yyyyww is not None
            and inputs.source_relation.startswith("DEVELOPMENT_")
            and all(m.fsn_source_status != "VERIFIED" for m in inputs.metrics),
            "CLASSIFICATION_DEVELOPMENT_HISTORY_NOT_OPERATIONAL_CLOSE",
        )
    elif inputs.decision_yyyyww is not None:
        require(seven_axis_mode, "CLASSIFICATION_DECISION_VERSION_UNSUPPORTED")
        require(
            inputs.source_relation == "dsdm.tb_dyn_demand_dtl"
            and type(inputs.closure_revision_no) is int
            and inputs.closure_revision_no > 0
            and isinstance(inputs.publication_id, str)
            and bool(inputs.publication_id.strip()),
            "CLASSIFICATION_ACTUAL_CLOSE_INVALID",
        )

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
    start_monday, end_monday = classification_window(history_yyyyww, lookback_weeks)
    if seven_axis_mode:
        lineage_start, _ = classification_window(history_yyyyww, lineage_lookback_weeks)
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
    axis_windows = _axis_window_evidence(config, history_yyyyww)
    source_revision = (
        (
            "DEVELOPMENT-PINNED-WINDOW-"
            if inputs.history_source_type == "DEVELOPMENT_PINNED"
            else "ACTUAL-CLOSE-DECISION-WINDOW-"
            if inputs.decision_yyyyww is not None
            else "ACTUAL-CLOSE-WINDOW-"
        )
        + f"{inputs.actual_close_window_start_yyyyww}-{history_yyyyww}-"
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
        if "policy_search" in value:
            base = copy.deepcopy(
                _validate_v2_config(
                    {key: item for key, item in value.items() if key != "policy_search"}
                )
            )
            # V2.1 mirrors the producer's typed JSON. Do not change historical
            # V1/V2 hash semantics: 28 and 28.0 normalize only in the new contract.
            for axis, fields in {
                "abc": ("a_cumulative_share", "b_cumulative_share"),
                "xyz": ("x_max_cv2", "y_max_cv2"),
                "fsn": ("fast_min_active_week_ratio",),
                "sde": (
                    "difficult_min_lead_time_days",
                    "scarce_min_lead_time_days",
                    "scarce_max_on_time_rate",
                    "difficult_max_on_time_rate",
                ),
                "hml": ("medium_min_percentile", "high_min_percentile"),
            }.items():
                for field in fields:
                    number = base["segmentation"][axis][field]
                    require(type(number) in (int, float), "CLASSIFICATION_CONFIG_INVALID")
                    base["segmentation"][axis][field] = float(number)
            for cell in base["policy_matrix"]["cells"]:
                require(
                    type(cell["target_service_level"]) in (int, float),
                    "CLASSIFICATION_CONFIG_INVALID",
                )
                cell["target_service_level"] = float(cell["target_service_level"])
            for grade, number in base["policy_matrix"]["ved_service_level_floor"].items():
                require(type(number) in (int, float), "CLASSIFICATION_CONFIG_INVALID")
                base["policy_matrix"]["ved_service_level_floor"][grade] = float(number)
            try:
                base["policy_search"] = normalize_policy_search(value["policy_search"])
            except ValueError as exc:
                raise InventoryInputError(str(exc)) from exc
            return base
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
    if "policy_search" in value:
        value = validate_config(value)
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


def previous_actual_week(decision_yyyyww: str) -> str:
    """Resolve the exact prior ISO week, including ISO-year boundaries."""

    yyyyww(decision_yyyyww)
    try:
        monday = date.fromisocalendar(int(decision_yyyyww[:4]), int(decision_yyyyww[4:]), 1)
        return (monday - timedelta(weeks=1)).strftime("%G%V")
    except ValueError as exc:
        raise InventoryInputError("CLASSIFICATION_DECISION_WEEK_INVALID") from exc


def classification_window(value: str, lookback_weeks: int) -> tuple[date, date]:
    require(lookback_weeks >= 1, "CLASSIFICATION_LOOKBACK_INVALID")
    try:
        end_exclusive = date.fromisocalendar(int(value[:4]), int(value[4:]), 1) + timedelta(weeks=1)
        return end_exclusive - timedelta(weeks=lookback_weeks), end_exclusive
    except ValueError as exc:
        raise InventoryInputError("CLASSIFICATION_ACTUAL_WEEK_INVALID") from exc


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


def _item_identifier(value: Any) -> str:
    return item_identifier(value)

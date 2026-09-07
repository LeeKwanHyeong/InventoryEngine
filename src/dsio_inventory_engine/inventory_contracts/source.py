"""Pinned source interchange. Source hashes and Canonical hashes are distinct."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from .canonical import CONTEXT_FIELDS, ROW_FIELDS, METADATA_FIELDS
from .values import (
    canonical_json,
    choice,
    day,
    digest,
    hash_value,
    identifier,
    integer,
    read_json,
    records,
    require,
    shape,
    text,
    yyyyww,
)

ADAPTER_KINDS = {
    "CANONICAL_V1": set(ROW_FIELDS),
    "DSDM_MASTER_V1": {"master"},
    "DSDM_FORECAST_V1": {"forecast"},
    "ERP_POSITION_V1": {"inventory"},
    "UNVERIFIED_DUE_IN_V1": {"receipts"},
    "SOURCE_POLICY_V1": {"policies"},
}
SELECTOR_FIELDS = {
    k: identifier
    for k in (
        "tenant_id",
        "project_id",
        "demand_run_id",
        "company_cd",
        "subs_cd",
        "plant_cd",
        "site_cd",
        "plan_id",
        "target_cd",
        "bukt_cd",
        "snrio_id",
        "snrio_grp",
        "regul_type",
        "forecast_stat_cd",
    )
} | {
    "plan_yyyyww": yyyyww,
    "fcst_w0_yyyyww": yyyyww,
    "forecast_from_yyyyww": yyyyww,
    "forecast_to_yyyyww": yyyyww,
}


def forecast_selector(value: Any) -> dict:
    result = shape(value, SELECTOR_FIELDS)
    require(
        result["forecast_from_yyyyww"] <= result["forecast_to_yyyyww"], "INVALID_FORECAST_RANGE"
    )
    try:
        require(str(UUID(result["demand_run_id"])) == result["demand_run_id"], "INVALID_RUN_UUID")
    except ValueError:
        require(False, "INVALID_RUN_UUID")
    return result


def object_value(value: Any) -> dict:
    require(type(value) is dict, "JSON_OBJECT_REQUIRED")
    return read_json(canonical_json(value))


def raw_rows(value: Any) -> list[dict]:
    require(type(value) is list and len(value) <= 100_000, "SOURCE_ROW_LIMIT")
    return sorted([object_value(row) for row in value], key=canonical_json)


def binding(value: Any) -> dict:
    return shape(value, {"snapshot_id": identifier, "content_hash": hash_value})


@dataclass(frozen=True)
class SourceInputRequest:
    document: str

    @classmethod
    def from_dict(cls, value: dict) -> "SourceInputRequest":
        data = shape(
            value,
            {
                "contract_id": choice("io-source-input-v1"),
                "contract_version": choice("1.0.0"),
                "context": lambda v: shape(v, CONTEXT_FIELDS),
                "quantity_rules": lambda v: records(
                    v,
                    {
                        "uom": identifier,
                        "scale": integer,
                        "tolerance_qty": text,
                        "approval_reference": text,
                    },
                    100,
                ),
                "source_bindings": lambda v: shape(v, {k: binding for k in ROW_FIELDS}),
            },
        )
        require(
            len({b["snapshot_id"] for b in data["source_bindings"].values()}) == 8,
            "DUPLICATE_SOURCE_SNAPSHOT_ID",
        )
        return cls(canonical_json(data))

    def to_dict(self) -> dict:
        return read_json(self.document)


@dataclass(frozen=True)
class SourceSnapshot:
    document: str

    @classmethod
    def from_dict(cls, value: dict) -> "SourceSnapshot":
        data = shape(
            value,
            {
                "contract_id": choice("io-source-snapshot-v1"),
                "snapshot_type": choice(*ROW_FIELDS),
                "snapshot_id": identifier,
                "content_hash": hash_value,
                "adapter": choice(*ADAPTER_KINDS),
                "origin_type": choice(
                    "ACTUAL_SOURCE", "SYNTHETIC_SOURCE", "UNKNOWN_SOURCE", "DEVELOPMENT_FIXTURE"
                ),
                "source_reference": text,
                "source_as_of_date": day,
                "status": choice("COLLECTING", "SEALED", "REJECTED", "SUPERSEDED"),
                "row_count": integer,
                "metadata": object_value,
                "semantics": object_value,
                "rows": raw_rows,
            },
        )
        require(
            data["snapshot_type"] in ADAPTER_KINDS[data["adapter"]], "SOURCE_ADAPTER_KIND_MISMATCH"
        )
        shape(data["metadata"], METADATA_FIELDS[data["snapshot_type"]])
        require(data["row_count"] == len(data["rows"]), "SOURCE_ROW_COUNT_MISMATCH")
        require(data["content_hash"] == source_hash(data), "SOURCE_HASH_MISMATCH")
        return cls(canonical_json(data))

    def to_dict(self) -> dict:
        return read_json(self.document)


def source_hash(data: dict) -> str:
    """Hash the complete source attestation incl. state/semantics; order independent."""
    return digest(
        {
            **{k: v for k, v in data.items() if k != "content_hash"},
            "rows": sorted(data["rows"], key=canonical_json),
        }
    )

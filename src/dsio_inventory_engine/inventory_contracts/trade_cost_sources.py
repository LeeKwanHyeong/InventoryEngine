"""Approval contract for authoritative Trade Cost source collection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .trade_cost import V1_IMPORT_COUNTRIES, V1_SCOPE
from .values import (
    canonical_json,
    choice,
    day,
    digest,
    hash_value,
    identifier,
    integer,
    optional,
    read_json,
    require,
    shape,
    text,
    timestamp,
)


SOURCE_COLLECTION_MANIFEST_ID = "io-trade-cost-source-collection-manifest-v1"
SOURCE_COLLECTION_CONTRACT_VERSION = "1.0.0"
SOURCE_DOMAINS = (
    "CUSTOMS_FX",
    "CUSTOMS_TARIFF",
    "IMPORT_TAX_PROFILE",
    "ITEM_CLASSIFICATION",
    "ITEM_ORIGIN",
    "ITEM_PHYSICAL_ATTRIBUTE",
    "LANE_CHARGE",
    "PREFERENTIAL_ORIGIN_EVIDENCE",
    "PURCHASE_SHIPMENT",
)
DESTINATION_DOMAINS = frozenset(
    {
        "CUSTOMS_FX",
        "CUSTOMS_TARIFF",
        "IMPORT_TAX_PROFILE",
        "ITEM_CLASSIFICATION",
        "LANE_CHARGE",
        "PREFERENTIAL_ORIGIN_EVIDENCE",
    }
)
ORIGIN_DOMAINS = frozenset({"ITEM_ORIGIN", "ITEM_PHYSICAL_ATTRIBUTE", "PURCHASE_SHIPMENT"})


def _optional_text(value: Any) -> str | None:
    return None if value is None else text(value)


def _optional_day(value: Any) -> str | None:
    return None if value is None else day(value)


def _optional_hash(value: Any) -> str | None:
    return None if value is None else hash_value(value)


def _countries(value: Any) -> list[str]:
    require(type(value) is list and bool(value), "SOURCE_JURISDICTIONS_REQUIRED")
    result = []
    for country in value:
        require(
            type(country) is str and country in {"KR", *V1_IMPORT_COUNTRIES},
            "SOURCE_JURISDICTION_INVALID",
        )
        result.append(country)
    require(len(result) == len(set(result)), "SOURCE_JURISDICTION_DUPLICATE")
    return sorted(result)


def _sites(value: Any) -> list[str]:
    require(type(value) is list and bool(value), "SOURCE_DESTINATION_SITES_REQUIRED")
    result = [identifier(site) for site in value]
    require(len(result) == len(set(result)), "SOURCE_DESTINATION_SITE_DUPLICATE")
    return sorted(result)


ENTRY_FIELDS = {
    "entry_id": identifier,
    "source_domain": choice(*SOURCE_DOMAINS),
    "jurisdiction_country_codes": _countries,
    "assignment_status": choice("UNASSIGNED", "PROPOSED", "CONFIRMED"),
    "source_owner": _optional_text,
    "source_system": _optional_text,
    "collection_method": optional(
        choice("DATABASE_READ", "BATCH_API", "FILE_UPLOAD", "BROKER_FILE", "MANUAL_CAPTURE")
    ),
    "collection_cadence": optional(
        choice("DAILY", "WEEKLY", "MONTHLY", "ON_CHANGE", "PER_DECLARATION")
    ),
    "expected_freshness_hours": optional(integer),
    "effective_from": _optional_day,
    "effective_to": _optional_day,
    "raw_content_hash": _optional_hash,
    "evidence_reference": _optional_text,
    "license_review_status": choice("PENDING", "APPROVED", "NOT_REQUIRED"),
    "notes": text,
}


def _entries(value: Any) -> list[dict[str, Any]]:
    require(type(value) is list and len(value) >= len(SOURCE_DOMAINS), "SOURCE_ENTRIES_REQUIRED")
    entries = [shape(row, ENTRY_FIELDS) for row in value]
    require(
        [row["entry_id"] for row in entries] == sorted(row["entry_id"] for row in entries),
        "SOURCE_ENTRIES_NOT_CANONICAL",
    )
    require(
        len(entries) == len({row["entry_id"] for row in entries}),
        "SOURCE_ENTRY_DUPLICATE",
    )
    for row in entries:
        jurisdictions = set(row["jurisdiction_country_codes"])
        if row["source_domain"] in DESTINATION_DOMAINS:
            require(jurisdictions <= V1_IMPORT_COUNTRIES, "DESTINATION_SOURCE_SCOPE_INVALID")
        if row["source_domain"] in ORIGIN_DOMAINS:
            require(jurisdictions == {"KR"}, "ORIGIN_SOURCE_SCOPE_INVALID")
        if row["effective_from"] is not None and row["effective_to"] is not None:
            require(row["effective_from"] < row["effective_to"], "SOURCE_EFFECTIVE_PERIOD_INVALID")
    require(
        {row["source_domain"] for row in entries} == set(SOURCE_DOMAINS),
        "SOURCE_DOMAIN_COVERAGE_INVALID",
    )
    for domain in DESTINATION_DOMAINS:
        covered = {
            country
            for row in entries
            if row["source_domain"] == domain
            for country in row["jurisdiction_country_codes"]
        }
        require(covered == V1_IMPORT_COUNTRIES, "DESTINATION_SOURCE_COVERAGE_INVALID")
    return entries


@dataclass(frozen=True)
class TradeCostSourceCollectionManifest:
    document: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TradeCostSourceCollectionManifest":
        data = shape(
            value,
            {
                "contract_id": choice(SOURCE_COLLECTION_MANIFEST_ID),
                "contract_version": choice(SOURCE_COLLECTION_CONTRACT_VERSION),
                "scope": lambda item: shape(
                    item,
                    {
                        "company_cd": identifier,
                        "subs_cd": identifier,
                        "origin_site_cd": identifier,
                        "destination_site_cds": _sites,
                    },
                ),
                "manifest_status": choice("DRAFT", "APPROVED"),
                "collected_at": timestamp,
                "entries": _entries,
                "approved_by": _optional_text,
                "approved_at": optional(timestamp),
                "content_hash": hash_value,
            },
        )
        require(data["scope"] == V1_SCOPE, "TRADE_COST_V1_SCOPE_MISMATCH")
        if data["manifest_status"] == "APPROVED":
            require(data["approved_by"] is not None, "SOURCE_MANIFEST_APPROVER_REQUIRED")
            require(data["approved_at"] is not None, "SOURCE_MANIFEST_APPROVED_AT_REQUIRED")
            for row in data["entries"]:
                require(row["assignment_status"] == "CONFIRMED", "SOURCE_OWNER_NOT_CONFIRMED")
                for field in (
                    "source_owner",
                    "source_system",
                    "collection_method",
                    "collection_cadence",
                    "expected_freshness_hours",
                    "raw_content_hash",
                    "evidence_reference",
                ):
                    require(row[field] is not None, f"SOURCE_APPROVAL_{field.upper()}_REQUIRED")
                require(row["license_review_status"] != "PENDING", "SOURCE_LICENSE_REVIEW_PENDING")
        else:
            require(
                data["approved_by"] is None and data["approved_at"] is None,
                "DRAFT_SOURCE_MANIFEST_HAS_APPROVAL",
            )
        require(
            data["content_hash"] == source_collection_manifest_hash(data),
            "SOURCE_MANIFEST_HASH_MISMATCH",
        )
        return cls(canonical_json(data))

    def to_dict(self) -> dict[str, Any]:
        return read_json(self.document)


def source_collection_manifest_hash(value: Mapping[str, Any]) -> str:
    return digest({key: item for key, item in value.items() if key != "content_hash"})


def seal_source_collection_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    candidate = {**value, "content_hash": "0" * 64}
    candidate["content_hash"] = source_collection_manifest_hash(candidate)
    return TradeCostSourceCollectionManifest.from_dict(candidate).to_dict()

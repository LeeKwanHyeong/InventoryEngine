"""Sealed Platform Trade Cost Revision Set projection contract."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from .values import (
    InventoryInputError,
    boolean,
    canonical_json,
    choice,
    day,
    digest,
    hash_value,
    identifier,
    integer,
    read_json,
    require,
    shape,
)


TRADE_COST_PROJECTION_CONTRACT_ID = "inventory-trade-cost-revision-set-projection-v1"
TRADE_COST_PROJECTION_CONTRACT_VERSION = "1.0.0"


def _uuid(value: Any) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError):
        raise InventoryInputError("TRADE_COST_PROJECTION_UUID_INVALID") from None


def _positive(value: Any) -> int:
    parsed = integer(value)
    require(parsed > 0, "TRADE_COST_PROJECTION_REVISION_INVALID")
    return parsed


def _country(value: Any) -> str:
    require(
        isinstance(value, str) and re.fullmatch(r"[A-Z]{2}", value) is not None,
        "TRADE_COST_PROJECTION_COUNTRY_INVALID",
    )
    return value


def _bounded_text(value: Any, *, limit: int = 1000) -> str:
    require(
        isinstance(value, str)
        and bool(value.strip())
        and value == value.strip()
        and len(value) <= limit
        and "\x00" not in value,
        "TRADE_COST_PROJECTION_TEXT_INVALID",
    )
    return value


def _optional_text(value: Any) -> str | None:
    return None if value is None else _bounded_text(value)


def _timestamp_text(value: Any) -> str:
    value = _bounded_text(value, limit=80)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise InventoryInputError("TRADE_COST_PROJECTION_TIMESTAMP_INVALID") from None
    require(parsed.tzinfo is not None, "TRADE_COST_PROJECTION_TIMESTAMP_INVALID")
    return value


def _optional_timestamp_text(value: Any) -> str | None:
    return None if value is None else _timestamp_text(value)


def _optional_day(value: Any) -> str | None:
    return None if value is None else day(value)


def _revision_set_header(value: Any) -> dict[str, Any]:
    result = shape(
        value,
        {
            "revision_set_id": _uuid,
            "tenant_id": identifier,
            "project_id": identifier,
            "revision_set_code": identifier,
            "revision_no": _positive,
            "company_cd": identifier,
            "subs_cd": identifier,
            "origin_site_cd": identifier,
            "environment_scope": choice("DEVELOPMENT", "PRODUCTION"),
            "valid_from": day,
            "valid_to": _optional_day,
            "source_manifest_hash": hash_value,
        },
    )
    require(
        result["valid_to"] is None or result["valid_from"] < result["valid_to"],
        "TRADE_COST_PROJECTION_PERIOD_INVALID",
    )
    return result


def _source_header(value: Any) -> dict[str, Any]:
    result = shape(
        value,
        {
            "source_revision_id": _uuid,
            "tenant_id": identifier,
            "project_id": identifier,
            "source_domain": identifier,
            "revision_no": _positive,
            "company_cd": identifier,
            "subs_cd": identifier,
            "origin_site_cd": identifier,
            "jurisdiction_country_code": _country,
            "environment_scope": choice("DEVELOPMENT", "PRODUCTION"),
            "valid_from": day,
            "valid_to": _optional_day,
            "source_manifest_hash": hash_value,
        },
    )
    require(
        result["valid_to"] is None or result["valid_from"] < result["valid_to"],
        "TRADE_COST_SOURCE_PERIOD_INVALID",
    )
    return result


def _member(value: Any) -> dict[str, Any]:
    return shape(
        value,
        {
            "source_domain": identifier,
            "jurisdiction_country_code": _country,
            "source_revision_id": _uuid,
            "content_hash": hash_value,
        },
    )


def _document(value: Any) -> dict[str, Any]:
    return shape(
        value,
        {
            "source_revision_id": _uuid,
            "source_document_id": lambda item: _bounded_text(item, limit=128),
            "document_type": identifier,
            "source_owner": lambda item: _bounded_text(item, limit=160),
            "source_system": lambda item: _bounded_text(item, limit=160),
            "external_reference": _bounded_text,
            "published_at": _optional_timestamp_text,
            "collected_at": _timestamp_text,
            "effective_from": day,
            "effective_to": _optional_day,
            "raw_content_hash": hash_value,
            "license_review_status": identifier,
        },
    )


def _record(value: Any) -> dict[str, str | int | bool | None]:
    require(
        type(value) is dict and 1 <= len(value) <= 64,
        "TRADE_COST_PROJECTION_RECORD_INVALID",
    )
    result: dict[str, str | int | bool | None] = {}
    for key, item in value.items():
        require(
            isinstance(key, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) is not None,
            "TRADE_COST_PROJECTION_RECORD_KEY_INVALID",
        )
        require(
            item is None or type(item) in {str, int, bool},
            "TRADE_COST_PROJECTION_RECORD_VALUE_INVALID",
        )
        if isinstance(item, str):
            require(
                len(item) <= 10_000 and "\x00" not in item,
                "TRADE_COST_PROJECTION_RECORD_VALUE_INVALID",
            )
        result[key] = item
    return {key: result[key] for key in sorted(result)}


def _canonical_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=canonical_json)


def _source(value: Any) -> dict[str, Any]:
    result = shape(
        value,
        {
            "header": _source_header,
            "status": choice("APPROVED"),
            "content_hash": hash_value,
            "documents": lambda rows: (
                [_document(row) for row in rows]
                if type(rows) is list
                else require(False, "TRADE_COST_PROJECTION_DOCUMENTS_INVALID")
            ),
            "records": lambda rows: (
                [_record(row) for row in rows]
                if type(rows) is list
                else require(False, "TRADE_COST_PROJECTION_RECORDS_INVALID")
            ),
        },
    )
    require(
        result["documents"] == _canonical_rows(result["documents"])
        and result["records"] == _canonical_rows(result["records"]),
        "TRADE_COST_PROJECTION_ROWS_NOT_CANONICAL",
    )
    require(
        result["content_hash"]
        == digest(
            {
                "header": result["header"],
                "documents": result["documents"],
                "records": result["records"],
            }
        ),
        "TRADE_COST_SOURCE_PROJECTION_HASH_MISMATCH",
    )
    return result


def _projection(value: Any) -> dict[str, Any]:
    result = shape(
        value,
        {
            "contract_id": choice(TRADE_COST_PROJECTION_CONTRACT_ID),
            "contract_version": choice(TRADE_COST_PROJECTION_CONTRACT_VERSION),
            "revision_set": _revision_set_header,
            "status": choice("APPROVED"),
            "revision_set_content_hash": hash_value,
            "row_version": _positive,
            "valuation_date": day,
            "members": lambda rows: (
                [_member(row) for row in rows]
                if type(rows) is list
                else require(False, "TRADE_COST_PROJECTION_MEMBERS_INVALID")
            ),
            "sources": lambda rows: (
                [_source(row) for row in rows]
                if type(rows) is list
                else require(False, "TRADE_COST_PROJECTION_SOURCES_INVALID")
            ),
            "development_eligible": boolean,
            "operational_eligible": boolean,
            "projection_content_hash": hash_value,
        },
    )
    header = result["revision_set"]
    require(
        header["valid_from"] <= result["valuation_date"]
        and (header["valid_to"] is None or result["valuation_date"] < header["valid_to"]),
        "TRADE_COST_PROJECTION_VALUATION_DATE_OUT_OF_RANGE",
    )
    require(
        result["members"] == _canonical_rows(result["members"]),
        "TRADE_COST_PROJECTION_MEMBERS_NOT_CANONICAL",
    )
    member_keys = [
        (
            row["source_domain"],
            row["jurisdiction_country_code"],
            row["source_revision_id"],
            row["content_hash"],
        )
        for row in result["members"]
    ]
    source_keys = [
        (
            row["header"]["source_domain"],
            row["header"]["jurisdiction_country_code"],
            row["header"]["source_revision_id"],
            row["content_hash"],
        )
        for row in result["sources"]
    ]
    require(
        member_keys == source_keys and len(member_keys) == len(set(member_keys)),
        "TRADE_COST_PROJECTION_SOURCE_MEMBERSHIP_MISMATCH",
    )
    for source in result["sources"]:
        source_header = source["header"]
        require(
            source_header["tenant_id"] == header["tenant_id"]
            and source_header["project_id"] == header["project_id"]
            and source_header["company_cd"] == header["company_cd"]
            and source_header["subs_cd"] == header["subs_cd"]
            and source_header["origin_site_cd"] == header["origin_site_cd"]
            and source_header["environment_scope"] == header["environment_scope"],
            "TRADE_COST_PROJECTION_SOURCE_SCOPE_MISMATCH",
        )
        require(
            source_header["valid_from"] <= result["valuation_date"]
            and (
                source_header["valid_to"] is None
                or result["valuation_date"] < source_header["valid_to"]
            ),
            "TRADE_COST_PROJECTION_SOURCE_DATE_OUT_OF_RANGE",
        )
        require(
            all(
                document["source_revision_id"] == source_header["source_revision_id"]
                for document in source["documents"]
            ),
            "TRADE_COST_PROJECTION_DOCUMENT_SOURCE_MISMATCH",
        )
    require(
        result["revision_set_content_hash"]
        == digest({"header": header, "members": result["members"]}),
        "TRADE_COST_REVISION_SET_PROJECTION_HASH_MISMATCH",
    )
    require(
        result["development_eligible"] == (header["environment_scope"] == "DEVELOPMENT")
        and result["operational_eligible"] == (header["environment_scope"] == "PRODUCTION"),
        "TRADE_COST_PROJECTION_ELIGIBILITY_INVALID",
    )
    require(
        result["projection_content_hash"]
        == digest({key: item for key, item in result.items() if key != "projection_content_hash"}),
        "TRADE_COST_PROJECTION_CONTENT_HASH_MISMATCH",
    )
    return result


@dataclass(frozen=True, slots=True)
class TradeCostRevisionSetProjection:
    document: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TradeCostRevisionSetProjection":
        return cls(canonical_json(_projection(value)))

    @classmethod
    def from_json(cls, document: str) -> "TradeCostRevisionSetProjection":
        return cls.from_dict(read_json(document))

    def to_dict(self) -> dict[str, Any]:
        return read_json(self.document)


def validate_trade_cost_projection_runtime_binding(
    request: Any,
    projection: TradeCostRevisionSetProjection,
    *,
    environment_scope: str,
) -> dict[str, Any]:
    data = projection.to_dict()
    binding = request.trade_cost_binding
    context = request.canonical_context_binding
    require(binding is not None, "TRADE_COST_RUNTIME_BINDING_REQUIRED")
    require(context is not None, "TRADE_COST_RUNTIME_CONTEXT_REQUIRED")
    header = data["revision_set"]
    claim = request.value["claim"]
    require(
        header["revision_set_id"] == binding["source_snapshot_id"]
        and data["revision_set_content_hash"] == binding["source_content_hash"],
        "TRADE_COST_RUNTIME_BINDING_MISMATCH",
    )
    require(
        data["valuation_date"] == context["master_as_of_date"],
        "TRADE_COST_RUNTIME_VALUATION_DATE_MISMATCH",
    )
    require(
        header["tenant_id"] == claim["tenant_id"]
        and header["project_id"] == claim["project_id"]
        and header["company_cd"] == claim["scope"]["company_cd"]
        and header["subs_cd"] == claim["scope"]["subs_cd"]
        and header["origin_site_cd"] == claim["scope"]["site_cd"],
        "TRADE_COST_RUNTIME_SCOPE_MISMATCH",
    )
    require(
        header["environment_scope"] == environment_scope,
        "TRADE_COST_RUNTIME_ENVIRONMENT_MISMATCH",
    )
    return data


__all__ = [
    "TRADE_COST_PROJECTION_CONTRACT_ID",
    "TRADE_COST_PROJECTION_CONTRACT_VERSION",
    "TradeCostRevisionSetProjection",
    "validate_trade_cost_projection_runtime_binding",
]

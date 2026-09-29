"""Fail-closed Trade Cost source and Landed Cost result contracts.

The module deliberately validates already selected legal/business inputs.  It
does not guess tariff classifications, origin, tax recoverability, exchange
rates, or missing monetary components.  Country-specific calculation adapters
may be added later, but every adapter must emit this common assessment shape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Sequence

from .values import (
    InventoryInputError,
    boolean,
    canonical_json,
    choice,
    day,
    decimal_string,
    digest,
    hash_value,
    identifier,
    integer,
    item_identifier,
    optional,
    read_json,
    require,
    shape,
    text,
    timestamp,
)


TRADE_COST_SOURCE_CONTRACT_ID = "io-trade-cost-source-catalog-v1"
LANDED_COST_ASSESSMENT_CONTRACT_ID = "io-landed-cost-assessment-v1"
TRADE_COST_CONTRACT_VERSION = "1.0.0"

TRADE_COST_FIELDS = (
    "HS_CLASSIFICATION",
    "MANUFACTURING_ORIGIN",
    "SHIPPING_COUNTRY",
    "TRANSACTION_VALUE",
    "TRANSACTION_CURRENCY",
    "QUANTITY_UOM",
    "WEIGHT_VOLUME",
    "INCOTERMS_INSURANCE",
    "INTERNATIONAL_FREIGHT",
    "INLAND_FREIGHT",
    "CUSTOMS_FX_RATE",
    "VAT_RECOVERABILITY",
    "PREFERENTIAL_ORIGIN_CERTIFICATE",
)
SOURCE_STATUSES = (
    "AUTHORITATIVE",
    "UNVERIFIED",
    "DEVELOPMENT_FIXTURE",
    "NOT_FOUND",
)
CALCULATION_STATUSES = (
    "CALCULABLE",
    "UNVERIFIED_CLASSIFICATION",
    "UNVERIFIED_ORIGIN",
    "UNVERIFIED_TAX_RECOVERABILITY",
    "MISSING_EXCHANGE_RATE",
    "NOT_CALCULABLE",
)
COMPONENT_TYPES = (
    "TRANSACTION_VALUE",
    "ORIGIN_HANDLING",
    "INTERNATIONAL_FREIGHT",
    "INSURANCE",
    "PACKING",
    "ASSIST_VALUE",
    "ROYALTY",
    "CUSTOMS_DUTY_AD_VALOREM",
    "CUSTOMS_DUTY_SPECIFIC",
    "CUSTOMS_DUTY_OTHER",
    "IMPORT_VAT",
    "EXCISE_TAX",
    "BROKERAGE",
    "DESTINATION_HANDLING",
    "INLAND_FREIGHT",
    "STORAGE",
    "OTHER_NONRECOVERABLE",
)
V1_SCOPE = {
    "company_cd": "DSE",
    "subs_cd": "C100",
    "origin_site_cd": "V100",
    "destination_site_cds": ["V101", "V102", "V103", "V104"],
}
V1_IMPORT_COUNTRIES = {"JP", "CN", "SA", "AE"}


def _iso_code(value: Any, *, size: int, code: str) -> str:
    require(
        isinstance(value, str) and re.fullmatch(rf"[A-Z]{{{size}}}", value) is not None,
        code,
    )
    return value


def _country(value: Any) -> str:
    return _iso_code(value, size=2, code="INVALID_COUNTRY_CODE")


def _currency(value: Any) -> str:
    return _iso_code(value, size=3, code="INVALID_CURRENCY_CODE")


def _optional_text(value: Any) -> str | None:
    return None if value is None else text(value)


def _optional_day(value: Any) -> str | None:
    return None if value is None else day(value)


def _optional_hash(value: Any) -> str | None:
    return None if value is None else hash_value(value)


def _identifiers(value: Any) -> list[str]:
    require(type(value) is list and 1 <= len(value) <= 100, "IDENTIFIER_LIST_INVALID")
    result = [identifier(item) for item in value]
    require(len(result) == len(set(result)), "IDENTIFIER_LIST_DUPLICATE")
    return sorted(result)


def _reason_codes(value: Any) -> list[str]:
    require(type(value) is list and len(value) <= 32, "REASON_CODES_INVALID")
    result = [identifier(item) for item in value]
    require(len(result) == len(set(result)), "REASON_CODES_DUPLICATE")
    return sorted(result)


SOURCE_FIELD_RULES = {
    "field_name": choice(*TRADE_COST_FIELDS),
    "status": choice(*SOURCE_STATUSES),
    "source_reference": _optional_text,
    "source_revision": _optional_text,
    "source_as_of_date": _optional_day,
    "coverage_numerator": integer,
    "coverage_denominator": integer,
    "evidence_hash": _optional_hash,
    "notes": text,
}


def _source_fields(value: Any) -> list[dict[str, Any]]:
    require(type(value) is list and len(value) == len(TRADE_COST_FIELDS), "SOURCE_FIELD_COUNT")
    fields = [shape(row, SOURCE_FIELD_RULES) for row in value]
    require(
        [row["field_name"] for row in fields] == sorted(TRADE_COST_FIELDS),
        "SOURCE_FIELDS_NOT_CANONICAL",
    )
    for row in fields:
        require(
            row["coverage_numerator"] <= row["coverage_denominator"],
            "SOURCE_COVERAGE_RANGE",
        )
        if row["status"] in {"AUTHORITATIVE", "DEVELOPMENT_FIXTURE"}:
            require(row["source_reference"] is not None, "SOURCE_REFERENCE_REQUIRED")
            require(row["source_revision"] is not None, "SOURCE_REVISION_REQUIRED")
            require(row["source_as_of_date"] is not None, "SOURCE_AS_OF_DATE_REQUIRED")
            require(row["evidence_hash"] is not None, "SOURCE_EVIDENCE_HASH_REQUIRED")
        if row["status"] == "NOT_FOUND":
            require(row["coverage_numerator"] == 0, "SOURCE_NOT_FOUND_HAS_COVERAGE")
    return fields


@dataclass(frozen=True)
class TradeCostSourceCatalog:
    """One immutable field-level source audit for a bounded network scope."""

    document: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TradeCostSourceCatalog":
        data = shape(
            value,
            {
                "contract_id": choice(TRADE_COST_SOURCE_CONTRACT_ID),
                "contract_version": choice(TRADE_COST_CONTRACT_VERSION),
                "scope": lambda item: shape(
                    item,
                    {
                        "company_cd": identifier,
                        "subs_cd": identifier,
                        "origin_site_cd": identifier,
                        "destination_site_cds": _identifiers,
                        "environment": choice("DEVELOPMENT", "PRODUCTION"),
                    },
                ),
                "audited_at": timestamp,
                "fields": _source_fields,
                "content_hash": hash_value,
            },
        )
        expected = trade_cost_source_catalog_hash(data)
        require(
            all(data["scope"][key] == expected_value for key, expected_value in V1_SCOPE.items()),
            "TRADE_COST_V1_SCOPE_MISMATCH",
        )
        require(data["content_hash"] == expected, "TRADE_COST_SOURCE_HASH_MISMATCH")
        return cls(canonical_json(data))

    def to_dict(self) -> dict[str, Any]:
        return read_json(self.document)


def trade_cost_source_catalog_hash(value: Mapping[str, Any]) -> str:
    return digest({key: item for key, item in value.items() if key != "content_hash"})


def seal_trade_cost_source_catalog(value: Mapping[str, Any]) -> dict[str, Any]:
    """Seal and validate a bounded source audit."""

    candidate = {**value, "content_hash": "0" * 64}
    candidate["content_hash"] = trade_cost_source_catalog_hash(candidate)
    return TradeCostSourceCatalog.from_dict(candidate).to_dict()


COMPONENT_RULES = {
    "component_type": choice(*COMPONENT_TYPES),
    "amount": decimal_string,
    "source_status": choice(*SOURCE_STATUSES),
    "source_reference": text,
    "zero_value_reason": _optional_text,
    "included_in_customs_value": boolean,
    "included_in_gross_landed_cost": boolean,
    "recoverable": boolean,
}


def _components(value: Any) -> list[dict[str, Any]]:
    require(type(value) is list and len(value) <= len(COMPONENT_TYPES), "COST_COMPONENTS_INVALID")
    result = [shape(row, COMPONENT_RULES) for row in value]
    require(
        [row["component_type"] for row in result]
        == sorted(row["component_type"] for row in result),
        "COST_COMPONENTS_NOT_CANONICAL",
    )
    require(
        len(result) == len({row["component_type"] for row in result}),
        "COST_COMPONENT_DUPLICATE",
    )
    for row in result:
        require(
            not row["recoverable"] or row["included_in_gross_landed_cost"],
            "RECOVERABLE_COMPONENT_NOT_IN_GROSS",
        )
        if _amount(row["amount"]) == 0:
            require(row["zero_value_reason"] is not None, "ZERO_VALUE_REASON_REQUIRED")
        else:
            require(row["zero_value_reason"] is None, "ZERO_VALUE_REASON_NOT_APPLICABLE")
    return result


ASSESSMENT_FIELDS = {
    "contract_id": choice(LANDED_COST_ASSESSMENT_CONTRACT_ID),
    "contract_version": choice(TRADE_COST_CONTRACT_VERSION),
    "grain": lambda item: shape(
        item,
        {
            "shipment_id": identifier,
            "shipment_line_id": identifier,
            "lane_id": identifier,
            "item_id": item_identifier,
            "valuation_date": day,
        },
    ),
    "judgment_at": timestamp,
    "calculation_purpose": choice("OPERATIONAL", "DEVELOPMENT_FIXTURE", "ACTUAL_REPLAY"),
    "calculation_status": choice(*CALCULATION_STATUSES),
    "blocking_reason_codes": _reason_codes,
    "source_revision_set_hash": hash_value,
    "hs_code_version": optional(identifier),
    "national_tariff_code": optional(identifier),
    "manufacturing_origin_country_code": optional(_country),
    "export_country_code": _country,
    "import_country_code": _country,
    "preferential_eligibility_status": choice(
        "NOT_REQUESTED", "UNVERIFIED", "ELIGIBLE", "INELIGIBLE"
    ),
    "tax_recoverability_status": choice(
        "VERIFIED_RECOVERABLE",
        "VERIFIED_NONRECOVERABLE",
        "UNVERIFIED",
        "NOT_APPLICABLE",
    ),
    "tariff_basis": optional(
        choice("MFN", "PREFERENTIAL", "BROKER_VERIFIED", "ACTUAL_DECLARATION_REPLAY")
    ),
    "fixed_cost_allocation_basis": choice("CUSTOMS_VALUE", "WEIGHT", "VOLUME", "QUANTITY"),
    "cost_currency": _currency,
    "rounding_rule": lambda item: shape(
        item,
        {
            "currency_scale": integer,
            "mode": choice("HALF_UP", "HALF_EVEN", "DOWN", "UP"),
            "application_stage": choice("COMPONENT", "LEGAL_RULE", "FINAL_TOTAL"),
            "rule_reference": text,
        },
    ),
    "components": _components,
    "customs_value_amount": optional(decimal_string),
    "gross_landed_cost_amount": optional(decimal_string),
    "recoverable_tax_amount": optional(decimal_string),
    "net_landed_cost_amount": optional(decimal_string),
    "operational_eligible": boolean,
    "content_hash": hash_value,
}


@dataclass(frozen=True)
class LandedCostAssessment:
    """Validated Landed Cost components and totals for one shipment line."""

    document: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LandedCostAssessment":
        data = shape(value, ASSESSMENT_FIELDS)
        _validate_assessment_semantics(data)
        require(
            data["content_hash"] == landed_cost_assessment_hash(data),
            "LANDED_COST_HASH_MISMATCH",
        )
        return cls(canonical_json(data))

    def to_dict(self) -> dict[str, Any]:
        return read_json(self.document)


def landed_cost_assessment_hash(value: Mapping[str, Any]) -> str:
    return digest({key: item for key, item in value.items() if key != "content_hash"})


def seal_landed_cost_assessment(value: Mapping[str, Any]) -> dict[str, Any]:
    """Seal and validate an assessment without mutating the caller's mapping."""

    candidate = {**value, "content_hash": "0" * 64}
    candidate["content_hash"] = landed_cost_assessment_hash(candidate)
    return LandedCostAssessment.from_dict(candidate).to_dict()


def _amount(value: str) -> Decimal:
    return Decimal(value)


def _validate_assessment_semantics(data: Mapping[str, Any]) -> None:
    require(data["export_country_code"] == "KR", "TRADE_COST_V1_EXPORT_COUNTRY")
    require(data["import_country_code"] in V1_IMPORT_COUNTRIES, "TRADE_COST_V1_IMPORT_COUNTRY")
    require(data["rounding_rule"]["currency_scale"] <= 6, "ROUNDING_SCALE_RANGE")
    status = data["calculation_status"]
    components: Sequence[Mapping[str, Any]] = data["components"]
    totals = (
        data["customs_value_amount"],
        data["gross_landed_cost_amount"],
        data["recoverable_tax_amount"],
        data["net_landed_cost_amount"],
    )
    if status != "CALCULABLE":
        require(all(value is None for value in totals), "NON_CALCULABLE_TOTAL_PRESENT")
        require(bool(data["blocking_reason_codes"]), "BLOCKING_REASON_REQUIRED")
        require(not data["operational_eligible"], "NON_CALCULABLE_OPERATIONAL")
        return

    require(not data["blocking_reason_codes"], "CALCULABLE_HAS_BLOCKING_REASON")
    require(all(value is not None for value in totals), "CALCULABLE_TOTAL_MISSING")
    require(
        sum(row["component_type"] == "TRANSACTION_VALUE" for row in components) == 1,
        "TRANSACTION_VALUE_REQUIRED",
    )
    require(data["tariff_basis"] is not None, "TARIFF_BASIS_REQUIRED")
    require(data["hs_code_version"] is not None, "HS_CODE_VERSION_REQUIRED")
    require(data["national_tariff_code"] is not None, "NATIONAL_TARIFF_CODE_REQUIRED")
    require(
        data["manufacturing_origin_country_code"] is not None,
        "MANUFACTURING_ORIGIN_REQUIRED",
    )
    require(
        data["tax_recoverability_status"] != "UNVERIFIED",
        "TAX_RECOVERABILITY_UNVERIFIED",
    )
    if data["tariff_basis"] == "PREFERENTIAL":
        require(
            data["preferential_eligibility_status"] == "ELIGIBLE",
            "PREFERENTIAL_EVIDENCE_REQUIRED",
        )
    if data["tariff_basis"] == "ACTUAL_DECLARATION_REPLAY":
        require(data["calculation_purpose"] == "ACTUAL_REPLAY", "ACTUAL_REPLAY_ONLY")
    if data["calculation_purpose"] == "ACTUAL_REPLAY":
        require(
            data["tariff_basis"] == "ACTUAL_DECLARATION_REPLAY",
            "ACTUAL_REPLAY_BASIS_REQUIRED",
        )

    customs_value = sum(
        (_amount(row["amount"]) for row in components if row["included_in_customs_value"]),
        Decimal("0"),
    )
    gross = sum(
        (_amount(row["amount"]) for row in components if row["included_in_gross_landed_cost"]),
        Decimal("0"),
    )
    recoverable = sum(
        (_amount(row["amount"]) for row in components if row["recoverable"]),
        Decimal("0"),
    )
    require(customs_value == _amount(data["customs_value_amount"]), "CUSTOMS_VALUE_MISMATCH")
    require(gross == _amount(data["gross_landed_cost_amount"]), "GROSS_LANDED_COST_MISMATCH")
    require(
        recoverable == _amount(data["recoverable_tax_amount"]),
        "RECOVERABLE_TAX_MISMATCH",
    )
    require(
        gross - recoverable == _amount(data["net_landed_cost_amount"]),
        "NET_LANDED_COST_MISMATCH",
    )

    has_unverified = any(row["source_status"] in {"UNVERIFIED", "NOT_FOUND"} for row in components)
    has_fixture = any(row["source_status"] == "DEVELOPMENT_FIXTURE" for row in components)
    require(not has_unverified, "CALCULABLE_SOURCE_UNVERIFIED")
    if data["calculation_purpose"] == "OPERATIONAL":
        require(not has_fixture, "OPERATIONAL_FIXTURE_FORBIDDEN")
        require(data["operational_eligible"], "OPERATIONAL_ELIGIBILITY_REQUIRED")
    else:
        require(not data["operational_eligible"], "NON_OPERATIONAL_RESULT_ELIGIBLE")


def validate_landed_cost_assessment(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return LandedCostAssessment.from_dict(value).to_dict()
    except InventoryInputError:
        raise

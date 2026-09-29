"""Closed development Shipment input contract for run-bound Landed Cost evidence."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

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
    read_json,
    require,
    shape,
)

SHIPMENT_CONTRACT_ID = "io-landed-cost-shipment-input-v1"
CHILD_CONTRACT_ID = "io-landed-cost-child-artifact-v1"
CONTRACT_VERSION = "1.0.0"
DESTINATIONS = {"V101": "JP", "V102": "CN", "V103": "SA", "V104": "AE"}
CHARGE_TYPES = (
    "ORIGIN_HANDLING",
    "INTERNATIONAL_FREIGHT",
    "INSURANCE",
    "PACKING",
    "ASSIST_VALUE",
    "ROYALTY",
    "BROKERAGE",
    "DESTINATION_HANDLING",
    "INLAND_FREIGHT",
    "STORAGE",
    "OTHER_NONRECOVERABLE",
)


def currency(value: Any) -> str:
    require(
        isinstance(value, str) and re.fullmatch(r"[A-Z]{3}", value) is not None,
        "LANDED_COST_CURRENCY_INVALID",
    )
    return value


def uuid_value(value: Any) -> str:
    try:
        parsed = str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise InventoryInputError("LANDED_COST_UUID_INVALID") from None
    require(parsed == value, "LANDED_COST_UUID_NOT_CANONICAL")
    return parsed


def positive(value: Any) -> str:
    parsed = decimal_string(value)
    require(Decimal(parsed) > 0, "LANDED_COST_POSITIVE_VALUE_REQUIRED")
    return parsed


def timestamp(value: Any) -> str:
    require(
        isinstance(value, str)
        and re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", value
        )
        is not None,
        "LANDED_COST_TIMESTAMP_INVALID",
    )
    try:
        parsed = datetime.fromisoformat(value)
    except (ValueError, AttributeError):
        raise InventoryInputError("LANDED_COST_TIMESTAMP_INVALID") from None
    require(parsed.tzinfo is not None, "LANDED_COST_TIMESTAMP_INVALID")
    return value


def landed_cost_run_binding(value: Any) -> dict:
    result = shape(
        value,
        {
            "engine_run_id": identifier,
            "attempt_no": integer,
            "tenant_id": identifier,
            "project_id": identifier,
            "company_cd": identifier,
            "subs_cd": identifier,
            "origin_site_cd": identifier,
            "canonical_input_hash": hash_value,
            "revision_set_id": uuid_value,
            "revision_set_content_hash": hash_value,
            "network_revision_id": uuid_value,
            "valuation_date": day,
            "judgment_at": timestamp,
        },
    )
    require(result["attempt_no"] > 0, "INVALID_ATTEMPT_NO")
    require(
        result["subs_cd"] == "C100" and result["origin_site_cd"] == "V100",
        "LANDED_COST_V1_SCOPE_INVALID",
    )
    return result


def _charge(value: Any) -> dict:
    result = shape(
        value,
        {
            "component_type": choice(*CHARGE_TYPES),
            "amount": decimal_string,
            "currency": currency,
            "included_in_customs_value": boolean,
            "included_in_gross_landed_cost": boolean,
            "recoverable": boolean,
            "source_status": choice("DEVELOPMENT_FIXTURE", "AUTHORITATIVE"),
            "source_reference": identifier,
        },
    )
    require(
        not result["recoverable"] or result["included_in_gross_landed_cost"],
        "LANDED_COST_RECOVERABLE_NOT_IN_GROSS",
    )
    require(
        not result["included_in_customs_value"] or result["included_in_gross_landed_cost"],
        "LANDED_COST_CUSTOMS_NOT_IN_GROSS",
    )
    return result


def _line(value: Any) -> dict:
    return shape(
        value,
        {
            "shipment_line_id": identifier,
            "item_id": item_identifier,
            "quantity": positive,
            "quantity_uom": choice("EA"),
            "transaction_value": positive,
            "transaction_currency": currency,
            "source_status": choice("DEVELOPMENT_FIXTURE", "AUTHORITATIVE"),
            "source_reference": identifier,
            "charges": lambda rows: _rows(rows, _charge),
        },
    )


def _rows(value: Any, rule) -> list:
    require(type(value) is list and len(value) <= 10_000, "LANDED_COST_ROW_LIMIT")
    return sorted((rule(row) for row in value), key=canonical_json)


def _exemption(value: Any) -> dict:
    return shape(
        value,
        {
            "component_type": choice(*CHARGE_TYPES),
            "reason_code": identifier,
            "source_reference": identifier,
        },
    )


def validate_shipment_input(value: Any) -> dict:
    result = shape(
        value,
        {
            "contract_id": choice(SHIPMENT_CONTRACT_ID),
            "contract_version": choice(CONTRACT_VERSION),
            "binding": landed_cost_run_binding,
            "calculation_purpose": choice("DEVELOPMENT_FIXTURE"),
            "shipment_id": identifier,
            "lane_id": identifier,
            "destination_site_cd": choice(*DESTINATIONS),
            "import_country_code": choice(*DESTINATIONS.values()),
            "export_country_code": choice("KR"),
            "cost_currency": currency,
            "fixed_cost_allocation_basis": choice("CUSTOMS_VALUE", "QUANTITY", "WEIGHT", "VOLUME"),
            "tariff_basis": choice("MFN"),
            "currency_scale": integer,
            "rounding_mode": choice("HALF_UP", "HALF_EVEN", "DOWN", "UP"),
            "rounding_rule_reference": identifier,
            "transaction_value_basis": choice("GOODS_ONLY_EXCLUDING_ADDITIONAL_CHARGES"),
            "charge_exemptions": lambda rows: _rows(rows, _exemption),
            "lines": lambda rows: _rows(rows, _line),
            "content_hash": hash_value,
        },
    )
    require(result["currency_scale"] <= 6, "ROUNDING_SCALE_RANGE")
    require(bool(result["lines"]), "LANDED_COST_LINES_EMPTY")
    keys = [line["shipment_line_id"] for line in result["lines"]]
    require(len(keys) == len(set(keys)), "SHIPMENT_LINE_DUPLICATE")
    exemptions = [row["component_type"] for row in result["charge_exemptions"]]
    require(len(exemptions) == len(set(exemptions)), "LANDED_COST_EXEMPTION_DUPLICATE")
    for line in result["lines"]:
        charges = [row["component_type"] for row in line["charges"]]
        require(len(charges) == len(set(charges)), "LANDED_COST_CHARGE_DUPLICATE")
        require(not set(charges) & set(exemptions), "LANDED_COST_CHARGE_EXEMPTION_CONFLICT")
    require(
        DESTINATIONS[result["destination_site_cd"]] == result["import_country_code"],
        "LANDED_COST_DESTINATION_MISMATCH",
    )
    require(
        result["lane_id"]
        in {f"V100-{result['destination_site_cd']}-{mode}" for mode in ("SEA", "AIR", "ROAD")},
        "LANDED_COST_LANE_SCOPE_INVALID",
    )
    body = {key: item for key, item in result.items() if key != "content_hash"}
    require(digest(body) == result["content_hash"], "LANDED_COST_SHIPMENT_HASH_MISMATCH")
    return result


def seal_shipment_input(body: Mapping[str, Any]) -> dict:
    result = dict(body)
    require("content_hash" not in result, "LANDED_COST_ALREADY_SEALED")
    result["lines"] = _rows(result["lines"], _line)
    result["charge_exemptions"] = _rows(result["charge_exemptions"], _exemption)
    return validate_shipment_input({**result, "content_hash": digest(result)})


@dataclass(frozen=True, slots=True)
class LandedCostShipmentInput:
    document: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> LandedCostShipmentInput:
        return cls(canonical_json(validate_shipment_input(dict(value))))

    def to_dict(self) -> dict:
        return validate_shipment_input(read_json(self.document))


__all__ = ["LandedCostShipmentInput", "landed_cost_run_binding", "seal_shipment_input"]

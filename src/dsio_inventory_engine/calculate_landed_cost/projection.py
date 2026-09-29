"""Resolve explicit development Revision Set records; never discover latest master data."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal, localcontext

from dsio_inventory_engine.inventory_contracts.landed_cost_artifact import (
    CHARGE_TYPES,
    LandedCostShipmentInput,
    currency,
)
from dsio_inventory_engine.inventory_contracts.trade_cost_projection import (
    TradeCostRevisionSetProjection,
)
from dsio_inventory_engine.inventory_contracts.values import (
    boolean,
    day,
    decimal_string,
    require,
)

from .application import (
    ROUNDING_MODES,
    AllocationLine,
    Charge,
    FxQuote,
    LandedCostRequest,
    TariffRule,
    TaxProfile,
    allocate_fixed_charge,
    calculate_landed_cost,
)

_DOMAIN_FIELDS = {
    "ITEM_CLASSIFICATION": {
        "item_id",
        "destination_country_code",
        "hs_code_version",
        "national_tariff_code",
    },
    "ITEM_ORIGIN": {"item_id", "manufacturing_origin_country_code"},
    "CUSTOMS_TARIFF": {
        "tariff_rule_id",
        "destination_country_code",
        "hs_code_version",
        "national_tariff_code",
        "origin_country_code",
        "tariff_basis",
        "duty_method",
        "ad_valorem_rate",
        "specific_rate",
        "specific_uom",
        "duty_currency",
        "minimum_duty_amount",
        "maximum_duty_amount",
        "currency_scale",
        "rounding_mode",
    },
    "CUSTOMS_FX": {
        "destination_country_code",
        "from_currency",
        "to_currency",
        "effective_date",
        "rate",
    },
    "IMPORT_TAX_PROFILE": {
        "tax_profile_id",
        "importer_site_cd",
        "destination_country_code",
        "import_vat_rate",
        "recoverability_status",
        "include_duty_in_vat_base",
        "include_excise_in_vat_base",
    },
    "LANE_CHARGE": {
        "lane_charge_id",
        "destination_country_code",
        "network_revision_id",
        "lane_id",
        "component_type",
        "charge_basis",
        "charge_currency",
        "charge_amount",
        "charge_rate",
        "minimum_charge_amount",
        "maximum_charge_amount",
        "included_in_customs_value",
    },
    "ITEM_PHYSICAL_ATTRIBUTE": {"item_id", "net_weight", "weight_uom", "volume", "volume_uom"},
}


def _number(value) -> Decimal:
    # PostgreSQL NUMERIC rates may have 18 decimals, unlike EA quantities.
    require(isinstance(value, str) and len(value) <= 80, "LANDED_COST_SOURCE_DECIMAL_INVALID")
    try:
        result = Decimal(value)
    except ArithmeticError:
        require(False, "LANDED_COST_SOURCE_DECIMAL_INVALID")
    require(result.is_finite() and result >= 0, "LANDED_COST_SOURCE_DECIMAL_INVALID")
    return result


def _optional_number(value) -> Decimal | None:
    return None if value is None else _number(value)


def _period(row: dict, at: str) -> bool:
    start = day(row.get("valid_from"))
    end = row.get("valid_to")
    require(end is None or start < day(end), "LANDED_COST_RECORD_PERIOD_INVALID")
    return start <= at and (end is None or at < end)


class _Records:
    def __init__(self, projection: dict, country: str, at: str, judgment_at: str):
        self.sources = projection["sources"]
        self.country, self.at = country, at
        self.judgment_at = datetime.fromisoformat(judgment_at)

    def rows(self, domain: str, jurisdiction: str | None = None, **keys) -> list[dict]:
        result = []
        for source in self.sources:
            header = source["header"]
            if header["source_domain"] != domain or header["jurisdiction_country_code"] != (
                jurisdiction or self.country
            ):
                continue
            for row in source["records"]:
                require(_DOMAIN_FIELDS[domain] <= row.keys(), "LANDED_COST_RECORD_FIELDS_MISSING")
                require(
                    row.get("source_revision_id") == header["source_revision_id"],
                    "LANDED_COST_RECORD_REVISION_MISMATCH",
                )
                require(
                    row.get("source_domain", domain) == domain, "LANDED_COST_RECORD_DOMAIN_MISMATCH"
                )
                if any(row.get(key) != item for key, item in keys.items()):
                    continue
                if domain != "CUSTOMS_FX" and not _period(row, self.at):
                    continue
                if domain == "CUSTOMS_FX" and day(row["effective_date"]) > self.at:
                    continue
                documents = [
                    doc
                    for doc in source["documents"]
                    if doc["source_document_id"] == row.get("source_document_id")
                ]
                require(len(documents) == 1, "LANDED_COST_RECORD_DOCUMENT_MISSING")
                doc = documents[0]
                require(
                    datetime.fromisoformat(doc["collected_at"]) <= self.judgment_at
                    and (
                        doc["published_at"] is None
                        or datetime.fromisoformat(doc["published_at"]) <= self.judgment_at
                    ),
                    "LANDED_COST_SOURCE_AFTER_JUDGMENT",
                )
                require(
                    doc["effective_from"] <= self.at
                    and (doc["effective_to"] is None or self.at < doc["effective_to"]),
                    "LANDED_COST_DOCUMENT_OUT_OF_RANGE",
                )
                result.append(
                    {
                        **row,
                        "_reference": f"{header['source_revision_id']}:{doc['source_document_id']}",
                    }
                )
        return result

    def one(self, domain: str, jurisdiction: str | None = None, **keys) -> dict | None:
        rows = self.rows(domain, jurisdiction, **keys)
        require(len(rows) <= 1, "LANDED_COST_SOURCE_AMBIGUOUS")
        return rows[0] if rows else None


def _tariff(row: dict | None, shipment: dict) -> TariffRule | None:
    if row is None:
        return None
    require(row["duty_currency"] == shipment["cost_currency"], "LANDED_COST_DUTY_CURRENCY_MISMATCH")
    require(
        row["currency_scale"] == shipment["currency_scale"]
        and row["rounding_mode"] == shipment["rounding_mode"],
        "LANDED_COST_ROUNDING_MISMATCH",
    )
    return TariffRule(
        row["tariff_basis"],
        row["duty_method"],
        row["duty_currency"],
        _optional_number(row["ad_valorem_rate"]),
        _optional_number(row["specific_rate"]),
        row["specific_uom"],
        _optional_number(row["minimum_duty_amount"]),
        _optional_number(row["maximum_duty_amount"]),
        "DEVELOPMENT_FIXTURE",
        row["_reference"],
    )


def _tax(row: dict | None) -> TaxProfile | None:
    if row is None:
        return None
    return TaxProfile(
        _number(row["import_vat_rate"]),
        row["recoverability_status"],
        boolean(row["include_duty_in_vat_base"]),
        boolean(row["include_excise_in_vat_base"]),
        "DEVELOPMENT_FIXTURE",
        row["_reference"],
    )


def _charge(row: dict) -> Charge:
    return Charge(
        row["component_type"],
        Decimal(decimal_string(row["amount"])),
        row["currency"],
        row["included_in_customs_value"],
        row["included_in_gross_landed_cost"],
        row["recoverable"],
        row["source_status"],
        row["source_reference"],
    )


def _fx(records: _Records, shipment: dict) -> tuple[FxQuote, ...]:
    latest = {}
    for row in records.rows("CUSTOMS_FX", to_currency=shipment["cost_currency"]):
        at = day(row["effective_date"])
        if at > records.at:
            continue
        pair = (currency(row["from_currency"]), currency(row["to_currency"]))
        current = latest.get(pair)
        require(current is None or current["effective_date"] != at, "LANDED_COST_FX_AMBIGUOUS")
        if current is None or current["effective_date"] < at:
            latest[pair] = row
    return tuple(
        FxQuote(*pair, _number(row["rate"]), "DEVELOPMENT_FIXTURE", row["_reference"])
        for pair, row in sorted(latest.items())
    )


def _convert(amount: Decimal, source: str, shipment: dict, quotes: tuple[FxQuote, ...]) -> Decimal:
    target = shipment["cost_currency"]
    if source != target:
        matches = [
            quote
            for quote in quotes
            if quote.from_currency == source and quote.to_currency == target
        ]
        require(len(matches) == 1, "CUSTOMS_EXCHANGE_RATE_MISSING")
        require(matches[0].rate > 0, "LANDED_COST_FX_RATE_INVALID")
        amount *= matches[0].rate
    return amount.quantize(
        Decimal(1).scaleb(-shipment["currency_scale"]),
        rounding=ROUNDING_MODES[shipment["rounding_mode"]],
    )


def calculate_projection_shipment(
    projection: TradeCostRevisionSetProjection,
    shipment: LandedCostShipmentInput,
) -> list[dict]:
    """Return assessed lines. Invalid bindings abort; incomplete inputs preserve blocked evidence."""
    projected = TradeCostRevisionSetProjection.from_dict(projection.to_dict()).to_dict()
    inputs = shipment.to_dict()
    binding, header = inputs["binding"], projected["revision_set"]
    require(
        header["environment_scope"] == "DEVELOPMENT" and projected["development_eligible"],
        "LANDED_COST_DEVELOPMENT_ONLY",
    )
    for key in (
        "tenant_id",
        "project_id",
        "company_cd",
        "subs_cd",
        "origin_site_cd",
        "revision_set_id",
    ):
        require(binding[key] == header[key], "LANDED_COST_SCOPE_MISMATCH")
    require(
        binding["revision_set_content_hash"] == projected["revision_set_content_hash"],
        "LANDED_COST_REVISION_SET_HASH_MISMATCH",
    )
    require(
        binding["valuation_date"] == projected["valuation_date"], "LANDED_COST_VALUATION_MISMATCH"
    )
    with localcontext() as context:
        context.prec = 48
        return _calculate(projected, inputs)


def _calculate(projection: dict, shipment: dict) -> list[dict]:
    binding = shipment["binding"]
    records = _Records(
        projection,
        shipment["import_country_code"],
        binding["valuation_date"],
        binding["judgment_at"],
    )
    quotes = _fx(records, shipment)
    tax = _tax(
        records.one(
            "IMPORT_TAX_PROFILE",
            importer_site_cd=shipment["destination_site_cd"],
            destination_country_code=shipment["import_country_code"],
        )
    )
    lanes = records.rows(
        "LANE_CHARGE",
        lane_id=shipment["lane_id"],
        network_revision_id=binding["network_revision_id"],
        destination_country_code=shipment["import_country_code"],
    )
    keys = [row["component_type"] for row in lanes]
    require(len(keys) == len(set(keys)), "LANDED_COST_LANE_CHARGE_DUPLICATE")
    input_charge_types = {
        charge["component_type"] for line in shipment["lines"] for charge in line["charges"]
    }
    require(not set(keys) & input_charge_types, "LANDED_COST_CHARGE_SOURCE_OVERLAP")
    for lane in lanes:
        minimum = _optional_number(lane["minimum_charge_amount"])
        maximum = _optional_number(lane["maximum_charge_amount"])
        require(
            minimum is None or maximum is None or minimum <= maximum,
            "LANDED_COST_LANE_BOUNDS_INVALID",
        )
    requests = []
    for line in shipment["lines"]:
        item = line["item_id"]
        classification = records.one(
            "ITEM_CLASSIFICATION",
            item_id=item,
            destination_country_code=shipment["import_country_code"],
        )
        origin = records.one("ITEM_ORIGIN", "KR", item_id=item)
        tariff = None
        if classification and origin:
            candidates = records.rows(
                "CUSTOMS_TARIFF",
                destination_country_code=shipment["import_country_code"],
                hs_code_version=classification["hs_code_version"],
                national_tariff_code=classification["national_tariff_code"],
                tariff_basis=shipment["tariff_basis"],
            )
            candidates = [
                row
                for row in candidates
                if row["origin_country_code"] in (None, origin["manufacturing_origin_country_code"])
            ]
            require(len(candidates) <= 1, "LANDED_COST_SOURCE_AMBIGUOUS")
            tariff = _tariff(candidates[0] if candidates else None, shipment)
        requests.append(
            LandedCostRequest(
                grain={
                    "shipment_id": shipment["shipment_id"],
                    "shipment_line_id": line["shipment_line_id"],
                    "item_id": item,
                    "lane_id": shipment["lane_id"],
                    "valuation_date": binding["valuation_date"],
                },
                judgment_at=binding["judgment_at"],
                calculation_purpose="DEVELOPMENT_FIXTURE",
                source_revision_set_hash=binding["revision_set_content_hash"],
                hs_code_version=classification["hs_code_version"] if classification else None,
                national_tariff_code=classification["national_tariff_code"]
                if classification
                else None,
                manufacturing_origin_country_code=origin["manufacturing_origin_country_code"]
                if origin
                else None,
                export_country_code=shipment["export_country_code"],
                import_country_code=shipment["import_country_code"],
                preferential_eligibility_status="INELIGIBLE",
                transaction_value=Decimal(line["transaction_value"]),
                transaction_currency=line["transaction_currency"],
                transaction_source_status=line["source_status"],
                transaction_source_reference=line["source_reference"],
                quantity=Decimal(line["quantity"]),
                quantity_uom=line["quantity_uom"],
                cost_currency=shipment["cost_currency"],
                charges=tuple(_charge(row) for row in line["charges"]),
                fx_quotes=quotes,
                tariff_rule=tariff,
                tax_profile=tax,
                fixed_cost_allocation_basis=shipment["fixed_cost_allocation_basis"],
                currency_scale=shipment["currency_scale"],
                rounding_mode=shipment["rounding_mode"],
                rounding_rule_reference=shipment["rounding_rule_reference"],
            )
        )
    # Only allocate when all required inputs are resolvable. No partial-shipment allocation.
    preflight = [calculate_landed_cost(request) for request in requests]
    missing = []
    if not lanes:
        missing.append("LANE_CHARGE_NOT_FOUND")
    exemptions = {row["component_type"] for row in shipment["charge_exemptions"]}
    lane_types = {row["component_type"] for row in lanes}
    require(not exemptions & lane_types, "LANDED_COST_CHARGE_EXEMPTION_CONFLICT")
    for request in requests:
        covered = lane_types | {charge.component_type for charge in request.charges} | exemptions
        if covered != set(CHARGE_TYPES):
            missing.append("CHARGE_COVERAGE_INCOMPLETE")
    if any(result["calculation_status"] != "CALCULABLE" for result in preflight):
        missing.append("SHIPMENT_ALLOCATION_INPUT_INCOMPLETE")
    if missing:
        return [
            {
                "grain": dict(request.grain),
                "assessment": None,
                "calculation_status": result["calculation_status"]
                if result["calculation_status"] != "CALCULABLE"
                else "NOT_CALCULABLE",
                "blocking_reason_codes": sorted(set(missing + result["blocking_reason_codes"])),
            }
            for request, result in zip(requests, preflight, strict=True)
        ]

    for lane in lanes:
        require(
            lane["component_type"]
            not in {charge.component_type for request in requests for charge in request.charges},
            "LANDED_COST_CHARGE_SOURCE_OVERLAP",
        )
        require(lane["charge_basis"] == "SHIPMENT", "LANDED_COST_LANE_BASIS_NOT_SUPPORTED")
        require(
            lane["charge_amount"] is not None and lane["charge_rate"] is None,
            "LANDED_COST_LANE_AMOUNT_NOT_SUPPORTED",
        )
        total = _convert(
            _number(lane["charge_amount"]), currency(lane["charge_currency"]), shipment, quotes
        )
        minimum, maximum = (
            _optional_number(lane[key])
            for key in ("minimum_charge_amount", "maximum_charge_amount")
        )
        if minimum is not None:
            total = max(total, _convert(minimum, lane["charge_currency"], shipment, quotes))
        if maximum is not None:
            total = min(total, _convert(maximum, lane["charge_currency"], shipment, quotes))

        factors = []
        for request, result in zip(requests, preflight, strict=True):
            physical = records.one(
                "ITEM_PHYSICAL_ATTRIBUTE", "KR", item_id=request.grain["item_id"]
            )
            basis = shipment["fixed_cost_allocation_basis"]
            if basis in {"WEIGHT", "VOLUME"}:
                require(physical is not None, "LANDED_COST_PHYSICAL_SOURCE_MISSING")
                expected_uom = "KG" if basis == "WEIGHT" else "M3"
                require(
                    physical["weight_uom" if basis == "WEIGHT" else "volume_uom"] == expected_uom,
                    "LANDED_COST_PHYSICAL_UOM_UNSUPPORTED",
                )
            factors.append(
                AllocationLine(
                    request.grain["shipment_line_id"],
                    request.grain["item_id"],
                    Decimal(result["customs_value_amount"]),
                    request.quantity,
                    _number(physical["net_weight"]) * request.quantity
                    if physical and basis == "WEIGHT"
                    else None,
                    _number(physical["volume"]) * request.quantity
                    if physical and basis == "VOLUME"
                    else None,
                )
            )
        allocated = allocate_fixed_charge(
            total,
            tuple(factors),
            basis=shipment["fixed_cost_allocation_basis"],
            currency_scale=shipment["currency_scale"],
        )
        requests = [
            replace(
                request,
                charges=request.charges
                + (
                    Charge(
                        lane["component_type"],
                        Decimal(allocated[request.grain["shipment_line_id"]]),
                        shipment["cost_currency"],
                        boolean(lane["included_in_customs_value"]),
                        True,
                        False,
                        "DEVELOPMENT_FIXTURE",
                        lane["_reference"],
                    ),
                ),
            )
            for request in requests
        ]
    return [
        {
            "grain": dict(request.grain),
            "assessment": result,
            "calculation_status": result["calculation_status"],
            "blocking_reason_codes": result["blocking_reason_codes"],
        }
        for request in requests
        for result in (calculate_landed_cost(request),)
    ]

"""Deterministic Decimal calculator for source-bound Landed Cost scenarios.

The calculator does not discover classifications, rates, origins, exchange
rates, or tax recoverability.  Callers must bind those decisions to approved
source revisions.  Missing decisions produce a fail-closed assessment rather
than an assumed zero amount.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import (
    ROUND_DOWN,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    ROUND_UP,
    Decimal,
)
from typing import Mapping

from dsio_inventory_engine.inventory_contracts.trade_cost import (
    COMPONENT_TYPES,
    SOURCE_STATUSES,
    seal_landed_cost_assessment,
)
from dsio_inventory_engine.inventory_contracts.values import (
    decimal_string,
    hash_value,
    identifier,
    item_identifier,
    quantity_text,
    require,
)


ROUNDING_MODES = {
    "HALF_UP": ROUND_HALF_UP,
    "HALF_EVEN": ROUND_HALF_EVEN,
    "DOWN": ROUND_DOWN,
    "UP": ROUND_UP,
}
INPUT_CHARGE_TYPES = frozenset(
    {
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
    }
)


@dataclass(frozen=True)
class FxQuote:
    from_currency: str
    to_currency: str
    rate: Decimal
    source_status: str
    source_reference: str


@dataclass(frozen=True)
class Charge:
    component_type: str
    amount: Decimal
    currency: str
    included_in_customs_value: bool
    included_in_gross_landed_cost: bool
    recoverable: bool
    source_status: str
    source_reference: str


@dataclass(frozen=True)
class TariffRule:
    tariff_basis: str
    duty_method: str
    currency: str
    ad_valorem_rate: Decimal | None
    specific_rate: Decimal | None
    specific_uom: str | None
    minimum_duty_amount: Decimal | None
    maximum_duty_amount: Decimal | None
    source_status: str
    source_reference: str


@dataclass(frozen=True)
class TaxProfile:
    import_vat_rate: Decimal
    recoverability_status: str
    include_duty_in_vat_base: bool
    include_excise_in_vat_base: bool
    source_status: str
    source_reference: str


@dataclass(frozen=True)
class LandedCostRequest:
    grain: Mapping[str, str]
    judgment_at: str
    calculation_purpose: str
    source_revision_set_hash: str
    hs_code_version: str | None
    national_tariff_code: str | None
    manufacturing_origin_country_code: str | None
    export_country_code: str
    import_country_code: str
    preferential_eligibility_status: str
    transaction_value: Decimal
    transaction_currency: str
    transaction_source_status: str
    transaction_source_reference: str
    quantity: Decimal
    quantity_uom: str
    cost_currency: str
    charges: tuple[Charge, ...]
    fx_quotes: tuple[FxQuote, ...]
    tariff_rule: TariffRule | None
    tax_profile: TaxProfile | None
    fixed_cost_allocation_basis: str
    currency_scale: int
    rounding_mode: str
    rounding_rule_reference: str


@dataclass(frozen=True)
class AllocationLine:
    shipment_line_id: str
    item_id: str
    customs_value: Decimal
    quantity: Decimal
    weight: Decimal | None
    volume: Decimal | None


def _money(value: Decimal, scale: int, mode: str) -> Decimal:
    require(value.is_finite() and value >= 0, "INVALID_MONEY")
    require(0 <= scale <= 6, "ROUNDING_SCALE_RANGE")
    require(mode in ROUNDING_MODES, "INVALID_ROUNDING_MODE")
    quantum = Decimal(1).scaleb(-scale)
    return value.quantize(quantum, rounding=ROUNDING_MODES[mode])


def _decimal(value: Decimal) -> str:
    return decimal_string(quantity_text(value))


def _validate_source(status: str, reference: str, *, purpose: str | None = None) -> None:
    require(status in SOURCE_STATUSES, "INVALID_SOURCE_STATUS")
    require(status not in {"UNVERIFIED", "NOT_FOUND"}, "CALCULABLE_SOURCE_UNVERIFIED")
    require(bool(reference.strip()), "SOURCE_REFERENCE_REQUIRED")
    if purpose == "OPERATIONAL":
        require(status == "AUTHORITATIVE", "OPERATIONAL_FIXTURE_FORBIDDEN")


def _fx_index(quotes: tuple[FxQuote, ...], *, purpose: str) -> dict[tuple[str, str], FxQuote]:
    result: dict[tuple[str, str], FxQuote] = {}
    for quote in quotes:
        key = (quote.from_currency, quote.to_currency)
        require(key not in result, "FX_QUOTE_DUPLICATE")
        require(quote.rate.is_finite() and quote.rate > 0, "FX_RATE_INVALID")
        _validate_source(quote.source_status, quote.source_reference, purpose=purpose)
        result[key] = quote
    return result


def _convert(
    amount: Decimal,
    from_currency: str,
    to_currency: str,
    quotes: Mapping[tuple[str, str], FxQuote],
    scale: int,
    mode: str,
) -> Decimal:
    require(amount.is_finite() and amount >= 0, "INVALID_MONEY")
    if from_currency == to_currency:
        return _money(amount, scale, mode)
    quote = quotes.get((from_currency, to_currency))
    require(quote is not None, "MISSING_EXCHANGE_RATE")
    return _money(amount * quote.rate, scale, mode)


def _blocking_status(request: LandedCostRequest) -> tuple[str | None, list[str]]:
    if request.hs_code_version is None or request.national_tariff_code is None:
        return "UNVERIFIED_CLASSIFICATION", ["HS_CLASSIFICATION_UNVERIFIED"]
    if request.manufacturing_origin_country_code is None:
        return "UNVERIFIED_ORIGIN", ["MANUFACTURING_ORIGIN_UNVERIFIED"]
    if (
        request.tariff_rule is not None
        and request.tariff_rule.tariff_basis == "PREFERENTIAL"
        and request.preferential_eligibility_status != "ELIGIBLE"
    ):
        return "UNVERIFIED_ORIGIN", ["PREFERENTIAL_ORIGIN_EVIDENCE_UNVERIFIED"]
    if request.tax_profile is None or request.tax_profile.recoverability_status == "UNVERIFIED":
        return "UNVERIFIED_TAX_RECOVERABILITY", ["TAX_RECOVERABILITY_UNVERIFIED"]
    currencies = {request.transaction_currency, *(charge.currency for charge in request.charges)}
    pairs = {(quote.from_currency, quote.to_currency) for quote in request.fx_quotes}
    if any(
        currency != request.cost_currency and (currency, request.cost_currency) not in pairs
        for currency in currencies
    ):
        return "MISSING_EXCHANGE_RATE", ["CUSTOMS_EXCHANGE_RATE_MISSING"]
    if request.tariff_rule is None:
        return "NOT_CALCULABLE", ["TARIFF_RULE_NOT_BOUND"]
    statuses = [
        request.transaction_source_status,
        *(charge.source_status for charge in request.charges),
        *(quote.source_status for quote in request.fx_quotes),
        request.tariff_rule.source_status,
        request.tax_profile.source_status,
    ]
    if any(status in {"UNVERIFIED", "NOT_FOUND"} for status in statuses):
        return "NOT_CALCULABLE", ["SOURCE_UNVERIFIED"]
    if request.calculation_purpose == "OPERATIONAL" and any(
        status != "AUTHORITATIVE" for status in statuses
    ):
        return "NOT_CALCULABLE", ["SOURCE_NOT_OPERATIONAL"]
    return None, []


def _base_assessment(
    request: LandedCostRequest,
    *,
    status: str,
    reasons: list[str],
    components: list[dict] | None = None,
    totals: tuple[Decimal, Decimal, Decimal, Decimal] | None = None,
) -> dict:
    tariff_basis = request.tariff_rule.tariff_basis if request.tariff_rule else None
    tax_status = request.tax_profile.recoverability_status if request.tax_profile else "UNVERIFIED"
    customs, gross, recoverable, net = totals or (None, None, None, None)
    return seal_landed_cost_assessment(
        {
            "contract_id": "io-landed-cost-assessment-v1",
            "contract_version": "1.0.0",
            "grain": dict(request.grain),
            "judgment_at": request.judgment_at,
            "calculation_purpose": request.calculation_purpose,
            "calculation_status": status,
            "blocking_reason_codes": reasons,
            "source_revision_set_hash": request.source_revision_set_hash,
            "hs_code_version": request.hs_code_version,
            "national_tariff_code": request.national_tariff_code,
            "manufacturing_origin_country_code": request.manufacturing_origin_country_code,
            "export_country_code": request.export_country_code,
            "import_country_code": request.import_country_code,
            "preferential_eligibility_status": request.preferential_eligibility_status,
            "tax_recoverability_status": tax_status,
            "tariff_basis": tariff_basis,
            "fixed_cost_allocation_basis": request.fixed_cost_allocation_basis,
            "cost_currency": request.cost_currency,
            "rounding_rule": {
                "currency_scale": request.currency_scale,
                "mode": request.rounding_mode,
                "application_stage": "LEGAL_RULE",
                "rule_reference": request.rounding_rule_reference,
            },
            "components": components or [],
            "customs_value_amount": None if customs is None else _decimal(customs),
            "gross_landed_cost_amount": None if gross is None else _decimal(gross),
            "recoverable_tax_amount": None if recoverable is None else _decimal(recoverable),
            "net_landed_cost_amount": None if net is None else _decimal(net),
            "operational_eligible": status == "CALCULABLE"
            and request.calculation_purpose == "OPERATIONAL",
        }
    )


def _component(
    component_type: str,
    amount: Decimal,
    *,
    source_status: str,
    source_reference: str,
    customs: bool,
    gross: bool = True,
    recoverable: bool = False,
    zero_reason: str | None = None,
) -> dict:
    require(component_type in COMPONENT_TYPES, "INVALID_COMPONENT_TYPE")
    return {
        "component_type": component_type,
        "amount": _decimal(amount),
        "source_status": source_status,
        "source_reference": source_reference,
        "zero_value_reason": zero_reason,
        "included_in_customs_value": customs,
        "included_in_gross_landed_cost": gross,
        "recoverable": recoverable,
    }


def _calculate_duty(
    request: LandedCostRequest,
    customs_value: Decimal,
) -> tuple[Decimal, list[dict]]:
    rule = request.tariff_rule
    require(rule is not None, "TARIFF_RULE_NOT_BOUND")
    _validate_source(
        rule.source_status,
        rule.source_reference,
        purpose=request.calculation_purpose,
    )
    require(rule.currency == request.cost_currency, "TARIFF_RULE_CURRENCY_MISMATCH")
    require(rule.duty_method in {"AD_VALOREM", "SPECIFIC", "COMPOUND"}, "DUTY_METHOD_INVALID")
    ad_valorem = Decimal("0")
    specific = Decimal("0")
    if rule.duty_method in {"AD_VALOREM", "COMPOUND"}:
        require(
            rule.ad_valorem_rate is not None and Decimal("0") <= rule.ad_valorem_rate <= 1,
            "AD_VALOREM_RATE_INVALID",
        )
        ad_valorem = customs_value * rule.ad_valorem_rate
    else:
        require(rule.ad_valorem_rate is None, "AD_VALOREM_RATE_NOT_APPLICABLE")
    if rule.duty_method in {"SPECIFIC", "COMPOUND"}:
        require(
            rule.specific_rate is not None and rule.specific_rate >= 0,
            "SPECIFIC_RATE_INVALID",
        )
        require(rule.specific_uom == request.quantity_uom, "SPECIFIC_UOM_MISMATCH")
        specific = request.quantity * rule.specific_rate
    else:
        require(
            rule.specific_rate is None and rule.specific_uom is None,
            "SPECIFIC_RATE_NOT_APPLICABLE",
        )
    raw = ad_valorem + specific
    if rule.minimum_duty_amount is not None:
        require(rule.minimum_duty_amount >= 0, "MINIMUM_DUTY_INVALID")
        raw = max(raw, rule.minimum_duty_amount)
    if rule.maximum_duty_amount is not None:
        require(rule.maximum_duty_amount >= 0, "MAXIMUM_DUTY_INVALID")
        if rule.minimum_duty_amount is not None:
            require(
                rule.maximum_duty_amount >= rule.minimum_duty_amount,
                "DUTY_MINIMUM_MAXIMUM_ORDER",
            )
        raw = min(raw, rule.maximum_duty_amount)
    duty = _money(raw, request.currency_scale, request.rounding_mode)
    if duty == 0:
        return duty, []
    clamped = duty != _money(
        ad_valorem + specific,
        request.currency_scale,
        request.rounding_mode,
    )
    if clamped or rule.duty_method == "COMPOUND":
        component_type = "CUSTOMS_DUTY_OTHER"
    elif rule.duty_method == "AD_VALOREM":
        component_type = "CUSTOMS_DUTY_AD_VALOREM"
    else:
        component_type = "CUSTOMS_DUTY_SPECIFIC"
    return duty, [
        _component(
            component_type,
            duty,
            source_status=rule.source_status,
            source_reference=rule.source_reference,
            customs=False,
        )
    ]


def calculate_landed_cost(request: LandedCostRequest) -> dict:
    """Calculate one shipment-line assessment or return a fail-closed result."""

    hash_value(request.source_revision_set_hash)
    require(request.transaction_value > 0, "TRANSACTION_VALUE_REQUIRED")
    require(request.quantity > 0, "SHIPMENT_QUANTITY_REQUIRED")
    identifier(request.quantity_uom)
    require(
        request.calculation_purpose in {"OPERATIONAL", "DEVELOPMENT_FIXTURE"}, "PURPOSE_INVALID"
    )
    require(
        request.fixed_cost_allocation_basis in {"CUSTOMS_VALUE", "WEIGHT", "VOLUME", "QUANTITY"},
        "ALLOCATION_BASIS_INVALID",
    )
    require(request.rounding_mode in ROUNDING_MODES, "INVALID_ROUNDING_MODE")
    status, reasons = _blocking_status(request)
    if status is not None:
        return _base_assessment(request, status=status, reasons=reasons)

    quotes = _fx_index(request.fx_quotes, purpose=request.calculation_purpose)
    _validate_source(
        request.transaction_source_status,
        request.transaction_source_reference,
        purpose=request.calculation_purpose,
    )
    transaction = _convert(
        request.transaction_value,
        request.transaction_currency,
        request.cost_currency,
        quotes,
        request.currency_scale,
        request.rounding_mode,
    )
    components = [
        _component(
            "TRANSACTION_VALUE",
            transaction,
            source_status=request.transaction_source_status,
            source_reference=request.transaction_source_reference,
            customs=True,
        )
    ]
    for charge in request.charges:
        require(charge.component_type in INPUT_CHARGE_TYPES, "INPUT_CHARGE_TYPE_INVALID")
        _validate_source(
            charge.source_status,
            charge.source_reference,
            purpose=request.calculation_purpose,
        )
        amount = _convert(
            charge.amount,
            charge.currency,
            request.cost_currency,
            quotes,
            request.currency_scale,
            request.rounding_mode,
        )
        components.append(
            _component(
                charge.component_type,
                amount,
                source_status=charge.source_status,
                source_reference=charge.source_reference,
                customs=charge.included_in_customs_value,
                gross=charge.included_in_gross_landed_cost,
                recoverable=charge.recoverable,
                zero_reason="SOURCE_CONFIRMED_ZERO" if amount == 0 else None,
            )
        )

    customs_value = sum(
        (Decimal(row["amount"]) for row in components if row["included_in_customs_value"]),
        Decimal("0"),
    )
    duty, duty_components = _calculate_duty(request, customs_value)
    components.extend(duty_components)

    tax = request.tax_profile
    require(tax is not None, "TAX_PROFILE_NOT_BOUND")
    _validate_source(
        tax.source_status,
        tax.source_reference,
        purpose=request.calculation_purpose,
    )
    require(Decimal("0") <= tax.import_vat_rate <= 1, "IMPORT_VAT_RATE_INVALID")
    vat_base = customs_value
    if tax.include_duty_in_vat_base:
        vat_base += duty
    if tax.include_excise_in_vat_base:
        vat_base += sum(
            (Decimal(row["amount"]) for row in components if row["component_type"] == "EXCISE_TAX"),
            Decimal("0"),
        )
    vat = _money(vat_base * tax.import_vat_rate, request.currency_scale, request.rounding_mode)
    if vat > 0:
        components.append(
            _component(
                "IMPORT_VAT",
                vat,
                source_status=tax.source_status,
                source_reference=tax.source_reference,
                customs=False,
                recoverable=tax.recoverability_status == "VERIFIED_RECOVERABLE",
            )
        )
    components.sort(key=lambda row: row["component_type"])
    gross = sum(
        (Decimal(row["amount"]) for row in components if row["included_in_gross_landed_cost"]),
        Decimal("0"),
    )
    recoverable = sum(
        (Decimal(row["amount"]) for row in components if row["recoverable"]),
        Decimal("0"),
    )
    return _base_assessment(
        request,
        status="CALCULABLE",
        reasons=[],
        components=components,
        totals=(customs_value, gross, recoverable, gross - recoverable),
    )


def allocate_fixed_charge(
    total_amount: Decimal,
    lines: tuple[AllocationLine, ...],
    *,
    basis: str,
    currency_scale: int,
) -> dict[str, str]:
    """Allocate a fixed shipment charge exactly using largest remainder."""

    require(total_amount.is_finite() and total_amount >= 0, "FIXED_CHARGE_INVALID")
    require(bool(lines), "ALLOCATION_LINES_REQUIRED")
    require(
        basis in {"CUSTOMS_VALUE", "WEIGHT", "VOLUME", "QUANTITY"},
        "ALLOCATION_BASIS_INVALID",
    )
    require(0 <= currency_scale <= 6, "ROUNDING_SCALE_RANGE")
    keys = [line.shipment_line_id for line in lines]
    require(len(keys) == len(set(keys)), "SHIPMENT_LINE_DUPLICATE")
    factors: dict[str, Decimal] = {}
    for line in lines:
        identifier(line.shipment_line_id)
        item_identifier(line.item_id)
        factor = {
            "CUSTOMS_VALUE": line.customs_value,
            "WEIGHT": line.weight,
            "VOLUME": line.volume,
            "QUANTITY": line.quantity,
        }[basis]
        require(factor is not None, f"{basis}_ALLOCATION_SOURCE_MISSING")
        require(factor.is_finite() and factor >= 0, "ALLOCATION_FACTOR_INVALID")
        factors[line.shipment_line_id] = factor
    denominator = sum(factors.values(), Decimal("0"))
    require(denominator > 0, "ALLOCATION_DENOMINATOR_ZERO")
    quantum = Decimal(1).scaleb(-currency_scale)
    rounded_total = total_amount.quantize(quantum, rounding=ROUND_HALF_UP)
    exact = {key: rounded_total * factor / denominator for key, factor in factors.items()}
    result = {key: value.quantize(quantum, rounding=ROUND_DOWN) for key, value in exact.items()}
    remainder_units = int((rounded_total - sum(result.values(), Decimal("0"))) / quantum)
    line_by_id = {line.shipment_line_id: line for line in lines}
    order = sorted(
        result,
        key=lambda key: (
            -(exact[key] - result[key]),
            key,
            line_by_id[key].item_id,
        ),
    )
    for key in order[:remainder_units]:
        result[key] += quantum
    require(sum(result.values(), Decimal("0")) == rounded_total, "ALLOCATION_NOT_CONSERVED")
    return {key: _decimal(result[key]) for key in sorted(result)}

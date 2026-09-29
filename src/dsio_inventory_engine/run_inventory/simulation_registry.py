"""Approved simulation inputs resolved by immutable Registry identity.

The bundled entries are development-only fixtures.  They exercise the same
hash and approval boundary as an authoritative registry without pretending
that the rates or shocks are production business values.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, localcontext
from typing import Any, Mapping

from dsio_inventory_engine.inventory_contracts.result_bundle import (
    derive_cost_profile_content_hash,
    derive_stress_scenario_content_hash,
)
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    canonical_json,
    decimal_string,
    digest,
    identifier,
    quantity_text,
    require,
)
from dsio_inventory_engine.prepare_inventory.application.inventory_input import (
    PreparedInventoryInput,
)


DEVELOPMENT_COST_PROFILE_PAYLOAD = {
    "currency": "KRW",
    "holding_cost_per_unit_week": "10",
    "backorder_cost_per_unit_week": "100",
    "fixed_order_cost": "1000",
    "variable_order_cost_per_unit": "2",
}
DEVELOPMENT_STRESS_PAYLOADS = {
    "DEMAND_SURGE_20_PERCENT": {
        "forecast_multiplier": "1.2",
        "customer_order_multiplier": "1",
        "confirmed_receipt_delay_days": 0,
    },
    "CONFIRMED_RECEIPT_DELAY_7_DAYS": {
        "forecast_multiplier": "1",
        "customer_order_multiplier": "1",
        "confirmed_receipt_delay_days": 7,
    },
}
_COST_PROFILE_ID = "DEV-KRW-COST-BASELINE"
_COST_PROFILE_VERSION = "1.0.0"
_STRESS_CONTRACT_KEY = "inventory.stress_scenario"
_STRESS_CONTRACT_VERSION = "1.0.0"
_RUNNER_ID = "inventory.stress.demand_receipt_transform"
_RUNNER_VERSION = "1.0.0"
_RUNNER_HASH = digest(
    {
        "implementation_id": _RUNNER_ID,
        "version": _RUNNER_VERSION,
        "behavior": "scale_forecast_and_customer_orders_then_delay_confirmed_receipts",
    }
)


@dataclass(frozen=True, slots=True)
class DevelopmentSimulationRegistry:
    """Resolve approved development Cost and Stress entries deterministically."""

    approval_reference: str = "DEV-SIMULATION-REGISTRY-APPROVAL-20260915"

    def __post_init__(self) -> None:
        identifier(self.approval_reference)

    def cost_profile_binding(self) -> dict[str, Any]:
        payload = validate_cost_profile_payload(DEVELOPMENT_COST_PROFILE_PAYLOAD)
        payload_hash = digest(payload)
        return {
            "cost_profile_id": _COST_PROFILE_ID,
            "profile_contract_key": "inventory.cost_profile",
            "profile_contract_version": _COST_PROFILE_VERSION,
            "profile_payload_content_hash": payload_hash,
            "profile_content_hash": derive_cost_profile_content_hash(
                cost_profile_id=_COST_PROFILE_ID,
                profile_contract_key="inventory.cost_profile",
                profile_contract_version=_COST_PROFILE_VERSION,
                profile_payload_content_hash=payload_hash,
            ),
            "source_type": "DEVELOPMENT_SYNTHETIC",
            "approval_reference": self.approval_reference,
        }

    def resolved_cost_profile(self):
        from .psi_orchestrator import ResolvedCostProfile

        binding = self.cost_profile_binding()
        payload = validate_cost_profile_payload(DEVELOPMENT_COST_PROFILE_PAYLOAD)
        return ResolvedCostProfile(
            **binding,
            profile_payload_json=canonical_json(payload),
        )

    def stress_scenario_bindings(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            self._stress_binding(scenario_id, payload)
            for scenario_id, payload in sorted(DEVELOPMENT_STRESS_PAYLOADS.items())
        )

    def resolved_stress_scenarios(self) -> dict[str, Any]:
        from .psi_orchestrator import ResolvedStressScenario

        result = {}
        for scenario_id, payload in sorted(DEVELOPMENT_STRESS_PAYLOADS.items()):
            normalized = validate_stress_payload(payload)
            binding = self._stress_binding(scenario_id, normalized)
            binding.pop("scenario_type")
            result[scenario_id] = ResolvedStressScenario(
                **binding,
                scenario_payload_json=canonical_json(normalized),
                runner_factory=DemandReceiptStressRunner,
            )
        return result

    def _stress_binding(self, scenario_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        identifier(scenario_id)
        normalized = validate_stress_payload(payload)
        payload_hash = digest(normalized)
        seed = 20260915
        return {
            "scenario_id": scenario_id,
            "scenario_type": "STRESS",
            "scenario_contract_key": _STRESS_CONTRACT_KEY,
            "scenario_contract_version": _STRESS_CONTRACT_VERSION,
            "scenario_payload_content_hash": payload_hash,
            "deterministic_seed": seed,
            "scenario_content_hash": derive_stress_scenario_content_hash(
                scenario_id=scenario_id,
                scenario_contract_key=_STRESS_CONTRACT_KEY,
                scenario_contract_version=_STRESS_CONTRACT_VERSION,
                scenario_payload_content_hash=payload_hash,
                deterministic_seed=seed,
            ),
            "runner_implementation_id": _RUNNER_ID,
            "runner_implementation_version": _RUNNER_VERSION,
            "runner_implementation_content_hash": _RUNNER_HASH,
        }


def validate_cost_profile_payload(value: Mapping[str, Any]) -> dict[str, str]:
    require(type(value) is dict, "COST_PROFILE_PAYLOAD_INVALID")
    expected = {
        "currency",
        "holding_cost_per_unit_week",
        "backorder_cost_per_unit_week",
        "fixed_order_cost",
        "variable_order_cost_per_unit",
    }
    require(set(value) == expected, "COST_PROFILE_PAYLOAD_INVALID")
    currency = value["currency"]
    require(
        isinstance(currency, str) and len(currency) == 3 and currency.isupper(),
        "COST_PROFILE_CURRENCY_INVALID",
    )
    result = {"currency": currency}
    for key in sorted(expected - {"currency"}):
        try:
            raw = decimal_string(value[key])
        except InventoryInputError as exc:
            raise InventoryInputError("COST_PROFILE_RATE_INVALID") from exc
        require(raw == value[key] and Decimal(raw) >= 0, "COST_PROFILE_RATE_INVALID")
        result[key] = raw
    return result


def validate_stress_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    require(type(value) is dict, "STRESS_SCENARIO_PAYLOAD_INVALID")
    require(
        set(value)
        == {
            "forecast_multiplier",
            "customer_order_multiplier",
            "confirmed_receipt_delay_days",
        },
        "STRESS_SCENARIO_PAYLOAD_INVALID",
    )
    forecast = decimal_string(value["forecast_multiplier"])
    orders = decimal_string(value["customer_order_multiplier"])
    delay = value["confirmed_receipt_delay_days"]
    require(
        forecast == value["forecast_multiplier"]
        and orders == value["customer_order_multiplier"]
        and Decimal(forecast) >= 0
        and Decimal(orders) >= 0
        and type(delay) is int
        and 0 <= delay <= 365,
        "STRESS_SCENARIO_PAYLOAD_INVALID",
    )
    return {
        "forecast_multiplier": forecast,
        "customer_order_multiplier": orders,
        "confirmed_receipt_delay_days": delay,
    }


class DemandReceiptStressRunner:
    """Apply a sealed demand multiplier and committed-receipt delay only."""

    def transform(
        self,
        *,
        prepared_input: PreparedInventoryInput,
        scenario_payload: Mapping[str, Any],
        deterministic_seed: int,
    ) -> PreparedInventoryInput:
        require(
            type(deterministic_seed) is int and deterministic_seed >= 0,
            "INVALID_DETERMINISTIC_SEED",
        )
        payload = validate_stress_payload(scenario_payload)
        document = copy.deepcopy(prepared_input.to_dict())
        with localcontext() as context:
            context.prec = 40
            forecast_multiplier = Decimal(payload["forecast_multiplier"])
            order_multiplier = Decimal(payload["customer_order_multiplier"])
            for row in document["demands"]:
                for key in ("gross_forecast_qty", "forecast_consumed_qty", "net_forecast_qty"):
                    if row[key] is not None:
                        row[key] = quantity_text(Decimal(row[key]) * forecast_multiplier)
                row["confirmed_customer_order_qty"] = quantity_text(
                    Decimal(row["confirmed_customer_order_qty"]) * order_multiplier
                )
            self._delay_receipts(
                document,
                delay_days=payload["confirmed_receipt_delay_days"],
            )
        manifest = dict(document["manifest"])
        manifest.pop("prepared_content_hash", None)
        manifest["receipt_decisions_hash"] = digest(document["receipt_decisions"])
        unsigned = {
            "manifest": manifest,
            **{key: value for key, value in document.items() if key != "manifest"},
        }
        document["manifest"] = {**manifest, "prepared_content_hash": digest(unsigned)}
        return PreparedInventoryInput(canonical_json(document))

    @staticmethod
    def _delay_receipts(document: dict[str, Any], *, delay_days: int) -> None:
        if delay_days == 0:
            return
        bucket_by_date: dict[str, str] = {}
        for bucket in document["calendar"]:
            start = date.fromisoformat(bucket["start_date"])
            end = date.fromisoformat(bucket["end_date"])
            for offset in range((end - start).days + 1):
                bucket_by_date[(start + timedelta(days=offset)).isoformat()] = bucket["yyyyww"]
        for row in document["receipt_decisions"]:
            if row["status"] not in {"CONFIRMED", "IN_TRANSIT"}:
                continue
            row["due_date"] = (
                date.fromisoformat(row["due_date"]) + timedelta(days=delay_days)
            ).isoformat()
            row["yyyyww"] = bucket_by_date.get(row["due_date"])
            row["exclusion_reason"] = None if row["yyyyww"] is not None else "OUTSIDE_PLAN_HORIZON"
            row["included_qty"] = row["due_qty"] if row["yyyyww"] is not None else "0"


__all__ = [
    "DEVELOPMENT_COST_PROFILE_PAYLOAD",
    "DEVELOPMENT_STRESS_PAYLOADS",
    "DemandReceiptStressRunner",
    "DevelopmentSimulationRegistry",
    "validate_cost_profile_payload",
    "validate_stress_payload",
]

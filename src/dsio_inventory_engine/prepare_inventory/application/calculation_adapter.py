"""Canonical -> explicit C# boundary. Opt-in local calculations, never a Gate.

Legacy parameters are separate sealed evidence, not inferred from target policy
names. Baseline target uses the separate exact transition. Recommended target
strategy/Guard orchestration is deliberately not implemented here.
"""

from datetime import date, timedelta
from decimal import Decimal, localcontext
import math
import re
from typing import Protocol

from dsio_inventory_engine.inventory_contracts.calculation_boundary import (
    CalculationBoundaryBinding,
)
from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.values import (
    day,
    digest,
    identifier,
    item_identifier,
    quantity_text,
    require,
    text,
)
from .inventory_input import PreparedInventoryInput, validate_prepared_projection

LEGACY_NUMBERS = (
    "demand",
    "due_in",
    "boh",
    "eoh",
    "backorder",
    "backorder_close",
    "pending_po_close",
    "purchase_order",
    "demand_allocation",
    "stock_allocation",
    "weekly_allocation_rate",
    "exp_mos",
    "rop",
    "target",
)
TARGET_NUMBERS = (
    "boh_qty",
    "reserved_qty",
    "on_hand_boh_qty",
    "confirmed_supplier_receipt_qty",
    "recommended_receipt_qty",
    "available_qty",
    "backorder_open_qty",
    "fulfilled_backorder_qty",
    "fulfilled_confirmed_customer_order_qty",
    "fulfilled_forecast_qty",
    "eoh_qty",
    "on_hand_eoh_qty",
    "backorder_close_qty",
    "forecast_shortage_qty",
)


class CalculationKernel(Protocol):
    dll_sha256: str

    def run_legacy(self, payload: dict) -> dict: ...

    def advance_target(self, quantities: dict) -> dict: ...


def fields(value: dict, names) -> None:
    require(type(value) is dict and set(value) == set(names), "CALCULATION_FIELDS")


def target_number(value: str) -> Decimal:
    require(
        type(value) is str
        and len(value) <= 48
        and re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,6})?", value) is not None,
        "CALCULATION_TARGET_PRECISION",
    )
    number = Decimal(value)
    # Match the DLL fixed-six-place integer bound without Decimal context rounding.
    digits = value.split(".")[0].lstrip("0")
    require(len(digits) <= 34, "CALCULATION_TARGET_PRECISION")
    return number


def legacy_number(value: str) -> str:
    require(type(value) is str and len(value) <= 128, "CALCULATION_LEGACY_NUMBER")
    try:
        number = Decimal(value)
        binary = float(number)
        ok = number.is_finite() and number >= 0 and math.isfinite(binary)
        ok = ok and Decimal(str(binary)) == number
    except (ValueError, ArithmeticError):
        ok = False
    require(ok, "CALCULATION_LEGACY_PRECISION")
    # 0.1 is intentionally binary64 in replay. Values losing decimal round-trip
    # digits (e.g. 9007199254740993) are refused, not silently rounded.
    return value


def receipt_totals(source: dict) -> dict:
    totals = {}
    with localcontext() as ctx:
        ctx.prec = 40
        for row in source["receipt_decisions"]:
            key = row["item_id"], row["yyyyww"]
            totals[key] = totals.get(key, Decimal(0)) + Decimal(row["included_qty"])
    return totals


class CanonicalCalculationAdapter:
    def __init__(self, deployment: DeploymentScope, kernel: CalculationKernel):
        self.deployment, self.kernel = deployment, kernel

    def _source(self, canonical, prepared, binding, *, scenario, parameters=None, reference=None):
        canonical = CanonicalInputRequest.from_dict(canonical.to_dict())
        verified = validate_prepared_projection(canonical, prepared, self.deployment).to_dict()
        legacy = parameters is not None
        binding.verify_sources(
            canonical,
            prepared_content_hash=verified["manifest"]["prepared_content_hash"],
            psi_simulator={
                "implementation_id": "inventory.psi.legacy_replay"
                if legacy
                else "inventory.psi.target_guarded.csharp",
                "version": "1.0.0",
                "implementation_content_hash": self.kernel.dll_sha256,
            },
            calculation_backend="CSHARP_PYTHONNET",
            semantics_profile="LEGACY_REPLAY" if legacy else "TARGET_GUARDED",
            psi_scenario_type=scenario,
            legacy_parameters_binding={
                "reference": text(reference),
                "content_hash": digest(parameters),
            }
            if legacy
            else None,
        )
        return verified

    def prepare_legacy(
        self,
        canonical: CanonicalInputRequest,
        prepared: PreparedInventoryInput,
        binding: CalculationBoundaryBinding,
        *,
        parameters: dict,
        reference: str,
    ) -> dict:
        require(type(parameters) is dict, "LEGACY_PARAMETERS_REQUIRED")
        canonical = CanonicalInputRequest.from_dict(canonical.to_dict())
        binding = CalculationBoundaryBinding.from_dict(binding.to_dict())
        source = self._source(
            canonical,
            prepared,
            binding,
            scenario="RECOMMENDED",
            parameters=parameters,
            reference=reference,
        )
        fields(parameters, ("canonical_input_hash", "prepared_content_hash", "source_type", "rows"))
        require(
            parameters["canonical_input_hash"] == canonical.input_hash
            and parameters["prepared_content_hash"] == source["manifest"]["prepared_content_hash"],
            "CALCULATION_LEGACY_SOURCE_MISMATCH",
        )
        require(
            parameters["source_type"] == "LEGACY_SOURCE"
            or (
                parameters["source_type"] == "SYNTHETIC_DEVELOPMENT"
                and self.deployment.environment == "DEVELOPMENT"
            ),
            "CALCULATION_LEGACY_SOURCE_TYPE",
        )
        master = {r["item_id"] for r in source["master"]}
        rows = parameters["rows"]
        require(type(rows) is list and bool(rows), "CALCULATION_LEGACY_COVERAGE")
        require(len(rows) * len(source["calendar"]) <= 200_000, "CALCULATION_GRID_LIMIT")
        positions = {r["item_id"]: r for r in source["positions"]}
        demands = {(r["item_id"], r["yyyyww"]): r for r in source["demands"]}
        receipts = receipt_totals(source)
        requests, seen = [], set()
        with localcontext() as ctx:
            ctx.prec = 40
            for row in rows:
                fields(
                    row,
                    (
                        "item_id",
                        "candidate_id",
                        "stock_policy",
                        "internal_lead_time",
                        "lot",
                        "policies",
                    ),
                )
                item, candidate = item_identifier(row["item_id"]), identifier(row["candidate_id"])
                require(
                    item in master and (item, candidate) not in seen, "CALCULATION_LEGACY_COVERAGE"
                )
                seen.add((item, candidate))
                require(
                    row["stock_policy"] in {"OUTL", "ROPQ", "MINQ", "SFST"},
                    "CALCULATION_LEGACY_POLICY",
                )
                fields(row["lot"], ("minimum", "maximum", "step"))
                for n in row["lot"].values():
                    legacy_number(n)
                lo, hi, step = (Decimal(row["lot"][k]) for k in ("minimum", "maximum", "step"))
                require(lo <= hi and step > 0, "CALCULATION_LEGACY_LOT")
                lead = legacy_number(row["internal_lead_time"])
                days = int(float(lead) / 28800)
                require(days <= 2147483647, "CALCULATION_LEGACY_LEAD_RANGE")
                try:
                    date.fromisoformat(source["calendar"][-1]["start_date"]) + timedelta(days=days)
                except (ValueError, OverflowError):
                    require(False, "CALCULATION_LEGACY_LEAD_RANGE")
                policies = row["policies"]
                require(
                    type(policies) is list and bool(policies), "CALCULATION_LEGACY_POLICY_COVERAGE"
                )
                for policy in policies:
                    fields(policy, ("start", "end", "rop", "target", "mos", "ma_demand"))
                    require(
                        day(policy["start"]) <= day(policy["end"]),
                        "CALCULATION_LEGACY_POLICY_PERIOD",
                    )
                    for k in ("rop", "target", "mos", "ma_demand"):
                        legacy_number(policy[k])
                demand_rows, receipt_rows = [], []
                for bucket in source["calendar"]:
                    month = bucket["base_month"][:4] + "-" + bucket["base_month"][4:] + "-01"
                    require(
                        any(p["start"] <= month <= p["end"] for p in policies),
                        "CALCULATION_LEGACY_POLICY_COVERAGE",
                    )
                    demand = demands[(item, bucket["yyyyww"])]
                    qty = sum(
                        (
                            Decimal(demand[k])
                            for k in ("net_forecast_qty", "confirmed_customer_order_qty")
                        ),
                        Decimal(0),
                    )
                    demand_rows.append(
                        {
                            "when": bucket["start_date"],
                            "quantity": legacy_number(quantity_text(qty)),
                        }
                    )
                    receipt = receipts.get((item, bucket["yyyyww"]), Decimal(0))
                    receipt_rows.append(
                        {
                            "when": bucket["start_date"],
                            "quantity": legacy_number(quantity_text(receipt)),
                        }
                    )
                position = positions[item]
                pending = sum((Decimal(r["quantity"]) for r in receipt_rows), Decimal(0))
                requests.append(
                    {
                        "item_id": item,
                        "candidate_id": candidate,
                        "boh": legacy_number(position["available_qty"]),
                        "backorder": legacy_number(position["backorder_qty"]),
                        "pending_po": legacy_number(quantity_text(pending)),
                        "internal_lead_time": lead,
                        "stock_policy": row["stock_policy"],
                        "lot": dict(row["lot"]),
                        "policies": [dict(p) for p in policies],
                        "demands": demand_rows,
                        "receipts": receipt_rows,
                    }
                )
        require({i for i, _ in seen} == master, "CALCULATION_LEGACY_COVERAGE")
        return {"binding": binding.to_dict(), "calendar": source["calendar"], "requests": requests}

    def run_legacy(self, canonical, prepared, binding, *, parameters, reference) -> dict:
        payload = self.prepare_legacy(
            canonical, prepared, binding, parameters=parameters, reference=reference
        )
        result = self.kernel.run_legacy(payload)
        fields(result, ("binding", "rows"))
        require(result["binding"] == payload["binding"], "CALCULATION_RESULT_BINDING")
        expected = [(r, b) for r in payload["requests"] for b in payload["calendar"]]
        require(
            type(result["rows"]) is list and len(result["rows"]) == len(expected),
            "CALCULATION_RESULT_COVERAGE",
        )
        for row, (request, bucket) in zip(result["rows"], expected, strict=True):
            fields(
                row, ("item_id", "candidate_id", "yyyyww", "quantities", "order_date", "due_date")
            )
            require(
                (row["item_id"], row["candidate_id"], row["yyyyww"])
                == (request["item_id"], request["candidate_id"], bucket["yyyyww"]),
                "CALCULATION_RESULT_COVERAGE",
            )
            fields(row["quantities"], LEGACY_NUMBERS)
            for key, n in row["quantities"].items():
                require(type(n) is str and len(n) <= 128, "CALCULATION_NONFINITE_RESULT")
                try:
                    value = float(n)
                except ValueError:
                    value = float("nan")
                require(
                    math.isfinite(value) and (value >= 0 or key == "pending_po_close"),
                    "CALCULATION_NONFINITE_RESULT",
                )
            order = bucket["start_date"] if float(row["quantities"]["purchase_order"]) > 0 else None
            due = (
                None
                if order is None
                else (
                    date.fromisoformat(order)
                    + timedelta(days=int(float(request["internal_lead_time"]) / 28800))
                ).isoformat()
            )
            require(
                (row["order_date"], row["due_date"]) == (order, due), "CALCULATION_RESULT_DATES"
            )
        return result

    def run_target_baseline(self, canonical, prepared, binding) -> dict:
        canonical = CanonicalInputRequest.from_dict(canonical.to_dict())
        binding = CalculationBoundaryBinding.from_dict(binding.to_dict())
        source = self._source(canonical, prepared, binding, scenario="BASELINE")
        positions = {r["item_id"]: r for r in source["positions"]}
        demands = {(r["item_id"], r["yyyyww"]): r for r in source["demands"]}
        receipts = receipt_totals(source)
        rows = []
        scope = {k: source["context"][k] for k in ("company_cd", "subs_cd", "site_cd")}
        for item in source["master"]:
            state = {
                k: positions[item["item_id"]][k]
                for k in ("available_qty", "reserved_qty", "backorder_qty")
            }
            for bucket in source["calendar"]:
                demand = demands[(item["item_id"], bucket["yyyyww"])]
                receipt = receipts.get((item["item_id"], bucket["yyyyww"]), Decimal(0))
                quantities = {
                    **state,
                    **{k: demand[k] for k in ("confirmed_customer_order_qty", "net_forecast_qty")},
                    "confirmed_supplier_receipt_qty": quantity_text(receipt),
                    "recommended_receipt_qty": "0",
                }
                for n in quantities.values():
                    target_number(n)
                result = self.kernel.advance_target(quantities)
                verify_target_result(quantities, result)
                rows.append(
                    {**scope, **bucket, **demand, "psi_scenario_type": "BASELINE", **result}
                )
                state = {
                    "available_qty": result["eoh_qty"],
                    "reserved_qty": result["reserved_qty"],
                    "backorder_qty": result["backorder_close_qty"],
                }
        # Existing PSI row/hash semantics, no new Run/Snapshot IDs or receipts.
        return {"binding": binding.to_dict(), "psi_rows": rows, "psi_content_hash": digest(rows)}


def verify_target_result(given: dict, result: dict) -> None:
    """Conservation and input echoes; full numerical equivalence is tested separately."""
    fields(result, TARGET_NUMBERS)
    with localcontext() as ctx:
        ctx.prec = 40
        q = {k: target_number(v) for k, v in result.items()}
        g = {k: target_number(v) for k, v in given.items()}
        require(
            q["boh_qty"] == g["available_qty"]
            and q["reserved_qty"] == g["reserved_qty"]
            and q["backorder_open_qty"] == g["backorder_qty"]
            and q["confirmed_supplier_receipt_qty"] == g["confirmed_supplier_receipt_qty"]
            and q["recommended_receipt_qty"] == g["recommended_receipt_qty"]
            and q["available_qty"]
            == g["available_qty"]
            + g["confirmed_supplier_receipt_qty"]
            + g["recommended_receipt_qty"]
            and q["on_hand_boh_qty"] == q["boh_qty"] + q["reserved_qty"]
            and q["on_hand_eoh_qty"] == q["eoh_qty"] + q["reserved_qty"]
            and q["available_qty"]
            == q["eoh_qty"]
            + q["fulfilled_backorder_qty"]
            + q["fulfilled_confirmed_customer_order_qty"]
            + q["fulfilled_forecast_qty"]
            and g["backorder_qty"] + g["confirmed_customer_order_qty"]
            == q["backorder_close_qty"]
            + q["fulfilled_backorder_qty"]
            + q["fulfilled_confirmed_customer_order_qty"]
            and g["net_forecast_qty"] == q["forecast_shortage_qty"] + q["fulfilled_forecast_qty"],
            "CALCULATION_TARGET_CONSERVATION",
        )
        require(
            q["fulfilled_backorder_qty"] == min(q["available_qty"], g["backorder_qty"])
            and q["fulfilled_confirmed_customer_order_qty"]
            == min(
                q["available_qty"] - q["fulfilled_backorder_qty"], g["confirmed_customer_order_qty"]
            )
            and q["fulfilled_forecast_qty"]
            == min(
                q["available_qty"]
                - q["fulfilled_backorder_qty"]
                - q["fulfilled_confirmed_customer_order_qty"],
                g["net_forecast_qty"],
            ),
            "CALCULATION_TARGET_ALLOCATION_ORDER",
        )

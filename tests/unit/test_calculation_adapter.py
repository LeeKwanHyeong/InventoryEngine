import copy
from decimal import Decimal

import pytest

from dsio_inventory_engine.inventory_contracts.calculation_boundary import (
    CalculationBoundaryBinding,
)
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    canonical_json,
    digest,
)
from dsio_inventory_engine.prepare_inventory.application.calculation_adapter import (
    CanonicalCalculationAdapter,
    LEGACY_NUMBERS,
    legacy_number,
    target_number,
    verify_target_result,
)
from dsio_inventory_engine.prepare_inventory.application.inventory_input import (
    PreparedInventoryInput,
)
from dsio_inventory_engine.simulate_inventory.application.baseline import RunPsiSimulationUseCase
from dsio_inventory_engine.simulate_inventory.application.step import InventoryState, advance_bucket
from tests.support.calculation_adapter_fixtures import DEPLOYMENT, bind, case


class ReferencePort:
    """Unit tests only. Actual DLL execution is a separate isolated verification."""

    dll_sha256 = "a" * 64

    def advance_target(self, quantities):
        state = InventoryState(
            *(Decimal(quantities[k]) for k in ("available_qty", "reserved_qty", "backorder_qty"))
        )
        return advance_bucket(
            state,
            **{
                k: Decimal(v)
                for k, v in quantities.items()
                if k not in {"available_qty", "reserved_qty", "backorder_qty"}
            },
        )[1]


class ReferenceLegacyPort(ReferencePort):
    def run_legacy(self, payload):
        from datetime import timedelta
        from tests.support.legacy_reference_fixtures import replay_request
        from dsio_inventory_engine.simulate_inventory.application.legacy_reference import (
            run_legacy_reference,
        )

        rows = []
        for request in payload["requests"]:
            given = {
                **request,
                "buckets": [
                    {
                        "bucket_id": r["yyyyww"],
                        "start": r["start_date"],
                        "end": r["end_date"],
                        "base_month": r["base_month"],
                    }
                    for r in payload["calendar"]
                ],
            }
            for row in run_legacy_reference(replay_request(given)):
                order = row.bucket.start if row.purchase_order > 0 else None
                days = int(float(request["internal_lead_time"]) / 28800)
                rows.append(
                    {
                        "item_id": request["item_id"],
                        "candidate_id": request["candidate_id"],
                        "yyyyww": row.bucket.bucket_id,
                        "quantities": {k: str(getattr(row, k)) for k in LEGACY_NUMBERS},
                        "order_date": None if order is None else order.isoformat(),
                        "due_date": None
                        if order is None
                        else (order + timedelta(days=days)).isoformat(),
                    }
                )
        return {"binding": copy.deepcopy(payload["binding"]), "rows": rows}


def prepare(parts=None):
    canonical, prepared, binding, params, reference = parts or case()
    return CanonicalCalculationAdapter(DEPLOYMENT, ReferencePort()).prepare_legacy(
        canonical, prepared, binding, parameters=params, reference=reference
    )


def test_fresh_canonical_mapping_is_immutable_and_not_fixed_to_benchmark_grain():
    parts = case(candidates=33)
    before = [p.to_dict() if hasattr(p, "to_dict") else copy.deepcopy(p) for p in parts]
    a, b = prepare(parts), prepare(parts)
    assert a == b and len(a["requests"]) == 33 and len(a["calendar"]) == 3
    assert before == [p.to_dict() if hasattr(p, "to_dict") else p for p in parts]


def test_aggregate_receipts_and_consumed_forecast_exactly_once():
    parts = case(
        items=[
            {
                "id": "ITEM-A",
                "boh": "10",
                "orders": ["8", "0", "0"],
                "forecast": ["10", "10", "10"],
                "receipts": [
                    {"week": 0, "qty": "4", "status": "CONFIRMED"},
                    {"week": 0, "qty": "6", "status": "IN_TRANSIT"},
                    {"week": 1, "qty": "99", "status": "UNVERIFIED_DUE_IN"},
                ],
            }
        ]
    )
    row = prepare(parts)["requests"][0]
    assert row["demands"][0]["quantity"] == "10"  # 2 net forecast + 8 customer orders.
    assert row["receipts"][0]["quantity"] == row["pending_po"] == "10"
    assert row["receipts"][1]["quantity"] == "0"


@pytest.mark.parametrize(
    "mutation",
    ["empty", "duplicate", "orphan", "no_policy", "gap", "lot", "lead", "precision", "unknown"],
)
def test_missing_or_unsupported_legacy_parameters_fail_after_valid_seal(mutation):
    canonical, prepared, _, params, ref = case()
    if mutation == "empty":
        params["rows"] = []
    elif mutation == "duplicate":
        params["rows"] *= 2
    elif mutation == "orphan":
        params["rows"][0]["item_id"] = "ORPHAN"
    elif mutation == "no_policy":
        params["rows"][0]["policies"] = []
    elif mutation == "gap":
        params["rows"][0]["policies"][0]["end"] = "2026-08-31"
    elif mutation == "lot":
        params["rows"][0]["lot"]["step"] = "0"
    elif mutation == "lead":
        params["rows"][0]["internal_lead_time"] = "28800000000000"
    elif mutation == "precision":
        params["rows"][0]["lot"]["maximum"] = "9007199254740993"
    else:
        params["rows"][0]["guess"] = True
    binding = bind(canonical, prepared, "a" * 64, params=params)
    with pytest.raises((InventoryInputError, ValueError)):
        prepare((canonical, prepared, binding, params, ref))


@pytest.mark.parametrize("mutation", ["params", "reference", "prepared", "build", "run", "profile"])
def test_bound_source_cannot_be_substituted(mutation):
    canonical, prepared, binding, params, ref = case()
    port = ReferencePort()
    if mutation == "params":
        params["rows"][0]["policies"][0]["rop"] = "20"
    elif mutation == "reference":
        ref = "fixture:other"
    elif mutation == "prepared":
        document = prepared.to_dict()
        document["positions"][0]["available_qty"] = "999"
        del document["manifest"]["prepared_content_hash"]
        document["manifest"]["prepared_content_hash"] = digest(document)
        prepared = PreparedInventoryInput(canonical_json(document))
    elif mutation == "build":
        port.dll_sha256 = "b" * 64
    elif mutation == "run":
        from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest

        document = canonical.to_dict()
        document["context"]["engine_run_id"] = "OTHER-RUN"
        canonical = CanonicalInputRequest.from_dict(document)
    else:
        binding = bind(canonical, prepared, "a" * 64, legacy=False)
    with pytest.raises(InventoryInputError):
        CanonicalCalculationAdapter(DEPLOYMENT, port).prepare_legacy(
            canonical, prepared, binding, parameters=params, reference=ref
        )


@pytest.mark.parametrize(
    "value", ["9007199254740993", "NaN", "Infinity", "-1", "1e-999", "0.1234567890123456789"]
)
def test_no_silent_legacy_precision_loss(value):
    with pytest.raises(InventoryInputError):
        legacy_number(value)


@pytest.mark.parametrize("value", ["0.0000001", "-0", "1e-6", "NaN", "9" * 35, "01"])
def test_no_target_precision_downgrade(value):
    with pytest.raises(InventoryInputError):
        target_number(value)


def test_target_baseline_mapping_preserves_existing_rows_and_hash():
    canonical, prepared, binding, _, _ = case(legacy=False)
    actual = CanonicalCalculationAdapter(DEPLOYMENT, ReferencePort()).run_target_baseline(
        canonical, prepared, binding
    )
    expected = RunPsiSimulationUseCase(DEPLOYMENT).execute(canonical)
    assert actual["psi_rows"] == expected["psi_rows"]
    assert actual["psi_content_hash"] == expected["psi_content_hash"]


def test_target_baseline_does_not_accept_recommended_or_legacy_binding():
    canonical, prepared, binding, _, _ = case()
    with pytest.raises(InventoryInputError):
        CanonicalCalculationAdapter(DEPLOYMENT, ReferencePort()).run_target_baseline(
            canonical, prepared, binding
        )


def test_target_result_cannot_change_receipts_or_reserved():
    given = {
        k: "1"
        for k in (
            "available_qty",
            "reserved_qty",
            "backorder_qty",
            "confirmed_customer_order_qty",
            "net_forecast_qty",
            "confirmed_supplier_receipt_qty",
            "recommended_receipt_qty",
        )
    }
    actual = ReferencePort().advance_target(given)
    verify_target_result(given, actual)
    actual["reserved_qty"] = "2"
    with pytest.raises(InventoryInputError):
        verify_target_result(given, actual)


def test_existing_binding_version_does_not_register_new_backend():
    canonical, prepared, binding, _, _ = case(legacy=False)
    document = binding.to_dict()
    document["psi_simulator"]["version"] = "9.0.0"
    with pytest.raises(InventoryInputError):
        CanonicalCalculationAdapter(DEPLOYMENT, ReferencePort()).run_target_baseline(
            canonical, prepared, CalculationBoundaryBinding.from_dict(document)
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "row_missing",
        "row_duplicate",
        "binding",
        "item",
        "candidate",
        "bucket",
        "quantity_field",
        "nan",
        "negative",
        "order_date",
        "due_date",
        "extra",
    ],
)
def test_output_coverage_numbers_dates_and_binding_tampering_rejected(mutation):
    class TamperedPort(ReferenceLegacyPort):
        def run_legacy(self, payload):
            value = super().run_legacy(payload)
            row = value["rows"][0]
            if mutation == "row_missing":
                value["rows"].pop()
            elif mutation == "row_duplicate":
                value["rows"][1] = row
            elif mutation == "binding":
                value["binding"]["engine_run_id"] = "OTHER"
            elif mutation in {"item", "candidate"}:
                row[mutation + "_id"] = "OTHER"
            elif mutation == "bucket":
                row["yyyyww"] = "202601"
            elif mutation == "quantity_field":
                row["quantities"].pop("rop")
            elif mutation == "nan":
                row["quantities"]["eoh"] = "NaN"
            elif mutation == "negative":
                row["quantities"]["eoh"] = "-1"
            elif mutation == "order_date":
                row["order_date"] = "2020-01-01"
            elif mutation == "due_date":
                row["due_date"] = "2020-01-01"
            else:
                row["approved"] = True
            return value

    canonical, prepared, binding, params, ref = case()
    with pytest.raises(InventoryInputError):
        CanonicalCalculationAdapter(DEPLOYMENT, TamperedPort()).run_legacy(
            canonical, prepared, binding, parameters=params, reference=ref
        )


def test_valid_legacy_output_and_existing_source_refs_preserved():
    canonical, prepared, binding, params, ref = case()
    output = CanonicalCalculationAdapter(DEPLOYMENT, ReferenceLegacyPort()).run_legacy(
        canonical, prepared, binding, parameters=params, reference=ref
    )
    assert output["binding"] == binding.to_dict()
    assert len(output["rows"]) == 3


def test_conserved_but_wrong_customer_vs_forecast_allocation_rejected():
    given = {
        "available_qty": "1",
        "reserved_qty": "0",
        "backorder_qty": "0",
        "confirmed_customer_order_qty": "1",
        "net_forecast_qty": "1",
        "confirmed_supplier_receipt_qty": "0",
        "recommended_receipt_qty": "0",
    }
    actual = ReferencePort().advance_target(given)
    actual.update(
        fulfilled_confirmed_customer_order_qty="0",
        fulfilled_forecast_qty="1",
        forecast_shortage_qty="0",
        backorder_close_qty="1",
    )
    with pytest.raises(InventoryInputError, match="CALCULATION_TARGET_ALLOCATION_ORDER"):
        verify_target_result(given, actual)


def test_synthetic_parameters_rejected_outside_development():
    from dsio_inventory_engine.prepare_inventory.application.inventory_input import (
        PrepareInventoryInputUseCase,
    )

    deployment = DeploymentScope("DSE", "PRODUCTION")
    canonical, _, _, params, ref = case()
    prepared = PrepareInventoryInputUseCase(deployment).execute(canonical)
    params["prepared_content_hash"] = prepared.to_dict()["manifest"]["prepared_content_hash"]
    binding = bind(canonical, prepared, "a" * 64, params=params)
    with pytest.raises(InventoryInputError, match="CALCULATION_LEGACY_SOURCE_TYPE"):
        CanonicalCalculationAdapter(deployment, ReferencePort()).prepare_legacy(
            canonical, prepared, binding, parameters=params, reference=ref
        )


@pytest.mark.parametrize("version", ["2.0.0", "2.1.0"])
def test_canonical_v2_v21_conversion_keeps_existing_forecast_source_binding(version):
    canonical, prepared, binding, _, _ = case(legacy=False, version=version)
    before = canonical.document_json
    actual = CanonicalCalculationAdapter(DEPLOYMENT, ReferencePort()).run_target_baseline(
        canonical, prepared, binding
    )
    assert (
        actual["psi_content_hash"]
        == RunPsiSimulationUseCase(DEPLOYMENT).execute(canonical)["psi_content_hash"]
    )
    assert canonical.document_json == before
    if version == "2.1.0":
        assert actual["binding"]["input_bindings"]["forecast"]["snapshot_id"].endswith(".canonical")
        assert not canonical.to_dict()["upstream_bindings"]["forecast"]["snapshot_id"].endswith(
            ".canonical"
        )

"""Real, isolated Python.NET verification. No DB, network, Runtime or publication.

Run inside the pinned local Python.NET image with repo/build read-only mounts.
Unit fixture-port tests are separate and are never counted as actual DLL calls.
"""

import argparse
import copy
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import random

from dsio_inventory_engine.infrastructure.calculation_pythonnet import CalculationPythonNetKernel
from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.prepare_inventory.application.calculation_adapter import (
    CanonicalCalculationAdapter,
    LEGACY_NUMBERS,
    verify_target_result,
)
from dsio_inventory_engine.prepare_inventory.application.inventory_input import (
    PrepareInventoryInputUseCase,
)
from dsio_inventory_engine.simulate_inventory.application.baseline import RunPsiSimulationUseCase
from dsio_inventory_engine.simulate_inventory.application.step import InventoryState, advance_bucket
from tests.support.calculation_adapter_fixtures import DEPLOYMENT, bind, case
from tests.support.legacy_reference_fixtures import replay_request
from dsio_inventory_engine.simulate_inventory.application.legacy_reference import (
    run_legacy_reference,
)

ROOT = Path(__file__).resolve().parents[1]


def reference_payload(given):
    # Raw numerical vectors only: no fabricated Canonical Run, Snapshot or approval.
    calendar = [
        {
            "yyyyww": r["bucket_id"],
            "seq": i,
            "start_date": r["start"],
            "end_date": r["end"],
            "base_month": r["base_month"] or r["start"][:7].replace("-", ""),
        }
        for i, r in enumerate(given["buckets"])
    ]
    request = {
        k: given[k]
        for k in (
            "boh",
            "backorder",
            "pending_po",
            "internal_lead_time",
            "stock_policy",
            "lot",
            "policies",
            "demands",
            "receipts",
        )
    }
    return {
        "binding": {"semantics_profile": "LEGACY_REPLAY"},
        "calendar": calendar,
        "requests": [{"item_id": "RAW-REFERENCE", "candidate_id": "RAW-CANDIDATE", **request}],
    }


def verify(build: Path):
    dll = build / "Inventory.Calculation.dll"
    dll_hash = hashlib.sha256(dll.read_bytes()).hexdigest()
    kernel = CalculationPythonNetKernel(dll, expected_dll_sha256=dll_hash)
    adapter = CanonicalCalculationAdapter(DEPLOYMENT, kernel)
    vectors = json.loads((ROOT / "tests/fixtures/legacy_replay_vectors.json").read_text())[
        "replay_cases"
    ]
    capture = {
        r["case"]: r["actual"]
        for r in json.loads(
            (ROOT / "tests/fixtures/native_legacy_replay_vectors_output.json").read_text()
        )["replays"]
    }
    counts = {
        "raw_legacy_cases": 0,
        "raw_legacy_rows": 0,
        "canonical_legacy_rows": 0,
        "canonical_target_rows": 0,
        "target_transition_cases": 0,
        "rejections": 0,
    }
    for vector in vectors:
        output = kernel.run_legacy(reference_payload(vector["input"]))
        expected = capture[vector["case"]]
        assert [r["yyyyww"] for r in output["rows"]] == expected["bucket_ids"]
        for key in LEGACY_NUMBERS:
            assert [float(r["quantities"][key]).hex() for r in output["rows"]] == [
                float(v).hex() for v in expected[key]
            ], (vector["case"], key)
        for key in ("order_dates", "due_dates"):
            field = "order_date" if key == "order_dates" else "due_date"
            assert [r[field] for r in output["rows"] if r[field] is not None] == expected[key]
        counts["raw_legacy_cases"] += 1
        counts["raw_legacy_rows"] += len(output["rows"])

    # Real Canonical conversion across policy codes, zero/fractional/out-of-horizon LT.
    original = case(kernel_hash=dll_hash, candidates=33)
    canonical, prepared, _, params, reference = original
    for code in ("OUTL", "ROPQ", "MINQ", "SFST"):
        for lead in ("0", "14400", "201600", "2016000"):
            parameters = copy.deepcopy(params)
            for r in parameters["rows"]:
                r.update(stock_policy=code, internal_lead_time=lead)
            binding = bind(canonical, prepared, dll_hash, params=parameters)
            wire = adapter.prepare_legacy(
                canonical, prepared, binding, parameters=parameters, reference=reference
            )
            result = adapter.run_legacy(
                canonical, prepared, binding, parameters=parameters, reference=reference
            )
            for i, request in enumerate(wire["requests"]):
                given = {
                    **request,
                    "buckets": [
                        {
                            "bucket_id": b["yyyyww"],
                            "start": b["start_date"],
                            "end": b["end_date"],
                            "base_month": b["base_month"],
                        }
                        for b in wire["calendar"]
                    ],
                }
                expected = run_legacy_reference(replay_request(given))
                for row, want in zip(result["rows"][i * 3 : (i + 1) * 3], expected, strict=True):
                    for key in LEGACY_NUMBERS:
                        assert (
                            float(row["quantities"][key]).hex() == float(getattr(want, key)).hex()
                        ), key
            counts["canonical_legacy_rows"] += len(result["rows"])

    # Non-benchmark year and explicit BaseMonth differing from start month.
    canonical, prepared, _, parameters, reference = case(kernel_hash=dll_hash, start="2027-12-27")
    raw = canonical.to_dict()
    from tests.support.fixtures import reseal

    raw["snapshots"]["calendar"]["rows"][0]["base_month"] = "202801"
    canonical = CanonicalInputRequest.from_dict(reseal(raw))
    prepared = PrepareInventoryInputUseCase(DEPLOYMENT).execute(canonical)
    parameters["canonical_input_hash"] = canonical.input_hash
    parameters["prepared_content_hash"] = prepared.to_dict()["manifest"]["prepared_content_hash"]
    parameters["rows"][0]["policies"] = [
        {
            "start": "2028-01-01",
            "end": "2028-01-01",
            "rop": "9",
            "target": "27",
            "mos": "1",
            "ma_demand": "10",
        },
        {
            "start": "2027-01-01",
            "end": "2029-01-01",
            "rop": "8",
            "target": "26",
            "mos": "1",
            "ma_demand": "10",
        },
    ]
    binding = bind(canonical, prepared, dll_hash, params=parameters)
    result = adapter.run_legacy(
        canonical, prepared, binding, parameters=parameters, reference=reference
    )
    assert (
        result["rows"][0]["quantities"]["target"] == "27"
    )  # Inclusive BaseMonth and first policy.
    counts["canonical_legacy_rows"] += len(result["rows"])

    # Full Baseline rows and existing logical hashes, including reserved/BO and exclusions.
    fixtures = [
        [{"id": "ITEM-A", "boh": "10", "forecast": ["10", "10", "10"]}],
        [
            {
                "id": "ITEM-A",
                "boh": "7",
                "reserved": "3",
                "backorder": "2",
                "orders": ["8", "2", "0"],
                "forecast": ["10", "10", "10"],
                "receipts": [
                    {"week": 0, "qty": "3", "status": "CONFIRMED"},
                    {"week": 0, "qty": "4", "status": "IN_TRANSIT"},
                    {"week": 1, "qty": "200", "status": "UNVERIFIED_DUE_IN"},
                ],
            }
        ],
        [
            {"id": "A", "boh": "3", "forecast": ["0", "5", "9"]},
            {"id": "B", "boh": "40", "forecast": ["1", "2", "3"]},
        ],
        [{"id": "FRACTIONAL", "uom": "KG", "boh": "0.3", "forecast": ["0.1", "0.2", "0.3"]}],
    ]
    fixtures.append(
        [{"id": "ITEM-" + str(i), "boh": "10", "forecast": ["10"] * 3} for i in range(17)]
    )
    baseline_hashes = []
    for items in fixtures:
        canonical, prepared, binding, _, _ = case(items=items, kernel_hash=dll_hash, legacy=False)
        actual = adapter.run_target_baseline(canonical, prepared, binding)
        expected = RunPsiSimulationUseCase(DEPLOYMENT).execute(canonical)
        assert actual["psi_rows"] == expected["psi_rows"]
        assert actual["psi_content_hash"] == expected["psi_content_hash"]
        baseline_hashes.append(actual["psi_content_hash"])
        counts["canonical_target_rows"] += len(actual["psi_rows"])

    for version in ("2.0.0", "2.1.0"):
        canonical, prepared, binding, _, _ = case(
            items=[{"id": "FRACTIONAL-EA", "boh": "2", "forecast": ["0.4"] * 3}],
            kernel_hash=dll_hash,
            legacy=False,
            version=version,
        )
        actual = adapter.run_target_baseline(canonical, prepared, binding)
        expected = RunPsiSimulationUseCase(DEPLOYMENT).execute(canonical)
        assert actual["psi_rows"] == expected["psi_rows"]
        assert actual["psi_content_hash"] == expected["psi_content_hash"]
        baseline_hashes.append(actual["psi_content_hash"])
        counts["canonical_target_rows"] += len(actual["psi_rows"])

    # No 16-item cap; candidate IDs retain their independent per-item grain.
    canonical, prepared, binding, parameters, reference = case(
        items=fixtures[-1], kernel_hash=dll_hash
    )
    actual = adapter.run_legacy(
        canonical, prepared, binding, parameters=parameters, reference=reference
    )
    assert len(actual["rows"]) == 17 * 3
    counts["canonical_legacy_rows"] += len(actual["rows"])

    for length in (1, 53):
        given = copy.deepcopy(vectors[0]["input"])
        start = date(2026, 9, 28)
        given["buckets"] = [
            {
                "bucket_id": "BUCKET-" + str(i),
                "start": (start + timedelta(weeks=i)).isoformat(),
                "end": (start + timedelta(weeks=i, days=6)).isoformat(),
                "base_month": None,
            }
            for i in range(length)
        ]
        result = kernel.run_legacy(reference_payload(given))
        expected = run_legacy_reference(replay_request(given))
        assert len(result["rows"]) == length
        for row, want in zip(result["rows"], expected, strict=True):
            for key in LEGACY_NUMBERS:
                assert float(row["quantities"][key]).hex() == float(getattr(want, key)).hex()

    randomizer = random.Random(20261021)
    for index in range(125):
        numbers = [str(Decimal(randomizer.randrange(100000)) / 1000) for _ in range(7)]
        if index == 0:
            numbers = ["0.1", "0.2", "0.3", "0.4", "0.5", "0.6", "0.7"]
        if index == 1:
            numbers = ["123456789012345678901234567890.123456"] + ["0.000001"] * 6
        given = dict(
            zip(
                (
                    "available_qty",
                    "reserved_qty",
                    "backorder_qty",
                    "confirmed_customer_order_qty",
                    "net_forecast_qty",
                    "confirmed_supplier_receipt_qty",
                    "recommended_receipt_qty",
                ),
                numbers,
                strict=True,
            )
        )
        result = kernel.advance_target(given)
        state = InventoryState(
            *(Decimal(given[k]) for k in ("available_qty", "reserved_qty", "backorder_qty"))
        )
        expected = advance_bucket(
            state,
            **{
                k: Decimal(v)
                for k, v in given.items()
                if k not in {"available_qty", "reserved_qty", "backorder_qty"}
            },
        )[1]
        assert result == expected, (index, result, expected)
        verify_target_result(given, result)
        counts["target_transition_cases"] += 1

    def reject(fn, expected_code):
        try:
            fn()
        except Exception as error:
            assert expected_code in str(error), (expected_code, str(error))
            counts["rejections"] += 1
        else:
            raise AssertionError("EXPECTED_REJECTION")

    base = {k: "1" for k in given}
    for bad in ("0.0000001", "NaN", "-1", "01", "9" * 35):
        reject(
            lambda bad=bad: kernel.advance_target({**base, "available_qty": bad}),
            "DLL_TARGET_PRECISION",
        )
    reject(lambda: kernel.advance_target({**base, "unknown": "0"}), "DLL_FIELDS")
    reject(
        lambda: kernel.advance_target(
            {**base, "available_qty": "9" * 34 + ".999999", "confirmed_supplier_receipt_qty": "1"}
        ),
        "DLL_TARGET_PRECISION",
    )
    from System.Threading import CancellationToken

    reject(
        lambda: kernel._call("AdvanceTarget", base, cancellation_token=CancellationToken(True)),
        "canceled",
    )
    assert kernel.advance_target(base) == kernel.advance_target(base)  # No cancellation/state leak.

    # A real Canonical Source must still fail if its projection is changed and self-resealed.
    canonical, prepared, binding, parameters, ref = case(kernel_hash=dll_hash)
    for mutation in (
        "missing_policy",
        "missing_item",
        "precision",
        "period",
        "reference",
        "profile",
        "build",
    ):
        params = copy.deepcopy(parameters)
        if mutation == "missing_policy":
            params["rows"][0]["policies"] = []
        elif mutation == "missing_item":
            params["rows"] = []
        elif mutation == "precision":
            params["rows"][0]["lot"]["maximum"] = "9007199254740993"
        elif mutation == "period":
            params["rows"][0]["policies"][0]["end"] = "2026-08-31"
        bad_binding = bind(
            canonical,
            prepared,
            "b" * 64 if mutation == "build" else dll_hash,
            legacy=mutation != "profile",
            params=params,
        )
        expected_code = {
            "missing_policy": "CALCULATION_LEGACY_POLICY_COVERAGE",
            "missing_item": "CALCULATION_LEGACY_COVERAGE",
            "precision": "CALCULATION_LEGACY_PRECISION",
            "period": "CALCULATION_LEGACY_POLICY_COVERAGE",
        }.get(mutation, "CALCULATION_SOURCE_BINDING_MISMATCH")
        reject(
            lambda: adapter.run_legacy(
                canonical,
                prepared,
                bad_binding,
                parameters=params,
                reference="fixture:other" if mutation == "reference" else ref,
            ),
            expected_code,
        )

    return {
        "status": "ACTUAL_DLL_OFFLINE_VERIFIED_NOT_REGISTERED",
        "dll_sha256": dll_hash,
        "counts": counts,
        "baseline_hashes": baseline_hashes,
        "canonical_versions": ["1.0.0", "2.0.0", "2.1.0"],
        "additional_generic_horizon_lengths": [1, 53],
        "legacy_capture": "tests/fixtures/native_legacy_replay_vectors_output.json",
        "recommended_target_strategy_guard_wiring": "NOT_IMPLEMENTED",
        "database_writes": False,
        "runtime_registration": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.build), sort_keys=True))

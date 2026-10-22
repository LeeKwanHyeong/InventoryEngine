"""Compare captured native outputs and current Python with identical scoped inputs.

This is a test tool, not an Inventory runtime strategy or Legacy calculator.
"""

from __future__ import annotations

import argparse
import copy
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.recommend_replenishment.application.guard import (
    map_arrival,
    policy_at,
    validate_action,
)
from dsio_inventory_engine.recommend_replenishment.application.policy_search import (
    RunPolicySearchUseCase,
    evaluate_rows,
)
from dsio_inventory_engine.recommend_replenishment.application.policy_selection import (
    assess_candidate,
    rank_candidate_assessments,
)
from dsio_inventory_engine.recommend_replenishment.application.run import RunRecommendedPsiUseCase
from dsio_inventory_engine.simulate_inventory.application.step import InventoryState, advance_bucket
from strategy_fixtures import ContractProbe, canonical, request
from tests.support.selected_order_plan_fixtures import golden_parts

ROOT = Path(__file__).resolve().parents[3]
PACK = ROOT / "tests/fixtures/golden_legacy_python_semantics.json"
CAPTURE = ROOT / "tests/fixtures/native_legacy_reference_output.json"
DEPLOYMENT = DeploymentScope("DSE", "DEVELOPMENT")


def subset(actual, expected):
    if isinstance(expected, dict):
        assert isinstance(actual, dict)
        for key, value in expected.items():
            assert key in actual, key
            subset(actual[key], value)
    else:
        assert actual == expected, (actual, expected)


def verify_native(pack, native):
    assert native["input_sha256"] == hashlib.sha256(PACK.read_bytes()).hexdigest()
    assert native["scope"] == "EXTRACTED_ORIGINAL_METHODS_NO_DB_NO_ORIGINAL_ENTRYPOINT"
    assert native["runtime"].startswith(".NET 8.")
    checks = []
    for family, output_key in (("transition_cases", "transitions"), ("order_cases", "orders")):
        indexed = {row["case"]: row for row in pack[family]}
        expected_runs = len(indexed) * (4 if output_key == "orders" else 1)
        assert len(native[output_key]) == expected_runs
        for row in native[output_key]:
            subset(row["actual"], indexed[row["case"]]["legacy_expected"])
            checks.append(row["case"])
        assert set(checks) >= set(indexed)
        if output_key == "orders":
            for name in indexed:
                codes = [row["stock_policy"] for row in native["orders"] if row["case"] == name]
                assert len(codes) == 4 and set(codes) == {"OUTL", "ROPQ", "MINQ", "SFST"}
    assert native["trigger"]["case"] == pack["trigger_case"]["case"]
    assert (
        native["trigger"]["actual"]["order_triggered"]
        == pack["trigger_case"]["legacy_order_triggered"]
    )
    checks.append(native["trigger"]["case"])
    for family, output_key in (
        ("lot_cases", "lots"),
        ("arrival_cases", "arrivals"),
        ("policy_cases", "policies"),
        ("selection_cases", "selections"),
    ):
        indexed = {row["case"]: row for row in pack[family]}
        if output_key == "selections":
            indexed = {
                k: v for k, v in indexed.items() if v["legacy_individual_condition_met"] is not None
            }
        assert {row["case"] for row in native[output_key]} == set(indexed)
        assert len(native[output_key]) == len(indexed)
        for row in native[output_key]:
            case, actual = indexed[row["case"]], row["actual"]
            if output_key == "lots":
                assert actual == case["legacy_expected"]
            elif output_key == "arrivals":
                subset(
                    actual,
                    {
                        "lead_time_days": case["lead_time_days"],
                        "due_date": case["due_date"],
                        "containing_bucket_start": case["legacy_containing_bucket_start"],
                    },
                )
            elif output_key == "policies":
                assert actual["rop"] == case["legacy_rop"]
                month = case["base_month"] or case["bucket_start"][:7].replace("-", "")
                assert actual["base_month"] == month
            else:
                subset(
                    actual,
                    {
                        "individual_condition_met": case["legacy_individual_condition_met"],
                        "force_eligible_label": case["legacy_obj01_force_eligible_label"],
                        "final_selected_label": None,
                    },
                )
            checks.append(row["case"])
    assert native["unsupported"] == [
        {"case": "missing_cost_is_not_zero_cost", "reason": "NO_COMPARABLE_LEGACY_COST_CONTRACT"}
    ]
    assert len(checks) == 29 and len(set(checks)) == 20
    return checks


def compare(native_path=CAPTURE):
    raw = PACK.read_bytes()
    pack = json.loads(raw)
    native_raw = Path(native_path).read_bytes()
    native = json.loads(native_raw)
    native_checks = verify_native(pack, native)
    python = {}
    for case in pack["transition_cases"]:
        given = case["input"]
        _, row = advance_bucket(
            InventoryState(Decimal(given["boh"]), Decimal(0), Decimal(given["backorder"])),
            confirmed_customer_order_qty=Decimal(given["confirmed"]),
            net_forecast_qty=Decimal(given["forecast"]),
            confirmed_supplier_receipt_qty=Decimal(given["receipt"]),
        )
        if case["case"] == "no_demand_is_not_perfect_service":
            row.update(
                evaluate_rows(
                    [{**row, "confirmed_customer_order_qty": "0", "net_forecast_qty": "0"}],
                    [],
                    [],
                    None,
                    "0.95",
                    Decimal(0),
                )
            )
        subset(row, case["python_expected"])
        python[case["case"]] = {k: row[k] for k in case["python_expected"]}
    triggers = {}
    for case in pack["order_cases"]:
        given = case["input"]
        parts = golden_parts(
            {
                "id": case["case"],
                "kind": given["kind"],
                "minimum_fill_rate": "0.7",
                "boh": given["boh"],
                "forecast": given["forecast"],
                "policy": {"lead_time_days": given["lead_time_days"]},
            }
        )
        before = copy.deepcopy(parts)
        source = RunPolicySearchUseCase(DEPLOYMENT).execute(
            MathematicalPolicyRequest.from_dict(parts[0]),
            config_values=parts[1],
            classification_snapshot=parts[2],
        )
        assert before == parts
        (child,) = source["candidate_evaluations"]
        assert child["status"] == "SUCCEEDED"
        actual = {
            "eoh": [r["eoh_qty"] for r in child["psi_rows"]],
            "receipt": [r["recommended_receipt_qty"] for r in child["psi_rows"]],
            "orders": [
                [o[k] for k in ("order_date", "due_date", "receipt_date", "quantity")]
                for o in child["orders"]
            ],
            "validation_reasons": [
                d["validation"]["reason_codes"] for d in child["decision_evidence"]
            ],
        }
        assert actual == case["python_expected"]
        first = child["decision_evidence"][0]
        assert first["validation"]["calculated_policy"]["rop_qty"] == given["rop"]
        assert first["validation"]["calculated_policy"]["target_inventory_qty"] == given["target"]
        assert (
            not child["automatic_publish_allowed"]
            and not child["automatic_order_allowed"]
            and not source["database_writes"]
        )
        python[case["case"]] = actual
        if given["lead_time_days"] == 7:
            assert (
                first["observation"]["state"]["inventory_position_qty"]
                == pack["trigger_case"]["position"]
            )
            triggers[given["kind"]] = Decimal(first["validation"]["accepted_order_qty"]) > 0
    assert triggers == {"ROP_FIXED_Q": True, "PERIODIC_ORDER_UP_TO": True}
    python[pack["trigger_case"]["case"]] = triggers
    probe = ContractProbe(action="HOLD", quantity="0")
    RunRecommendedPsiUseCase(DEPLOYMENT).execute(request(canonical(boh="0")), probe)
    observation = probe.observations[0].to_dict()
    for case in pack["lot_cases"]:
        obs = copy.deepcopy(observation)
        obs["policy"].update(moq=case["min"], order_multiple=case["step"])
        row = validate_action(
            obs, {"action_type": "ORDER_QTY", "quantity": case["raw"], "calculated_policy": {}}
        )
        assert row["accepted_order_qty"] == case["python_expected"]
        python[case["case"]] = row["accepted_order_qty"]
    for case in pack["arrival_cases"]:
        due, bucket = map_arrival(
            pack["calendar"][0]["start_date"], case["lead_time_days"], pack["calendar"]
        )
        arrival = bucket["start_date"] if bucket else None
        assert due == case["due_date"] and arrival == case["python_receipt_date"]
        python[case["case"]] = {"due_date": due, "receipt_date": arrival}
    for case in pack["policy_cases"]:
        row = policy_at(pack["policy_schedule"], case["bucket_start"])
        assert row["policy_id"] == case["python_policy"] and row["rop"] == case["python_rop"]
        python[case["case"]] = {"policy": row["policy_id"], "rop": row["rop"]}
    for case in pack["selection_cases"]:
        assessments, labels = [], {}
        for index, candidate in enumerate(case["candidates"], 1):
            identity = "PC-" + f"{index:064x}"
            labels[identity] = candidate["label"]
            metrics = {
                "service_status": "AVAILABLE",
                "service_feasible": Decimal(candidate["fill_rate"])
                >= Decimal(case["minimum_fill_rate"]),
                "current_demand_fill_rate": candidate["fill_rate"],
                "ending_backorder_qty": "0",
                "cost_status": "AVAILABLE_DEVELOPMENT_ASSUMPTION"
                if case["cost_bound"]
                else "COST_PROFILE_NOT_BOUND",
                "total_cost": candidate["total_cost"],
                "currency": "USD" if case["cost_bound"] else None,
                "average_on_hand_eoh_qty": "1",
                "order_count": 1,
            }
            assessments.append(
                assess_candidate(
                    {
                        "candidate": {"candidate_id": identity},
                        "content_hash": "1" * 64,
                        "status": "SUCCEEDED",
                        "failure_reason": None,
                    },
                    {
                        "metrics": metrics,
                        "blocked": False,
                        "calculable": True,
                        "minimum_fill_rate": Decimal(case["minimum_fill_rate"]),
                    },
                )
            )
        eligible, _ = rank_candidate_assessments(assessments)
        selected = labels[eligible[0]["candidate_id"]] if eligible else None
        reasons = sorted(
            {r for a in assessments if a["status"] == "INELIGIBLE" for r in a["reason_codes"]}
        )
        assert (
            selected == case["python_selected_label"]
            and reasons == case["python_ineligible_reasons"]
        )
        python[case["case"]] = {"selected_label": selected, "ineligible_reasons": reasons}
    assert len(python) == 21
    csharp = {}
    for name in ("transitions", "orders", "lots", "arrivals", "policies", "selections"):
        for row in native[name]:
            if name != "orders" or row["stock_policy"] == "OUTL":
                csharp[row["case"]] = row["actual"]
    csharp[native["trigger"]["case"]] = native["trigger"]["actual"]
    comparisons = []
    for name, value in python.items():
        legacy = csharp.get(name)
        common = []
        if legacy is None:
            status = "NO_COMPARABLE_LEGACY_CONTRACT"
        elif name in {r["case"] for r in pack["selection_cases"]}:
            status = "ELIGIBILITY_COMPONENTS_NOT_FULL_WINNER_EQUIVALENCE"
        else:
            if name in {r["case"] for r in pack["transition_cases"]}:
                common = [
                    ("eoh", legacy["eoh"], value["eoh_qty"]),
                    ("backorder_close", legacy["backorder_close"], value["backorder_close_qty"]),
                ]
                if name == "no_demand_is_not_perfect_service":
                    common.append(
                        (
                            "service_representation",
                            legacy["weekly_demand_allocation_rate"],
                            value["current_demand_fill_rate"],
                        )
                    )
            elif name in {r["case"] for r in pack["order_cases"]}:
                quantities = {row[0]: row[3] for row in value["orders"]}
                common = [
                    ("eoh", legacy["eoh"], value["eoh"]),
                    (
                        "new_order_qty_by_bucket",
                        legacy["purchase_order"],
                        [quantities.get(b["start_date"], "0") for b in pack["calendar"]],
                    ),
                ]
            elif name == pack["trigger_case"]["case"]:
                common = [(kind, legacy["order_triggered"], flag) for kind, flag in value.items()]
            elif name in {r["case"] for r in pack["lot_cases"]}:
                common = [("order_qty", legacy, value)]
            elif name in {r["case"] for r in pack["arrival_cases"]}:
                common = [
                    (
                        "receipt_bucket_start",
                        legacy["containing_bucket_start"],
                        value["receipt_date"],
                    )
                ]
            elif name in {r["case"] for r in pack["policy_cases"]}:
                common = [("rop", legacy["rop"], value["rop"])]
            assert common
            status = (
                "SCOPED_FIELDS_MATCH"
                if all(a == b for _, a, b in common)
                else "DOCUMENTED_SEMANTIC_DIFFERENCE"
            )
        comparisons.append(
            {
                "case": name,
                "legacy_actual": legacy,
                "python_actual": value,
                "status": status,
                "common_field_checks": [
                    {"field": field, "legacy": a, "python": b, "equal": a == b}
                    for field, a, b in common
                ],
            }
        )
    return {
        "golden_sha256": hashlib.sha256(raw).hexdigest(),
        "native_capture_sha256": hashlib.sha256(native_raw).hexdigest(),
        "native_case_count": len(set(native_checks)),
        "native_execution_count": len(native_checks),
        "python_case_count": len(python),
        "golden_mismatches": [],
        "runtime": native["runtime"],
        "scope": native["scope"],
        "comparisons": comparisons,
        "legacy_full_engine_equivalence_verified": False,
        "database_writes": False,
        "production_calculation_changed": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-output", type=Path, default=CAPTURE)
    print(json.dumps(compare(parser.parse_args().native_output), indent=2))

"""Pure PSI Child Artifact and row-derived comparison tests."""

import copy
import unittest
from datetime import date, timedelta
from decimal import Decimal, localcontext

from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from dsio_inventory_engine.inventory_contracts.replenishment import ReplenishmentObservation
from dsio_inventory_engine.recommend_replenishment.application.guard import validate_action
from dsio_inventory_engine.run_inventory.result_artifacts import (
    PSI_RESULT_ARTIFACT_CONTRACT_KEY,
    build_failed_psi_child,
    build_psi_child_artifact,
    compare_psi_children,
    compare_psi_children_detailed,
    derive_psi_child_result_id,
    seal_action_observation_source,
    summarize_psi_rows,
)


RUN_ID = "10000000-0000-4000-8000-000000000001"
CANONICAL_HASH = "a" * 64
SCENARIO_HASH = "b" * 64
OBSERVATION_INPUT_HASH = "f" * 64


def psi_row(
    *,
    item_id: str = "ITEM-A",
    uom: str = "EA",
    seq: int = 0,
    scenario: str = "BASELINE",
    strategy: str | None = None,
    confirmed: str = "0",
    forecast: str = "0",
    boh: str | None = None,
    reserved: str | None = None,
    confirmed_receipt: str = "0",
    recommended_receipt: str | None = None,
    backorder_open: str | None = None,
    fulfilled_backorder: str = "0",
    fulfilled_confirmed: str = "0",
    fulfilled_forecast: str = "0",
    eoh: str = "0",
    on_hand_eoh: str | None = None,
    backorder: str = "0",
    shortage: str | None = None,
) -> dict:
    start = date(2026, 9, 28) + timedelta(weeks=seq)
    eoh_value = Decimal(eoh)
    on_hand_eoh_value = eoh_value if on_hand_eoh is None else Decimal(on_hand_eoh)
    reserved_value = on_hand_eoh_value - eoh_value if reserved is None else Decimal(reserved)
    confirmed_receipt_value = Decimal(confirmed_receipt)
    fulfilled_backorder_value = Decimal(fulfilled_backorder)
    fulfilled_confirmed_value = Decimal(fulfilled_confirmed)
    fulfilled_forecast_value = Decimal(fulfilled_forecast)
    confirmed_value = Decimal(confirmed)
    forecast_value = Decimal(forecast)
    backorder_value = Decimal(backorder)
    available_value = (
        eoh_value + fulfilled_backorder_value + fulfilled_confirmed_value + fulfilled_forecast_value
    )
    boh_value = (
        available_value - confirmed_receipt_value
        if boh is None and scenario == "BASELINE" and recommended_receipt is None
        else Decimal(0)
        if boh is None
        else Decimal(boh)
    )
    recommended_receipt_value = (
        Decimal(0)
        if recommended_receipt is None and scenario == "BASELINE"
        else available_value - boh_value - confirmed_receipt_value
        if recommended_receipt is None
        else Decimal(recommended_receipt)
    )
    backorder_open_value = (
        backorder_value + fulfilled_backorder_value + fulfilled_confirmed_value - confirmed_value
        if backorder_open is None
        else Decimal(backorder_open)
    )
    shortage_value = (
        forecast_value - fulfilled_forecast_value if shortage is None else Decimal(shortage)
    )

    def text(value: Decimal) -> str:
        return format(value, "f")

    row = {
        "company_cd": "DSE",
        "subs_cd": "C100",
        "site_cd": "V100",
        "item_id": item_id,
        "uom": uom,
        "yyyyww": f"2026{40 + seq:02d}",
        "seq": seq,
        "start_date": start.isoformat(),
        "end_date": (start + timedelta(days=6)).isoformat(),
        "base_month": start.strftime("%Y%m"),
        "gross_forecast_qty": text(forecast_value),
        "forecast_consumed_qty": "0",
        "forecast_netting_mode": "UPSTREAM_NETTED",
        "source_snapshot_id": "FORECAST-SNAPSHOT-1",
        "source_content_hash": "9" * 64,
        "psi_scenario_type": scenario,
        "boh_qty": text(boh_value),
        "reserved_qty": text(reserved_value),
        "on_hand_boh_qty": text(boh_value + reserved_value),
        "confirmed_supplier_receipt_qty": text(confirmed_receipt_value),
        "recommended_receipt_qty": text(recommended_receipt_value),
        "available_qty": text(available_value),
        "backorder_open_qty": text(backorder_open_value),
        "confirmed_customer_order_qty": text(confirmed_value),
        "net_forecast_qty": text(forecast_value),
        "fulfilled_backorder_qty": text(fulfilled_backorder_value),
        "fulfilled_confirmed_customer_order_qty": text(fulfilled_confirmed_value),
        "fulfilled_forecast_qty": text(fulfilled_forecast_value),
        "eoh_qty": text(eoh_value),
        "on_hand_eoh_qty": text(on_hand_eoh_value),
        "backorder_close_qty": text(backorder_value),
        "forecast_shortage_qty": text(shortage_value),
    }
    if strategy is not None:
        row["strategy_type"] = strategy
        row["physical_capacity_excess_qty"] = "0"
    return row


def strategy_descriptor(strategy_type: str) -> dict:
    return {
        "strategy_type": strategy_type,
        "implementation_id": (
            "inventory.mathematical.v1" if strategy_type == "MATHEMATICAL" else "inventory.ppo.v1"
        ),
        "version": "1.0.0",
        "model": (
            None
            if strategy_type == "MATHEMATICAL"
            else {"model_id": "PPO-MODEL-1", "version": "1.0.0", "content_hash": "c" * 64}
        ),
    }


def decision(
    index: int,
    *,
    item_id: str = "ITEM-A",
    uom: str = "EA",
    strategy_type: str = "MATHEMATICAL",
    accepted_qty: str = "0",
    receipt_bucket: str | None = None,
    decision_date: str | None = None,
    source_row: dict | None = None,
    all_rows: list[dict] | None = None,
) -> dict:
    row = source_row or psi_row(
        item_id=item_id,
        uom=uom,
        seq=index,
        scenario="RECOMMENDED",
        strategy=strategy_type,
        recommended_receipt=accepted_qty,
        eoh=accepted_qty,
    )
    decision_id = "DEC-" + digest([OBSERVATION_INPUT_HASH, row["item_id"], row["yyyyww"]])
    decision_date = decision_date or row["start_date"]
    accepted = Decimal(accepted_qty)
    action_type = "ORDER_QTY" if accepted > 0 else "HOLD"
    calculated_policy = {
        "safety_stock_qty": "1",
        "rop_qty": "2",
        "target_inventory_qty": "3",
    }
    observation = ReplenishmentObservation.from_dict(
        guard_observation(
            row,
            all_rows=all_rows or [row],
            strategy_type=strategy_type,
        )
    ).to_dict()
    proposal = {
        "contract_id": "io-replenishment-decision-v1",
        "decision_id": decision_id,
        "observation_hash": digest(observation),
        "strategy": strategy_descriptor(strategy_type),
        "action_type": action_type,
        "quantity": accepted_qty,
        "calculated_policy": calculated_policy,
        "reason_codes": ["ROP_TRIGGERED"] if accepted > 0 else ["HOLD"],
    }
    validation = validate_action(observation, proposal)
    if accepted > 0 and receipt_bucket is not None:
        assert validation["receipt_bucket"] == receipt_bucket
    return {
        "observation": observation,
        "observation_hash": digest(observation),
        "proposal": proposal,
        "validation": validation,
    }


def decision_for_row(row: dict, *, strategy_type: str, all_rows: list[dict] | None = None) -> dict:
    return decision(
        row["seq"],
        item_id=row["item_id"],
        uom=row["uom"],
        strategy_type=strategy_type,
        accepted_qty=row["recommended_receipt_qty"],
        receipt_bucket=row["yyyyww"],
        decision_date=row["start_date"],
        source_row=row,
        all_rows=all_rows,
    )


def rejected_decision_for_row(
    row: dict,
    *,
    reason_code: str,
    action_type: str = "ORDER_QTY",
    requested_qty: str = "10",
    inventory_position_qty: str = "0",
    lot_rounded_qty: str = "0",
    due_date: str | None = None,
    receipt_date: str | None = None,
    receipt_bucket: str | None = None,
    capacity_headroom_qty: str | None = None,
) -> dict:
    evidence = decision_for_row(row, strategy_type="MATHEMATICAL")
    observation = evidence["observation"]
    observation["state"].update(
        available_qty=inventory_position_qty,
        on_hand_qty=inventory_position_qty,
        inventory_position_qty=inventory_position_qty,
    )
    evidence["proposal"].update(
        action_type=action_type,
        quantity=requested_qty,
        reason_codes=["ROP_TRIGGERED"],
    )
    if reason_code == "ACTION_NOT_APPROVED":
        observation["allowed_action_types"] = ["HOLD", "ORDER_UP_TO"]
    elif reason_code == "TARGET_OUTSIDE_APPROVED_BOUNDS":
        observation["control"]["max_target_qty"] = "0"
    elif reason_code == "ORDER_CALENDAR_CLOSED":
        observation["control"]["order_dates"] = []
    elif reason_code == "ARRIVAL_OUTSIDE_PLAN_HORIZON":
        observation["policy"]["lead_time_days"] = 1
    elif reason_code == "NO_FEASIBLE_ORDER_QUANTITY":
        observation["policy"]["moq"] = "100"
        observation["control"]["max_order_qty"] = "50"
        observation["policy"]["physical_max_capacity"] = "20"
    observation["policy_schedule"] = [observation["policy"]]
    evidence["observation_hash"] = digest(observation)
    evidence["proposal"]["observation_hash"] = evidence["observation_hash"]
    evidence["validation"] = validate_action(observation, evidence["proposal"])
    assert evidence["validation"]["reason_codes"] == [reason_code]
    return evidence


def guard_observation(
    row: dict,
    *,
    all_rows: list[dict] | None = None,
    strategy_type: str = "MATHEMATICAL",
) -> dict:
    universe_rows = sorted(
        [
            value
            for value in (all_rows or [row])
            if value["item_id"] == row["item_id"] and value["uom"] == row["uom"]
        ],
        key=lambda value: value["seq"],
    )
    all_rows = [value for value in universe_rows if value["seq"] >= row["seq"]]
    policy = {
        "company_cd": row["company_cd"],
        "subs_cd": row["subs_cd"],
        "site_cd": row["site_cd"],
        "policy_id": f"POLICY-{row['item_id']}",
        "item_id": row["item_id"],
        "uom": row["uom"],
        "effective_from": universe_rows[0]["start_date"],
        "effective_to": (
            date.fromisoformat(universe_rows[-1]["end_date"]) + timedelta(days=1)
        ).isoformat(),
        "lead_time_days": 0,
        "order_multiple": "0.000001",
        "moq": "0",
        "physical_max_capacity": None,
        "approved_service_level": "0.95",
        "source_target_inventory_qty": None,
        "source_rop_qty": None,
        "policy_source_type": "SOURCE_MASTER",
        "source_semantics_cd": "TEST_POLICY",
    }
    pending_supply = [
        {
            "supply_id": f"CONFIRMED:TEST-{value['item_id']}-{value['yyyyww']}",
            "supply_kind": "CONFIRMED",
            "due_date": value["start_date"],
            "receipt_bucket": value["yyyyww"],
            "quantity": value["confirmed_supplier_receipt_qty"],
        }
        for value in all_rows
        if Decimal(value["confirmed_supplier_receipt_qty"]) > 0
    ]
    position = (
        Decimal(row["boh_qty"])
        + sum((Decimal(value["quantity"]) for value in pending_supply), Decimal(0))
        - Decimal(row["backorder_open_qty"])
    )
    return {
        "contract_id": "io-replenishment-observation-v1",
        "decision_id": "DEC-" + digest([OBSERVATION_INPUT_HASH, row["item_id"], row["yyyyww"]]),
        "input_content_hash": OBSERVATION_INPUT_HASH,
        "context": {
            "company_cd": row["company_cd"],
            "subs_cd": row["subs_cd"],
            "site_cd": row["site_cd"],
            "engine_run_id": RUN_ID,
            "planning_cycle_id": "PC-202640",
            "planning_cycle_revision_id": "PCR-202640-1",
            "cycle_site_execution_id": "CSE-V100-1",
            "plan_id": "PLAN-1",
            "plan_type": "POSM",
            "plan_yyyyww": "202640",
            "plan_start_date": "2026-09-28",
            "plan_end_date": universe_rows[-1]["end_date"],
            "master_as_of_date": "2026-09-27",
            "master_snapshot_revision": "MASTER-1",
            "demand_run_id": "DEMAND-RUN-1",
            "configuration_revision": "CONFIG-1",
            "business_timezone": "Asia/Seoul",
            "inventory_cutoff_at": "2026-09-27T14:59:59Z",
            "inventory_source_watermark": "WM-1",
        },
        "item_id": row["item_id"],
        "uom": row["uom"],
        "decision_date": row["start_date"],
        "bucket": {
            key: row[key] for key in ("seq", "yyyyww", "start_date", "end_date", "base_month")
        },
        "allowed_action_types": ["HOLD", "ORDER_QTY", "ORDER_UP_TO"],
        "control": {
            "item_id": row["item_id"],
            "uom": row["uom"],
            "max_order_qty": "1000000000000",
            "min_target_qty": "0",
            "max_target_qty": "1000000000000",
            "order_dates": [value["start_date"] for value in universe_rows],
        },
        "policy": policy,
        "policy_schedule": [policy],
        "quantity_rule": {
            "uom": row["uom"],
            "tolerance_qty": "0",
            "approval_reference": "UOM-APPROVAL-1",
            "scale": 6,
        },
        "state": {
            "available_qty": row["boh_qty"],
            "reserved_qty": row["reserved_qty"],
            "on_hand_qty": row["on_hand_boh_qty"],
            "backorder_qty": row["backorder_open_qty"],
            "inventory_position_qty": format(position, "f"),
        },
        "calendar": [
            {key: value[key] for key in ("seq", "yyyyww", "start_date", "end_date", "base_month")}
            for value in all_rows
        ],
        "future_demand": [
            {
                key: value[key]
                for key in (
                    "item_id",
                    "uom",
                    "yyyyww",
                    "gross_forecast_qty",
                    "forecast_consumed_qty",
                    "net_forecast_qty",
                    "confirmed_customer_order_qty",
                    "forecast_netting_mode",
                    "source_snapshot_id",
                    "source_content_hash",
                )
            }
            for value in all_rows
        ],
        "pending_supply": pending_supply,
        "strategy": strategy_descriptor(strategy_type),
        "approval_reference": "APPROVAL-1",
        "capacity_mode": "CONSERVATIVE_NO_DEMAND_CREDIT",
    }


def action_observation_source(
    rows: list[dict], decisions: list[dict], *, strategy_type: str
) -> dict:
    observations = [value["observation"] for value in decisions]
    first = observations[0]
    calendar = max((value["calendar"] for value in observations), key=len)
    demands_by_key = {
        (value["item_id"], value["uom"], value["yyyyww"]): value
        for observation in observations
        for value in observation["future_demand"]
    }
    policies_by_key = {
        (
            value["item_id"],
            value["uom"],
            value["policy_id"],
            value["effective_from"],
            value["effective_to"],
        ): value
        for observation in observations
        for value in observation["policy_schedule"]
    }
    controls_by_item = {value["item_id"]: value["control"] for value in observations}
    rules_by_uom = {value["uom"]: value["quantity_rule"] for value in observations}
    first_rows = {}
    for value in sorted(rows, key=lambda item: (item["item_id"], item["uom"], item["seq"])):
        first_rows.setdefault((value["item_id"], value["uom"]), value)
    positions = [
        {
            "item_id": value["item_id"],
            "uom": value["uom"],
            "on_hand_qty": value["on_hand_boh_qty"],
            "reserved_qty": value["reserved_qty"],
            "available_qty": value["boh_qty"],
            "backorder_qty": value["backorder_open_qty"],
        }
        for value in first_rows.values()
    ]
    receipt_decisions = [
        {
            "receipt_id": f"TEST-{value['item_id']}-{value['yyyyww']}",
            "item_id": value["item_id"],
            "uom": value["uom"],
            "due_date": value["start_date"],
            "yyyyww": value["yyyyww"],
            "included_qty": value["confirmed_supplier_receipt_qty"],
        }
        for value in sorted(rows, key=lambda item: (item["item_id"], item["seq"]))
        if Decimal(value["confirmed_supplier_receipt_qty"]) > 0
    ]
    prepared = {
        "manifest": {
            "input_content_hash": CANONICAL_HASH,
            "prepared_content_hash": "8" * 64,
            "quantity_rules": [rules_by_uom[key] for key in sorted(rules_by_uom)],
        },
        "context": first["context"],
        "calendar": calendar,
        "demands": [demands_by_key[key] for key in sorted(demands_by_key)],
        "positions": positions,
        "policies": [policies_by_key[key] for key in sorted(policies_by_key)],
        "receipt_decisions": receipt_decisions,
    }
    execution = {
        "item_controls": [controls_by_item[key] for key in sorted(controls_by_item)],
        "strategy": strategy_descriptor(strategy_type),
        "approval_reference": "APPROVAL-1",
        "allowed_action_types": first["allowed_action_types"],
        "capacity_mode": first["capacity_mode"],
        "execution_purpose": ("OPERATIONAL" if strategy_type == "MATHEMATICAL" else "SHADOW"),
    }
    if "strategy_input_binding" in first:
        execution["strategy_input_binding"] = first["strategy_input_binding"]
    return seal_action_observation_source(
        prepared_input=prepared,
        execution=execution,
        observation_input_hash=OBSERVATION_INPUT_HASH,
        admissions_by_item=None,
    )


def baseline(rows: list[dict]) -> dict:
    return build_psi_child_artifact(
        engine_run_id=RUN_ID,
        attempt_no=1,
        canonical_input_hash=CANONICAL_HASH,
        result_kind="BASELINE_PSI",
        execution_role="EVIDENCE_ONLY",
        strategy_type="NONE",
        scenario_id="BASE",
        scenario_content_hash=SCENARIO_HASH,
        psi_rows=rows,
    )


def mathematical(
    rows: list[dict],
    decisions: list[dict] | None = None,
    *,
    observation_source: dict | None = None,
) -> dict:
    actual_decisions = decisions or [
        decision_for_row(row, strategy_type="MATHEMATICAL", all_rows=rows) for row in rows
    ]
    return build_psi_child_artifact(
        engine_run_id=RUN_ID,
        attempt_no=1,
        canonical_input_hash=CANONICAL_HASH,
        result_kind="RECOMMENDED_PSI",
        execution_role="OPERATIONAL",
        strategy_type="MATHEMATICAL",
        scenario_id="BASE",
        scenario_content_hash=SCENARIO_HASH,
        psi_rows=rows,
        decision_evidence=actual_decisions,
        expected_strategy_descriptor=strategy_descriptor("MATHEMATICAL"),
        expected_execution_approval_reference="APPROVAL-1",
        expected_observation_input_hash=OBSERVATION_INPUT_HASH,
        expected_observation_source=observation_source
        or action_observation_source(rows, actual_decisions, strategy_type="MATHEMATICAL"),
    )


def ppo(rows: list[dict], decisions: list[dict] | None = None) -> dict:
    actual_decisions = decisions or [
        decision_for_row(row, strategy_type="DEEP_RL", all_rows=rows) for row in rows
    ]
    return build_psi_child_artifact(
        engine_run_id=RUN_ID,
        attempt_no=1,
        canonical_input_hash=CANONICAL_HASH,
        result_kind="RECOMMENDED_PSI",
        execution_role="SHADOW",
        strategy_type="DEEP_RL",
        scenario_id="BASE",
        scenario_content_hash=SCENARIO_HASH,
        psi_rows=rows,
        decision_evidence=actual_decisions,
        challenger_id="PPO-1",
        model_content_hash="c" * 64,
        expected_strategy_descriptor=strategy_descriptor("DEEP_RL"),
        expected_execution_approval_reference="APPROVAL-1",
        expected_observation_input_hash=OBSERVATION_INPUT_HASH,
        expected_observation_source=action_observation_source(
            rows, actual_decisions, strategy_type="DEEP_RL"
        ),
    )


class ResultArtifactTests(unittest.TestCase):
    def test_raw_action_persists_the_sealed_authoritative_observation_source(self):
        row = psi_row(
            scenario="RECOMMENDED",
            strategy="MATHEMATICAL",
            forecast="1",
            fulfilled_forecast="1",
        )

        child = mathematical([row])
        raw_source = child["action_artifacts"]["raw_action"]["artifact"]["observation_source"]

        self.assertEqual(raw_source["contract_id"], "inventory-action-observation-source-v1")
        self.assertEqual(
            raw_source["content_hash"],
            digest({key: value for key, value in raw_source.items() if key != "content_hash"}),
        )
        self.assertIsNone(
            child["action_artifacts"]["constrained_action"]["artifact"]["observation_source"]
        )
        self.assertIsNone(
            child["action_artifacts"]["adjustment_reasons"]["artifact"]["observation_source"]
        )

    def test_resealed_context_cannot_replace_the_authoritative_observation_source(self):
        row = psi_row(scenario="RECOMMENDED", strategy="MATHEMATICAL")
        valid = decision_for_row(row, strategy_type="MATHEMATICAL")
        source = action_observation_source([row], [valid], strategy_type="MATHEMATICAL")
        forged = copy.deepcopy(valid)
        forged["observation"]["context"]["plan_id"] = "FORGED-PLAN"
        forged["observation_hash"] = digest(forged["observation"])
        forged["proposal"]["observation_hash"] = forged["observation_hash"]
        forged["validation"] = validate_action(forged["observation"], forged["proposal"])

        with self.assertRaisesRegex(InventoryInputError, "ACTION_OBSERVATION_CONTEXT_MISMATCH"):
            mathematical([row], [forged], observation_source=source)

    def test_decision_id_is_rederived_from_the_observation_input_and_bucket(self):
        row = psi_row(scenario="RECOMMENDED", strategy="MATHEMATICAL")
        valid = decision_for_row(row, strategy_type="MATHEMATICAL")
        source = action_observation_source([row], [valid], strategy_type="MATHEMATICAL")
        forged = copy.deepcopy(valid)
        forged["observation"]["decision_id"] = "DEC-FORGED"
        forged["observation_hash"] = digest(forged["observation"])
        forged["proposal"]["decision_id"] = "DEC-FORGED"
        forged["proposal"]["observation_hash"] = forged["observation_hash"]
        forged["validation"] = validate_action(forged["observation"], forged["proposal"])

        with self.assertRaisesRegex(
            InventoryInputError,
            "ACTION_EVIDENCE_DECISION_ID_MISMATCH",
        ):
            mathematical([row], [forged], observation_source=source)

    def test_psi_row_contract_rejects_unclaimed_volatile_fields(self):
        row = psi_row()
        row["volatile_note"] = "not-part-of-v1"

        with self.assertRaisesRegex(InventoryInputError, "PSI_ARTIFACT_ROW_CONTRACT_INVALID"):
            baseline([row])

    def test_physical_capacity_excess_is_recomputed_from_the_authoritative_policy(self):
        row = psi_row(
            scenario="RECOMMENDED",
            strategy="MATHEMATICAL",
            boh="10",
            eoh="10",
        )
        evidence = decision_for_row(row, strategy_type="MATHEMATICAL")
        evidence["observation"]["policy"]["physical_max_capacity"] = "5"
        evidence["observation"]["policy_schedule"] = [evidence["observation"]["policy"]]
        evidence["observation_hash"] = digest(evidence["observation"])
        evidence["proposal"]["observation_hash"] = evidence["observation_hash"]
        evidence["validation"] = validate_action(evidence["observation"], evidence["proposal"])

        with self.assertRaisesRegex(
            InventoryInputError,
            "PSI_PHYSICAL_CAPACITY_EXCESS_MISMATCH",
        ):
            mathematical([row], [evidence])

    def test_actual_rows_build_child_actions_and_all_metrics(self):
        base_rows = [
            psi_row(
                seq=0,
                confirmed="2",
                forecast="8",
                fulfilled_confirmed="2",
                fulfilled_forecast="3",
                shortage="5",
            ),
            psi_row(seq=1, confirmed="4", forecast="6", backorder="4", shortage="6"),
        ]
        candidate_rows = [
            psi_row(
                seq=0,
                scenario="RECOMMENDED",
                strategy="MATHEMATICAL",
                confirmed="2",
                forecast="8",
                fulfilled_confirmed="2",
                fulfilled_forecast="8",
                boh="5",
                eoh="2",
            ),
            psi_row(
                seq=1,
                scenario="RECOMMENDED",
                strategy="MATHEMATICAL",
                boh="2",
                confirmed="4",
                forecast="6",
                fulfilled_confirmed="4",
                fulfilled_forecast="6",
                eoh="7",
            ),
        ]

        base = baseline(base_rows)
        candidate = mathematical(candidate_rows)
        comparison = compare_psi_children(base, candidate)
        detailed = compare_psi_children_detailed(base, candidate)

        self.assertEqual(
            set(candidate),
            {"child_result", "artifact_reference", "artifact", "metrics", "action_artifacts"},
        )
        self.assertEqual(
            candidate["child_result"]["artifact_contract_key"],
            PSI_RESULT_ARTIFACT_CONTRACT_KEY,
        )
        self.assertEqual(candidate["child_result"]["row_count"], 2)
        self.assertEqual(
            candidate["child_result"]["child_content_hash"],
            candidate["artifact"]["content_hash"],
        )
        self.assertEqual(
            candidate["artifact"]["content_hash"],
            digest(
                {
                    key: value
                    for key, value in candidate["artifact"].items()
                    if key != "content_hash"
                }
            ),
        )
        self.assertEqual(candidate["metrics"]["horizon_demand_qty"], "20")
        self.assertEqual(candidate["metrics"]["on_time_fulfilled_qty"], "20")
        self.assertEqual(candidate["metrics"]["projected_service_level"], "1")
        self.assertEqual(candidate["metrics"]["ending_available_inventory_qty"], "7")
        self.assertEqual(candidate["metrics"]["ending_on_hand_inventory_qty"], "7")
        self.assertEqual(candidate["metrics"]["ending_backorder_qty"], "0")
        self.assertEqual(
            set(candidate["action_artifacts"]),
            {"raw_action", "constrained_action", "adjustment_reasons"},
        )
        self.assertEqual(comparison["service_level_delta"]["value"], "0.75")
        self.assertEqual(comparison["backorder_qty_delta"]["value"], "-4")
        self.assertEqual(
            comparison["cost_delta"],
            {"status": "NOT_AVAILABLE", "value": None, "reason_code": "COST_PROFILE_NOT_BOUND"},
        )
        self.assertEqual(detailed["bundle_comparison"], comparison)
        self.assertEqual(detailed["ending_available_inventory_qty_delta"]["value"], "7")
        self.assertEqual(detailed["ending_on_hand_inventory_qty_delta"]["value"], "7")

    def test_row_and_decision_order_do_not_change_artifact_or_references(self):
        rows = [
            psi_row(
                seq=0,
                scenario="RECOMMENDED",
                strategy="MATHEMATICAL",
                forecast="3",
                fulfilled_forecast="1",
            ),
            psi_row(
                seq=1,
                scenario="RECOMMENDED",
                strategy="MATHEMATICAL",
                forecast="3",
                fulfilled_forecast="2",
            ),
        ]
        decisions = [
            decision_for_row(row, strategy_type="MATHEMATICAL", all_rows=rows) for row in rows
        ]

        first = mathematical(rows, decisions)
        second = mathematical(list(reversed(rows)), list(reversed(decisions)))

        self.assertEqual(first, second)
        self.assertEqual(
            first["child_result"]["child_result_id"],
            derive_psi_child_result_id(
                engine_run_id=RUN_ID,
                attempt_no=1,
                canonical_input_hash=CANONICAL_HASH,
                result_kind="RECOMMENDED_PSI",
                execution_role="OPERATIONAL",
                strategy_type="MATHEMATICAL",
                scenario_id="BASE",
                scenario_content_hash=SCENARIO_HASH,
            ),
        )

    def test_all_items_must_share_the_same_pinned_bucket_dates(self):
        item_a = psi_row(item_id="ITEM-A")
        item_b = psi_row(item_id="ITEM-B")
        item_b["start_date"] = "2026-10-05"
        item_b["end_date"] = "2026-10-11"

        with self.assertRaisesRegex(
            InventoryInputError,
            "PSI_ARTIFACT_HORIZON_MISMATCH",
        ):
            baseline([item_a, item_b])

    def test_bucket_range_and_continuity_are_validated(self):
        too_long = psi_row()
        too_long["end_date"] = "2026-10-05"
        with self.assertRaisesRegex(
            InventoryInputError,
            "PSI_ARTIFACT_BUCKET_RANGE_INVALID",
        ):
            baseline([too_long])

        first = psi_row(seq=0)
        second = psi_row(seq=1)
        second["start_date"] = "2026-10-06"
        second["end_date"] = "2026-10-12"
        with self.assertRaisesRegex(
            InventoryInputError,
            "PSI_ARTIFACT_BUCKET_CONTINUITY_INVALID",
        ):
            baseline([first, second])

    def test_partial_edge_bucket_keeps_the_pinned_business_week_key(self):
        partial = psi_row()
        partial["end_date"] = partial["start_date"]

        built = baseline([partial])

        self.assertEqual(built["artifact"]["psi_rows"][0]["yyyyww"], "202640")

    def test_multi_item_metrics_can_exceed_the_per_row_quantity_limit(self):
        rows = [
            psi_row(
                item_id=item_id,
                forecast="600000000000",
                fulfilled_forecast="600000000000",
            )
            for item_id in ("ITEM-A", "ITEM-B")
        ]

        built = baseline(rows)

        self.assertEqual(built["metrics"]["horizon_demand_qty"], "1200000000000")
        self.assertEqual(built["metrics"]["on_time_fulfilled_qty"], "1200000000000")
        self.assertEqual(built["metrics"]["projected_service_level"], "1")

    def test_service_delta_rounds_once_from_exact_row_totals(self):
        base = baseline([psi_row(forecast="3", fulfilled_forecast="1")])
        candidate = mathematical(
            [
                psi_row(
                    scenario="RECOMMENDED",
                    strategy="MATHEMATICAL",
                    forecast="3",
                    fulfilled_forecast="2",
                    boh="1",
                )
            ]
        )

        comparison = compare_psi_children(base, candidate)

        self.assertEqual(base["metrics"]["projected_service_level"], "0.333333")
        self.assertEqual(candidate["metrics"]["projected_service_level"], "0.666667")
        self.assertEqual(comparison["service_level_delta"]["value"], "0.333333")

    def test_successful_ppo_actions_carry_full_child_semantic_binding(self):
        rows = [
            psi_row(
                scenario="RECOMMENDED",
                strategy="DEEP_RL",
                forecast="2",
                fulfilled_forecast="2",
            )
        ]

        child = ppo(rows)

        self.assertEqual(child["child_result"]["execution_role"], "SHADOW")
        for action in child["action_artifacts"].values():
            artifact = action["artifact"]
            self.assertEqual(artifact["canonical_input_hash"], CANONICAL_HASH)
            self.assertEqual(artifact["result_kind"], "RECOMMENDED_PSI")
            self.assertEqual(artifact["execution_role"], "SHADOW")
            self.assertEqual(artifact["strategy_type"], "DEEP_RL")
            self.assertEqual(artifact["scenario_content_hash"], SCENARIO_HASH)
            self.assertEqual(artifact["challenger_id"], "PPO-1")
            self.assertEqual(artifact["model_content_hash"], "c" * 64)

    def test_zero_demand_service_is_explicitly_not_available(self):
        base = baseline([psi_row(eoh="10", on_hand_eoh="12")])
        candidate = mathematical(
            [
                psi_row(
                    scenario="RECOMMENDED",
                    strategy="MATHEMATICAL",
                    boh="10",
                    eoh="15",
                    on_hand_eoh="17",
                )
            ]
        )

        comparison = compare_psi_children(base, candidate)

        self.assertIsNone(summarize_psi_rows([psi_row()])["projected_service_level"])
        self.assertEqual(
            comparison["service_level_delta"],
            {"status": "NOT_AVAILABLE", "value": None, "reason_code": "NO_DEMAND_IN_HORIZON"},
        )
        self.assertEqual(comparison["backorder_qty_delta"]["value"], "0")

    def test_failed_candidates_never_fabricate_artifacts_or_metrics(self):
        base = baseline([psi_row(forecast="1", fulfilled_forecast="1")])
        candidate = build_failed_psi_child(
            engine_run_id=RUN_ID,
            attempt_no=1,
            canonical_input_hash=CANONICAL_HASH,
            result_kind="RECOMMENDED_PSI",
            execution_role="SHADOW",
            strategy_type="DEEP_RL",
            scenario_id="BASE",
            scenario_content_hash=SCENARIO_HASH,
            challenger_id="PPO-1",
            model_content_hash="c" * 64,
            failure_reason_code="PPO_INFERENCE_FAILED",
            status="FAILED",
        )
        comparison = compare_psi_children(base, candidate)
        detailed = compare_psi_children_detailed(base, candidate)

        self.assertIsNone(candidate["artifact"])
        self.assertIsNone(candidate["metrics"])
        self.assertEqual(candidate["action_artifacts"], {})
        expected = {
            "status": "NOT_AVAILABLE",
            "value": None,
            "reason_code": "CANDIDATE_RESULT_FAILED",
        }
        self.assertTrue(
            all(
                comparison[key] == expected
                for key in ("cost_delta", "service_level_delta", "backorder_qty_delta")
            )
        )
        self.assertEqual(detailed["ending_available_inventory_qty_delta"], expected)
        self.assertEqual(detailed["ending_on_hand_inventory_qty_delta"], expected)

    def test_unavailable_stress_candidate_is_explicitly_skipped_without_artifacts(self):
        base = baseline([psi_row(forecast="1", fulfilled_forecast="1")])
        candidate = build_failed_psi_child(
            engine_run_id=RUN_ID,
            attempt_no=1,
            canonical_input_hash=CANONICAL_HASH,
            result_kind="STRESS_PSI",
            execution_role="EVIDENCE_ONLY",
            strategy_type="MATHEMATICAL",
            scenario_id="LATE-SUPPLY",
            scenario_content_hash="e" * 64,
            failure_reason_code="STRESS_SCENARIO_UNAVAILABLE",
            status="SKIPPED",
        )

        comparison = compare_psi_children(base, candidate)
        self.assertIsNone(candidate["artifact"])
        self.assertEqual(
            comparison["service_level_delta"],
            {
                "status": "NOT_AVAILABLE",
                "value": None,
                "reason_code": "CANDIDATE_RESULT_SKIPPED",
            },
        )

    def test_scalar_comparison_fails_closed_for_multiple_uoms(self):
        base = baseline(
            [
                psi_row(item_id="ITEM-A", uom="EA", forecast="1", fulfilled_forecast="1"),
                psi_row(item_id="ITEM-B", uom="KG", forecast="1", fulfilled_forecast="1"),
            ]
        )
        candidate_rows = [
            psi_row(
                item_id="ITEM-A",
                uom="EA",
                scenario="RECOMMENDED",
                strategy="MATHEMATICAL",
                forecast="1",
                fulfilled_forecast="1",
                boh="1",
            ),
            psi_row(
                item_id="ITEM-B",
                uom="KG",
                scenario="RECOMMENDED",
                strategy="MATHEMATICAL",
                forecast="1",
                fulfilled_forecast="1",
                boh="1",
            ),
        ]
        candidate = mathematical(
            candidate_rows,
            [
                decision_for_row(candidate_rows[0], strategy_type="MATHEMATICAL"),
                decision_for_row(candidate_rows[1], strategy_type="MATHEMATICAL"),
            ],
        )

        with self.assertRaisesRegex(InventoryInputError, "PSI_COMPARISON_MIXED_UOM_UNSUPPORTED"):
            compare_psi_children(base, candidate)

    def test_base_strategy_cannot_change_forecast_or_initial_inventory(self):
        base = baseline([psi_row(forecast="10")])
        for candidate_row, reason in (
            (
                psi_row(
                    scenario="RECOMMENDED",
                    strategy="MATHEMATICAL",
                    forecast="100",
                ),
                "PSI_BASE_SCENARIO_EXOGENOUS_INPUT_MISMATCH",
            ),
            (
                psi_row(
                    scenario="RECOMMENDED",
                    strategy="MATHEMATICAL",
                    forecast="10",
                    boh="1",
                    fulfilled_forecast="1",
                    recommended_receipt="0",
                ),
                "PSI_BASE_SCENARIO_INITIAL_STATE_MISMATCH",
            ),
        ):
            with self.subTest(reason=reason):
                candidate = mathematical([candidate_row])
                with self.assertRaisesRegex(InventoryInputError, reason):
                    compare_psi_children(base, candidate)

    def test_tampered_artifact_is_rejected_before_comparison(self):
        base = baseline([psi_row(forecast="1", fulfilled_forecast="1")])
        candidate = mathematical(
            [
                psi_row(
                    scenario="RECOMMENDED",
                    strategy="MATHEMATICAL",
                    forecast="1",
                    fulfilled_forecast="1",
                )
            ]
        )
        tampered = copy.deepcopy(candidate)
        tampered["artifact"]["psi_rows"][0]["eoh_qty"] = "999"

        with self.assertRaisesRegex(InventoryInputError, "PSI_CHILD_ARTIFACT_HASH_MISMATCH"):
            compare_psi_children(base, tampered)

    def test_resealed_artifact_cannot_claim_a_false_row_count(self):
        base = baseline([psi_row(forecast="1", fulfilled_forecast="1")])
        candidate = mathematical(
            [
                psi_row(
                    scenario="RECOMMENDED",
                    strategy="MATHEMATICAL",
                    forecast="1",
                    fulfilled_forecast="1",
                )
            ]
        )
        tampered = copy.deepcopy(candidate)
        tampered["artifact"]["row_count"] = 999
        tampered["artifact"]["content_hash"] = digest(
            {key: value for key, value in tampered["artifact"].items() if key != "content_hash"}
        )
        tampered["child_result"]["row_count"] = 999
        tampered["child_result"]["child_content_hash"] = tampered["artifact"]["content_hash"]

        with self.assertRaisesRegex(InventoryInputError, "PSI_CHILD_ARTIFACT_ROW_COUNT_MISMATCH"):
            compare_psi_children(base, tampered)

    def test_tampered_action_artifact_is_rejected_before_comparison(self):
        base = baseline([psi_row(forecast="1", fulfilled_forecast="1")])
        candidate = mathematical(
            [
                psi_row(
                    scenario="RECOMMENDED",
                    strategy="MATHEMATICAL",
                    forecast="1",
                    fulfilled_forecast="1",
                )
            ]
        )
        tampered = copy.deepcopy(candidate)
        tampered["action_artifacts"]["raw_action"]["artifact"]["rows"][0]["proposal"][
            "quantity"
        ] = "999"

        with self.assertRaisesRegex(InventoryInputError, "ACTION_EVIDENCE_HASH_MISMATCH"):
            compare_psi_children(base, tampered)

    def test_raw_action_must_satisfy_the_official_proposal_contract(self):
        row = psi_row(
            scenario="RECOMMENDED",
            strategy="MATHEMATICAL",
            eoh="1",
            recommended_receipt="1",
        )
        invalid = decision_for_row(row, strategy_type="MATHEMATICAL")
        del invalid["proposal"]["observation_hash"]

        with self.assertRaisesRegex(InventoryInputError, "CONTRACT_FIELDS"):
            mathematical([row], [invalid])

    def test_constrained_action_uses_a_closed_validation_contract(self):
        row = psi_row(
            scenario="RECOMMENDED",
            strategy="MATHEMATICAL",
            eoh="1",
            recommended_receipt="1",
        )
        invalid = decision_for_row(row, strategy_type="MATHEMATICAL")
        invalid["validation"]["invented_override"] = "UNTRUSTED"

        with self.assertRaisesRegex(InventoryInputError, "ACTION_VALIDATION_CONTRACT_INVALID"):
            mathematical([row], [invalid])

    def test_accepted_actions_must_reconcile_to_recommended_psi_receipts(self):
        row = psi_row(
            scenario="RECOMMENDED",
            strategy="MATHEMATICAL",
            eoh="10",
            recommended_receipt="10",
        )
        mismatched = decision(
            0,
            accepted_qty="9",
            receipt_bucket=row["yyyyww"],
            decision_date=row["start_date"],
        )

        with self.assertRaisesRegex(InventoryInputError, "ACTION_OBSERVATION_PSI_RECEIPT_MISMATCH"):
            mathematical([row], [mismatched])

    def test_adjustment_reason_must_match_the_actual_quantity_or_date_change(self):
        row = psi_row(
            scenario="RECOMMENDED",
            strategy="MATHEMATICAL",
            eoh="10",
            recommended_receipt="10",
        )
        false_adjustment = decision_for_row(row, strategy_type="MATHEMATICAL")
        false_adjustment["validation"]["status"] = "ADJUSTED"
        false_adjustment["validation"]["reason_codes"] = ["MOQ_OR_MULTIPLE_ROUND_UP"]

        with self.assertRaisesRegex(InventoryInputError, "ACTION_VALIDATION_ADJUSTMENT_INVALID"):
            mathematical([row], [false_adjustment])

    def test_each_guard_rejection_stage_builds_valid_action_evidence(self):
        row = psi_row(scenario="RECOMMENDED", strategy="MATHEMATICAL")
        cases = (
            rejected_decision_for_row(row, reason_code="ACTION_NOT_APPROVED"),
            rejected_decision_for_row(
                row,
                reason_code="TARGET_OUTSIDE_APPROVED_BOUNDS",
                action_type="ORDER_UP_TO",
            ),
            rejected_decision_for_row(row, reason_code="ORDER_CALENDAR_CLOSED"),
            rejected_decision_for_row(
                row,
                reason_code="ARRIVAL_OUTSIDE_PLAN_HORIZON",
                due_date=row["start_date"],
            ),
            rejected_decision_for_row(
                row,
                reason_code="NO_FEASIBLE_ORDER_QUANTITY",
                lot_rounded_qty="100",
                due_date=row["start_date"],
                receipt_date=row["start_date"],
                receipt_bucket=row["yyyyww"],
                capacity_headroom_qty="20",
            ),
        )

        for evidence in cases:
            with self.subTest(reason=evidence["validation"]["reason_codes"][0]):
                artifact = mathematical([row], [evidence])
                constrained = artifact["action_artifacts"]["constrained_action"]["artifact"]
                self.assertEqual(constrained["rows"][0], evidence["validation"])

    def test_actual_guard_rejection_outputs_are_admitted_by_the_same_state_machine(self):
        row = psi_row(scenario="RECOMMENDED", strategy="MATHEMATICAL")
        base_proposal = decision(
            0,
            accepted_qty="10",
            receipt_bucket=row["yyyyww"],
            decision_date=row["start_date"],
        )["proposal"]
        cases = []

        observation = guard_observation(row)
        observation["allowed_action_types"] = ["HOLD", "ORDER_UP_TO"]
        cases.append(("ACTION_NOT_APPROVED", observation, copy.deepcopy(base_proposal)))

        observation = guard_observation(row)
        observation["control"]["max_target_qty"] = "100"
        proposal = copy.deepcopy(base_proposal)
        proposal.update(action_type="ORDER_UP_TO", quantity="200")
        cases.append(("TARGET_OUTSIDE_APPROVED_BOUNDS", observation, proposal))

        observation = guard_observation(row)
        observation["control"]["order_dates"] = []
        cases.append(("ORDER_CALENDAR_CLOSED", observation, copy.deepcopy(base_proposal)))

        observation = guard_observation(row)
        observation["policy"]["lead_time_days"] = 1
        cases.append(("ARRIVAL_OUTSIDE_PLAN_HORIZON", observation, copy.deepcopy(base_proposal)))

        observation = guard_observation(row)
        observation["policy"]["moq"] = "100"
        observation["control"]["max_order_qty"] = "50"
        observation["policy_schedule"] = [observation["policy"]]
        cases.append(("NO_FEASIBLE_ORDER_QUANTITY", observation, copy.deepcopy(base_proposal)))

        for reason_code, observation, proposal in cases:
            with self.subTest(reason=reason_code):
                observation = ReplenishmentObservation.from_dict(observation).to_dict()
                proposal["observation_hash"] = digest(observation)
                validation = validate_action(observation, proposal)
                self.assertEqual(validation["status"], "REJECTED")
                self.assertEqual(validation["reason_codes"], [reason_code])
                artifact = mathematical(
                    [row],
                    [
                        {
                            "observation": observation,
                            "observation_hash": digest(observation),
                            "proposal": proposal,
                            "validation": validation,
                        }
                    ],
                )
                self.assertEqual(
                    artifact["action_artifacts"]["constrained_action"]["artifact"]["rows"][0],
                    validation,
                )

    def test_decimal_context_cannot_change_roll_forward_receipts_deltas_or_hashes(self):
        base_rows = [
            psi_row(seq=0, eoh="1234.56"),
            psi_row(seq=1, eoh="1234.56"),
        ]
        candidate_rows = [
            psi_row(
                seq=0,
                scenario="RECOMMENDED",
                strategy="MATHEMATICAL",
                boh="1234.56",
                recommended_receipt="0",
                eoh="1234.56",
            ),
            psi_row(
                seq=1,
                scenario="RECOMMENDED",
                strategy="MATHEMATICAL",
                boh="1234.56",
                recommended_receipt="2345.67",
                eoh="3580.23",
            ),
        ]
        decisions = [
            decision_for_row(row, strategy_type="MATHEMATICAL", all_rows=candidate_rows)
            for row in candidate_rows
        ]

        def execute(precision: int) -> tuple[dict, dict, dict]:
            with localcontext() as context:
                context.prec = precision
                base = baseline(copy.deepcopy(base_rows))
                candidate = mathematical(copy.deepcopy(candidate_rows), copy.deepcopy(decisions))
                comparison = compare_psi_children_detailed(base, candidate)
                return base, candidate, comparison

        low_precision = execute(2)
        contract_precision = execute(40)

        self.assertEqual(low_precision, contract_precision)
        self.assertEqual(
            low_precision[2]["ending_available_inventory_qty_delta"]["value"],
            "2345.67",
        )
        self.assertEqual(
            low_precision[2]["ending_on_hand_inventory_qty_delta"]["value"],
            "2345.67",
        )

    def test_decimal_context_cannot_change_adjusted_action_conservation(self):
        row = psi_row(
            scenario="RECOMMENDED",
            strategy="MATHEMATICAL",
            recommended_receipt="1200",
            eoh="1200",
        )
        evidence = decision_for_row(row, strategy_type="MATHEMATICAL")
        evidence["observation"]["policy"]["order_multiple"] = "100"
        evidence["observation"]["control"]["max_order_qty"] = "1200"
        evidence["observation"]["policy_schedule"] = [evidence["observation"]["policy"]]
        evidence["observation_hash"] = digest(evidence["observation"])
        evidence["proposal"]["observation_hash"] = evidence["observation_hash"]
        evidence["proposal"]["quantity"] = "1234.56"
        evidence["validation"] = validate_action(evidence["observation"], evidence["proposal"])

        builds = []
        for precision in (2, 40):
            with localcontext() as context:
                context.prec = precision
                builds.append(mathematical([copy.deepcopy(row)], [copy.deepcopy(evidence)]))

        self.assertEqual(builds[0], builds[1])

    def test_accepted_order_cannot_exceed_reported_capacity_headroom(self):
        row = psi_row(
            scenario="RECOMMENDED",
            strategy="MATHEMATICAL",
            recommended_receipt="25",
            eoh="25",
        )
        evidence = decision_for_row(row, strategy_type="MATHEMATICAL")
        evidence["proposal"]["quantity"] = "25"
        evidence["validation"].update(
            requested_qty="25",
            raw_order_qty="25",
            lot_rounded_qty="25",
            accepted_order_qty="25",
            uncovered_order_qty="0",
            capacity_headroom_qty="1",
            status="ACCEPTED",
            reason_codes=["CONSTRAINTS_PASSED"],
        )

        with self.assertRaisesRegex(
            InventoryInputError,
            "ACTION_VALIDATION_CAPACITY_HEADROOM_INVALID",
        ):
            mathematical([row], [evidence])

    def test_early_guard_rejections_cannot_claim_later_stage_state(self):
        row = psi_row(scenario="RECOMMENDED", strategy="MATHEMATICAL")
        cases = (
            (
                "ACTION_NOT_APPROVED",
                {"lot_rounded_qty": "10"},
            ),
            (
                "TARGET_OUTSIDE_APPROVED_BOUNDS",
                {"due_date": row["start_date"]},
            ),
            (
                "ORDER_CALENDAR_CLOSED",
                {"capacity_headroom_qty": "10"},
            ),
        )
        for reason_code, tampering in cases:
            with self.subTest(reason=reason_code):
                evidence = rejected_decision_for_row(
                    row,
                    reason_code=reason_code,
                    action_type=(
                        "ORDER_UP_TO"
                        if reason_code == "TARGET_OUTSIDE_APPROVED_BOUNDS"
                        else "ORDER_QTY"
                    ),
                )
                evidence["validation"].update(tampering)
                with self.assertRaisesRegex(
                    InventoryInputError, "ACTION_VALIDATION_REJECTION_INVALID"
                ):
                    mathematical([row], [evidence])

    def test_arrival_outside_rejection_cannot_claim_a_mapped_receipt(self):
        row = psi_row(scenario="RECOMMENDED", strategy="MATHEMATICAL")
        evidence = rejected_decision_for_row(
            row,
            reason_code="ARRIVAL_OUTSIDE_PLAN_HORIZON",
            due_date=row["start_date"],
            receipt_date=row["start_date"],
            receipt_bucket=row["yyyyww"],
        )
        evidence["validation"].update(
            due_date=row["start_date"],
            receipt_date=row["start_date"],
            receipt_bucket=row["yyyyww"],
        )

        with self.assertRaisesRegex(InventoryInputError, "ACTION_VALIDATION_REJECTION_INVALID"):
            mathematical([row], [evidence])

    def test_no_feasible_rejection_requires_the_guard_arrival_and_rounding_state(self):
        row = psi_row(scenario="RECOMMENDED", strategy="MATHEMATICAL")
        cases = (
            {
                "lot_rounded_qty": "0",
                "due_date": None,
                "receipt_date": None,
                "receipt_bucket": None,
                "capacity_headroom_qty": None,
            },
            {"lot_rounded_qty": "0"},
        )
        for tampering in cases:
            with self.subTest(tampering=tampering):
                evidence = rejected_decision_for_row(
                    row,
                    reason_code="NO_FEASIBLE_ORDER_QUANTITY",
                )
                evidence["validation"].update(tampering)
                with self.assertRaisesRegex(
                    InventoryInputError, "ACTION_VALIDATION_REJECTION_INVALID"
                ):
                    mathematical([row], [evidence])

    def test_rejection_reason_must_be_reachable_at_that_guard_stage(self):
        row = psi_row(scenario="RECOMMENDED", strategy="MATHEMATICAL")
        target_unreachable = rejected_decision_for_row(
            row,
            reason_code="ACTION_NOT_APPROVED",
            action_type="ORDER_QTY",
        )
        target_unreachable["validation"]["reason_codes"] = ["TARGET_OUTSIDE_APPROVED_BOUNDS"]
        closed_unreachable = rejected_decision_for_row(
            row,
            reason_code="ORDER_CALENDAR_CLOSED",
        )
        closed_unreachable["proposal"]["quantity"] = "0"
        closed_unreachable["validation"].update(
            requested_qty="0",
            raw_order_qty="0",
            uncovered_order_qty="0",
        )
        cases = (target_unreachable, closed_unreachable)
        for evidence in cases:
            with self.subTest(reason=evidence["validation"]["reason_codes"][0]):
                with self.assertRaisesRegex(
                    InventoryInputError, "ACTION_VALIDATION_REJECTION_INVALID"
                ):
                    mathematical([row], [evidence])

    def test_no_feasible_rejection_receipt_mapping_must_match_the_psi_calendar(self):
        row = psi_row(scenario="RECOMMENDED", strategy="MATHEMATICAL")
        evidence = rejected_decision_for_row(
            row,
            reason_code="NO_FEASIBLE_ORDER_QUANTITY",
            lot_rounded_qty="100",
            due_date=row["start_date"],
            receipt_date=row["start_date"],
            receipt_bucket="202641",
        )
        evidence["validation"]["receipt_bucket"] = "202641"

        with self.assertRaisesRegex(InventoryInputError, "ACTION_GUARD_REPLAY_MISMATCH"):
            mathematical([row], [evidence])

    def test_lot_rounding_cannot_round_a_raw_order_down(self):
        row = psi_row(
            scenario="RECOMMENDED",
            strategy="MATHEMATICAL",
            eoh="5",
            recommended_receipt="5",
        )
        invalid = decision_for_row(row, strategy_type="MATHEMATICAL")
        invalid["proposal"]["quantity"] = "10"
        invalid["validation"].update(
            requested_qty="10",
            raw_order_qty="10",
            uncovered_order_qty="5",
            status="ADJUSTED",
            reason_codes=["MOQ_OR_MULTIPLE_ROUND_UP"],
        )

        with self.assertRaisesRegex(InventoryInputError, "ACTION_VALIDATION_ACCEPTANCE_INVALID"):
            mathematical([row], [invalid])

    def test_impossible_inventory_balance_is_rejected_before_artifact_sealing(self):
        row = psi_row(forecast="1", fulfilled_forecast="1")
        row["eoh_qty"] = "999"
        row["on_hand_eoh_qty"] = "999"

        with self.assertRaisesRegex(InventoryInputError, "PSI_ARTIFACT_STOCK_CONSERVATION_INVALID"):
            baseline([row])

    def test_fulfillment_must_consume_old_backorder_before_confirmed_order(self):
        row = psi_row(
            boh="5",
            backorder_open="5",
            confirmed="5",
            fulfilled_backorder="0",
            fulfilled_confirmed="5",
            backorder="5",
            recommended_receipt="0",
        )

        with self.assertRaisesRegex(
            InventoryInputError, "PSI_ARTIFACT_FULFILLMENT_PRIORITY_INVALID"
        ):
            baseline([row])

    def test_stress_child_requires_explicit_stress_row_semantics(self):
        with self.assertRaisesRegex(InventoryInputError, "STRESS_PSI_ROW_SEMANTICS_INVALID"):
            build_psi_child_artifact(
                engine_run_id=RUN_ID,
                attempt_no=1,
                canonical_input_hash=CANONICAL_HASH,
                result_kind="STRESS_PSI",
                execution_role="EVIDENCE_ONLY",
                strategy_type="MATHEMATICAL",
                scenario_id="DEMAND_X2",
                scenario_content_hash="d" * 64,
                psi_rows=[
                    psi_row(
                        scenario="RECOMMENDED",
                        strategy="MATHEMATICAL",
                        forecast="1",
                        fulfilled_forecast="1",
                    )
                ],
                decision_evidence=[decision(0)],
            )


if __name__ == "__main__":
    unittest.main()

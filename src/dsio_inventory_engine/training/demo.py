"""Explicit deterministic development recipe; not DemandEngine observed history."""

from datetime import date, timedelta

from dsio_inventory_engine.inventory_contracts.training import TrainingRequest, content, normalize
from dsio_inventory_engine.inventory_contracts.values import digest


def build_demo_request() -> TrainingRequest:
    # These are fixture Calendar codes, never used to convert a supplied planning Calendar.
    start = date(2025, 1, 6)
    calendar = [
        {
            "yyyyww": (start + timedelta(weeks=i)).strftime("%G%V"),
            "start_date": (start + timedelta(weeks=i)).isoformat(),
            "end_date": (start + timedelta(weeks=i, days=6)).isoformat(),
        }
        for i in range(130)
    ]
    names = ["NORMAL", "INTERMITTENT", "DECLINING", "SURGE", "ZERO"]
    items = [
        {
            "item_id": name,
            "uom": "EA",
            "quantity_step": "1",
            "active_from_index": 0,
            "lead_time_weeks": 2,
            "policy_source": "SYNTHETIC_PROFILE",
            "policy_reference": "demo-standard-v1",
            "service_level_code": "SL_95",
            "moq": "10",
            "lot_multiple": "5",
            "physical_capacity_qty": None,
            "legacy_target_inventory_qty": "100",
        }
        for name in names
    ]
    demand: list[dict] = []
    for i, week in enumerate(calendar):
        quantities = [
            10 + (i % 3 - 1) * 2,
            30 if i % 5 == 0 else 0,
            max(0, 20 - i // 6),
            40 if 119 <= i <= 121 else 10,
            0,
        ]
        demand.extend(
            {"item_id": name, "uom": "EA", "yyyyww": week["yyyyww"], "demand_qty": str(qty)}
            for name, qty in zip(names, quantities, strict=True)
        )
    base: dict = {
        "scenario_id": "BASE",
        "delay_kind": "NO_DELAY",
        "delay_weeks": 0,
        "selected_order_ids": [],
        "disruption_from_index": 0,
        "disruption_to_index": 0,
        "service_level_code": None,
        "cost": {
            "currency": "USD",
            "holding_per_unit_week": "1",
            "backlog_per_unit_week": "5",
            "fixed_per_order": "2",
            "purchase_per_unit": "3",
            "terminal_backlog_per_unit": "10",
        },
    }
    scenarios = [
        base,
        {
            **base,
            "scenario_id": "HIGH_SHORTAGE_COST",
            "cost": {**base["cost"], "backlog_per_unit_week": "20"},
        },
        {**base, "scenario_id": "SERVICE_99", "service_level_code": "SL_99"},
        {**base, "scenario_id": "DELAY_1W", "delay_kind": "FIXED_DELAY", "delay_weeks": 1},
        {**base, "scenario_id": "DELAY_2W", "delay_kind": "FIXED_DELAY", "delay_weeks": 2},
        {
            **base,
            "scenario_id": "DISRUPTION",
            "delay_kind": "DISRUPTION_WINDOW",
            "delay_weeks": 2,
            "disruption_from_index": 119,
            "disruption_to_index": 122,
        },
    ]
    data = {
        "contract_id": "io-training-foundation-v1",
        "contract_version": "1.0.0",
        "dataset_id": "DEMO-130W-v1",
        "simulation_run_id": "SYNTH-DEMO-v1",
        "content_hash": "0" * 64,
        "context": {
            "company_cd": "DSE",
            "subs_cd": "C100",
            "site_cd": "V101",
            "planning_cycle_id": "PC-SYNTH-DEMO",
            "planning_cycle_revision_id": "REV-1",
            "cycle_site_execution_id": "PC-SYNTH-DEMO-V101",
            "configuration_revision": "TRAINING-DEMO-v1",
            "plan_type": "TGSM",
        },
        "calendar_snapshot_id": "CALENDAR-DEMO-130W",
        "demand_snapshot_id": "DEMAND-DEMO-130W",
        "policy_snapshot_id": "POLICY-DEMO-v1",
        "w0_index": 78,
        "warmup_weeks": 52,
        "lookback_weeks": 26,
        "lookback_reason": "DEFAULT_26_WEEKS",
        "replenishment_cycle_weeks": 1,
        "missing_history": "REJECT",
        "train_weeks": 26,
        "validation_weeks": 13,
        "test_weeks": 13,
        "calendar": calendar,
        "items": items,
        "demand": demand,
        "scenarios": scenarios,
    }
    data = normalize(data)
    data["content_hash"] = digest(content(data))
    return TrainingRequest.from_dict(data)

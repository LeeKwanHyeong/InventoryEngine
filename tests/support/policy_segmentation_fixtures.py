"""Explicit synthetic temporal experiment, not V100 Source or operational evidence."""

import copy
import random
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING

from fixtures import build_request
from math_fixtures import GOLDEN_MATH, build_math, reseal_math
from strategy_fixtures import request as recommendation_request
from tests.unit.test_seven_axis_segmentation import inputs, v2_metric, v2_values

from dsio_inventory_engine.classify_inventory.application import (
    CONFIG_SCHEMA_V21_HASH,
    CONFIG_SCHEMA_V21_ID,
    CONFIG_SCHEMA_V21_VERSION,
    InventoryScope,
    ItemMetric,
    config_hash,
)
from dsio_inventory_engine.inventory_contracts.values import digest
from dsio_inventory_engine.evaluate_replenishment.policy_segmentation import ExperimentCase

PROFILES = {
    profile_id: {
        "policy_kinds": ["ROP_FIXED_Q", "PERIODIC_ORDER_UP_TO"],
        "rmos_values": ["0"],
        "emos_values": emos,
        "service_level_values": ["0.95"],
        "review_cycle_weeks": [1],
    }
    for profile_id, emos in (
        ("SMALL", ["0.15", "0.35"]),
        ("MEDIUM", ["0.75", "1"]),
        ("LARGE", ["1.5", "2"]),
    )
}
ABC_PROFILES = {
    "AX": "SMALL",
    "AY": "MEDIUM",
    "AZ": "LARGE",
    "BX": "SMALL",
    "BY": "MEDIUM",
    "BZ": "LARGE",
    "CX": "SMALL",
    "CY": "MEDIUM",
    "CZ": "LARGE",
}


def _path(rng, kind, size):
    if kind == "STEADY":
        return [rng.randint(6, 10) for _ in range(size)]
    if kind == "HIGH":
        return [rng.randint(24, 36) for _ in range(size)]
    if kind == "INTERMITTENT":
        return [rng.randint(20, 40) if rng.random() < 0.35 else 0 for _ in range(size)]
    return [rng.choice([0, 5, 15, 40]) for _ in range(size)]


def case(start=date(2023, 1, 2), split="TRAIN", seed=11, fixed_cost="5", *, edges=False):
    weeks, lookback = 13, 26
    case_id = f"{split}-{start.isoformat()}-seed-{seed}"
    calendar = [(start + timedelta(weeks=i)).strftime("%G%V") for i in range(weeks)]
    histories, actual, items, leads = {}, {}, [], {}
    for index, kind in enumerate(("STEADY", "HIGH", "INTERMITTENT", "VOLATILE")):
        item = "SKU-" + kind
        history = _path(random.Random(seed * 100 + index), kind, 52)
        future = _path(random.Random(seed * 10000 + index), kind, weeks)
        mean = sum(Decimal(q) for q in history[-lookback:]) / lookback
        forecast = [str(int(mean.to_integral_value(rounding=ROUND_CEILING)))] * weeks
        lead = (7, 14, 7, 21)[index]
        boh = str(int((mean * (lead // 7 + 2)).to_integral_value(rounding=ROUND_CEILING)))
        histories[item], actual[item], leads[item] = history, list(map(str, future)), lead
        items.append(
            {
                "id": item,
                "boh": boh,
                "forecast": forecast,
                "orders": ["0"] * weeks,
                "receipts": [{"week": 4, "qty": str(int(mean * 2)), "status": "CONFIRMED"}],
            }
        )
    if edges:
        for name, history, forecast in (
            ("ZERO-HISTORY", [0] * 52, ["1"] * weeks),
            ("NO-FUTURE-DEMAND", [10] * 52, ["0"] * weeks),
        ):
            item = "SKU-" + name
            histories[item], actual[item], leads[item] = history, forecast[:], 7
            items.append({"id": item, "boh": "2", "forecast": forecast, "orders": ["0"] * weeks})
    canonical = build_request(
        {
            "id": case_id,
            "start_date": start.isoformat(),
            "weeks": calendar,
            "source_type": "SYNTHETIC_BOH",
            "items": items,
        }
    )
    canonical["context"]["plan_end_date"] = (start + timedelta(weeks=weeks, days=-1)).isoformat()
    for row in canonical["snapshots"]["policies"]["rows"]:
        row.update(
            effective_to=(start + timedelta(weeks=weeks)).isoformat(),
            lead_time_days=leads[row["item_id"]],
            moq="5",
            order_multiple="5",
        )

    def package(source):
        raw = build_math(GOLDEN_MATH["cases"][0])
        rec = recommendation_request(source).to_dict()
        rec["execution"].update(
            contract_version="1.1.0", strategy=raw["recommendation"]["execution"]["strategy"]
        )
        raw["recommendation"] = rec
        ctx = source["context"]
        raw["policy_input"]["context"].update(
            **{k: ctx[k] for k in ("company_cd", "subs_cd", "site_cd", "configuration_revision")},
            canonical_input_hash=rec["execution"]["canonical_input_hash"],
            as_of_date=(start - timedelta(days=1)).isoformat(),
            available_at=ctx["inventory_cutoff_at"],
        )
        raw["policy_input"]["snapshot_id"] = "MATH-" + case_id
        raw["policy_input"]["profile"]["lookback_weeks"] = lookback
        raw["policy_input"]["calendar"] = [
            {
                "yyyyww": (start - timedelta(weeks=lookback - i)).strftime("%G%V"),
                "start_date": (start - timedelta(weeks=lookback - i)).isoformat(),
                "end_date": (start - timedelta(weeks=lookback - i, days=-6)).isoformat(),
            }
            for i in range(lookback)
        ]
        raw["policy_input"]["history"] = [
            {
                "item_id": item,
                "uom": "EA",
                "yyyyww": bucket["yyyyww"],
                "demand_qty": str(q),
            }
            for item, history in histories.items()
            for bucket, q in zip(raw["policy_input"]["calendar"], history[-lookback:], strict=True)
        ]
        return reseal_math(raw)

    planning = package(copy.deepcopy(canonical))
    replay_source = copy.deepcopy(canonical)
    for row in replay_source["snapshots"]["forecast"]["rows"]:
        row["forecast_qty"] = actual[row["item_id"]][calendar.index(row["yyyyww"])]
    replay = package(replay_source)
    values = v2_values()
    values["policy_search"] = {
        "contract_version": "1.0.0",
        "enabled": True,
        "evaluation_mode": "DEVELOPMENT_SHADOW",
        "month_basis": "52_WEEKS_PER_YEAR",
        "lookback_weeks": lookback,
        "max_candidates_per_item": 12,
        "minimum_fill_rate": "0.9",
        "selection_rule": "SERVICE_FEASIBLE_THEN_LOWEST_COST",
        "item_overrides": [],
        "group_profiles": [
            {"segment_key": segment, **copy.deepcopy(PROFILES[profile])}
            for segment, profile in ABC_PROFILES.items()
        ],
        "cost_assumptions": {
            "currency": "USD",
            "source_type": "DEVELOPMENT_ASSUMPTION",
            "allocation_basis": "PER_ITEM_ORDER",
            "holding_cost_per_unit_week": "1",
            "backorder_cost_per_unit_week": "10",
            "fixed_order_cost": fixed_cost,
            "variable_order_cost_per_unit": "0",
        },
    }
    metrics = []
    for index, (item, history) in enumerate(histories.items()):
        quantities = list(map(Decimal, history))
        positive = [i for i, q in enumerate(quantities) if q > 0]
        last_positive = (
            (start - timedelta(weeks=52 - positive[-1])).strftime("%G%V") if positive else None
        )
        total = sum(quantities)
        metrics.append(
            v2_metric(
                ItemMetric(
                    item, 52, 0, 52, total, sum(q * q for q in quantities), total * (index + 1)
                ),
                positive_week_count=len(positive),
                last_positive_demand_yyyyww=last_positive,
            )
        )
    ctx = planning["recommendation"]["canonical_input"]["context"]
    classification_inputs = replace(
        inputs(),
        scope=InventoryScope("offline-segmentation", "DSE", "C100", "V101", "V101"),
        config_revision_id=ctx["configuration_revision"],
        config_values=values,
        config_hash=config_hash(values),
        config_schema_id=CONFIG_SCHEMA_V21_ID,
        config_schema_version=CONFIG_SCHEMA_V21_VERSION,
        config_schema_hash=CONFIG_SCHEMA_V21_HASH,
        actual_yyyyww=(start - timedelta(weeks=1)).strftime("%G%V"),
        actual_close_window_start_yyyyww=(start - timedelta(weeks=52)).strftime("%G%V"),
        actual_close_history_hash=digest(histories),
        source_manifest_sha256=digest(histories),
        source_relation="SYNTHETIC_FIXTURE.CLOSED_DEMAND_NOT_DATABASE",
        metrics=tuple(metrics),
    )
    return ExperimentCase(case_id, split, planning, replay, classification_inputs)


def experiment_cases():
    return [
        case(date(2023, 1, 2), "TRAIN", 11, "5"),
        case(date(2023, 4, 3), "TRAIN", 23, "50"),
        case(date(2023, 7, 3), "TRAIN", 37, "100"),
        case(date(2023, 10, 2), "VALIDATION", 41, "20"),
        case(date(2024, 1, 1), "HOLDOUT", 53, "10", edges=True),
        case(date(2024, 4, 1), "HOLDOUT", 67, "80", edges=True),
    ]

"""Explicit synthetic development experiments, never operating inventory or cost evidence."""

import copy
import random
from datetime import date, timedelta

from fixtures import build_request
from math_fixtures import build_math, GOLDEN_MATH, reseal_math
from strategy_fixtures import request as recommendation_request
from tests.support.policy_search_fixtures import bundle, GOLDEN
from tests.unit.test_policy_search import execute


def path(seed, weeks=108):
    rng = random.Random(seed)
    return [str(rng.randint(4, 16) + (12 if i % 19 == 7 else 0)) for i in range(weeks)]


def source(weeks=13, seed=7, *, values=None, boh="30", lead=7, order_stride=1):
    raw = build_math({**GOLDEN_MATH["cases"][0], "source_type": "SYNTHETIC_BOH"})
    w0 = date(2026, 9, 28)
    codes = [(w0 + timedelta(weeks=i)).strftime("%G%V") for i in range(weeks)]
    canonical = build_request(
        {
            "id": "sensitivity-fixture",
            "source_type": "SYNTHETIC_BOH",
            "weeks": codes,
            "items": [
                {
                    "id": "ITEM-A",
                    "boh": boh,
                    "forecast": values or path(seed)[:weeks],
                    "orders": ["0"] * weeks,
                }
            ],
        }
    )
    canonical["context"]["plan_end_date"] = (w0 + timedelta(weeks=weeks, days=-1)).isoformat()
    canonical["snapshots"]["policies"]["rows"][0].update(
        effective_to="2099-01-01", lead_time_days=lead
    )
    rec = recommendation_request(canonical).to_dict()
    rec["execution"].update(
        contract_version="1.1.0", strategy=raw["recommendation"]["execution"]["strategy"]
    )
    rec["execution"]["item_controls"][0]["order_dates"] = rec["execution"]["item_controls"][0][
        "order_dates"
    ][::order_stride]
    raw["recommendation"] = rec
    raw["policy_input"]["context"]["canonical_input_hash"] = rec["execution"][
        "canonical_input_hash"
    ]
    search = copy.deepcopy(GOLDEN["search_config"])
    search["max_candidates_per_item"] = 4
    search["minimum_fill_rate"] = "0.9"
    search["group_profiles"][0]["emos_values"] = ["0.25", "1"]
    return execute(bundle(reseal_math(raw), search))


def experiment(cases=None, *, pairs=None):
    cost = copy.deepcopy(GOLDEN["search_config"]["cost_assumptions"])
    return {
        "contract_id": "inventory-policy-sensitivity-request",
        "contract_version": "1.0.0",
        "evaluation_id": "DEVELOPMENT-HORIZON-COST-V1",
        "source_type": "SYNTHETIC_PLANNING_INPUT",
        "reference_case_id": "validation-13",
        "reference_cost_profile_id": "base",
        "cases": cases
        or [
            {
                "case_id": "validation-13",
                "trace_id": "validation-7",
                "split": "VALIDATION",
                "seed": 7,
                "search_result": source(),
            }
        ],
        "cost_profiles": [
            {"profile_id": "base", "cost_assumptions": cost},
            {
                "profile_id": "holding-x5",
                "cost_assumptions": {**cost, "holding_cost_per_unit_week": "5"},
            },
            {"profile_id": "order-x20", "cost_assumptions": {**cost, "fixed_order_cost": "100"}},
            {
                "profile_id": "variable-x2",
                "cost_assumptions": {**cost, "variable_order_cost_per_unit": "4"},
            },
            {
                "profile_id": "backorder-x5",
                "cost_assumptions": {**cost, "backorder_cost_per_unit_week": "50"},
            },
            {"profile_id": "not-bound", "cost_assumptions": None},
        ],
        "terminal_pairs": pairs or [],
    }


def long_experiment():
    cases = [
        {
            "case_id": f"validation-{h}",
            "trace_id": "validation-7",
            "split": "VALIDATION",
            "seed": 7,
            "search_result": source(h),
        }
        for h in (13, 26, 52, 104, 108)
    ] + [
        {
            "case_id": f"holdout-{seed}-104",
            "trace_id": f"holdout-{seed}",
            "split": "HOLDOUT",
            "seed": seed,
            "search_result": source(104, seed),
        }
        for seed in (17, 29)
    ]
    return experiment(
        cases,
        pairs=[
            {"short_case_id": f"validation-{h}", "extended_case_id": "validation-108"}
            for h in (13, 26, 52, 104)
        ],
    )

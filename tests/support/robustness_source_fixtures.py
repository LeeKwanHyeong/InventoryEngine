"""Manual development inputs only. No official Seeds, periods or stochastic paths."""

from copy import deepcopy
from datetime import date, timedelta

from fixtures import build_request, reseal
from strategy_fixtures import request as recommendation_request, ContractProbe
from dsio_inventory_engine.evaluate_replenishment.robustness.development_source import (
    DevelopmentSourceProducer,
    GENERATOR_VERSION,
)
from dsio_inventory_engine.evaluate_replenishment.robustness.source_binding import (
    RealizedDemandPath,
)
from dsio_inventory_engine.inventory_contracts.effective_policy_v2 import (
    EFFECTIVE_POLICY_V2_FIELDS,
    effective_policy_content_hash,
)
from dsio_inventory_engine.inventory_contracts.replenishment import RecommendationRequest
from dsio_inventory_engine.inventory_contracts.values import digest
from tests.unit.test_runtime_effective_policy_v2 import _effective_policy, CONFIG_HASH

W0 = date(2055, 1, 4)


def history_request(*, lead=35, demand=1):
    return {
        "scope": {
            "company_cd": "DSE",
            "subs_cd": "C100",
            "site_cd": "V101",
            "item_id": "ITEM-A",
            "uom": "EA",
        },
        "w0": W0.isoformat(),
        "history_snapshot_id": "HAND-UNCENSORED-HISTORY",
        "history_revision": "HAND-R1",
        "master_revision": "MASTER-R1",
        "calendar_revision": "manual-causal:calendar",
        "lead_time_days": lead,
        "moq": "5",
        "order_multiple": "5",
        "known_future_customer_order_qty": "0",
        "history": [
            {
                "start_date": (W0 + timedelta(weeks=i - 104)).isoformat(),
                "gross_demand_qty": str(demand),
                "confirmed_customer_order_qty": "0",
            }
            for i in range(104)
        ],
    }


def source_case(document=None, *, action="REVIEW", horizon=65):
    raw = deepcopy(document or history_request())
    source = DevelopmentSourceProducer().produce(raw)
    future = source["planning_forecast"][:horizon]
    canonical = build_request(
        {
            "id": "manual-causal",
            "start_date": source["w0"],
            "weeks": [r["yyyyww"] for r in future],
            "source_type": "SYNTHETIC_BOH",
            "items": [
                {
                    "id": "ITEM-A",
                    "boh": source["w0_position"]["on_hand_qty"],
                    "backorder": source["w0_position"]["backorder_qty"],
                    "forecast": [r["gross_forecast_qty"] for r in future],
                    "orders": [r["confirmed_customer_order_qty"] for r in future],
                }
            ],
        }
    )
    ctx, snaps = canonical["context"], canonical["snapshots"]
    ctx["plan_end_date"] = (W0 + timedelta(weeks=horizon, days=-1)).isoformat()
    ctx["master_as_of_date"] = source["w0"]
    snaps["policies"]["rows"][0].update(
        **source["physical_constraints"], effective_to=(W0 + timedelta(weeks=horizon)).isoformat()
    )
    for kind in ("inventory", "prior_inventory", "receipts"):
        snaps[kind]["metadata"].update(
            generator_version=GENERATOR_VERSION, simulation_run_id="WU-" + source["content_hash"]
        )
    snaps["receipts"]["rows"] = [
        {
            **source["scope"],
            "receipt_id": r["receipt_id"],
            "due_date": r["due_date"],
            "due_qty": r["quantity"],
            "status": "CONFIRMED",
            "supply_type": "SYNTHETIC_PURCHASE_ORDER",
        }
        for r in source["w0_due_in"]
    ]
    canonical = reseal(canonical)
    recommendation = recommendation_request(canonical).to_dict()
    item_policy = _effective_policy(action=action, p90_days="49")
    projection = {"item_id": "ITEM-A", **{k: item_policy[k] for k in EFFECTIVE_POLICY_V2_FIELDS}}
    execution = recommendation["execution"]
    execution.update(
        contract_version="2.0.0",
        execution_mode="LOCAL_SHADOW",
        execution_purpose="SHADOW",
        model_approval=None,
        strategy_input_binding={
            "contract_id": "HAND-STRATEGY-INPUT",
            "contract_version": "1.0.0",
            "snapshot_id": "HAND-PRE-W0-ONLY",
            "content_hash": digest(source["uncensored_history"][-26:]),
        },
        effective_policy_binding={
            "contract_id": "inventory-effective-policy-run-binding-v2",
            "contract_version": "2.0.0",
            "classification_snapshot_id": "HAND-CLASSIFICATION",
            "classification_config_hash": CONFIG_HASH,
            "effective_policy_content_hash": effective_policy_content_hash([projection]),
        },
        effective_item_policies=[projection],
    )
    return raw, source, RecommendationRequest.from_dict(recommendation)


def path_for(request, *, values=None, key="HAND-TRUTH", seed=11):
    n = len(request.to_dict()["canonical_input"]["snapshots"]["calendar"]["rows"])
    values = ["1"] * n if values is None else values
    return RealizedDemandPath.from_dict(
        {
            "contract_id": "inventory-robustness-realized-demand",
            "contract_version": "1.0.0",
            "planning_request_hash": request.input_hash,
            "path_id": key,
            "split": "VALIDATION",
            "seed": seed,
            "rows": [{"seq": i, "realized_gross_demand_qty": v} for i, v in enumerate(values)],
        }
    )


def probe(request, *, action="ORDER_UP_TO", qty="10"):
    strategy = ContractProbe(action=action, quantity=qty)
    strategy.input_binding = request.to_dict()["execution"]["strategy_input_binding"]
    return strategy


def bindings(case, path):
    raw, source, _ = case
    return {
        "source": source,
        "source_request": raw,
        "expected_source_hash": source["content_hash"],
        "realization": path,
        "expected_realization_hash": path.content_hash,
    }


def mathematical_case(case):
    from math_fixtures import build_math, reseal_math
    from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest

    raw, source, recommendation = case
    data = build_math()
    rec = recommendation.to_dict()
    rec["execution"]["strategy"] = data["recommendation"]["execution"]["strategy"]
    data["recommendation"] = rec
    ctx = rec["canonical_input"]["context"]
    snap = data["policy_input"]
    snap["context"].update(
        **{k: ctx[k] for k in ("company_cd", "subs_cd", "site_cd", "configuration_revision")},
        canonical_input_hash=rec["execution"]["canonical_input_hash"],
        as_of_date=(W0 - timedelta(days=1)).isoformat(),
        available_at=ctx["inventory_cutoff_at"],
    )
    snap["profile"].update(lookback_weeks=26, stddev_ddof=0)
    snap["calendar"] = [
        {
            "yyyyww": (W0 + timedelta(weeks=i - 26)).strftime("%G%V"),
            "start_date": (W0 + timedelta(weeks=i - 26)).isoformat(),
            "end_date": (W0 + timedelta(weeks=i - 26, days=6)).isoformat(),
        }
        for i in range(26)
    ]
    snap["history"] = [
        {
            "item_id": "ITEM-A",
            "uom": "EA",
            "yyyyww": b["yyyyww"],
            "demand_qty": h["gross_demand_qty"],
        }
        for b, h in zip(snap["calendar"], source["uncensored_history"][-26:], strict=True)
    ]
    data = reseal_math(data)
    math_request = MathematicalPolicyRequest.from_dict(data)
    return (
        raw,
        source,
        RecommendationRequest.from_dict(math_request.to_dict()["recommendation"]),
    ), math_request

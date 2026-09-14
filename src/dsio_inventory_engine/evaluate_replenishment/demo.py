"""Explicit synthetic packaging, never a real Snapshot sealing/approval service."""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import cast

from dsio_inventory_engine.inventory_contracts.canonical import (
    CanonicalInputRequest,
    ROW_FIELDS,
    normalize_snapshot,
    snapshot_content,
)
from dsio_inventory_engine.inventory_contracts.evaluation import ProductionEvaluationRequest
from dsio_inventory_engine.inventory_contracts.mathematical import (
    MATH_DESCRIPTOR,
    normalize_policy_input,
    policy_input_binding,
    policy_input_content,
)
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.training import TrainingRequest, VERSION
from dsio_inventory_engine.inventory_contracts.values import (
    digest,
    identifier,
    quantity_text as q,
    require,
)
from dsio_inventory_engine.training.evaluation import ReferenceEpisode
from dsio_inventory_engine.training.reference import demand_series
from .admission import SERVICE_LEVEL, episode_range, frozen_mean


def build_evaluation_request(
    training: TrainingRequest,
    deployment: DeploymentScope,
    *,
    split: str = "TEST",
    scenario_id: str = "BASE",
    evaluation_id: str = "EVAL-MATH-DEMO",
) -> ProductionEvaluationRequest:
    require(deployment.environment == "DEVELOPMENT", "LOCAL_EVALUATION_ONLY")
    identifier(evaluation_id)
    training = TrainingRequest.from_dict(training.to_dict())
    data = training.to_dict()
    world = ReferenceEpisode(
        training, deployment, split, scenario_id, delay_scope="EPISODE_RECEIPTS_ONLY"
    )
    initial = world.observe()
    origin, end = episode_range(data, split)
    scenario = next(s for s in data["scenarios"] if s["scenario_id"] == scenario_id)
    # Bounded opaque IDs do not encode future actual demand or future delays.
    prefix = "EVAL-" + digest(evaluation_id)[:24]
    start = date.fromisoformat(data["calendar"][origin]["start_date"])
    finish = date.fromisoformat(data["calendar"][end - 1]["end_date"])
    midnight = datetime.combine(start, time.min, timezone.utc)
    cutoff = (midnight - timedelta(seconds=1)).isoformat()
    scope = {k: data["context"][k] for k in ("company_cd", "subs_cd", "site_cd")}
    context = {
        **data["context"],
        "engine_run_id": prefix + ":RUN",
        "plan_id": prefix + ":PLAN",
        "plan_yyyyww": data["calendar"][origin]["yyyyww"],
        "plan_start_date": start.isoformat(),
        "plan_end_date": finish.isoformat(),
        "master_as_of_date": start.isoformat(),
        "master_snapshot_revision": prefix + ":MASTER",
        "demand_run_id": prefix + ":DEMAND",
        "business_timezone": "UTC",
        "inventory_cutoff_at": cutoff,
        "inventory_source_watermark": prefix + ":WATERMARK",
    }
    snaps: dict[str, dict] = {
        kind: {
            "snapshot_id": prefix + ":" + kind,
            "status": "SEALED",
            "content_hash": "0" * 64,
            "row_count": 0,
            "metadata": dict(scope),
            "rows": [],
        }
        for kind in ROW_FIELDS
    }
    lineage = {"simulation_run_id": data["simulation_run_id"], "generator_version": VERSION}
    snaps["master"]["metadata"].update(master_snapshot_revision=context["master_snapshot_revision"])
    snaps["forecast"]["metadata"].update(
        demand_run_id=context["demand_run_id"],
        demand_run_status="SUCCEEDED",
        evidence_status="VERIFIED",
        calendar_snapshot_id=snaps["calendar"]["snapshot_id"],
        calendar_content_hash="0" * 64,
        master_snapshot_revision=context["master_snapshot_revision"],
        master_content_hash="0" * 64,
    )
    snaps["customer_orders"]["metadata"].update(coverage="COMPLETE")
    snaps["receipts"]["metadata"].update(coverage="COMPLETE", **lineage)
    snaps["prior_inventory"]["metadata"].update(
        as_of_date=(start - timedelta(days=1)).isoformat(),
        position_source_type="SYNTHETIC_BOH",
        **lineage,
    )
    snaps["inventory"]["metadata"].update(
        position_source_type="SYNTHETIC_BOH",
        business_timezone="UTC",
        cutoff_at=cutoff,
        source_watermark=context["inventory_source_watermark"],
        complete_through=midnight.isoformat(),
        extracted_at=midnight.isoformat(),
        sealed_at=midnight.isoformat(),
        prior_snapshot_id=snaps["prior_inventory"]["snapshot_id"],
        prior_content_hash="0" * 64,
        reserved_treatment="UNAVAILABLE_EXCLUDED",
        adjustments=[],
        source_events=[],
        **lineage,
    )
    for seq, row in enumerate(data["calendar"][origin:end]):
        snaps["calendar"]["rows"].append(
            {
                **row,
                "seq": seq,
                "base_month": (date.fromisoformat(row["start_date"]) + timedelta(days=2)).strftime(
                    "%Y%m"
                ),
            }
        )
    states = {r["item_id"]: r for r in initial["items"]}
    rules: dict[str, dict] = {}
    for item in data["items"]:
        key, uom = item["item_id"], item["uom"]
        step = Decimal(item["quantity_step"])
        scale = -cast(int, step.normalize().as_tuple().exponent)
        require(
            0 <= scale <= 6 and step == Decimal(1).scaleb(-scale), "EVALUATION_UOM_STEP_UNSUPPORTED"
        )
        require(uom not in rules or rules[uom]["scale"] == scale, "EVALUATION_UOM_STEP_MISMATCH")
        rules[uom] = {
            "uom": uom,
            "scale": scale,
            "tolerance_qty": "0",
            "approval_reference": "SYNTHETIC-FIXTURE-ONLY",
        }
        fields = {**scope, "item_id": key, "uom": uom}
        state = states[key]
        snaps["master"]["rows"].append({**fields, "active": True, "stock_managed": True})
        snaps["inventory"]["rows"].append(
            {
                **fields,
                "position_date": start.isoformat(),
                "on_hand_qty": state["boh_qty"],
                "reserved_qty": "0",
                "available_qty": state["boh_qty"],
                "backorder_qty": state["backorder_qty"],
            }
        )
        snaps["prior_inventory"]["rows"].append({**fields, "eoh_qty": state["boh_qty"]})
        snaps["policies"]["rows"].append(
            {
                **fields,
                "policy_id": "POL-" + digest(key)[:24],
                "effective_from": start.isoformat(),
                "effective_to": (finish + timedelta(days=1)).isoformat(),
                "lead_time_days": item["lead_time_weeks"] * 7,
                "moq": item["moq"],
                "order_multiple": item["lot_multiple"],
                "physical_max_capacity": item["physical_capacity_qty"],
                "approved_service_level": SERVICE_LEVEL[
                    scenario["service_level_code"] or item["service_level_code"]
                ],
                "source_target_inventory_qty": item["legacy_target_inventory_qty"],
                "source_rop_qty": None,
                "policy_source_type": "SYNTHETIC_POLICY",
                "source_semantics_cd": "SYNTHETIC_PROFILE",
            }
        )
        mean = frozen_mean(data, item, origin, data["lookback_weeks"])
        for bucket in data["calendar"][origin:end]:
            snaps["forecast"]["rows"].append(
                {
                    **fields,
                    "yyyyww": bucket["yyyyww"],
                    "forecast_qty": mean,
                    "forecast_netting_mode": "SAME_BUCKET_CONSUMPTION",
                    "upstream_gross_forecast_qty": None,
                    "upstream_consumed_qty": None,
                }
            )
        for order in state["pending_supply"]:
            require(
                origin <= order["planned_due_index"] < end,
                "EVALUATION_INITIAL_SUPPLY_OUTSIDE_HORIZON",
            )
            snaps["receipts"]["rows"].append(
                {
                    **fields,
                    "receipt_id": order["order_id"],
                    "due_date": order["due_date"],
                    "due_qty": order["quantity"],
                    "status": "CONFIRMED",
                    "supply_type": "SYNTHETIC_PURCHASE_ORDER",
                }
            )
    canonical = {
        "contract_id": "io-canonical-input-v1",
        "contract_version": "1.0.0",
        "context": context,
        "quantity_rules": list(rules.values()),
        "input_bindings": {},
        "snapshots": snaps,
    }
    seal_fixture(canonical)
    source = CanonicalInputRequest.from_dict(canonical)
    lookback = data["lookback_weeks"]
    history_calendar = data["calendar"][origin - lookback : origin]
    policy = {
        "contract_id": "io-mathematical-policy-input-v1",
        "contract_version": "1.0.0",
        "snapshot_id": prefix + ":MATH",
        "content_hash": "0" * 64,
        "status": "SEALED",
        "history_row_count": 0,
        "context": {
            **scope,
            "canonical_input_hash": source.input_hash,
            "configuration_revision": context["configuration_revision"],
            "as_of_date": (start - timedelta(days=1)).isoformat(),
            "available_at": cutoff,
            "history_source_type": "SYNTHETIC_DEMAND",
            "quantity_semantics": "UNCENSORED_DEMAND",
        },
        "profile": {
            "profile_id": "EVALUATION-NORMAL",
            "revision": "1",
            "formula": "HISTORICAL_NORMAL_R_S_V1",
            "history_mode": "FROZEN_PRE_W0",
            "lookback_weeks": lookback,
            "stddev_ddof": 1,
            "replenishment_cycle_weeks": data["replenishment_cycle_weeks"],
            "service_level_metric": "CYCLE_SERVICE_LEVEL",
            "lead_time_mapping": "CEIL_CALENDAR_WEEKS",
            "allow_legacy_fallback": False,
            "approval_reference": "SYNTHETIC-FIXTURE-NOT-OPERATIONAL-APPROVAL",
        },
        "calendar": history_calendar,
        "history": [
            {
                "item_id": item["item_id"],
                "uom": item["uom"],
                "yyyyww": row["yyyyww"],
                "demand_qty": q(cast(Decimal, qty)),
            }
            for item in data["items"]
            for row, qty in zip(
                history_calendar, demand_series(data, item)[origin - lookback : origin], strict=True
            )
        ],
        "adjustments": [],
    }
    policy = normalize_policy_input(policy)
    policy["history_row_count"] = len(policy["history"])
    policy["content_hash"] = digest(policy_input_content(policy))
    execution = {
        "contract_id": "io-replenishment-v1",
        "contract_version": "1.1.0",
        "psi_scenario_type": "RECOMMENDED",
        "execution_mode": "LOCAL_SHADOW",
        "configuration_revision": context["configuration_revision"],
        "canonical_input_hash": source.input_hash,
        "strategy": dict(MATH_DESCRIPTOR),
        "strategy_input_binding": policy_input_binding(policy),
        "approval_reference": "SYNTHETIC-FIXTURE-NOT-OPERATIONAL-APPROVAL",
        "allowed_action_types": ["HOLD", "ORDER_UP_TO"],
        "decision_timing": "BUCKET_START_BEFORE_RECEIPTS",
        "receipt_mapping": "NEXT_BUCKET_START_ON_OR_AFTER_DUE_DATE",
        "capacity_mode": "CONSERVATIVE_NO_DEMAND_CREDIT",
        "item_controls": [
            {
                "item_id": i["item_id"],
                "uom": i["uom"],
                "max_order_qty": "1000000000000",
                "min_target_qty": "0",
                "max_target_qty": "1000000000000",
                "order_dates": [r["start_date"] for r in data["calendar"][origin:end]],
            }
            for i in data["items"]
        ],
    }
    return ProductionEvaluationRequest.from_dict(
        {
            "contract_id": "io-production-evaluation-v1",
            "contract_version": "1.0.0",
            "evaluation_id": evaluation_id,
            "split": split,
            "scenario_id": scenario_id,
            "delay_scope": "EPISODE_RECEIPTS_ONLY",
            "forecast_mode": "SYNTHETIC_FROZEN_MEAN",
            "training_request": data,
            "mathematical_request": {
                "recommendation": {"canonical_input": source.to_dict(), "execution": execution},
                "policy_input": policy,
            },
        }
    )


def seal_fixture(canonical: dict) -> None:
    """Hash packaging for local synthetic examples ONLY; authenticates nothing."""
    snaps = canonical["snapshots"]
    for kind in (
        "calendar",
        "master",
        "prior_inventory",
        "forecast",
        "customer_orders",
        "receipts",
        "policies",
        "inventory",
    ):
        if kind == "forecast":
            snaps[kind]["metadata"].update(
                calendar_content_hash=snaps["calendar"]["content_hash"],
                master_content_hash=snaps["master"]["content_hash"],
            )
        if kind == "inventory":
            snaps[kind]["metadata"]["prior_content_hash"] = snaps["prior_inventory"]["content_hash"]
        snaps[kind] = normalize_snapshot(kind, snaps[kind])
        snaps[kind]["row_count"] = len(snaps[kind]["rows"])
        snaps[kind]["content_hash"] = digest(snapshot_content(kind, snaps[kind]))
        canonical["input_bindings"][kind] = {
            k: snaps[kind][k] for k in ("snapshot_id", "content_hash")
        }

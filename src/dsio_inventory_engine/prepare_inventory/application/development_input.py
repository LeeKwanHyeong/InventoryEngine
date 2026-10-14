"""Deterministic development inputs from a sealed Forecast and pre-W0 history.

The 52-week BOH simulator is independent of the production PSI/strategy. These
positions, policies and empty customer-order coverage are explicitly synthetic.
"""

from datetime import date, timedelta
from decimal import Context, Decimal, ROUND_CEILING, localcontext
from statistics import NormalDist

from dsio_inventory_engine.inventory_contracts.canonical import (
    snapshot_content,
    normalize_snapshot,
    V2_ID,
    V21_VERSION,
)
from dsio_inventory_engine.inventory_contracts.development_universe import seal_development_universe
from dsio_inventory_engine.inventory_contracts.values import (
    canonical_json,
    digest,
    quantity_text,
    require,
)
from .forecast_handoff import _canonical_shared_snapshot


def snapshot(kind, identity, metadata, rows):
    body = {
        "snapshot_id": identity,
        "status": "SEALED",
        "metadata": metadata,
        "rows": sorted(rows, key=canonical_json),
        "row_count": len(rows),
    }
    body["content_hash"] = "0" * 64
    body = normalize_snapshot(kind, body, row_limit=200_000)
    body["content_hash"] = digest(snapshot_content(kind, body))
    return body


def synthetic_warmup(values, *, lead_weeks=2, review_weeks=2, service_level="0.95"):
    # Freeze the generator's original 28-digit arithmetic; a caller's Decimal
    # context must not silently change a supposedly reproducible BOH snapshot.
    with localcontext(Context(prec=28)):
        return _synthetic_warmup(
            values, lead_weeks=lead_weeks, review_weeks=review_weeks, service_level=service_level
        )


def _synthetic_warmup(values, *, lead_weeks, review_weeks, service_level):
    require(len(values) >= 52, "SYNTHETIC_BOH_52_WEEKS_REQUIRED")
    require(
        type(lead_weeks) is int
        and lead_weeks > 0
        and type(review_weeks) is int
        and review_weeks > 0
        and Decimal(0) < Decimal(service_level) < Decimal(1),
        "SYNTHETIC_POLICY_PARAMETERS",
    )
    on_hand = backorder = Decimal(0)
    pipeline, trace = [], []
    for week, value in enumerate(values):
        raw_demand = Decimal(value)
        require(raw_demand.is_finite() and raw_demand >= 0, "SYNTHETIC_DEMAND_NEGATIVE")
        demand = raw_demand.to_integral_value(rounding=ROUND_CEILING)
        receipt = sum((qty for due, qty in pipeline if due == week), Decimal(0))
        pipeline = [(due, qty) for due, qty in pipeline if due > week]
        available = on_hand + receipt
        sales = min(available, demand + backorder)
        backorder = demand + backorder - sales
        on_hand = available - sales
        observed = [Decimal(v) for v in values[max(0, week - 12) : week + 1]]
        avg = sum(observed) / len(observed)
        variance = sum((v - avg) ** 2 for v in observed) / len(observed)
        safety = (
            Decimal(str(NormalDist().inv_cdf(float(service_level))))
            * variance.sqrt()
            * Decimal(lead_weeks).sqrt()
        )
        rop = avg * lead_weeks + safety
        target = rop + avg * review_weeks
        position = on_hand + sum((qty for _, qty in pipeline), Decimal(0)) - backorder
        order = (
            max(Decimal(0), target - position).to_integral_value(rounding=ROUND_CEILING)
            if position <= rop
            else Decimal(0)
        )
        if order:
            pipeline.append((week + lead_weeks, order))
        trace.append(
            {
                "week_index": week,
                "demand_qty": quantity_text(demand),
                "receipt_qty": quantity_text(receipt),
                "sales_qty": quantity_text(sales),
                "eoh_qty": quantity_text(on_hand),
                "backorder_qty": quantity_text(backorder),
                "order_qty": quantity_text(order),
            }
        )
    return on_hand, backorder, pipeline, trace


def build_development_template(
    *,
    shared,
    selector,
    forecast_receipt,
    history_by_item,
    history_weeks,
    demand_window_rows,
    demand_window_file_hash,
    config_revision_id,
    engine_run_id,
    cycle_site_execution_id,
    approval_reference,
    captured_at,
):
    require(
        len(history_weeks) == 52 and history_weeks == sorted(set(history_weeks)),
        "SYNTHETIC_HISTORY_CALENDAR",
    )
    history_dates = [date.fromisocalendar(int(w[:4]), int(w[4:]), 1) for w in history_weeks]
    w0 = date.fromisocalendar(
        int(selector["fcst_w0_yyyyww"][:4]), int(selector["fcst_w0_yyyyww"][4:]), 1
    )
    require(
        all(b - a == timedelta(weeks=1) for a, b in zip(history_dates, history_dates[1:]))
        and history_dates[-1] + timedelta(weeks=1) == w0,
        "SYNTHETIC_HISTORY_CALENDAR",
    )
    require(max(history_weeks) < selector["fcst_w0_yyyyww"], "FUTURE_HISTORY_FORBIDDEN")
    included = sorted(history_by_item)
    master_ids = {
        r["item_id"] for r in shared["master"]["rows"] if r["active"] and r["stock_managed"]
    }
    windows = {r["item_id"]: r for r in demand_window_rows}
    require(len(windows) == len(demand_window_rows), "DEVELOPMENT_DEMAND_WINDOW_DUPLICATE")
    require(
        set(windows) == master_ids and set(included) <= master_ids,
        "DEVELOPMENT_SOURCE_MASTER_MISMATCH",
    )
    universe = seal_development_universe(
        {
            "contract_id": "io-development-calculation-universe-v1",
            "environment_scope": "DEVELOPMENT",
            "demand_run_id": selector["demand_run_id"],
            "plan_yyyyww": selector["fcst_w0_yyyyww"],
            "original_master_content_hash": shared["master"]["content_hash"],
            "original_forecast_content_hash": forecast_receipt["forecast_content_hash"],
            "source_demand_window_file_sha256": demand_window_file_hash,
            "approval_reference": approval_reference,
            "included_item_ids": included,
            "exclusions": [
                {
                    "item_id": item,
                    "reason": "EXCLUDED_ENDED_DEMAND_WINDOW",
                    "demand_end_yyyyww": str(windows[item]["demand_end_yyyyww"]),
                }
                for item in sorted(master_ids - set(included))
            ],
        }
    )
    scope = {key: selector[key] for key in ("company_cd", "subs_cd", "site_cd")}
    calendar = _canonical_shared_snapshot(
        "calendar", shared["calendar"], plant_cd=selector["plant_cd"]
    )
    master = _canonical_shared_snapshot("master", shared["master"], plant_cd=selector["plant_cd"])
    start = date.fromisoformat(min(calendar["rows"], key=lambda r: r["seq"])["start_date"])
    end = date.fromisoformat(max(calendar["rows"], key=lambda r: r["seq"])["end_date"])
    simulation_id = "SIM-" + digest(
        {
            "history": history_by_item,
            "weeks": history_weeks,
            "universe": universe["content_hash"],
            "generator_version": "52w-independent-r-s-v1",
        }
    )
    cutoff = (start - timedelta(days=1)).isoformat() + "T23:59:59+09:00"
    captured = captured_at
    watermark = simulation_id
    prior_rows, position_rows, policy_rows, receipt_rows, trace_hashes = [], [], [], [], []
    for item in included:
        values = history_by_item[item]
        require(len(values) == len(history_weeks), "INCOMPLETE_SYNTHETIC_HISTORY")
        on_hand, bo, pipeline, trace = synthetic_warmup(values)
        unit = {**scope, "item_id": item, "uom": "EA"}
        prior_rows.append({**unit, "eoh_qty": quantity_text(on_hand)})
        position_rows.append(
            {
                **unit,
                "position_date": start.isoformat(),
                "on_hand_qty": quantity_text(on_hand),
                "reserved_qty": "0",
                "available_qty": quantity_text(on_hand),
                "backorder_qty": quantity_text(bo),
            }
        )
        policy_rows.append(
            {
                **unit,
                "policy_id": "SYN-POLICY-" + item,
                "effective_from": start.isoformat(),
                "effective_to": end.isoformat(),
                "lead_time_days": 14,
                "moq": "1",
                "order_multiple": "1",
                "physical_max_capacity": None,
                "approved_service_level": "0.95",
                "source_target_inventory_qty": None,
                "source_rop_qty": None,
                "policy_source_type": "SYNTHETIC_POLICY",
                "source_semantics_cd": "DEVELOPMENT_APPROVED_ASSUMPTIONS_V1",
            }
        )
        for seq, (due, qty) in enumerate(pipeline):
            receipt_rows.append(
                {
                    **unit,
                    "receipt_id": "SYN-PO-" + item + "-" + str(seq),
                    "due_date": (start + timedelta(weeks=due - len(values))).isoformat(),
                    "due_qty": quantity_text(qty),
                    "status": "CONFIRMED",
                    "supply_type": "SYNTHETIC_PURCHASE_ORDER",
                }
            )
        trace_hashes.append({"item_id": item, "content_hash": digest(trace)})
    suffix = simulation_id[4:20]
    prior = snapshot(
        "prior_inventory",
        "DEV-PRIOR-" + suffix,
        {
            **scope,
            "as_of_date": (start - timedelta(days=1)).isoformat(),
            "position_source_type": "SYNTHETIC_BOH",
            "simulation_run_id": simulation_id,
            "generator_version": "52w-independent-r-s-v1",
        },
        prior_rows,
    )
    inventory = snapshot(
        "inventory",
        "DEV-BOH-" + suffix,
        {
            **scope,
            "position_source_type": "SYNTHETIC_BOH",
            "business_timezone": "Asia/Seoul",
            "cutoff_at": cutoff,
            "source_watermark": watermark,
            "complete_through": cutoff,
            "extracted_at": captured,
            "sealed_at": captured,
            "prior_snapshot_id": prior["snapshot_id"],
            "prior_content_hash": prior["content_hash"],
            "reserved_treatment": "UNAVAILABLE_EXCLUDED",
            "simulation_run_id": simulation_id,
            "generator_version": "52w-independent-r-s-v1",
            "adjustments": [],
            "source_events": [],
        },
        position_rows,
    )
    policies = snapshot("policies", "DEV-POLICY-" + suffix, scope, policy_rows)
    receipts = snapshot(
        "receipts",
        "DEV-RECEIPTS-" + suffix,
        {
            **scope,
            "coverage": "COMPLETE",
            "simulation_run_id": simulation_id,
            "generator_version": "52w-independent-r-s-v1",
        },
        receipt_rows,
    )
    orders = snapshot(
        "customer_orders", "DEV-ORDERS-" + suffix, {**scope, "coverage": "COMPLETE"}, []
    )
    forecast = snapshot(
        "forecast",
        forecast_receipt["forecast_snapshot_id"],
        {
            **scope,
            "demand_run_id": selector["demand_run_id"],
            "demand_run_status": "SUCCEEDED",
            "evidence_status": "VERIFIED",
            "calendar_snapshot_id": calendar["snapshot_id"],
            "calendar_content_hash": calendar["content_hash"],
            "master_snapshot_revision": shared["master_snapshot_revision"],
            "master_content_hash": master["content_hash"],
        },
        [],
    )
    datasets = {
        "calendar": calendar,
        "master": master,
        "forecast": forecast,
        "prior_inventory": prior,
        "inventory": inventory,
        "policies": policies,
        "receipts": receipts,
        "customer_orders": orders,
    }
    return {
        "contract_id": V2_ID,
        "contract_version": V21_VERSION,
        "context": {
            **scope,
            "engine_run_id": engine_run_id,
            "planning_cycle_id": shared["planning_cycle_id"],
            "planning_cycle_revision_id": shared["planning_cycle_revision_id"],
            "cycle_site_execution_id": cycle_site_execution_id,
            "plan_id": selector["plan_id"],
            "plan_type": "TGSM",
            "plan_yyyyww": selector["fcst_w0_yyyyww"],
            "plan_start_date": start.isoformat(),
            "plan_end_date": end.isoformat(),
            "master_as_of_date": shared["master_as_of_date"],
            "master_snapshot_revision": shared["master_snapshot_revision"],
            "demand_run_id": selector["demand_run_id"],
            "configuration_revision": config_revision_id,
            "business_timezone": "Asia/Seoul",
            "inventory_cutoff_at": cutoff,
            "inventory_source_watermark": watermark,
        },
        "quantity_rules": [
            {
                "uom": "EA",
                "planning_scale": 6,
                "physical_scale": 0,
                "tolerance_qty": "0",
                "approval_reference": approval_reference,
            }
        ],
        "snapshots": datasets,
        "input_bindings": {
            k: {f: s[f] for f in ("snapshot_id", "content_hash")} for k, s in datasets.items()
        },
        "development_universe": universe,
        "upstream_bindings": {
            "calendar": {f: shared["calendar"][f] for f in ("snapshot_id", "content_hash")},
            "master": {f: shared["master"][f] for f in ("snapshot_id", "content_hash")},
            "forecast": {
                "snapshot_id": forecast_receipt["forecast_snapshot_id"],
                "content_hash": forecast_receipt["forecast_content_hash"],
            },
        },
    }, {
        "simulation_run_id": simulation_id,
        "generator_version": "52w-independent-r-s-v1",
        "trace_content_hash": digest(trace_hashes),
        "history_end_yyyyww": max(history_weeks),
        "warmup_weeks": 52,
        "actual_inventory_accuracy_verified": False,
        "customer_order_coverage": "DEVELOPMENT_EXPLICIT_EMPTY_FIXTURE",
        "random_used": False,
    }

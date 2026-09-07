"""Test-only packaging and hashes. All numerical expectations live in literal Goldens."""

import copy
import json
from datetime import date, timedelta
from pathlib import Path

from fixtures import build_request, sha
from strategy_fixtures import request

GOLDEN_MATH = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures/golden_mathematical.json").read_text()
)


def reseal_math(data):
    data = copy.deepcopy(data)
    snapshot = data["policy_input"]
    for name in ("calendar", "history", "adjustments"):
        snapshot[name].sort(key=lambda r: json.dumps(r, sort_keys=True, separators=(",", ":")))
    snapshot["history_row_count"] = len(snapshot["history"])
    snapshot["content_hash"] = sha(
        {
            k: v
            for k, v in snapshot.items()
            if k not in ("content_hash", "status", "history_row_count")
        }
    )
    data["recommendation"]["execution"]["strategy_input_binding"] = {
        k: snapshot[k] for k in ("contract_id", "contract_version", "snapshot_id", "content_hash")
    }
    return data


def build_math(case=None):
    case = case or GOLDEN_MATH["cases"][1]
    source = build_request(
        {
            "id": "math-" + case["id"],
            "plan_type": case.get("plan_type", "POSM"),
            "source_type": case.get("source_type", "ACTUAL_BOH"),
            "items": [
                {
                    "id": "ITEM-A",
                    "boh": case.get("boh", "10"),
                    "uom": case.get("uom", "EA"),
                    "forecast": case.get("forecast", ["10", "10", "10"]),
                }
            ],
        }
    )
    rec = request(source, **case.get("policy", {})).to_dict()
    ctx = rec["canonical_input"]["context"]
    rec["execution"]["contract_version"] = "1.1.0"
    rec["execution"]["strategy"] = {
        "strategy_type": "MATHEMATICAL",
        "implementation_id": "historical-normal-r-s",
        "version": "1.0.0",
        "model": None,
    }
    history = GOLDEN_MATH["histories"][case["history"]]
    lookback = case.get("profile", {}).get("lookback_weeks", 13)
    if lookback == 26:
        history = history * 2
    w0 = date.fromisoformat(ctx["plan_start_date"])
    calendar = [
        {
            "yyyyww": f"2026{40 - lookback + i:02d}",
            "start_date": (w0 - timedelta(weeks=lookback - i)).isoformat(),
            "end_date": (w0 - timedelta(weeks=lookback - i) + timedelta(days=6)).isoformat(),
        }
        for i in range(lookback)
    ]
    snap = {
        "contract_id": "io-mathematical-policy-input-v1",
        "contract_version": "1.0.0",
        "snapshot_id": "MATH-INPUT-" + case["id"],
        "content_hash": "0" * 64,
        "status": "SEALED",
        "history_row_count": len(history),
        "context": {
            **{k: ctx[k] for k in ("company_cd", "subs_cd", "site_cd", "configuration_revision")},
            "canonical_input_hash": rec["execution"]["canonical_input_hash"],
            "as_of_date": (w0 - timedelta(days=1)).isoformat(),
            "available_at": ctx["inventory_cutoff_at"],
            "history_source_type": "SYNTHETIC_DEMAND",
            "quantity_semantics": "UNCENSORED_DEMAND",
        },
        "profile": {
            "profile_id": "GOLDEN-NORMAL",
            "revision": "1",
            "formula": "HISTORICAL_NORMAL_R_S_V1",
            "history_mode": "FROZEN_PRE_W0",
            "lookback_weeks": lookback,
            "stddev_ddof": 1,
            "replenishment_cycle_weeks": 1,
            "service_level_metric": "CYCLE_SERVICE_LEVEL",
            "lead_time_mapping": "CEIL_CALENDAR_WEEKS",
            "allow_legacy_fallback": False,
            "approval_reference": "GOLDEN-NOT-OPERATIONAL-APPROVAL",
            **case.get("profile", {}),
        },
        "calendar": calendar,
        "history": [
            {
                "item_id": "ITEM-A",
                "uom": case.get("uom", "EA"),
                "yyyyww": row["yyyyww"],
                "demand_qty": q,
            }
            for row, q in zip(calendar, history, strict=True)
        ],
        "adjustments": [],
    }
    return reseal_math({"recommendation": rec, "policy_input": snap})


def adjustment(kind="APPROVED_OVERRIDE", values=("6", "20", "40"), **changes):
    return {
        "adjustment_id": "ADJ-" + kind,
        "item_id": "ITEM-A",
        "uom": "EA",
        "kind": kind,
        "effective_from": "2026-09-28",
        "effective_to": "2026-10-19",
        "values": values
        if isinstance(values, dict)
        else dict(
            zip(("safety_stock_qty", "rop_qty", "target_inventory_qty"), values, strict=True)
        ),
        "reason": "Hand-reviewed test policy",
        "approved_by": "TEST-APPROVER",
        "approved_at": "2026-09-27T00:00:00Z",
        "approval_reference": "TEST-ONLY",
        "source_document_id": "TEST-POLICY-RECORD",
        **changes,
    }

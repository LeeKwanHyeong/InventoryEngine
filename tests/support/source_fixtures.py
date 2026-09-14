"""Source envelope packaging for tests only; does not grant operational approval."""

import copy

from fixtures import build_request, GOLDEN
from dsio_inventory_engine.inventory_contracts.source import SourceSnapshot, source_hash
from dsio_inventory_engine.prepare_inventory.application.source_input import ORDER

RUN_ID = "91930a93-8601-4b71-9736-1d404f44c6ff"


def wrap(canonical=None):
    data = copy.deepcopy(canonical or build_request(GOLDEN["cases"][0]))
    data["context"]["demand_run_id"] = RUN_ID
    data["snapshots"]["forecast"]["metadata"]["demand_run_id"] = RUN_ID
    sources = {}
    for kind, snapshot in data["snapshots"].items():
        synthetic = snapshot["metadata"].get("position_source_type") == "SYNTHETIC_BOH"
        sources[kind] = {
            "contract_id": "io-source-snapshot-v1",
            "snapshot_type": kind,
            "snapshot_id": snapshot["snapshot_id"],
            "content_hash": "0" * 64,
            "adapter": "CANONICAL_V1",
            "origin_type": "SYNTHETIC_SOURCE" if synthetic else "DEVELOPMENT_FIXTURE",
            "source_reference": "TEST-ONLY",
            "source_as_of_date": snapshot["metadata"]["as_of_date"]
            if kind == "prior_inventory"
            else data["context"]["master_as_of_date"],
            "status": "SEALED",
            "row_count": len(snapshot["rows"]),
            "metadata": snapshot["metadata"],
            "semantics": {},
            "rows": snapshot["rows"],
        }
    request = {
        "contract_id": "io-source-input-v1",
        "contract_version": "1.0.0",
        "context": data["context"],
        "quantity_rules": data["quantity_rules"],
        "source_bindings": {},
    }
    return seal(request, sources), sources


def seal(request, sources):
    for kind in ORDER:
        s = sources[kind]
        if kind == "forecast":
            s["metadata"].update(
                calendar_content_hash=sources["calendar"]["content_hash"],
                master_content_hash=sources["master"]["content_hash"],
            )
        if kind == "inventory":
            s["metadata"]["prior_content_hash"] = sources["prior_inventory"]["content_hash"]
        s["row_count"] = len(s["rows"])
        s["content_hash"] = source_hash(s)
        request["source_bindings"][kind] = {k: s[k] for k in ("snapshot_id", "content_hash")}
    return request


def mapped():
    req, sources = wrap()
    c = req["context"]
    selector = {k: c[k] for k in ("company_cd", "subs_cd", "site_cd", "demand_run_id")}
    selector.update(
        tenant_id="default",
        project_id="TEST-PROJECT",
        plant_cd=c["site_cd"],
        plan_id=c["plan_id"],
        plan_yyyyww="202639",
        fcst_w0_yyyyww=c["plan_yyyyww"],
        target_cd="DMD",
        bukt_cd="W",
        snrio_id="BASE",
        snrio_grp="BASE",
        regul_type="NORMAL",
        forecast_stat_cd="MEAN",
        forecast_from_yyyyww="202640",
        forecast_to_yyyyww="202642",
    )
    s = sources["forecast"]
    s.update(
        adapter="DSDM_FORECAST_V1",
        semantics={
            "selector": selector,
            "netting_mode": "SAME_BUCKET_CONSUMPTION",
            "approval_reference": "TEST-NETTING",
        },
    )
    s["rows"] = [
        {
            **{
                k: v
                for k, v in selector.items()
                if k
                not in (
                    "demand_run_id",
                    "tenant_id",
                    "project_id",
                    "forecast_from_yyyyww",
                    "forecast_to_yyyyww",
                )
            },
            "result_id": str(i + 1),
            "model_run_id": "TEST-MODEL",
            "oper_part_no": r["item_id"],
            "fcst_yyyyww": r["yyyyww"],
            "fcst_qty": r["forecast_qty"],
            "source_profile_sha256": "1" * 64,
            "input_manifest_sha256": "2" * 64,
            "model_run_status": "SUCCEEDED",
        }
        for i, r in enumerate(s["rows"])
    ]
    s = sources["master"]
    s.update(
        adapter="DSDM_MASTER_V1",
        rows=[
            {
                **{k: r[k] for k in ("company_cd", "subs_cd", "site_cd")},
                "oper_part_no": r["item_id"],
                "use_flag": "Y",
                "stock_flag": "Y",
            }
            for r in s["rows"]
        ],
    )
    s = sources["inventory"]
    s.update(
        adapter="ERP_POSITION_V1",
        semantics={"boh_qty_basis": "ON_HAND", "approval_reference": "TEST-ERP-SEMANTICS"},
    )
    s["rows"] = [
        {
            **{k: v for k, v in r.items() if k not in ("item_id", "uom", "on_hand_qty")},
            "oper_part_no": r["item_id"],
            "boh_qty": r["on_hand_qty"],
        }
        for r in s["rows"]
    ]
    s = sources["policies"]
    s.update(
        adapter="SOURCE_POLICY_V1",
        semantics={
            "lead_time_unit": "WEEK",
            "service_level_unit": "PERCENT",
            "max_qty_meaning": "LEGACY_TARGET_INVENTORY",
            "approval_reference": "TEST-POLICY",
        },
    )
    s["rows"] = [
        {
            **{
                k: r[k]
                for k in (
                    "company_cd",
                    "subs_cd",
                    "site_cd",
                    "policy_id",
                    "effective_from",
                    "effective_to",
                    "physical_max_capacity",
                )
            },
            "oper_part_no": r["item_id"],
            "min_po_qty": r["moq"],
            "po_lot_qty": r["order_multiple"],
            "stock_lt": "1",
            "svc_lv": "95",
            "max_qty": r["source_target_inventory_qty"],
            "rop_qty": r["source_rop_qty"],
        }
        for r in s["rows"]
    ]
    return seal(req, sources), sources


class MemoryReader:
    def __init__(self, sources):
        self.sources = sources

    async def read_snapshot(self, kind, snapshot_id):
        return SourceSnapshot.from_dict(self.sources[kind])

"""Attach verified POINT rows to a v2 Canonical request; retain source provenance."""

from copy import deepcopy
from datetime import date, timedelta

from dsio_inventory_engine.inventory_contracts.canonical import (
    CanonicalInputRequest,
    V2_ID,
    V2_VERSION,
    snapshot_content,
)
from dsio_inventory_engine.inventory_contracts.values import canonical_json, digest, require
from .inventory_input import PrepareInventoryInputUseCase


def _canonical_shared_snapshot(kind: str, snapshot: dict, *, plant_cd: str) -> dict:
    """Project Demand's plant-aware shared scope into Inventory's site grain."""
    projected = deepcopy(snapshot)
    require(projected["metadata"].get("plant_cd") == plant_cd, "HANDOFF_PLANT_SCOPE_MISMATCH")
    del projected["metadata"]["plant_cd"]
    if kind == "master":
        for row in projected["rows"]:
            require(row.get("plant_cd") == plant_cd, "HANDOFF_PLANT_SCOPE_MISMATCH")
            del row["plant_cd"]
    projected["content_hash"] = digest(snapshot_content(kind, projected))
    return projected


class PrepareForecastHandoffUseCase:
    def __init__(self, deployment, reader):
        self.deployment, self.reader = deployment, reader

    def execute(
        self,
        template: dict,
        *,
        manifest_path,
        snapshot_id: str,
        content_hash: str,
        manifest_hash: str,
    ) -> dict:
        export = self.reader.read(
            manifest_path,
            snapshot_id=snapshot_id,
            content_hash=content_hash,
            manifest_hash=manifest_hash,
        )
        value = deepcopy(template)
        require(
            value["contract_id"] == V2_ID and value["contract_version"] == V2_VERSION,
            "CANONICAL_V2_REQUIRED",
        )
        request = export["manifest"]["request"]
        shared, context, snapshots = request["shared"], value["context"], value["snapshots"]
        for key in ("company_cd", "subs_cd", "site_cd", "demand_run_id"):
            require(context[key] == request["selector"][key], "HANDOFF_SCOPE_MISMATCH")
        for key in (
            "planning_cycle_id",
            "planning_cycle_revision_id",
            "master_as_of_date",
            "master_snapshot_revision",
        ):
            require(context[key] == shared[key], "HANDOFF_CYCLE_MISMATCH")
        require(
            context["plan_yyyyww"] == request["selector"]["fcst_w0_yyyyww"], "HANDOFF_W0_MISMATCH"
        )
        plant_cd = request["selector"]["plant_cd"]
        canonical_shared = {
            kind: _canonical_shared_snapshot(kind, shared[kind], plant_cd=plant_cd)
            for kind in ("calendar", "master")
        }
        for kind in ("calendar", "master"):
            require(
                snapshots[kind] == canonical_shared[kind],
                "HANDOFF_SHARED_SNAPSHOT_MISMATCH",
            )
        for rule in value["quantity_rules"]:
            require(rule["planning_scale"] == 6, "HANDOFF_PLANNING_PRECISION")
        for bucket in snapshots["calendar"]["rows"]:
            start = date.fromisoformat(bucket["start_date"])
            monday = start - timedelta(days=start.weekday())
            require(bucket["base_month"] == monday.strftime("%Y%m"), "HANDOFF_BASE_MONTH")
        scope = {k: context[k] for k in ("company_cd", "subs_cd", "site_cd")}
        mapped = {
            "snapshot_id": snapshot_id + ".canonical",
            "status": "SEALED",
            "row_count": len(export["rows"]),
            "metadata": {
                **scope,
                "demand_run_id": context["demand_run_id"],
                "demand_run_status": "SUCCEEDED",
                "evidence_status": "VERIFIED",
                "calendar_snapshot_id": snapshots["calendar"]["snapshot_id"],
                "calendar_content_hash": snapshots["calendar"]["content_hash"],
                "master_snapshot_revision": context["master_snapshot_revision"],
                "master_content_hash": snapshots["master"]["content_hash"],
            },
            "rows": sorted(
                [
                    {
                        **scope,
                        **{k: r[k] for k in ("item_id", "uom", "yyyyww", "forecast_qty")},
                        "forecast_netting_mode": "SAME_BUCKET_CONSUMPTION",
                        "upstream_gross_forecast_qty": None,
                        "upstream_consumed_qty": None,
                    }
                    for r in export["rows"]
                ],
                key=canonical_json,
            ),
        }
        mapped["content_hash"] = digest(snapshot_content("forecast", mapped))
        snapshots["forecast"] = mapped
        value["input_bindings"]["forecast"] = {
            k: mapped[k] for k in ("snapshot_id", "content_hash")
        }
        canonical = CanonicalInputRequest.from_dict(value)
        prepared = PrepareInventoryInputUseCase(self.deployment).execute(canonical)
        return {
            "canonical_input": canonical,
            "prepared": prepared,
            "handoff_evidence": {
                "source_snapshot_id": snapshot_id,
                "source_content_hash": content_hash,
                "manifest_hash": manifest_hash,
                "canonical_forecast_hash": mapped["content_hash"],
                "mapping_revision": "POINT_TO_SAME_BUCKET_V1",
                "source_plant_cd": plant_cd,
                "shared_source_bindings": {
                    kind: {
                        "snapshot_id": shared[kind]["snapshot_id"],
                        "content_hash": shared[kind]["content_hash"],
                        "canonical_content_hash": canonical_shared[kind]["content_hash"],
                    }
                    for kind in ("calendar", "master")
                },
                "source_rows": export["rows"],
                "database_published": False,
                "approval_authenticated": False,
            },
        }

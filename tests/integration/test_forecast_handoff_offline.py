"""Real Producer -> independent Consumer -> Canonical/PSI, only temporary artifacts.

Set DEMAND_ENGINE_ROOT explicitly. Inventory production modules never import Demand.
"""

import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from fixtures import build_request, reseal

from dsio_inventory_engine.infrastructure.parquet.forecast_handoff import (
    ParquetForecastHandoffReader,
)
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    canonical_json,
    digest,
)
from dsio_inventory_engine.prepare_inventory.application.forecast_handoff import (
    PrepareForecastHandoffUseCase,
)
from dsio_inventory_engine.simulate_inventory.application.baseline import RunPsiSimulationUseCase

DEMAND_ROOT = os.environ.get("DEMAND_ENGINE_ROOT")
if DEMAND_ROOT:
    sys.path.insert(0, str(Path(DEMAND_ROOT)))
    sys.path.insert(0, str(Path(DEMAND_ROOT) / "src"))
    from demand_contracts.forecasting.export import ForecastExportRequest, bind_shared_inputs
    from deliver_demand.application.export_forecast_snapshot import ExportForecastSnapshotUseCase
    from deliver_demand.adapters.parquet.forecast_snapshot import ParquetForecastSnapshotWriter

DEPLOYMENT = DeploymentScope("DSE", "DEVELOPMENT")


def fixture(items=1, weeks=3):
    start = date(2026, 6, 29)
    week_ids = [(start + timedelta(weeks=i)).strftime("%G%V") for i in range(weeks)]
    template = build_request(
        {
            "id": "handoff",
            "start_date": start.isoformat(),
            "weeks": week_ids,
            "items": [
                {
                    "id": f"ITEM-{i:05d}",
                    "boh": "2",
                    "forecast": ["0.4"] * weeks,
                    "orders": ["0"] * weeks,
                }
                for i in range(items)
            ],
        }
    )
    template["contract_id"], template["contract_version"] = "io-canonical-input-v2", "2.0.0"
    template["quantity_rules"] = [
        {
            "uom": "EA",
            "planning_scale": 6,
            "physical_scale": 0,
            "tolerance_qty": "0",
            "approval_reference": "APPROVED-V2",
        }
    ]
    end = (start + timedelta(weeks=weeks, days=-1)).isoformat()
    template["context"]["plan_end_date"] = end
    for row in template["snapshots"]["calendar"]["rows"]:
        row["base_month"] = row["start_date"].replace("-", "")[:6]
    for row in template["snapshots"]["policies"]["rows"]:
        row["effective_to"] = (date.fromisoformat(end) + timedelta(days=1)).isoformat()
    template = reseal(template)
    context = template["context"]
    decisions = [
        {
            "item_id": f"ITEM-{i:05d}",
            "model_run_id": "MODEL-1",
            "selection_decision_id": f"DEC-{i:05d}",
        }
        for i in range(items)
    ]
    rows = (
        {
            **r,
            "uom": "EA",
            "yyyyww": week,
            "forecast_qty": "0.4",
            "result_id": f"RES-{r['item_id']}-{week}",
        }
        for r in decisions
        for week in week_ids
    )
    demand_shared = {
        kind: copy.deepcopy(template["snapshots"][kind]) for kind in ("calendar", "master")
    }
    for kind, snapshot in demand_shared.items():
        snapshot["metadata"]["plant_cd"] = "V101"
        if kind == "master":
            for row in snapshot["rows"]:
                row["plant_cd"] = "V101"
        snapshot["content_hash"] = digest(
            {
                "snapshot_type": kind,
                "snapshot_id": snapshot["snapshot_id"],
                "metadata": snapshot["metadata"],
                "rows": snapshot["rows"],
            }
        )
    request = ForecastExportRequest.from_dict(
        {
            "contract_id": "demand-io-forecast-export-v1",
            "selector": {
                **{k: context[k] for k in ("company_cd", "subs_cd", "site_cd", "demand_run_id")},
                "plant_cd": "V101",
                "tenant_id": "default",
                "project_id": "project-1",
                "plan_id": "PLAN-1",
                "plan_yyyyww": "202626",
                "fcst_w0_yyyyww": week_ids[0],
                "target_cd": "DS_T_01",
                "bukt_cd": "W",
                "snrio_id": "S1",
                "snrio_grp": "DEV",
                "regul_type": "R",
                "forecast_stat_cd": "POINT",
            },
            "shared": {
                **{
                    k: context[k]
                    for k in (
                        "planning_cycle_id",
                        "planning_cycle_revision_id",
                        "master_as_of_date",
                        "master_snapshot_revision",
                    )
                },
                **demand_shared,
                "month_rule": "SOURCE_WEEK_MONDAY_V1",
                "quantity_rule": "PLANNING_6_PHYSICAL_EA_0_V1",
            },
            "selection_content_hash": digest(sorted(decisions, key=canonical_json)),
            "input_manifest_sha256": "a" * 64,
            "source_profile_sha256": "b" * 64,
        }
    )
    return template, request, decisions, rows


def export(root, request, decisions, rows, part_rows=10_000):
    data = request.to_dict()
    shared = bind_shared_inputs(
        request,
        calendar=data["shared"]["calendar"],
        master=data["shared"]["master"],
        demand_run_id=data["selector"]["demand_run_id"],
        input_manifest_sha256="a" * 64,
    )
    return ExportForecastSnapshotUseCase(
        ParquetForecastSnapshotWriter(root, part_rows=part_rows)
    ).execute(
        request,
        run={
            "status": "SUCCEEDED",
            "selector": data["selector"],
            "input_manifest_sha256": "a" * 64,
        },
        shared_receipt=shared,
        decisions=decisions,
        rows=rows,
    )


def reference(receipt):
    return {
        k: receipt[k] for k in ("manifest_path", "snapshot_id", "content_hash", "manifest_hash")
    }


@unittest.skipUnless(DEMAND_ROOT, "set DEMAND_ENGINE_ROOT for cross-repository tests")
class ForecastHandoffIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_literal_handoff_psi_and_source_provenance(self):
        template, request, decisions, rows = fixture()
        receipt = export(self.root, request, decisions, rows, part_rows=1)
        prepared = PrepareForecastHandoffUseCase(
            DEPLOYMENT, ParquetForecastHandoffReader()
        ).execute(template, **reference(receipt))
        result = RunPsiSimulationUseCase(DEPLOYMENT).execute(prepared["canonical_input"])
        self.assertEqual([r["eoh_qty"] for r in result["psi_rows"]], ["1.6", "1.2", "0.8"])
        self.assertEqual(result["psi_rows"][0]["base_month"], "202606")
        self.assertEqual(
            prepared["handoff_evidence"]["source_rows"][0]["selection_decision_id"], "DEC-00000"
        )
        self.assertNotEqual(
            receipt["content_hash"], prepared["handoff_evidence"]["canonical_forecast_hash"]
        )
        self.assertEqual(prepared["handoff_evidence"]["source_plant_cd"], "V101")
        self.assertNotEqual(
            prepared["handoff_evidence"]["shared_source_bindings"]["master"]["content_hash"],
            prepared["handoff_evidence"]["shared_source_bindings"]["master"][
                "canonical_content_hash"
            ],
        )
        self.assertFalse(prepared["handoff_evidence"]["database_published"])

    def test_13_week_demand_is_5_point_2_not_zero_or_13(self):
        template, request, decisions, rows = fixture(weeks=13)
        receipt = export(self.root, request, decisions, rows)
        verified = ParquetForecastHandoffReader().read(**reference(receipt))
        self.assertEqual(sum(Decimal(r["forecast_qty"]) for r in verified["rows"]), Decimal("5.2"))

    def test_full_site_111020_rows_exceeding_legacy_json_limit(self):
        template, request, decisions, rows = fixture(items=4270, weeks=26)
        receipt = export(self.root, request, decisions, rows)
        self.assertEqual(receipt["row_count"], 111_020)
        prepared = PrepareForecastHandoffUseCase(
            DEPLOYMENT, ParquetForecastHandoffReader()
        ).execute(template, **reference(receipt))
        canonical = prepared["canonical_input"]
        self.assertGreater(len(canonical.document_json.encode()), 8_000_000)
        self.assertEqual(len(prepared["prepared"].to_dict()["demands"]), 111_020)

    def test_wrong_pinned_identity(self):
        _, request, decisions, rows = fixture()
        receipt = export(self.root, request, decisions, rows)
        for key, value in (
            ("manifest_hash", "0" * 64),
            ("content_hash", "0" * 64),
            ("snapshot_id", "WRONG"),
        ):
            with self.subTest(key=key), self.assertRaises(InventoryInputError):
                ParquetForecastHandoffReader().read(**{**reference(receipt), key: value})

    def test_changed_scope_or_shared_revision(self):
        template, request, decisions, rows = fixture()
        receipt = export(self.root, request, decisions, rows)
        for key in (
            "site_cd",
            "planning_cycle_revision_id",
            "master_snapshot_revision",
            "demand_run_id",
        ):
            wrong = copy.deepcopy(template)
            wrong["context"][key] = "OTHER"
            with self.subTest(key=key), self.assertRaises(InventoryInputError):
                PrepareForecastHandoffUseCase(DEPLOYMENT, ParquetForecastHandoffReader()).execute(
                    wrong, **reference(receipt)
                )

    def test_changed_demand_plant_scope_is_rejected(self):
        _, request, decisions, rows = fixture()
        data = request.to_dict()
        data["shared"]["calendar"]["metadata"]["plant_cd"] = "V999"
        data["shared"]["calendar"]["content_hash"] = digest(
            {
                "snapshot_type": "calendar",
                "snapshot_id": data["shared"]["calendar"]["snapshot_id"],
                "metadata": data["shared"]["calendar"]["metadata"],
                "rows": data["shared"]["calendar"]["rows"],
            }
        )
        with self.assertRaisesRegex(ValueError, "SHARED_SCOPE"):
            ForecastExportRequest.from_dict(data)

    def test_missing_or_corrupt_part_is_never_admitted(self):
        _, request, decisions, rows = fixture()
        receipt = export(self.root, request, decisions, rows)
        part = Path(receipt["manifest_path"]).parent / "part-00000.parquet"
        part.write_bytes(b"corruption")
        with self.assertRaises(InventoryInputError):
            ParquetForecastHandoffReader().read(**reference(receipt))
        part.unlink()
        with self.assertRaisesRegex(InventoryInputError, "MISSING_OR_UNSAFE_PART"):
            ParquetForecastHandoffReader().read(**reference(receipt))

    def test_duplicate_or_traversal_part_even_with_resealed_manifest(self):
        _, request, decisions, rows = fixture()
        receipt = export(self.root, request, decisions, rows, part_rows=1)
        path = Path(receipt["manifest_path"])
        original = json.loads(path.read_text())
        for name in ("../outside.parquet", "part-00000.parquet"):
            manifest = copy.deepcopy(original)
            manifest["parts"][1]["path"] = name
            encoded = canonical_json(manifest).encode()
            path.write_bytes(encoded)
            with self.assertRaisesRegex(InventoryInputError, "PART_ORDER_OR_PATH"):
                ParquetForecastHandoffReader().read(
                    **{**reference(receipt), "manifest_hash": hashlib.sha256(encoded).hexdigest()}
                )

    def test_fractional_physical_inventory_still_blocked_after_handoff(self):
        template, request, decisions, rows = fixture()
        receipt = export(self.root, request, decisions, rows)
        template["snapshots"]["inventory"]["rows"][0]["on_hand_qty"] = "0.4"
        template = reseal(template)
        with self.assertRaisesRegex(InventoryInputError, "UOM_QUANTITY_PRECISION"):
            PrepareForecastHandoffUseCase(DEPLOYMENT, ParquetForecastHandoffReader()).execute(
                template, **reference(receipt)
            )

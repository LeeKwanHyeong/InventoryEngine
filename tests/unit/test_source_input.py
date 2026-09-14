"""Source mappings preserve existing hand-written PSI, and fail closed on ambiguity."""

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from source_fixtures import MemoryReader, mapped, seal, wrap
from fixtures import GOLDEN, build_request
from evaluation_fixtures import fixture
from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.source import (
    SourceInputRequest,
    SourceSnapshot,
    source_hash,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from dsio_inventory_engine.prepare_inventory.application.source_input import (
    PrepareSourceInputUseCase,
)
from dsio_inventory_engine.simulate_inventory.application.baseline import RunPsiSimulationUseCase

DEV = DeploymentScope("DSE", "DEVELOPMENT")


class SourceTests(unittest.IsolatedAsyncioTestCase):
    async def run_source(self, req, sources, deployment=DEV):
        return await PrepareSourceInputUseCase(MemoryReader(sources), deployment).execute(
            SourceInputRequest.from_dict(req)
        )

    async def test_mappings_match_hand_calculated_psi(self):
        req, sources = mapped()
        out = await self.run_source(req, sources)
        canonical = out["canonical_request"]
        self.assertEqual(canonical["snapshots"]["master"]["rows"][0]["uom"], "EA")
        policies = canonical["snapshots"]["policies"]["rows"]
        self.assertEqual(
            (policies[0]["lead_time_days"], policies[0]["approved_service_level"]), (7, "0.95")
        )
        self.assertEqual(policies[0]["source_target_inventory_qty"], "100")
        psi = RunPsiSimulationUseCase(DEV).execute(CanonicalInputRequest.from_dict(canonical))
        self.assertEqual([r["eoh_qty"] for r in psi["psi_rows"]], ["30", "10", "10"])
        self.assertEqual(len(out["source_mapping_evidence"]), 8)
        self.assertFalse(
            out["database_writes"] or out["artifact_sealed"] or out["evidence_persisted"]
        )

    async def test_all_existing_golden_rows_unchanged_through_reader(self):
        for case in GOLDEN["cases"]:
            req, sources = wrap(build_request(case))
            with self.subTest(case=case["id"]):
                out = await self.run_source(req, sources)
                result = RunPsiSimulationUseCase(DEV).execute(
                    CanonicalInputRequest.from_dict(out["canonical_request"])
                )
                for item in case["items"]:
                    rows = [r for r in result["psi_rows"] if r["item_id"] == item["id"]]
                    self.assertEqual(
                        [[r[k] for k in GOLDEN["expected_columns"]] for r in rows], item["expected"]
                    )

    async def test_source_order_and_retry_run_id_deterministic(self):
        req, sources = mapped()
        a = await self.run_source(req, sources)
        req["context"]["engine_run_id"] = "IO-RETRY-NEW-ID"
        for source in sources.values():
            source["rows"].reverse()
        b = await self.run_source(req, sources)
        self.assertEqual(a["canonical_input_hash"], b["canonical_input_hash"])
        self.assertEqual(a["source_bindings_hash"], b["source_bindings_hash"])
        self.assertNotEqual(
            a["canonical_request"]["context"]["engine_run_id"],
            b["canonical_request"]["context"]["engine_run_id"],
        )

    async def test_unverified_due_in_keeps_quantity_date_and_exclusion(self):
        req, sources = mapped()
        s = sources["receipts"]
        s["adapter"] = "UNVERIFIED_DUE_IN_V1"
        s["rows"] = [
            {
                **{
                    k: v
                    for k, v in r.items()
                    if k not in ("item_id", "uom", "status", "supply_type")
                },
                "oper_part_no": r["item_id"],
            }
            for r in s["rows"]
        ]
        out = await self.run_source(seal(req, sources), sources)
        receipt = out["receipt_decisions"][0]
        self.assertEqual(
            (receipt["due_qty"], receipt["due_date"], receipt["included_qty"]),
            ("40", "2026-09-28", "0"),
        )
        self.assertEqual(receipt["exclusion_reason"], "VENDOR_COMMITMENT_NOT_VERIFIED")

    async def test_52_week_generator_boh_passes_without_relabeling_actual(self):
        canonical = fixture()["mathematical_request"]["recommendation"]["canonical_input"]
        req, sources = wrap(canonical)
        out = await self.run_source(req, sources)
        inv = out["canonical_request"]["snapshots"]["inventory"]
        self.assertEqual(inv["metadata"]["position_source_type"], "SYNTHETIC_BOH")
        self.assertEqual(
            inv["metadata"]["simulation_run_id"],
            canonical["snapshots"]["inventory"]["metadata"]["simulation_run_id"],
        )
        self.assertEqual(inv["rows"], canonical["snapshots"]["inventory"]["rows"])

    async def test_bad_scope_missing_orphan_asof_and_seal_fail(self):
        cases = [
            (
                "master",
                lambda s: s.update(source_as_of_date="2026-09-29"),
                "SOURCE_MASTER_AS_OF_MISMATCH",
            ),
            (
                "inventory",
                lambda s: s.update(source_as_of_date="2026-09-29"),
                "SOURCE_POSITION_AS_OF_MISMATCH",
            ),
            ("forecast", lambda s: s.update(status="COLLECTING"), "UNSEALED_SOURCE"),
            (
                "inventory",
                lambda s: s.update(origin_type="UNKNOWN_SOURCE"),
                "UNKNOWN_SOURCE_ORIGIN",
            ),
            ("inventory", lambda s: s["rows"][0].update(oper_part_no="ORPHAN"), "ORPHAN_ITEM"),
            ("forecast", lambda s: s["rows"].pop(), "MISSING_FORECAST_BUCKET"),
            (
                "forecast",
                lambda s: s["semantics"]["selector"].update(plan_id="PLAN-OTHER"),
                "FORECAST_SCOPE_MISMATCH",
            ),
            ("inventory", lambda s: s.update(rows=[]), "MISSING_POSITION"),
            ("master", lambda s: s["rows"][0].update(stock_flag="N"), "EMPTY_BUFFER_UNIVERSE"),
            ("inventory", lambda s: s["metadata"].update(site_cd="V999"), "SOURCE_SCOPE_MISMATCH"),
            ("forecast", lambda s: s["rows"][0].update(fcst_qty="1.5"), "UOM_QUANTITY_PRECISION"),
            (
                "customer_orders",
                lambda s: s["metadata"].update(coverage="UNAVAILABLE"),
                "SOURCE_COVERAGE_UNAVAILABLE",
            ),
            ("policies", lambda s: s["rows"][0].update(min_po_qty=None), "DECIMAL_STRING_REQUIRED"),
            (
                "policies",
                lambda s: s["semantics"].update(max_qty_meaning="PHYSICAL_CAPACITY"),
                "INVALID_CODE",
            ),
            (
                "policies",
                lambda s: s["rows"][0].update(stock_lt="0.1"),
                "LEAD_TIME_CALENDAR_MAPPING_REQUIRED",
            ),
        ]
        for kind, mutate, code in cases:
            req, sources = mapped()
            mutate(sources[kind])
            with self.subTest(code=code), self.assertRaisesRegex(InventoryInputError, code):
                await self.run_source(seal(req, sources), sources)

    async def test_cutoff_reconciliation_reserved_and_watermark_not_bypassed(self):
        cases = [
            (
                lambda s: s["rows"][0].update(boh_qty="101", available_qty="101"),
                "EOH_BOH_RECONCILIATION_FAILED",
            ),
            (
                lambda s: s["metadata"].update(complete_through="2026-09-27T00:00:00Z"),
                "SOURCE_WATERMARK_INCOMPLETE",
            ),
            (lambda s: s["metadata"].update(source_watermark="WRONG"), "CUTOFF_BINDING_MISMATCH"),
            (
                lambda s: (
                    s["rows"][0].update(reserved_qty="10", available_qty="90")
                    or s["metadata"].update(reserved_treatment="UNKNOWN")
                ),
                "RESERVED_SEMANTICS_UNSUPPORTED",
            ),
        ]
        for mutate, code in cases:
            req, sources = mapped()
            mutate(sources["inventory"])
            with self.subTest(code=code), self.assertRaisesRegex(InventoryInputError, code):
                await self.run_source(seal(req, sources), sources)

    async def test_pin_content_count_parent_and_origin_failures(self):
        for mutation, code in (
            (
                lambda r, s: r["source_bindings"]["forecast"].update(content_hash="f" * 64),
                "PINNED_SOURCE_MISMATCH",
            ),
            (lambda r, s: s["forecast"]["rows"][0].update(fcst_qty="999"), "SOURCE_HASH_MISMATCH"),
            (lambda r, s: s["forecast"].update(row_count=99), "SOURCE_ROW_COUNT_MISMATCH"),
        ):
            req, sources = mapped()
            mutation(req, sources)
            with self.subTest(code=code), self.assertRaisesRegex(InventoryInputError, code):
                await self.run_source(req, sources)
        req, sources = mapped()
        sources["forecast"]["metadata"]["master_content_hash"] = "f" * 64
        sources["forecast"]["content_hash"] = source_hash(sources["forecast"])
        req["source_bindings"]["forecast"]["content_hash"] = sources["forecast"]["content_hash"]
        with self.assertRaisesRegex(InventoryInputError, "SOURCE_PARENT_HASH_MISMATCH"):
            await self.run_source(req, sources)

    async def test_forecast_gap_and_ineligible_item_include_universe_evidence(self):
        req, sources = mapped()
        sources["forecast"]["rows"] = []
        with self.assertRaisesRegex(
            InventoryInputError,
            "MISSING_FORECAST_BUCKET",
        ) as missing:
            await self.run_source(seal(req, sources), sources)
        gap = json.loads(missing.exception.evidence_json)[0]
        self.assertEqual(gap["status"], "REJECTED_MISSING_FORECAST_BUCKETS")
        self.assertEqual(gap["missing_forecast_item_count"], 1)
        self.assertEqual(gap["uom_rule"], "FIXED_EA_V1")

        req, sources = mapped()
        sources["forecast"]["rows"][0]["oper_part_no"] = "NOT-IN-MASTER"
        with self.assertRaisesRegex(
            InventoryInputError,
            "ORPHAN_ITEM",
        ) as ineligible:
            await self.run_source(seal(req, sources), sources)
        mismatch = json.loads(ineligible.exception.evidence_json)[0]
        self.assertEqual(
            mismatch["status"],
            "REJECTED_INELIGIBLE_FORECAST_ITEMS",
        )
        self.assertEqual(mismatch["ineligible_forecast_item_sample"], ["NOT-IN-MASTER"])

    async def test_prod_rejects_fixtures_and_company_is_fixed(self):
        req, sources = mapped()
        with self.assertRaisesRegex(InventoryInputError, "DEVELOPMENT_SOURCE_FORBIDDEN"):
            await self.run_source(req, sources, DeploymentScope("DSE", "PRODUCTION"))
        with self.assertRaisesRegex(InventoryInputError, "DEPLOYMENT_COMPANY_MISMATCH"):
            await self.run_source(req, sources, DeploymentScope("OTHER", "DEVELOPMENT"))

    def test_dto_is_immutable_and_strict(self):
        req, sources = mapped()
        dto = SourceInputRequest.from_dict(req)
        req["context"]["engine_run_id"] = "CHANGED"
        self.assertNotEqual(dto.to_dict()["context"]["engine_run_id"], "CHANGED")
        bad = copy.deepcopy(sources["forecast"])
        bad["unexpected"] = 1
        with self.assertRaisesRegex(InventoryInputError, "CONTRACT_FIELDS"):
            SourceSnapshot.from_dict(bad)

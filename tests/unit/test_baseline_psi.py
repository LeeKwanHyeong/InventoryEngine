"""Literal independent oracles plus algebraic invariants and boundary rejection."""

import copy
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from decimal import Decimal, localcontext
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fixtures import GOLDEN, ROOT, build_request, golden_case, reseal, sha

from dsio_inventory_engine.entrypoints.cli import main
from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, read_json
from dsio_inventory_engine.prepare_inventory.application.inventory_input import (
    PrepareInventoryInputUseCase,
)
from dsio_inventory_engine.simulate_inventory.application.baseline import RunPsiSimulationUseCase

DEPLOYMENT = DeploymentScope("DSE", "DEVELOPMENT")


def run(request: dict) -> dict:
    return RunPsiSimulationUseCase(DEPLOYMENT).execute(CanonicalInputRequest.from_dict(request))


class GoldenTests(unittest.TestCase):
    def test_all_literal_golden_cases(self):
        for case in GOLDEN["cases"]:
            with self.subTest(case=case["id"]):
                output = run(build_request(case))
                rows = output["psi_rows"]
                self.assertEqual(len(rows), len(case["items"]) * 3)
                for item in case["items"]:
                    actual = [row for row in rows if row["item_id"] == item["id"]]
                    self.assertEqual(
                        [[r[k] for k in GOLDEN["expected_columns"]] for r in actual],
                        item["expected"],
                    )
                    for index, row in enumerate(actual):

                        def d(k):
                            return Decimal(row[k])

                        self.assertEqual(
                            d("boh_qty") + d("confirmed_supplier_receipt_qty"),
                            d("eoh_qty")
                            + d("fulfilled_backorder_qty")
                            + d("fulfilled_confirmed_customer_order_qty")
                            + d("fulfilled_forecast_qty"),
                        )
                        self.assertEqual(
                            d("backorder_open_qty") + d("confirmed_customer_order_qty"),
                            d("backorder_close_qty")
                            + d("fulfilled_backorder_qty")
                            + d("fulfilled_confirmed_customer_order_qty"),
                        )
                        self.assertEqual(
                            d("net_forecast_qty"),
                            d("fulfilled_forecast_qty") + d("forecast_shortage_qty"),
                        )
                        self.assertEqual(d("on_hand_eoh_qty"), d("eoh_qty") + d("reserved_qty"))
                        if index:
                            self.assertEqual(row["boh_qty"], actual[index - 1]["eoh_qty"])
                            self.assertEqual(
                                row["backorder_open_qty"], actual[index - 1]["backorder_close_qty"]
                            )
                self.assertFalse(
                    output["run_claimed"]
                    or output["database_writes"]
                    or output["evidence_persisted"]
                    or output["artifact_sealed"]
                )

    def test_supply_exclusions_retain_original_data(self):
        output = run(build_request(golden_case("supply_states")))
        evidence = output["prepared_input"]["receipt_decisions"]
        self.assertEqual(len(evidence), 8)
        unknown = next(r for r in evidence if r["status"] == "UNVERIFIED_DUE_IN")
        self.assertEqual(
            (
                unknown["due_qty"],
                unknown["included_qty"],
                unknown["due_date"],
                unknown["exclusion_reason"],
            ),
            ("50", "0", "2026-09-28", "VENDOR_COMMITMENT_NOT_VERIFIED"),
        )

    def test_upstream_netted_evidence_without_invented_gross(self):
        request = build_request(golden_case("upstream_netted"))
        output = run(request)
        self.assertIsNone(output["psi_rows"][0]["gross_forecast_qty"])
        row = next(r for r in request["snapshots"]["forecast"]["rows"] if r["yyyyww"] == "202640")
        row.update(upstream_gross_forecast_qty="110", upstream_consumed_qty="80")
        output = run(reseal(request))
        self.assertEqual(output["psi_rows"][0]["forecast_consumed_qty"], "80")
        self.assertEqual(output["psi_rows"][0]["eoh_qty"], "30")

    def test_retry_input_hash_excludes_run_but_includes_revision(self):
        request = build_request(golden_case())
        first = CanonicalInputRequest.from_dict(request)
        request["context"]["engine_run_id"] = "RETRY-RUN"
        second = CanonicalInputRequest.from_dict(request)
        self.assertEqual(first.input_hash, second.input_hash)
        self.assertEqual(
            run(first.to_dict())["psi_content_hash"], run(second.to_dict())["psi_content_hash"]
        )
        request["context"]["planning_cycle_revision_id"] = "NEW-REVISION"
        self.assertNotEqual(first.input_hash, CanonicalInputRequest.from_dict(request).input_hash)

        request = build_request(golden_case())
        request["context"]["plan_id"] = "PLAN-OTHER"
        self.assertNotEqual(first.input_hash, CanonicalInputRequest.from_dict(request).input_hash)

    def test_frozen_input_output_detached_and_row_order_irrelevant(self):
        request = build_request(golden_case())
        canonical = CanonicalInputRequest.from_dict(request)
        before = canonical.input_hash
        for snapshot in request["snapshots"].values():
            snapshot["rows"].reverse()
        self.assertEqual(before, CanonicalInputRequest.from_dict(request).input_hash)
        prepared = PrepareInventoryInputUseCase(DEPLOYMENT).execute(canonical)
        prepared.to_dict()["positions"][0]["on_hand_qty"] = "999"
        self.assertEqual(prepared.to_dict()["positions"][0]["on_hand_qty"], "100")

    def test_uom_tolerance_records_variance_without_rewriting_boh(self):
        request = build_request(golden_case("decimal_stock"))
        request["snapshots"]["prior_inventory"]["rows"][0]["eoh_qty"] = "0.299"
        output = run(reseal(request))
        evidence = next(
            r
            for r in output["prepared_input"]["reconciliation"]
            if r["reconciliation_type"] == "EOH_BOH"
        )
        self.assertEqual(
            (evidence["variance_qty"], evidence["status"], output["psi_rows"][0]["boh_qty"]),
            ("0.001", "PASS", "0.3"),
        )

    def test_due_date_last_bucket_day_and_after_horizon(self):
        request = build_request(golden_case())
        receipt = request["snapshots"]["receipts"]["rows"][0]
        receipt["due_date"] = "2026-10-04"
        self.assertEqual(
            run(reseal(request))["psi_rows"][0]["confirmed_supplier_receipt_qty"], "40"
        )
        receipt["due_date"] = "2026-10-19"
        output = run(reseal(request))
        self.assertEqual(output["psi_rows"][0]["confirmed_supplier_receipt_qty"], "0")
        self.assertEqual(
            output["prepared_input"]["receipt_decisions"][0]["exclusion_reason"],
            "OUTSIDE_PLAN_HORIZON",
        )

    def test_external_decimal_context_does_not_change_calculation(self):
        request = build_request(golden_case())
        expected = run(request)["psi_content_hash"]
        with localcontext() as ctx:
            ctx.prec = 2
            self.assertEqual(run(request)["psi_content_hash"], expected)


class RejectionTests(unittest.TestCase):
    def assert_rejected(self, request, code, reseal_content=True):
        if reseal_content:
            reseal(request)
        with patch(
            "dsio_inventory_engine.simulate_inventory.application.baseline._roll_forward"
        ) as calculation:
            with self.assertRaisesRegex(InventoryInputError, "^" + code + "$"):
                run(request)
            calculation.assert_not_called()

    def test_unsealed_states_for_all_snapshots(self):
        for kind in build_request(golden_case())["snapshots"]:
            for status in ("COLLECTING", "RECONCILING", "REJECTED", "SUPERSEDED"):
                with self.subTest(kind=kind, status=status):
                    request = build_request(golden_case())
                    request["snapshots"][kind]["status"] = status
                    self.assert_rejected(request, "UNSEALED_INPUT")

    def test_hash_and_row_count_tampering(self):
        for kind in build_request(golden_case())["snapshots"]:
            request = build_request(golden_case())
            request["snapshots"][kind]["content_hash"] = "0" * 64
            self.assert_rejected(request, "SNAPSHOT_HASH_MISMATCH", False)
        request = build_request(golden_case())
        request["snapshots"]["inventory"]["row_count"] = 9
        self.assert_rejected(request, "SNAPSHOT_ROW_COUNT_MISMATCH", False)

    def test_resealed_but_changed_input_binding_rejected(self):
        request = build_request(golden_case())
        request["snapshots"]["forecast"]["rows"][0]["forecast_qty"] = "120"
        reseal(request, update_bindings=False)
        self.assert_rejected(request, "PINNED_INPUT_MISMATCH", False)

    def test_company_site_scope_and_orphans(self):
        for kind in (
            "master",
            "forecast",
            "customer_orders",
            "inventory",
            "prior_inventory",
            "receipts",
            "policies",
        ):
            for field in ("company_cd", "subs_cd", "site_cd"):
                request = build_request(golden_case())
                request["snapshots"][kind]["rows"][0][field] = "FOREIGN"
                self.assert_rejected(request, "ROW_SCOPE_MISMATCH")
            if kind != "master":
                request = build_request(golden_case())
                request["snapshots"][kind]["rows"][0]["item_id"] = "ORPHAN"
                self.assert_rejected(request, "ORPHAN_ITEM")

    def test_missing_forecast_policy_position_or_source_coverage(self):
        for kind, code in (
            ("forecast", "MISSING_FORECAST_BUCKET"),
            ("policies", "MISSING_POLICY"),
            ("inventory", "MISSING_POSITION"),
            ("prior_inventory", "MISSING_POSITION"),
        ):
            request = build_request(golden_case())
            request["snapshots"][kind]["rows"].pop()
            self.assert_rejected(request, code)
        for kind in ("customer_orders", "receipts"):
            request = build_request(golden_case())
            request["snapshots"][kind]["metadata"]["coverage"] = "UNAVAILABLE"
            self.assert_rejected(request, "SOURCE_COVERAGE_UNAVAILABLE")

    def test_duplicate_source_rows(self):
        codes = {
            "master": "DUPLICATE_MASTER_ITEM",
            "forecast": "DUPLICATE_DEMAND_BUCKET",
            "customer_orders": "DUPLICATE_DEMAND_BUCKET",
            "inventory": "DUPLICATE_POSITION",
            "prior_inventory": "DUPLICATE_POSITION",
            "receipts": "DUPLICATE_RECEIPT",
            "policies": "DUPLICATE_POLICY",
        }
        for kind, code in codes.items():
            request = build_request(golden_case())
            request["snapshots"][kind]["rows"].append(
                copy.deepcopy(request["snapshots"][kind]["rows"][0])
            )
            self.assert_rejected(request, code)

    def test_eoh_boh_variance_reports_evidence(self):
        request = build_request(golden_case())
        request["snapshots"]["prior_inventory"]["rows"][0]["eoh_qty"] = "90"
        self.assert_rejected(request, "EOH_BOH_RECONCILIATION_FAILED")
        with self.assertRaises(InventoryInputError) as caught:
            run(request)
        row = json.loads(caught.exception.evidence_json)[-1]
        self.assertEqual(
            (row["expected_boh_qty"], row["reported_boh_qty"], row["variance_qty"]),
            ("90", "100", "10"),
        )

    def test_watermark_cutoff_and_prior_period(self):
        edits = [
            ("source_watermark", "NEW-TOKEN", "CUTOFF_BINDING_MISMATCH"),
            ("business_timezone", "UTC", "CUTOFF_BINDING_MISMATCH"),
            ("complete_through", "2026-09-27T00:00:00Z", "SOURCE_WATERMARK_INCOMPLETE"),
            ("sealed_at", "2026-09-27T00:00:00Z", "INVALID_SEAL_TIMELINE"),
        ]
        for key, value, code in edits:
            request = build_request(golden_case())
            request["snapshots"]["inventory"]["metadata"][key] = value
            self.assert_rejected(request, code)
        request = build_request(golden_case())
        request["snapshots"]["prior_inventory"]["metadata"]["as_of_date"] = "2026-09-26"
        self.assert_rejected(request, "PRIOR_PERIOD_MISMATCH")

    def test_sunday_business_monday_posting_is_rejected(self):
        request = build_request(golden_case())
        position = request["snapshots"]["inventory"]["rows"][0]
        event = {k: position[k] for k in ("company_cd", "subs_cd", "site_cd", "item_id", "uom")}
        event.update(
            event_id="LATE-1",
            quantity="-10",
            business_occurred_at="2026-09-27T14:00:00Z",
            erp_posted_at="2026-09-28T00:00:00Z",
            ingested_at="2026-09-28T01:00:00Z",
            source_document_id="SHIPMENT-1",
        )
        event["event_hash"] = sha(event)
        meta = request["snapshots"]["inventory"]["metadata"]
        meta.update(
            source_events=[event],
            complete_through="2026-09-28T01:00:00Z",
            extracted_at="2026-09-28T02:00:00Z",
            sealed_at="2026-09-28T03:00:00Z",
        )
        self.assert_rejected(request, "LATE_POSTING_DETECTED")

    def test_unapproved_or_corrupt_adjustments(self):
        request = build_request(golden_case("approved_adjustment"))
        request["snapshots"]["inventory"]["metadata"]["adjustments"][0]["event_hash"] = "0" * 64
        self.assert_rejected(request, "ADJUSTMENT_HASH_MISMATCH")
        request = build_request(golden_case("approved_adjustment"))
        request["snapshots"]["inventory"]["metadata"]["adjustments"][0]["approved_by"] = ""
        self.assert_rejected(request, "INVALID_IDENTIFIER")

    def test_available_reserved_and_uom_validation(self):
        edits = [
            ("available_qty", "99", "AVAILABLE_ON_HAND_RECONCILIATION_FAILED"),
            ("on_hand_qty", "-1", "NEGATIVE_QUANTITY"),
            ("on_hand_qty", "NaN", "NON_FINITE_QUANTITY"),
            ("on_hand_qty", 100, "DECIMAL_STRING_REQUIRED"),
            ("on_hand_qty", "100.5", "UOM_QUANTITY_PRECISION"),
            ("uom", "KG", "UOM_MISMATCH"),
        ]
        for key, value, code in edits:
            request = build_request(golden_case())
            request["snapshots"]["inventory"]["rows"][0][key] = value
            self.assert_rejected(request, code)
        request = build_request(golden_case("reserved_unavailable"))
        request["snapshots"]["inventory"]["metadata"]["reserved_treatment"] = (
            "CUSTOMER_ORDER_ALLOCATED"
        )
        self.assert_rejected(request, "RESERVED_SEMANTICS_UNSUPPORTED")

    def test_calendar_gaps_duplicates_and_wrong_w0(self):
        for key, value, code in (
            ("start_date", "2026-10-06", "CALENDAR_GAP_OR_OVERLAP"),
            ("yyyyww", "202640", "DUPLICATE_WEEK"),
            ("seq", 4, "INVALID_CALENDAR_BUCKET"),
        ):
            request = build_request(golden_case())
            request["snapshots"]["calendar"]["rows"][1][key] = value
            self.assert_rejected(request, code)
        request = build_request(golden_case())
        request["context"]["plan_yyyyww"] = "202641"
        self.assert_rejected(request, "PLAN_CALENDAR_MISMATCH")

    def test_forecast_lineage_and_states(self):
        for key, value, code in (
            ("demand_run_status", "RUNNING", "FORECAST_NOT_VERIFIED"),
            ("evidence_status", "UNVERIFIED", "FORECAST_NOT_VERIFIED"),
            ("calendar_snapshot_id", "OTHER", "DEMAND_CALENDAR_MISMATCH"),
            ("master_snapshot_revision", "OTHER", "DEMAND_MASTER_MISMATCH"),
        ):
            request = build_request(golden_case())
            request["snapshots"]["forecast"]["metadata"][key] = value
            self.assert_rejected(request, code)

    def test_overdue_commitment_is_not_automatically_moved_to_w0(self):
        request = build_request(golden_case())
        request["snapshots"]["receipts"]["rows"][0]["due_date"] = "2026-09-27"
        self.assert_rejected(request, "OVERDUE_CONFIRMED_RECEIPT")

    def test_synthetic_production_and_policy_proxy_are_blocked(self):
        request = build_request(golden_case("tgsm_target_not_boh"))
        with self.assertRaisesRegex(InventoryInputError, "SYNTHETIC_POLICY_FORBIDDEN"):
            RunPsiSimulationUseCase(DeploymentScope("DSE", "PRODUCTION")).execute(
                CanonicalInputRequest.from_dict(request)
            )
        request = build_request(golden_case())
        request["snapshots"]["inventory"]["metadata"]["position_source_type"] = "POLICY_PROXY"
        self.assert_rejected(request, "POLICY_PROXY_NOT_BASELINE")

    def test_duplicate_json_unknown_fields_and_naive_time(self):
        with self.assertRaisesRegex(InventoryInputError, "DUPLICATE_JSON_KEY"):
            read_json('{"a": 1, "a": 2}')
        with self.assertRaisesRegex(InventoryInputError, "DUPLICATE_JSON_KEY"):
            RunPsiSimulationUseCase(DEPLOYMENT).execute(CanonicalInputRequest('{"a": 1, "a": 2}'))
        request = build_request(golden_case())
        request["latest"] = True
        self.assert_rejected(request, "CONTRACT_FIELDS")
        request = build_request(golden_case())
        request["context"]["inventory_cutoff_at"] = "2026-09-27T23:59:59"
        self.assert_rejected(request, "TIMEZONE_REQUIRED")

    def test_policy_overlap_and_invalid_parameters(self):
        request = build_request(golden_case())
        policy = copy.deepcopy(request["snapshots"]["policies"]["rows"][0])
        policy["policy_id"] = "OVERLAP"
        request["snapshots"]["policies"]["rows"].append(policy)
        self.assert_rejected(request, "OVERLAPPING_POLICIES")
        for key, value, code in (
            ("order_multiple", "0", "INVALID_POLICY_INPUT"),
            ("approved_service_level", "1", "INVALID_POLICY_INPUT"),
            ("effective_to", "2026-09-28", "POLICY_EFFECTIVE_RANGE"),
        ):
            request = build_request(golden_case())
            request["snapshots"]["policies"]["rows"][0][key] = value
            self.assert_rejected(request, code)

    def test_ambiguously_netted_forecast_and_synthetic_lineage(self):
        request = build_request(golden_case("upstream_netted"))
        row = request["snapshots"]["forecast"]["rows"][0]
        row["upstream_gross_forecast_qty"] = "100"
        self.assert_rejected(request, "INCOMPLETE_UPSTREAM_NETTING_EVIDENCE")
        request = build_request(golden_case("tgsm_target_not_boh"))
        request["snapshots"]["receipts"]["metadata"]["simulation_run_id"] = "OTHER"
        self.assert_rejected(request, "SYNTHETIC_SUPPLY_LINEAGE_MISMATCH")
        request = build_request(golden_case("tgsm_target_not_boh"))
        request["snapshots"]["inventory"]["metadata"]["simulation_run_id"] = None
        self.assert_rejected(request, "SYNTHETIC_LINEAGE_REQUIRED")

    def test_valid_movement_is_evidence_not_added_to_boh_again(self):
        request = build_request(golden_case())
        event = {k: request["context"][k] for k in ("company_cd", "subs_cd", "site_cd")}
        event.update(
            item_id="A",
            uom="EA",
            event_id="ALREADY-IN-BOH",
            quantity="10",
            business_occurred_at="2026-09-27T12:00:00Z",
            erp_posted_at="2026-09-27T13:00:00Z",
            ingested_at="2026-09-27T14:00:00Z",
            source_document_id="DOC1",
        )
        event["event_hash"] = sha(event)
        request["snapshots"]["inventory"]["metadata"]["source_events"].append(event)
        output = run(reseal(request))
        self.assertEqual(output["psi_rows"][0]["boh_qty"], "100")
        self.assertEqual(output["prepared_input"]["reconciliation"][0]["source_event_count"], 1)

    def test_invalid_timezone_format_unknown_bucket_and_inactive_master(self):
        request = build_request(golden_case())
        request["context"]["business_timezone"] = "Invalid/Zone"
        self.assert_rejected(request, "INVALID_BUSINESS_TIMEZONE")
        request = build_request(golden_case())
        request["snapshots"]["forecast"]["rows"][0]["yyyyww"] = "202639"
        self.assert_rejected(request, "UNKNOWN_BUCKET")
        request = build_request(golden_case())
        request["snapshots"]["master"]["rows"][0]["active"] = False
        self.assert_rejected(request, "EMPTY_BUFFER_UNIVERSE")
        request = build_request(golden_case())
        request["quantity_rules"][0]["tolerance_qty"] = "1"
        self.assert_rejected(request, "EA_REQUIRES_EXACT_INTEGER")


class CliTests(unittest.TestCase):
    def test_baseline_cli_computes_locally_without_database(self):
        document = json.dumps(build_request(golden_case()))
        with (
            patch.object(Path, "read_text", return_value=document),
            patch.object(Path, "stat") as stat,
            patch.dict("os.environ", {"IO_COMPANY_CD": "DSE", "IO_ENVIRONMENT": "DEVELOPMENT"}),
            redirect_stdout(io.StringIO()) as stdout,
        ):
            stat.return_value.st_size = len(document)
            self.assertEqual(main(["simulate-baseline", "--request", "fixture.json"]), 0)
        output = json.loads(stdout.getvalue())
        self.assertEqual(output["psi_rows"][0]["eoh_qty"], "30")
        self.assertFalse(output["database_writes"])

    def test_cli_does_not_allow_db_flag_for_baseline(self):
        with redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(
                main(["simulate-baseline", "--request", "unused.json", "--read-postgres"]), 2
            )
        self.assertEqual(
            json.loads(stdout.getvalue())["error_code"], "BASELINE_DATABASE_ACCESS_FORBIDDEN"
        )

    def test_oracle_packager_has_no_engine_dependency(self):
        source = (ROOT / "tests/support/fixtures.py").read_text()
        self.assertNotIn("dsio_inventory_engine", source)


if __name__ == "__main__":
    unittest.main()

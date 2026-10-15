"""Vendored DSIM physical draft: no database or sibling-repository dependency."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from dsio_inventory_engine.inventory_contracts.canonical import (
    CONTRACT_VERSION,
    V2_VERSION,
    V21_VERSION,
)
from dsio_inventory_engine.inventory_contracts.result_bundle import RESULT_STRATEGY_TYPES
from dsio_inventory_engine.inventory_contracts.values import digest

CATALOG = Path(__file__).resolve().parents[2] / "schemas/inventory-dsim-physical-contract-v1.json"


class DsimPhysicalDraftContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        self.tables = {t["name"].split(".")[1]: t for t in self.catalog["tables"]}

    def test_draft_is_not_a_runtime_wire_version_or_applied_migration(self) -> None:
        self.assertEqual("DRAFT_NOT_APPLIED", self.catalog["status"])
        self.assertEqual(21, len(self.tables))
        self.assertEqual(3, len(self.catalog["deferred"]))
        self.assertIn("CALCULATION_ONLY", self.catalog["scope"])

    def test_existing_canonical_and_strategy_versions_are_not_invented(self) -> None:
        context = self.tables["tb_io_plan_context"]
        versions = next(s for s in context["checks"] if "canonical_contract_version IN" in s)
        for version in (CONTRACT_VERSION, V2_VERSION, V21_VERSION):
            self.assertIn("'" + version + "'", versions)
        child = self.tables["tb_io_result_child"]
        strategies = next(s for s in child["checks"] if s.startswith("strategy_type IN"))
        for strategy in RESULT_STRATEGY_TYPES:
            self.assertIn("'" + strategy + "'", strategies)
        self.assertNotIn("PREDICTIVE_ML", strategies)

    def test_all_fk_roots_are_same_run_and_no_mutable_master_fk(self) -> None:
        for table in self.tables.values():
            names = {c["name"] for c in table["columns"]}
            self.assertIn("engine_run_id", table["primary_key"])
            self.assertTrue(set(table["primary_key"]) <= names)
            for fk in table["foreign_keys"]:
                self.assertFalse(fk["table"].startswith("dsdm."))
                if fk["table"].startswith("dsim."):
                    self.assertEqual("engine_run_id", fk["columns"][0])

    def test_projection_has_unbounded_exact_numeric_not_six_decimal_truncation(self) -> None:
        for table in self.tables.values():
            for column in table["columns"]:
                self.assertNotIn("NUMERIC(", column["type"])
        quantity = next(d for d in self.catalog["domains"] if d["name"] == "dsim.io_quantity")
        self.assertEqual("NUMERIC", quantity["type"])
        self.assertIn("'NaN'", quantity["check"])
        self.assertIn("'Infinity'", quantity["check"])

    def test_classification_and_trade_are_reused_not_recreated(self) -> None:
        reused = {t for entry in self.catalog["reuse"] for t in entry["tables"]}
        self.assertIn("dsai.inventory_classification_snapshot_items", reused)
        self.assertIn("dsai.inventory_site_input_sets", reused)
        self.assertIn("dsim.tb_io_landed_cost_assessment", reused)
        self.assertNotIn("tb_mst_inventory_policy_revision", self.tables)

    def test_evaluation_id_separates_cost_period_and_scenario_contexts(self) -> None:
        first = dict(
            engine_run_id="run",
            child_result_id="evaluation",
            search_child_result_id="search",
            buffer_id="part@site",
            candidate_id="candidate",
            evaluation_context_id="52w-cost1",
        )
        same = dict(reversed(list(first.items())))
        second = dict(first, evaluation_context_id="104w-cost2")
        self.assertEqual(digest(first), digest(same))
        self.assertNotEqual(digest(first), digest(second))
        self.assertEqual(
            ["engine_run_id", "evaluation_id"],
            self.tables["tb_io_policy_evaluation"]["primary_key"],
        )

    def test_selection_and_projection_receipt_do_not_approve_orders(self) -> None:
        selection_names = {c["name"] for c in self.tables["tb_io_policy_selection"]["columns"]}
        self.assertNotIn("approved_by", selection_names)
        receipt_names = {c["name"] for c in self.tables["tb_io_publication_receipt"]["columns"]}
        self.assertNotIn("effective_run_id", receipt_names)
        self.assertNotIn("automatic_order_allowed", receipt_names)

"""Closed cost input/parent Schema and semantic binding parity."""

import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource

from dsio_inventory_engine.inventory_contracts.result_bundle import seal_strategy_execution_plan
from dsio_inventory_engine.inventory_contracts.run_cost import (
    seal_run_cost_input,
    validate_run_cost_input,
)
from dsio_inventory_engine.inventory_contracts.run_result_manifest import (
    validate_manifest_document,
    validate_run_result_manifest,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from dsio_inventory_engine.run_inventory.landed_cost_pipeline import build_run_cost_evidence
from dsio_inventory_engine.run_inventory.psi_orchestrator import RunPsiBundleUseCase
from tests.integration.test_psi_orchestrator_offline import DEPLOYMENT
from tests.support.run_cost_fixtures import bound_cost_command


class RunCostContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[2] / "schemas"
        cls.schemas = {
            path.name: json.loads(path.read_text()) for path in root.glob("*.schema.json")
        }
        cls.registry = Registry().with_resources(
            [
                (document["$id"], Resource.from_contents(document))
                for document in cls.schemas.values()
                if "$id" in document
            ]
        )
        cls.command, cls.cost = bound_cost_command()
        cls.psi_run = RunPsiBundleUseCase(DEPLOYMENT).execute(cls.command)
        cls.parent = build_run_cost_evidence(
            request=cls.command.runtime_request,
            run=cls.psi_run,
            resolved=cls.cost,
            cost_profile=cls.command.cost_profile,
        ).manifest

    def validator(self, name):
        return Draft202012Validator(
            self.schemas[name], registry=self.registry, format_checker=FormatChecker()
        )

    def test_input_parent_and_plan_12_validate(self):
        for name, value in (
            ("inventory_run_cost_input.schema.json", self.cost.input_document),
            ("inventory_run_result_manifest.schema.json", self.parent),
            (
                "inventory_result_bundle.schema.json",
                self.command.runtime_request.strategy_execution_plan,
            ),
        ):
            with self.subTest(name=name):
                self.validator(name).validate(value)

    def test_parent_nested_fields_and_status_semantics_fail_closed(self):
        for mode in (
            "extra",
            "empty",
            "operational",
            "floating",
            "null_total",
            "duplicate",
            "null_delta",
            "unavailable_delta",
            "comparison_reason",
        ):
            with self.subTest(mode=mode):
                parent = copy.deepcopy(self.parent)
                if mode == "extra":
                    parent["simulation_metrics"][0]["extra"] = True
                elif mode == "empty":
                    parent["simulation_metrics"] = []
                elif mode == "operational":
                    parent["operational_cost_eligible"] = True
                elif mode == "floating":
                    parent["simulation_metrics"][1]["total_cost"] = 666875.0
                elif mode == "null_total":
                    parent["simulation_metrics"][1]["total_cost"] = None
                elif mode == "null_delta":
                    parent["simulation_comparisons"][0]["total_cost_delta"] = None
                elif mode == "unavailable_delta":
                    parent["simulation_comparisons"][0]["status"] = "NOT_AVAILABLE"
                    parent["simulation_comparisons"][0]["reason_code"] = "PRICING_MISSING"
                elif mode == "comparison_reason":
                    parent["simulation_comparisons"][0]["reason_code"] = "PRICING_MISSING"
                else:
                    parent["simulation_metrics"].append(
                        copy.deepcopy(parent["simulation_metrics"][0])
                    )
                parent["content_hash"] = digest(
                    {key: value for key, value in parent.items() if key != "content_hash"}
                )
                with self.assertRaises(InventoryInputError):
                    validate_manifest_document(parent)
                if (
                    mode != "duplicate"
                ):  # Cross-row identity uniqueness requires semantic validation.
                    with self.assertRaises(ValidationError):
                        self.validator("inventory_run_result_manifest.schema.json").validate(parent)

    def test_parent_run_attempt_hash_scope_or_psi_pointer_substitution_fails(self):
        for key, value in (
            ("engine_run_id", "other-run"),
            ("attempt_no", 2),
            ("tenant_id", "other"),
            ("runtime_request_content_hash", "f" * 64),
            ("input_content_hash", "f" * 64),
            ("input_reference", "artifact:other"),
            ("psi_result", {**self.parent["psi_result"], "content_hash": "f" * 64}),
        ):
            with self.subTest(key=key):
                parent = {**self.parent, key: value}
                parent["content_hash"] = digest(
                    {key: value for key, value in parent.items() if key != "content_hash"}
                )
                with self.assertRaises(InventoryInputError):
                    validate_run_result_manifest(
                        self.command.runtime_request,
                        parent,
                        canonical_input_hash=self.psi_run.canonical_input.input_hash,
                        psi_snapshot_id=self.psi_run.result_bundle["result_bundle_id"],
                        psi_content_hash=self.psi_run.result_bundle["content_hash"],
                    )

    def test_plan_version_cannot_omit_or_downgrade_cost_binding(self):
        for mode in ("missing", "downgrade"):
            body = copy.deepcopy(self.command.runtime_request.strategy_execution_plan)
            body.pop("content_hash")
            if mode == "missing":
                body.pop("landed_cost_binding")
            else:
                body["contract_version"] = "1.1.0"
            with self.assertRaises(InventoryInputError):
                seal_strategy_execution_plan(body)
            with self.assertRaises(ValidationError):
                self.validator("inventory_result_bundle.schema.json").validate(
                    {**body, "content_hash": digest(body)}
                )

    def test_cost_input_overlap_naive_judgment_and_quantity_fail_in_both_contracts(self):
        for mode in ("overlap", "time", "quantity", "empty"):
            body = copy.deepcopy(self.cost.input_document)
            body.pop("content_hash")
            if mode == "overlap":
                body["profile_component_attribution"]["variable_order_cost_per_unit"] = "GOODS"
            elif mode == "time":
                body["judgment_at"] = "2026-09-28T00:00:00"
            elif mode == "quantity":
                body["shipment_templates"][0]["lines"][0]["quantity"] = "2"
            else:
                body["shipment_templates"] = []
            with self.assertRaises(InventoryInputError):
                seal_run_cost_input(body)
            with self.assertRaises(ValidationError):
                self.validator("inventory_run_cost_input.schema.json").validate(
                    {**body, "content_hash": digest(body)}
                )

    def test_unchanged_input_is_deterministic(self):
        self.assertEqual(
            validate_run_cost_input(self.cost.input_document), self.cost.input_document
        )
        self.assertEqual(validate_manifest_document(self.parent), self.parent)

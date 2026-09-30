"""Run parent, guarded-order pricing, atomic storage and recovery without remote writes."""

from __future__ import annotations

import copy
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from dataclasses import replace
from decimal import localcontext
from pathlib import Path

from dsio_inventory_engine.infrastructure.sqlite import SqliteInventoryEvidenceUnitOfWork
from dsio_inventory_engine.inventory_contracts.run_cost import (
    seal_run_cost_input,
    validate_run_cost_input,
)
from dsio_inventory_engine.inventory_contracts.landed_cost_artifact import seal_shipment_input
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from dsio_inventory_engine.inventory_evidence import PersistInventoryResultBundleUseCase
from dsio_inventory_engine.inventory_evidence.contracts import ArtifactObject
from dsio_inventory_engine.run_inventory.landed_cost_pipeline import (
    RunCostEvidence,
    build_run_cost_evidence,
    run_manifest_reference,
)
from dsio_inventory_engine.run_inventory.psi_orchestrator import RunPsiBundleUseCase
from dsio_inventory_engine.run_inventory.result_pipeline import InventoryResultPipelineHandler
from dsio_inventory_engine.run_inventory.runtime_execution import InventoryRuntimeWorker
from tests.integration.test_psi_orchestrator_offline import DEPLOYMENT
from tests.integration.test_result_pipeline_offline import _Platform, _Resolver
from tests.support.run_cost_fixtures import CostResolver, bound_cost_command, rebind_cost_command
from tests.support.landed_cost_fixtures import rebind, reseal_projection, source_for


class RunCostPipelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="inventory-run-cost-")
        self.addCleanup(self.folder.cleanup)
        self.database = Path(self.folder.name) / "evidence.sqlite"
        self.store = SqliteInventoryEvidenceUnitOfWork(self.database)
        self.command, self.cost = bound_cost_command()
        self.cost_resolver = CostResolver(self.cost)
        self.commands = _Resolver(self.command)
        self.platform = _Platform()
        self.pipeline = InventoryResultPipelineHandler(
            command_resolver=self.commands,
            orchestrator=RunPsiBundleUseCase(DEPLOYMENT),
            persistence=PersistInventoryResultBundleUseCase(DEPLOYMENT, self.store),
            evidence_repository=self.store,
            landed_cost_resolver=self.cost_resolver,
        )

    def worker(self, **changes):
        return InventoryRuntimeWorker(
            **{
                "handler": self.pipeline,
                "platform": self.platform,
                "publication_acknowledger": self.pipeline,
                "run_result_verifier": self.pipeline,
                **changes,
            }
        )

    def calculate(self):
        run = RunPsiBundleUseCase(DEPLOYMENT).execute(self.command)
        evidence = build_run_cost_evidence(
            request=self.command.runtime_request,
            run=run,
            resolved=self.cost,
            cost_profile=self.command.cost_profile,
        )
        return run, evidence

    async def test_actual_claim_parent_children_golden_and_exact_recovery(self):
        result = await self.worker().execute(self.command.runtime_request)
        self.assertEqual(result["status"], "succeeded")
        manifest_ref = result["run_result_manifest_reference"]
        manifest = self.store.read_artifact(manifest_ref).document()
        math = next(
            child
            for child in self.platform.publications[0][1].inventory_result_bundle["result_children"]
            if child["result_kind"] == "RECOMMENDED_PSI"
            and child["strategy_type"] == "MATHEMATICAL"
        )
        metric = next(
            row
            for row in manifest["simulation_metrics"]
            if row["psi_child_result_id"] == math["child_result_id"]
        )
        # Independent hand arithmetic: 25*(10+1+2) USD + fixed freight 100 USD;
        # 5% duty on goods+insurance+freight; recoverable VAT excluded; FX=1500.
        self.assertEqual(metric["accepted_order_qty"], "25")
        self.assertEqual(metric["net_landed_cost"], "665625")
        self.assertEqual(metric["operating_cost"], "1250")
        self.assertEqual(metric["total_cost"], "666875")
        self.assertFalse(manifest["operational_cost_eligible"])
        for child in manifest["cost_children"]:
            self.assertEqual(
                child["binding"]["engine_run_id"], self.command.runtime_request.engine_run_id
            )
            self.assertEqual(
                child["binding"]["canonical_input_hash"], manifest["canonical_input_hash"]
            )
        self.assertEqual(
            self.platform.events[-1].payload_redacted["run_result_manifest_content_hash"],
            manifest["content_hash"],
        )
        outbox = self.store.find_outbox(self.command.runtime_request.engine_run_id, 1)
        self.assertEqual(outbox.message.payload()["run_result_manifest_reference"], manifest_ref)
        self.assertEqual(outbox.status, "PUBLISHED")
        reopened = SqliteInventoryEvidenceUnitOfWork(self.database)
        self.pipeline.evidence_repository = reopened
        recovered = await self.worker().execute(self.command.runtime_request)
        self.assertEqual(recovered["run_result_manifest_content_hash"], manifest["content_hash"])
        self.assertEqual(self.cost_resolver.calls, 1)
        self.assertEqual(self.commands.calls, 1)

    async def test_review_preserves_parent_without_publication(self):
        self.command, self.cost = bound_cost_command(action="REVIEW", approval="HIGH_VALUE")
        self.pipeline.command_resolver = _Resolver(self.command)
        self.pipeline.landed_cost_resolver = CostResolver(self.cost)
        result = await self.worker().execute(self.command.runtime_request)
        self.assertEqual(result["status"], "review_required")
        self.assertEqual(self.platform.publications, [])
        self.assertTrue(self.store.read_artifact(result["run_result_manifest_reference"]))
        self.assertEqual(
            self.store.find_outbox(self.command.runtime_request.engine_run_id, 1).status,
            "WITHHELD_FOR_REVIEW",
        )

    async def test_missing_cost_resolver_fails_before_any_artifact_or_publish(self):
        self.pipeline.landed_cost_resolver = None
        with self.assertRaisesRegex(InventoryInputError, "RUN_COST_RESOLVER_REQUIRED"):
            await self.worker().execute(self.command.runtime_request)
        self.assertIsNone(self.store.find_outbox(self.command.runtime_request.engine_run_id, 1))
        self.assertEqual(self.platform.publications, [])

    async def test_cost_bound_worker_requires_independent_stored_result_verifier(self):
        with self.assertRaisesRegex(InventoryInputError, "RUNTIME_RUN_RESULT_VERIFIER_REQUIRED"):
            await self.worker(run_result_verifier=None).execute(self.command.runtime_request)
        self.assertEqual(self.platform.publications, [])

    async def test_publication_failure_recovers_without_refetch_or_recalculation(self):
        self.platform.fail_publication = True
        with self.assertRaisesRegex(RuntimeError, "publication unavailable"):
            await self.worker().execute(self.command.runtime_request)
        self.assertEqual(len(self.store.list_pending()), 1)
        self.platform.fail_publication = False
        await self.worker().execute(self.command.runtime_request)
        self.assertEqual(self.cost_resolver.calls, 1)
        self.assertEqual(self.commands.calls, 1)

    async def test_corrupted_parent_blocks_recovery_and_publication(self):
        await self.worker().execute(self.command.runtime_request)
        self.platform.publications.clear()
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE inventory_result_artifact SET document_bytes=? WHERE artifact_reference=?",
                (b"{}", run_manifest_reference(self.command.runtime_request)),
            )
        with self.assertRaisesRegex(InventoryInputError, "ARTIFACT_BYTE_HASH_MISMATCH"):
            await self.worker().execute(self.command.runtime_request)
        self.assertEqual(self.platform.publications, [])
        self.assertEqual(self.cost_resolver.calls, 1)

    async def test_resealed_wrong_total_rejected_by_source_replay(self):
        await self.worker().execute(self.command.runtime_request)
        root = run_manifest_reference(self.command.runtime_request)
        parent = self.store.read_artifact(root).document()
        parent["simulation_metrics"][1]["total_cost"] = "1"
        parent["content_hash"] = digest(
            {key: value for key, value in parent.items() if key != "content_hash"}
        )
        obj = ArtifactObject.from_document(
            reference=root, contract_key="inventory.run_result_manifest", document=parent
        )
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE inventory_result_artifact SET content_hash=?,byte_content_hash=?,document_bytes=? WHERE artifact_reference=?",
                (obj.content_hash, obj.byte_content_hash, obj.document_bytes, root),
            )
        with self.assertRaisesRegex(InventoryInputError, "RUN_RESULT_MANIFEST_REPLAY_MISMATCH"):
            await self.worker().execute(self.command.runtime_request)

    def test_late_parent_conflict_rolls_back_psi_cost_and_outbox(self):
        run, evidence = self.calculate()
        parent = {**evidence.manifest, "cost_currency": "USD"}
        parent["content_hash"] = digest(
            {key: value for key, value in parent.items() if key != "content_hash"}
        )
        self.store.commit_artifacts(
            [
                ArtifactObject.from_document(
                    reference=parent["artifact_reference"],
                    contract_key="inventory.run_result_manifest",
                    document=parent,
                )
            ]
        )
        with self.assertRaisesRegex(InventoryInputError, "ARTIFACT_WRITE_ONCE_CONFLICT"):
            PersistInventoryResultBundleUseCase(DEPLOYMENT, self.store).execute(
                run,
                strategy_execution_plan=self.command.runtime_request.strategy_execution_plan,
                runtime_request=self.command.runtime_request,
                run_cost_evidence=evidence,
            )
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM inventory_result_artifact").fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM inventory_result_publication_outbox"
                ).fetchone()[0],
                0,
            )

    def test_fake_parent_is_rejected_before_commit(self):
        run, evidence = self.calculate()
        fake = {**evidence.manifest, "simulation_metrics": []}
        with self.assertRaisesRegex(InventoryInputError, "RUN_COST_ARTIFACT_SET_MISMATCH"):
            PersistInventoryResultBundleUseCase(DEPLOYMENT, self.store).execute(
                run,
                strategy_execution_plan=self.command.runtime_request.strategy_execution_plan,
                runtime_request=self.command.runtime_request,
                run_cost_evidence=RunCostEvidence(fake, evidence.artifacts),
            )
        self.assertIsNone(self.store.find_outbox(self.command.runtime_request.engine_run_id, 1))

    def test_cost_and_hash_are_independent_of_decimal_ambient_precision(self):
        _, first = self.calculate()
        with localcontext() as context:
            context.prec = 8
            _, second = self.calculate()
        self.assertEqual(first.manifest, second.manifest)

    def test_evidence_budget_includes_parent_before_persistence(self):
        run, evidence = self.calculate()
        parent_bytes = len(evidence.artifacts[-1].document_bytes)
        without_parent = sum(len(row.document_bytes) for row in evidence.artifacts[:-1])
        limit = without_parent + parent_bytes // 2
        with (
            patch(
                "dsio_inventory_engine.run_inventory.landed_cost_pipeline._MAX_EVIDENCE_BYTES",
                limit,
            ),
            self.assertRaisesRegex(InventoryInputError, "RUN_COST_EVIDENCE_SIZE_LIMIT"),
        ):
            build_run_cost_evidence(
                request=self.command.runtime_request,
                run=run,
                resolved=self.cost,
                cost_profile=self.command.cost_profile,
            )
        self.assertIsNone(self.store.find_outbox(self.command.runtime_request.engine_run_id, 1))

    def test_unknown_or_overlapping_cost_profile_attribution_fails_closed(self):
        for value in ("GOODS", "FREIGHT", "DUTY", "UNKNOWN"):
            with self.subTest(value=value):
                body = copy.deepcopy(self.cost.input_document)
                body.pop("content_hash")
                body["profile_component_attribution"]["variable_order_cost_per_unit"] = value
                with self.assertRaises(InventoryInputError):
                    seal_run_cost_input(body)

    def test_unsealed_cost_input_and_wrong_claim_hash_are_rejected(self):
        bad = {**self.cost.input_document, "content_hash": "f" * 64}
        with self.assertRaisesRegex(InventoryInputError, "RUN_COST_INPUT_HASH_MISMATCH"):
            validate_run_cost_input(bad)
        run = RunPsiBundleUseCase(DEPLOYMENT).execute(self.command)
        with self.assertRaisesRegex(InventoryInputError, "RUN_COST_INPUT_HASH_MISMATCH"):
            build_run_cost_evidence(
                request=self.command.runtime_request,
                run=run,
                resolved=replace(self.cost, input_document=bad),
                cost_profile=self.command.cost_profile,
            )

    def test_missing_item_template_is_evidenced_not_zero_cost(self):
        body = copy.deepcopy(self.cost.input_document)
        body.pop("content_hash")
        template = body["shipment_templates"][0]
        template.pop("content_hash")
        template["lines"][0]["item_id"] = "OTHER-ITEM"
        body["shipment_templates"] = [seal_shipment_input(template)]
        inputs = seal_run_cost_input(body)
        self.command, self.cost = rebind_cost_command(
            self.command, inputs, self.cost.projection.to_dict()
        )
        _, evidence = self.calculate()
        baseline, math = evidence.manifest["simulation_metrics"][:2]
        self.assertEqual(baseline["total_cost"], "0")  # Legitimate no-order baseline.
        self.assertEqual(math["status"], "NOT_AVAILABLE")
        self.assertIsNone(math["total_cost"])
        self.assertEqual(math["unpriced_orders"][0]["item_id"], "ITEM-A")
        self.assertEqual(math["unpriced_orders"][0]["accepted_order_qty"], "25")
        self.assertEqual(evidence.manifest["cost_children"], [])

    def test_missing_fx_preserves_failed_cost_child_and_null_total(self):
        projection = self.cost.projection.to_dict()
        source_for(projection, "CUSTOMS_FX")["records"] = []
        projection = reseal_projection(projection)
        body = copy.deepcopy(self.cost.input_document)
        body.pop("content_hash")
        body["revision_set_content_hash"] = projection["revision_set_content_hash"]
        body["shipment_templates"] = [
            rebind(projection, template) for template in body["shipment_templates"]
        ]
        self.command, self.cost = rebind_cost_command(
            self.command, seal_run_cost_input(body), projection
        )
        _, evidence = self.calculate()
        self.assertIsNone(evidence.manifest["simulation_metrics"][1]["total_cost"])
        self.assertEqual(
            evidence.manifest["cost_children"][0]["calculation_status"], "NOT_CALCULABLE"
        )

    def test_ppo_failure_does_not_change_mathematical_cost(self):
        self.command = replace(self.command, ppo_challengers={})
        run, evidence = self.calculate()
        ppo = next(
            row for row in run.result_bundle["result_children"] if row["strategy_type"] == "DEEP_RL"
        )
        self.assertEqual(ppo["status"], "FAILED")
        metric = next(
            row
            for row in evidence.manifest["simulation_metrics"]
            if row["psi_child_result_id"] == ppo["child_result_id"]
        )
        self.assertIsNone(metric["total_cost"])
        self.assertEqual(evidence.manifest["simulation_metrics"][1]["total_cost"], "666875")

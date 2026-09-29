"""Claim → PSI → durable Artifact → Platform callback pipeline verification."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dsio_inventory_engine.infrastructure.sqlite import SqliteInventoryEvidenceUnitOfWork
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from dsio_inventory_engine.inventory_evidence import PersistInventoryResultBundleUseCase
from dsio_inventory_engine.run_inventory.psi_orchestrator import RunPsiBundleUseCase
from dsio_inventory_engine.run_inventory.result_pipeline import InventoryResultPipelineHandler
from dsio_inventory_engine.run_inventory.runtime_execution import (
    InventoryRuntimeStageEventReceipt,
    InventoryRuntimeWorker,
)
from tests.integration.test_psi_orchestrator_offline import DEPLOYMENT, _bound_command


class _Resolver:
    def __init__(self, command) -> None:
        self.command = command
        self.calls = 0

    async def resolve(self, request):
        self.calls += 1
        if request.canonical_hash != self.command.runtime_request.canonical_hash:
            raise AssertionError("unexpected claim")
        return self.command


class _Platform:
    def __init__(self) -> None:
        self.events = []
        self.publications = []
        self.row_version = 2
        self.fail_publication = False

    async def append_event(self, request, event):
        self.events.append(event)
        self.row_version += 1
        return InventoryRuntimeStageEventReceipt(
            site_row_version=self.row_version,
            run_status="running" if event.status == "running" else event.status,
            replayed=False,
        )

    async def publish(self, request, *, expected_site_row_version, result):
        self.publications.append((expected_site_row_version, result))
        if self.fail_publication:
            raise RuntimeError("platform publication unavailable")
        self.row_version += 1
        return {
            "engine_run_id": request.engine_run_id,
            "effective_run_id": request.engine_run_id,
            "site_status": "succeeded",
            "cycle_status": "succeeded",
            "site_row_version": self.row_version,
            "replayed": False,
        }


class ResultPipelineOfflineTests(unittest.IsolatedAsyncioTestCase):
    async def test_claim_calculation_persistence_callback_and_publish(self) -> None:
        command = _bound_command(action="ALLOW", approval="AUTO")
        resolver = _Resolver(command)
        platform = _Platform()
        with tempfile.TemporaryDirectory(prefix="inventory-runtime-") as directory:
            repository = SqliteInventoryEvidenceUnitOfWork(Path(directory) / "evidence.sqlite3")
            pipeline = InventoryResultPipelineHandler(
                command_resolver=resolver,
                orchestrator=RunPsiBundleUseCase(DEPLOYMENT),
                persistence=PersistInventoryResultBundleUseCase(DEPLOYMENT, repository),
                evidence_repository=repository,
            )
            result = await InventoryRuntimeWorker(
                handler=pipeline,
                platform=platform,
                publication_acknowledger=pipeline,
            ).execute(command.runtime_request)

            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(resolver.calls, 1)
            self.assertEqual(len(platform.publications), 1)
            self.assertEqual(
                [event.stage for event in platform.events],
                [
                    "admission",
                    "input_resolution",
                    "psi_execution",
                    "psi_execution",
                    "artifact_persistence",
                    "publication",
                ],
            )
            evidence_event = platform.events[-2]
            self.assertTrue(evidence_event.payload_redacted["outbox_staged"])
            outbox_id = platform.publications[0][1].publication_outbox_id
            self.assertEqual(repository.read_outbox(outbox_id).status, "PUBLISHED")

            replay = await InventoryRuntimeWorker(
                handler=pipeline,
                platform=platform,
                publication_acknowledger=pipeline,
            ).execute(command.runtime_request)
            self.assertEqual(
                replay["inventory_result_content_hash"], result["inventory_result_content_hash"]
            )
            self.assertEqual(resolver.calls, 1)
            self.assertEqual(len(platform.publications), 2)
            self.assertEqual(platform.events[-2].stage, "artifact_recovery")

    async def test_review_result_is_persisted_but_not_automatically_published(self) -> None:
        command = _bound_command()
        platform = _Platform()
        with tempfile.TemporaryDirectory(prefix="inventory-runtime-") as directory:
            repository = SqliteInventoryEvidenceUnitOfWork(Path(directory) / "evidence.sqlite3")
            pipeline = InventoryResultPipelineHandler(
                command_resolver=_Resolver(command),
                orchestrator=RunPsiBundleUseCase(DEPLOYMENT),
                persistence=PersistInventoryResultBundleUseCase(DEPLOYMENT, repository),
                evidence_repository=repository,
            )
            result = await InventoryRuntimeWorker(
                handler=pipeline,
                platform=platform,
                publication_acknowledger=pipeline,
            ).execute(command.runtime_request)

            self.assertEqual(result["status"], "review_required")
            self.assertEqual(platform.publications, [])
            self.assertEqual(repository.list_pending(), ())
            withheld = repository.find_outbox(
                command.runtime_request.engine_run_id,
                command.runtime_request.value["attempt_no"],
            )
            self.assertEqual(withheld.status, "WITHHELD_FOR_REVIEW")
            with self.assertRaisesRegex(
                InventoryInputError,
                "OUTBOX_PUBLICATION_NOT_ALLOWED",
            ):
                repository.mark_published(
                    withheld.message.outbox_id,
                    expected_payload_content_hash=withheld.message.payload_content_hash,
                )
            self.assertEqual(platform.events[-1].message_code, "INVENTORY_RESULT_REVIEW_REQUIRED")

    async def test_platform_failure_reuses_pending_outbox_without_recalculation(self) -> None:
        command = _bound_command(action="ALLOW", approval="AUTO")
        resolver = _Resolver(command)
        platform = _Platform()
        platform.fail_publication = True
        with tempfile.TemporaryDirectory(prefix="inventory-runtime-") as directory:
            repository = SqliteInventoryEvidenceUnitOfWork(Path(directory) / "evidence.sqlite3")
            pipeline = InventoryResultPipelineHandler(
                command_resolver=resolver,
                orchestrator=RunPsiBundleUseCase(DEPLOYMENT),
                persistence=PersistInventoryResultBundleUseCase(DEPLOYMENT, repository),
                evidence_repository=repository,
            )
            worker = InventoryRuntimeWorker(
                handler=pipeline,
                platform=platform,
                publication_acknowledger=pipeline,
            )

            with self.assertRaisesRegex(RuntimeError, "platform publication unavailable"):
                await worker.execute(command.runtime_request)
            pending = repository.list_pending()
            self.assertEqual(len(pending), 1)
            self.assertEqual(resolver.calls, 1)

            platform.fail_publication = False
            recovered = await worker.execute(command.runtime_request)
            self.assertEqual(recovered["status"], "succeeded")
            self.assertEqual(resolver.calls, 1)
            self.assertEqual(
                repository.read_outbox(pending[0].message.outbox_id).status, "PUBLISHED"
            )


if __name__ == "__main__":
    unittest.main()

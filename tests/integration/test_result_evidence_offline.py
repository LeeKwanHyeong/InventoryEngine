"""Offline durable Result Bundle Artifact and Outbox verification."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dsio_inventory_engine.infrastructure.sqlite import SqliteInventoryEvidenceUnitOfWork
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from dsio_inventory_engine.inventory_evidence import (
    ArtifactObject,
    OutboxMessage,
    PersistInventoryResultBundleUseCase,
    ReadAndVerifyInventoryResultBundleUseCase,
)
from dsio_inventory_engine.run_inventory.psi_orchestrator import RunPsiBundleUseCase
from tests.integration.test_psi_orchestrator_offline import DEPLOYMENT, _bound_command


class ResultEvidenceOfflineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="inventory-evidence-")
        self.database = Path(self.temporary.name) / "evidence.sqlite3"
        self.repository = SqliteInventoryEvidenceUnitOfWork(self.database)
        self.command = _bound_command(action="ALLOW", approval="AUTO")
        self.run = RunPsiBundleUseCase(DEPLOYMENT).execute(self.command)
        self.persistence = PersistInventoryResultBundleUseCase(DEPLOYMENT, self.repository)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_append_only_write_read_back_and_outbox_replay(self) -> None:
        first = self.persistence.execute(
            self.run,
            strategy_execution_plan=self.command.runtime_request.strategy_execution_plan,
        )
        replay = self.persistence.execute(
            self.run,
            strategy_execution_plan=self.command.runtime_request.strategy_execution_plan,
        )
        verified = ReadAndVerifyInventoryResultBundleUseCase(
            DEPLOYMENT,
            self.repository,
        ).execute(
            first.bundle_reference,
            strategy_execution_plan=self.command.runtime_request.strategy_execution_plan,
        )

        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(first.bundle_content_hash, self.run.result_bundle["content_hash"])
        self.assertEqual(verified.result_bundle, self.run.result_bundle)
        self.assertEqual(verified.artifact_count, first.artifact_count)
        self.assertEqual(len(self.repository.list_pending()), 1)
        published = self.repository.mark_published(
            first.outbox.message.outbox_id,
            expected_payload_content_hash=first.outbox.message.payload_content_hash,
        )
        published_replay = self.repository.mark_published(
            first.outbox.message.outbox_id,
            expected_payload_content_hash=first.outbox.message.payload_content_hash,
        )
        self.assertEqual((published.status, published.row_version), ("PUBLISHED", 2))
        self.assertEqual(published_replay, published)
        self.assertEqual(self.repository.list_pending(), ())

    def test_concurrent_exact_replay_serializes_to_one_write(self) -> None:
        def persist():
            return self.persistence.execute(
                self.run,
                strategy_execution_plan=self.command.runtime_request.strategy_execution_plan,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            receipts = list(executor.map(lambda _: persist(), range(2)))

        self.assertEqual(sorted(receipt.replayed for receipt in receipts), [False, True])
        with sqlite3.connect(self.database) as connection:
            artifact_count = connection.execute(
                "SELECT COUNT(*) FROM inventory_result_artifact"
            ).fetchone()[0]
            outbox_count = connection.execute(
                "SELECT COUNT(*) FROM inventory_result_publication_outbox"
            ).fetchone()[0]
        self.assertEqual(artifact_count, receipts[0].artifact_count)
        self.assertEqual(outbox_count, 1)

    def test_write_once_conflict_rolls_back_the_whole_batch(self) -> None:
        original = _artifact("artifact:inventory:RUN-1:1:fixed", "one")
        outbox = _outbox("RUN-1", 1)
        self.repository.commit([original], outbox)
        new = _artifact("artifact:inventory:RUN-1:1:new", "new")
        conflicting = _artifact(original.reference, "different")

        with self.assertRaisesRegex(InventoryInputError, "ARTIFACT_WRITE_ONCE_CONFLICT"):
            self.repository.commit([new, conflicting], outbox)
        with self.assertRaisesRegex(InventoryInputError, "ARTIFACT_NOT_FOUND"):
            self.repository.read_artifact(new.reference)
        self.assertEqual(self.repository.read_artifact(original.reference), original)

    def test_read_back_rejects_tampered_persisted_bytes(self) -> None:
        persisted = self.persistence.execute(
            self.run,
            strategy_execution_plan=self.command.runtime_request.strategy_execution_plan,
        )
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE inventory_result_artifact SET document_bytes = ? WHERE artifact_reference = ?",
                (b"{}", persisted.bundle_reference),
            )
            connection.commit()

        with self.assertRaisesRegex(InventoryInputError, "ARTIFACT_BYTE_HASH_MISMATCH"):
            ReadAndVerifyInventoryResultBundleUseCase(DEPLOYMENT, self.repository).execute(
                persisted.bundle_reference,
                strategy_execution_plan=self.command.runtime_request.strategy_execution_plan,
            )


def _artifact(reference: str, value: str) -> ArtifactObject:
    body = {
        "contract_id": "test-artifact-v1",
        "contract_version": "1.0.0",
        "value": value,
    }
    return ArtifactObject.from_document(
        reference=reference,
        contract_key="inventory.test_artifact",
        document={**body, "content_hash": digest(body)},
    )


def _outbox(engine_run_id: str, attempt_no: int) -> OutboxMessage:
    return OutboxMessage.from_body(
        {
            "contract_id": "test-outbox-v1",
            "contract_version": "1.0.0",
            "outbox_id": "IOO-TEST-1",
            "engine_run_id": engine_run_id,
            "attempt_no": attempt_no,
            "automatic_publish_allowed": True,
        }
    )


if __name__ == "__main__":
    unittest.main()

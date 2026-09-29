"""Local-only atomic storage, idempotency, corruption and independent Landed Cost replay."""

from __future__ import annotations

import copy
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dsio_inventory_engine.calculate_landed_cost.artifacts import (
    CHILD_KEY,
    PersistLandedCostChildArtifactUseCase,
    ReadAndVerifyLandedCostChildArtifactUseCase,
    build_landed_cost_child,
)
from dsio_inventory_engine.infrastructure.sqlite.inventory_evidence import (
    SqliteInventoryEvidenceUnitOfWork,
)
from dsio_inventory_engine.inventory_contracts.landed_cost_artifact import (
    LandedCostShipmentInput,
    seal_shipment_input,
)
from dsio_inventory_engine.inventory_contracts.trade_cost_projection import (
    TradeCostRevisionSetProjection,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from dsio_inventory_engine.inventory_evidence.contracts import ArtifactObject
from tests.support.landed_cost_fixtures import landed_fixture


class LandedCostEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.database = Path(self.folder.name) / "evidence.sqlite"
        self.store = SqliteInventoryEvidenceUnitOfWork(self.database)
        projection, shipment = landed_fixture()
        self.projection = TradeCostRevisionSetProjection.from_dict(projection)
        self.shipment = LandedCostShipmentInput.from_dict(shipment)

    def write(self):
        return PersistLandedCostChildArtifactUseCase(self.store).execute(
            projection=self.projection, shipment=self.shipment
        )

    def read(self, receipt, **changes):
        return ReadAndVerifyLandedCostChildArtifactUseCase(self.store).execute(
            **{
                "reference": receipt.reference,
                "expected_content_hash": receipt.content_hash,
                "expected_binding": self.shipment.to_dict()["binding"],
                "expected_shipment_hash": receipt.input_content_hash,
                **changes,
            }
        )

    def test_reopen_replay_and_no_publication_outbox(self):
        receipt = self.write()
        self.assertFalse(receipt.replayed)
        self.store = SqliteInventoryEvidenceUnitOfWork(self.database)
        result = self.read(receipt)
        self.assertEqual(result["totals"]["net_landed_cost_amount"], "177825")
        self.assertTrue(self.write().replayed)
        self.assertEqual(self.store.list_pending(), ())
        self.assertIsNone(self.store.find_outbox("IO-RUN-LC-1", 1))
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM inventory_result_artifact").fetchone()[0],
                3,
            )

    def test_same_reference_different_input_conflicts(self):
        self.write()
        body = self.shipment.to_dict()
        body.pop("content_hash")
        body["lines"][0]["quantity"] = "11"
        self.shipment = LandedCostShipmentInput.from_dict(seal_shipment_input(body))
        with self.assertRaisesRegex(InventoryInputError, "ARTIFACT_WRITE_ONCE_CONFLICT"):
            self.write()

    def test_late_conflict_rolls_back_earlier_artifact_inserts(self):
        child = build_landed_cost_child(self.projection, self.shipment)
        conflicting = {**child, "cost_currency": "USD"}
        conflicting["content_hash"] = digest(
            {key: value for key, value in conflicting.items() if key != "content_hash"}
        )
        self.store.commit_artifacts(
            [
                ArtifactObject.from_document(
                    reference=child["artifact_reference"],
                    contract_key=CHILD_KEY,
                    document=conflicting,
                )
            ]
        )
        with self.assertRaisesRegex(InventoryInputError, "ARTIFACT_WRITE_ONCE_CONFLICT"):
            self.write()
        with self.assertRaisesRegex(InventoryInputError, "ARTIFACT_NOT_FOUND"):
            self.store.read_artifact(child["shipment_reference"])
        with self.assertRaisesRegex(InventoryInputError, "ARTIFACT_NOT_FOUND"):
            self.store.read_artifact(child["projection_reference"])

    def test_concurrent_exact_replay_writes_one_artifact_set(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            receipts = list(executor.map(lambda _: self.write(), range(2)))
        self.assertEqual(sorted(receipt.replayed for receipt in receipts), [False, True])
        self.assertEqual(receipts[0].content_hash, receipts[1].content_hash)

    def test_external_claim_binding_and_hash_cannot_be_substituted(self):
        receipt = self.write()
        for key, value in (
            ("engine_run_id", "other-run"),
            ("attempt_no", 2),
            ("project_id", "other-project"),
            ("canonical_input_hash", "f" * 64),
            ("revision_set_content_hash", "f" * 64),
            ("valuation_date", "2026-09-27"),
        ):
            with self.subTest(key=key):
                binding = {**self.shipment.to_dict()["binding"], key: value}
                with self.assertRaisesRegex(
                    InventoryInputError, "LANDED_COST_RUN_BINDING_MISMATCH"
                ):
                    self.read(receipt, expected_binding=binding)
        with self.assertRaisesRegex(InventoryInputError, "LANDED_COST_INPUT_HASH_MISMATCH"):
            self.read(receipt, expected_shipment_hash="f" * 64)

    def test_byte_corruption_detected_on_read(self):
        receipt = self.write()
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE inventory_result_artifact SET document_bytes=? WHERE artifact_reference=?",
                (b"{}", receipt.reference),
            )
        with self.assertRaisesRegex(InventoryInputError, "ARTIFACT_BYTE_HASH_MISMATCH"):
            self.read(receipt)

    def test_resealed_wrong_totals_fail_recalculation_not_just_hash_check(self):
        receipt = self.write()
        child = self.read(receipt)
        child["totals"]["net_landed_cost_amount"] = "1"
        child["content_hash"] = digest(
            {key: value for key, value in child.items() if key != "content_hash"}
        )
        artifact = ArtifactObject.from_document(
            reference=receipt.reference, contract_key=CHILD_KEY, document=child
        )
        # Deliberate local test corruption; no production DB is accessed.
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE inventory_result_artifact SET content_hash=?, byte_content_hash=?, document_bytes=? WHERE artifact_reference=?",
                (
                    artifact.content_hash,
                    artifact.byte_content_hash,
                    artifact.document_bytes,
                    artifact.reference,
                ),
            )
        with self.assertRaisesRegex(InventoryInputError, "LANDED_COST_REPLAY_MISMATCH"):
            self.read(receipt, expected_content_hash=artifact.content_hash)

    def test_resealed_automatic_publish_flag_is_forbidden(self):
        receipt = self.write()
        child = self.read(receipt)
        child["automatic_publish_allowed"] = True
        child["content_hash"] = digest(
            {key: value for key, value in child.items() if key != "content_hash"}
        )
        artifact = ArtifactObject.from_document(
            reference=receipt.reference, contract_key=CHILD_KEY, document=child
        )
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "UPDATE inventory_result_artifact SET content_hash=?, byte_content_hash=?, document_bytes=? WHERE artifact_reference=?",
                (
                    artifact.content_hash,
                    artifact.byte_content_hash,
                    artifact.document_bytes,
                    artifact.reference,
                ),
            )
        with self.assertRaisesRegex(InventoryInputError, "LANDED_COST_PUBLICATION_FORBIDDEN"):
            self.read(receipt, expected_content_hash=artifact.content_hash)

    def test_empty_child_and_unavailable_calculator_version_are_rejected(self):
        receipt = self.write()
        original = self.read(receipt)
        for kind, reason in (
            ("empty", "LANDED_COST_CHILD_ROW_COUNT_INVALID"),
            ("version", "LANDED_COST_CALCULATOR_VERSION_UNAVAILABLE"),
        ):
            with self.subTest(kind=kind):
                child = copy.deepcopy(original)
                if kind == "empty":
                    child.update(row_count=0, rows=[])
                else:
                    child["calculator_binding"]["implementation_content_hash"] = "f" * 64
                child["content_hash"] = digest(
                    {key: value for key, value in child.items() if key != "content_hash"}
                )
                artifact = ArtifactObject.from_document(
                    reference=receipt.reference, contract_key=CHILD_KEY, document=child
                )
                with sqlite3.connect(self.database) as connection:
                    connection.execute(
                        "UPDATE inventory_result_artifact SET content_hash=?, byte_content_hash=?, document_bytes=? WHERE artifact_reference=?",
                        (
                            artifact.content_hash,
                            artifact.byte_content_hash,
                            artifact.document_bytes,
                            artifact.reference,
                        ),
                    )
                with self.assertRaisesRegex(InventoryInputError, reason):
                    self.read(receipt, expected_content_hash=artifact.content_hash)

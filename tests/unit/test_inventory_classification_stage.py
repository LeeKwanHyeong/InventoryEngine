"""Run-bound classification stage orchestration tests."""

import unittest

from dsio_inventory_engine.classify_inventory.application import InventoryScope
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from dsio_inventory_engine.run_inventory.classification_stage import (
    InventoryClassificationStageUseCase,
    InventoryRunContext,
)


class FakeLifecycle:
    def __init__(self, receipt=None, error=None):
        self.receipt = receipt
        self.error = error
        self.calls = []

    async def execute(self, scope, *, approved_by, publish=False):
        self.calls.append((scope, approved_by, publish))
        if self.error:
            raise self.error
        return self.receipt


class FakeRecorder:
    def __init__(self):
        self.events = []

    async def append(self, context, event):
        self.events.append((context, event))
        return {"publication_status": "published"}


def context() -> InventoryRunContext:
    return InventoryRunContext(
        engine_run_id="00000000-0000-0000-0000-000000000010",
        tenant_id="default",
        project_id="project-a",
        scope=InventoryScope("project-a", "DSE", "C100", "V100", "V100"),
    )


def receipt() -> dict:
    return {
        "publication_status": "published",
        "classification_snapshot_id": "00000000-0000-0000-0000-000000000011",
        "snapshot_revision": 2,
        "content_hash": "a" * 64,
        "item_result_contract_version": "1.1.0",
        "effective_policy_contract_version": "1.0.0",
        "effective_policy_content_hash": "b" * 64,
        "item_result_count": 7000,
        "eligible_sku_count": 7000,
        "classified_sku_count": 6009,
        "unclassified_sku_count": 991,
        "exact_replay": False,
        "database_writes": True,
        "run_claimed": False,
    }


class InventoryClassificationStageTests(unittest.IsolatedAsyncioTestCase):
    async def test_publication_keeps_run_running_for_following_psi_stages(self):
        lifecycle = FakeLifecycle(receipt=receipt())
        recorder = FakeRecorder()

        result = await InventoryClassificationStageUseCase(lifecycle, recorder).execute(
            context(), approved_by="admin"
        )

        self.assertEqual([event.status for _, event in recorder.events], ["running", "running"])
        self.assertEqual(lifecycle.calls[0][2], True)
        self.assertTrue(result["run_claimed"] and result["stage_event_persisted"])
        self.assertEqual(result["engine_run_id"], context().engine_run_id)
        payload = recorder.events[-1][1].payload_redacted
        self.assertEqual(payload["classified_sku_count"], 6009)
        self.assertNotIn("segments", payload)
        self.assertNotIn("items", payload)

    async def test_publication_failure_appends_failed_event_and_propagates(self):
        recorder = FakeRecorder()
        lifecycle = FakeLifecycle(error=InventoryInputError("CLASSIFICATION_CONFIG_HASH_MISMATCH"))

        with self.assertRaisesRegex(InventoryInputError, "CLASSIFICATION_CONFIG_HASH_MISMATCH"):
            await InventoryClassificationStageUseCase(lifecycle, recorder).execute(
                context(), approved_by="admin"
            )

        self.assertEqual([event.status for _, event in recorder.events], ["running", "failed"])
        self.assertEqual(
            recorder.events[-1][1].message_code,
            "CLASSIFICATION_CONFIG_HASH_MISMATCH",
        )

    async def test_dry_run_receipt_cannot_be_recorded_as_success(self):
        value = receipt()
        value["publication_status"] = "dry_run"
        recorder = FakeRecorder()

        with self.assertRaisesRegex(
            InventoryInputError,
            "INVENTORY_CLASSIFICATION_SNAPSHOT_NOT_PUBLISHED",
        ):
            await InventoryClassificationStageUseCase(
                FakeLifecycle(receipt=value), recorder
            ).execute(context(), approved_by="admin")

        self.assertEqual(recorder.events[-1][1].status, "failed")


if __name__ == "__main__":
    unittest.main()

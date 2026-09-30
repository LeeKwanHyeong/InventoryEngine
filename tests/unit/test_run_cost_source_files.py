"""Exact pricing snapshot reads reject scope/hash substitution and path indirection."""

import tempfile
import unittest
from pathlib import Path

from dsio_inventory_engine.infrastructure.run_cost_files import FileRunCostInputReader
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, canonical_json
from dsio_inventory_engine.run_inventory.landed_cost_pipeline import SealedRunLandedCostResolver
from tests.support.run_cost_fixtures import bound_cost_command


class RunCostSourceFileTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.command, self.cost = bound_cost_command()
        self.document = self.cost.input_document
        self.path = self.root / (self.document["snapshot_id"] + ".json")
        self.path.write_text(canonical_json(self.document), encoding="utf-8")
        self.reader = FileRunCostInputReader(self.root)
        self.arguments = {
            "snapshot_id": self.document["snapshot_id"],
            "content_hash": self.document["content_hash"],
            "tenant_id": self.document["tenant_id"],
            "project_id": self.document["project_id"],
        }

    async def test_exact_reader_and_sealed_projection_resolver(self):
        expected = self.cost

        class Projection:
            async def resolve(self, request):
                return expected.projection

        resolved = await SealedRunLandedCostResolver(
            inputs=self.reader, projection=Projection()
        ).resolve(self.command.runtime_request)
        self.assertEqual(resolved.input_document, expected.input_document)
        self.assertEqual(resolved.projection, expected.projection)

    async def test_hash_tenant_project_or_missing_snapshot_never_falls_back(self):
        for key, value in (
            ("content_hash", "f" * 64),
            ("tenant_id", "other"),
            ("project_id", "other"),
            ("snapshot_id", "NOT-EXISTS"),
            ("snapshot_id", "../escape"),
        ):
            with self.subTest(key=key, value=value):
                with self.assertRaises(InventoryInputError):
                    await self.reader.read(**{**self.arguments, key: value})

    async def test_symlink_snapshot_is_not_read(self):
        target = self.root / "other.json"
        self.path.rename(target)
        self.path.symlink_to(target)
        with self.assertRaisesRegex(InventoryInputError, "RUN_COST_INPUT_NOT_FOUND"):
            await self.reader.read(**self.arguments)

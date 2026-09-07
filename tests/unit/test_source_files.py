"""In-process coverage of supplied files; no real DB."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from source_fixtures import mapped
from dsio_inventory_engine.entrypoints.source_input import prepare_source
from dsio_inventory_engine.infrastructure.source_files import (
    FileSourceSnapshotReader,
)
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.source import (
    SourceInputRequest,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError


class SourceFileTests(unittest.IsolatedAsyncioTestCase):
    async def test_file_reader_preserves_pinned_forecast(self):
        req, sources = mapped()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for s in sources.values():
                (root / (s["snapshot_id"] + ".json")).write_text(json.dumps(s))
            files = FileSourceSnapshotReader(root)
            forecast = await files.read_snapshot("forecast", sources["forecast"]["snapshot_id"])
            self.assertEqual(
                forecast.to_dict()["content_hash"], sources["forecast"]["content_hash"]
            )
            output = await prepare_source(
                SourceInputRequest.from_dict(req), DeploymentScope("DSE", "DEVELOPMENT"), root
            )
            self.assertEqual(output["status"], "SOURCE_INPUT_PREPARED_LOCALLY")

    async def test_file_reader_rejects_symbolic_links(self):
        _, sources = mapped()
        s = sources["forecast"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original.json"
            original.write_text(json.dumps(s))
            (root / (s["snapshot_id"] + ".json")).symlink_to(original)
            with self.assertRaisesRegex(InventoryInputError, "SOURCE_SNAPSHOT_NOT_FOUND"):
                await FileSourceSnapshotReader(root).read_snapshot("forecast", s["snapshot_id"])

    async def test_file_reader_rejects_wrong_snapshot_type(self):
        _, sources = mapped()
        s = sources["forecast"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / (s["snapshot_id"] + ".json")).write_text(json.dumps(s))
            with self.assertRaisesRegex(InventoryInputError, "SOURCE_IDENTITY_MISMATCH"):
                await FileSourceSnapshotReader(root).read_snapshot("master", s["snapshot_id"])

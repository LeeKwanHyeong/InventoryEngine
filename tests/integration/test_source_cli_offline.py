"""Real local file/CLI chain, never connects to PostgreSQL."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests/support"))
from source_fixtures import mapped
from dsio_inventory_engine.infrastructure.source_files import FileSourceSnapshotReader
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError

ROOT = Path(__file__).resolve().parents[2]


class SourceCliTests(unittest.IsolatedAsyncioTestCase):
    def fixture_files(self, root):
        req, sources = mapped()
        path = root / "request.json"
        path.write_text(json.dumps(req))
        for source in sources.values():
            (root / (source["snapshot_id"] + ".json")).write_text(json.dumps(source))
        return path, sources

    def cli(self, path, root, *extra):
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "dsio_inventory_engine",
                "prepare-source",
                "--request",
                str(path),
                "--snapshot-root",
                str(root),
                *extra,
            ],
            cwd=ROOT,
            env={
                **os.environ,
                "PYTHONPATH": str(ROOT / "src"),
                "PYTHONDONTWRITEBYTECODE": "1",
                "IO_COMPANY_CD": "DSE",
                "IO_ENVIRONMENT": "DEVELOPMENT",
                "IO_POSTGRES_DSN": "",
            },
            capture_output=True,
            text=True,
            timeout=15,
        )

    async def test_process_reads_pins_and_preserves_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, sources = self.fixture_files(root)
            a, b = self.cli(path, root), self.cli(path, root)
            self.assertEqual(a.returncode, 0, a.stderr + a.stdout)
            self.assertEqual(a.stdout, b.stdout)
            result = json.loads(a.stdout)
            self.assertEqual(result["status"], "SOURCE_INPUT_PREPARED_LOCALLY")
            self.assertFalse(result["database_writes"] or result["evidence_persisted"])
            self.assertEqual(
                result["source_snapshots"]["forecast"]["content_hash"],
                sources["forecast"]["content_hash"],
            )

    async def test_missing_file_tamper_and_db_optin_fail_safely(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, sources = self.fixture_files(root)
            report = self.cli(path, root, "--read-postgres")
            self.assertEqual(json.loads(report.stdout)["error_code"], "IO_POSTGRES_DSN_REQUIRED")
            forecast = root / (sources["forecast"]["snapshot_id"] + ".json")
            sources["forecast"]["rows"][0]["fcst_qty"] = "999"
            forecast.write_text(json.dumps(sources["forecast"]))
            self.assertEqual(
                json.loads(self.cli(path, root).stdout)["error_code"], "SOURCE_HASH_MISMATCH"
            )
            forecast.unlink()
            self.assertEqual(
                json.loads(self.cli(path, root).stdout)["error_code"], "SOURCE_SNAPSHOT_NOT_FOUND"
            )

    async def test_path_traversal_symlink_size_and_identity_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, sources = self.fixture_files(root)
            reader = FileSourceSnapshotReader(root)
            with self.assertRaisesRegex(InventoryInputError, "INVALID_IDENTIFIER"):
                await reader.read_snapshot("forecast", "../../secret")
            (root / "linked.json").symlink_to(path)
            with self.assertRaisesRegex(InventoryInputError, "SOURCE_SNAPSHOT_NOT_FOUND"):
                await reader.read_snapshot("forecast", "linked")
            (root / "oversize.json").write_bytes(b" " * 8_000_001)
            with self.assertRaisesRegex(InventoryInputError, "SOURCE_FILE_SIZE_LIMIT"):
                await reader.read_snapshot("forecast", "oversize")
            with self.assertRaisesRegex(InventoryInputError, "SOURCE_IDENTITY_MISMATCH"):
                await reader.read_snapshot("master", sources["forecast"]["snapshot_id"])

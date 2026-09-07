"""Classification CLI admission checks without a database connection."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class ClassificationSnapshotCliOfflineTests(unittest.TestCase):
    def test_missing_dsn_is_rejected_before_database_import(self):
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        env.pop("IO_POSTGRES_DSN", None)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "dsio_inventory_engine.entrypoints.classification_snapshot",
                "--project-id",
                "project-a",
                "--company-cd",
                "DSE",
                "--subs-cd",
                "C100",
                "--plant-cd",
                "V100",
                "--site-cd",
                "V100",
            ],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout)["error_code"], "IO_POSTGRES_DSN_REQUIRED")

    def test_apply_is_opt_in(self):
        from dsio_inventory_engine.entrypoints.classification_snapshot import parser

        args = parser().parse_args(
            [
                "--project-id",
                "project-a",
                "--company-cd",
                "DSE",
                "--subs-cd",
                "C100",
                "--plant-cd",
                "V100",
                "--site-cd",
                "V100",
            ]
        )
        self.assertFalse(args.apply)
        self.assertIsNone(args.engine_run_id)
        self.assertEqual(args.tenant_id, "default")

    def test_claimed_run_requires_explicit_apply(self):
        from dsio_inventory_engine.entrypoints.classification_snapshot import (
            parser,
            validate_args,
        )
        from dsio_inventory_engine.inventory_contracts.values import InventoryInputError

        args = parser().parse_args(
            [
                "--dsn",
                "postgresql://not-opened",
                "--project-id",
                "project-a",
                "--company-cd",
                "DSE",
                "--subs-cd",
                "C100",
                "--plant-cd",
                "V100",
                "--site-cd",
                "V100",
                "--engine-run-id",
                "00000000-0000-0000-0000-000000000010",
            ]
        )

        with self.assertRaisesRegex(
            InventoryInputError,
            "INVENTORY_RUN_CLASSIFICATION_APPLY_REQUIRED",
        ):
            validate_args(args)


if __name__ == "__main__":
    unittest.main()

"""Classification CLI admission checks without a database connection."""

import asyncio
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


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

    def test_claimed_run_cannot_select_latest_without_a_decision_week(self):
        from dsio_inventory_engine.entrypoints.classification_snapshot import parser, validate_args
        from dsio_inventory_engine.inventory_contracts.values import InventoryInputError

        argv = [
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
            "--apply",
        ]
        with self.assertRaisesRegex(
            InventoryInputError, "INVENTORY_RUN_CLASSIFICATION_DECISION_WEEK_REQUIRED"
        ):
            validate_args(parser().parse_args(argv))
        selected = parser().parse_args([*argv, "--decision-yyyyww", "202512"])
        validate_args(selected)
        self.assertEqual(selected.decision_yyyyww, "202512")

    def test_invalid_iso_week_is_rejected_before_connecting(self):
        from dsio_inventory_engine.entrypoints.classification_snapshot import parser, validate_args
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
                "--decision-yyyyww",
                "202553",
            ]
        )
        with self.assertRaisesRegex(InventoryInputError, "CLASSIFICATION_DECISION_WEEK_INVALID"):
            validate_args(args)

    def test_decision_week_reaches_source_and_each_cli_execution_path(self):
        from dsio_inventory_engine.entrypoints.classification_snapshot import parser, run

        adapter = "dsio_inventory_engine.infrastructure.postgresql.classification_snapshot"
        entrypoint = "dsio_inventory_engine.entrypoints.classification_snapshot"
        for claimed in (False, True):
            with self.subTest(claimed=claimed):
                argv = [
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
                    "--approved-by",
                    "admin",
                    "--decision-yyyyww",
                    "202512",
                ]
                if claimed:
                    argv += ["--engine-run-id", "00000000-0000-0000-0000-000000000010", "--apply"]
                args = parser().parse_args(argv)
                connection = SimpleNamespace(close=AsyncMock())
                lifecycle = SimpleNamespace(execute=AsyncMock(return_value={}))
                stage = SimpleNamespace(execute=AsyncMock(return_value={}))
                with (
                    patch("asyncpg.connect", AsyncMock(return_value=connection)),
                    patch(adapter + ".PostgresClassificationSource") as source,
                    patch(adapter + ".PostgresClassificationPublisher"),
                    patch(
                        entrypoint + ".InventoryClassificationLifecycleUseCase",
                        return_value=lifecycle,
                    ),
                    patch(entrypoint + ".InventoryClassificationStageUseCase", return_value=stage),
                    patch(
                        "dsio_inventory_engine.infrastructure.postgresql.run_events.PostgresInventoryRunEventRecorder"
                    ),
                ):
                    asyncio.run(run(args))
                source.assert_called_once_with(connection, decision_yyyyww="202512")
                if claimed:
                    self.assertEqual(stage.execute.await_args.args[0].decision_yyyyww, "202512")
                    lifecycle.execute.assert_not_awaited()
                else:
                    self.assertEqual(
                        lifecycle.execute.await_args.kwargs["decision_yyyyww"], "202512"
                    )
                    self.assertFalse(lifecycle.execute.await_args.kwargs["publish"])
                    stage.execute.assert_not_awaited()
                connection.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()

"""Real offline process boundaries, no mocks of the generator or cost calculation."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from training_fixtures import training_fixture

ROOT = Path(__file__).resolve().parents[2]


class TrainingCliOfflineTests(unittest.TestCase):
    def invoke(self, data, *, environment="DEVELOPMENT", company="DSE", extra=()):
        with tempfile.TemporaryDirectory(prefix="io-training-cli-") as directory:
            path = Path(directory) / "request.json"
            path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
            return subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "dsio_inventory_engine",
                    "prepare-training",
                    "--request",
                    str(path),
                    *extra,
                ],
                cwd=ROOT,
                env={
                    **os.environ,
                    "PYTHONPATH": str(ROOT / "src"),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "IO_COMPANY_CD": company,
                    "IO_ENVIRONMENT": environment,
                    "IO_POSTGRES_DSN": "invalid-and-unused",
                },
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )

    def test_actual_file_prepares_splits_boh_and_costs(self):
        process = self.invoke(training_fixture())
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["status"], "TRAINING_FOUNDATION_PREPARED_LOCALLY")
        self.assertEqual(len(result["supervised"]["features"]), 6)
        hold = next(e for e in result["evaluations"] if e["benchmark"] == "HOLD")
        self.assertEqual(hold["total_cost"], "170")
        for key in (
            "database_writes",
            "artifact_sealed",
            "evidence_persisted",
            "model_trained",
            "run_claimed",
        ):
            self.assertFalse(result["manifest"][key])

    def test_production_company_and_database_access_rejected(self):
        for options, code in (
            ({"environment": "PRODUCTION"}, "SYNTHETIC_PRODUCTION_FORBIDDEN"),
            ({"company": "OTHER"}, "DEPLOYMENT_COMPANY_MISMATCH"),
            ({"extra": ["--read-postgres"]}, "TRAINING_DATABASE_ACCESS_FORBIDDEN"),
        ):
            result = self.invoke(training_fixture(), **options)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["error_code"], code)

    def test_hash_and_duplicate_json_rejections(self):
        data = training_fixture()
        data["content_hash"] = "0" * 64
        for payload, code in (
            (data, "TRAINING_INPUT_HASH_MISMATCH"),
            ('{"secret":"not-a-real-secret","secret":"duplicate"}', "DUPLICATE_JSON_KEY"),
        ):
            result = self.invoke(payload)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["error_code"], code)
            self.assertNotIn("not-a-real-secret", result.stdout + result.stderr)

    def test_demo_recipe_executes_all_six_profiles(self):
        process = subprocess.run(
            [sys.executable, "examples/training_foundation.py"],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(len(result["supervised"]["features"]), 260)
        self.assertEqual(len(result["warmups"]), 18)
        self.assertEqual(len(result["evaluations"]), 12)
        self.assertEqual({len(w["ledger"]) for w in result["warmups"]}, {260})
        self.assertFalse(result["manifest"]["operational_accuracy_verified"])
        recipe = subprocess.run(
            [sys.executable, "examples/training_foundation.py", "--request-only"],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(recipe.returncode, 0, recipe.stderr)
        self.assertEqual(
            json.loads(recipe.stdout),
            json.loads((ROOT / "examples/training_input.json").read_text()),
        )


if __name__ == "__main__":
    unittest.main()

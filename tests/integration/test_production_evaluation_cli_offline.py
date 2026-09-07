"""Real CLI process, production mathematical decision code and independent World."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from evaluation_fixtures import fixture

ROOT = Path(__file__).resolve().parents[2]


class ProductionEvaluationCliTests(unittest.TestCase):
    def invoke(self, data, environment="DEVELOPMENT", company="DSE", extra=()):
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "dsio_inventory_engine",
                "evaluate-mathematical",
                "--request",
                "/dev/stdin",
                *extra,
            ],
            input=data if isinstance(data, str) else json.dumps(data),
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
            cwd=ROOT,
            env={
                **os.environ,
                "PYTHONPATH": str(ROOT / "src"),
                "PYTHONDONTWRITEBYTECODE": "1",
                "IO_ENVIRONMENT": environment,
                "IO_COMPANY_CD": company,
                "IO_POSTGRES_DSN": "not-used",
            },
        )

    def test_production_evaluation_cli(self):
        process = self.invoke(fixture())
        self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
        result = json.loads(process.stdout)
        self.assertEqual(result["world_result"]["total_cost"], "52")
        self.assertEqual(result["evaluation_kind"], "PRODUCTION_MATHEMATICAL")
        for key in (
            "database_writes",
            "run_claimed",
            "artifact_sealed",
            "evidence_persisted",
            "approval_authenticated",
            "model_trained",
        ):
            self.assertFalse(result[key])

    def test_environment_company_database_and_tamper_rejected(self):
        for options, code in (
            ({"environment": "PRODUCTION"}, "LOCAL_EVALUATION_ONLY"),
            ({"company": "OTHER"}, "DEPLOYMENT_COMPANY_MISMATCH"),
            ({"extra": ["--read-postgres"]}, "EVALUATION_DATABASE_ACCESS_FORBIDDEN"),
        ):
            process = self.invoke(fixture(), **options)
            self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
            self.assertEqual(json.loads(process.stdout)["error_code"], code)
        process = self.invoke('{"secret":"SENSITIVE-TEST-TEXT","secret":"duplicate"}')
        self.assertEqual(process.returncode, 2)
        self.assertEqual(json.loads(process.stdout)["error_code"], "DUPLICATE_JSON_KEY")
        self.assertNotIn("SENSITIVE-TEST-TEXT", process.stdout + process.stderr)

    def test_demo_request_and_execution(self):
        command = [sys.executable, "examples/production_evaluation.py", "--scenario", "DELAY_1W"]
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
        request = subprocess.run(
            command + ["--request-only"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=True,
        )
        direct = subprocess.run(
            command, cwd=ROOT, env=env, text=True, capture_output=True, timeout=30, check=True
        )
        result = self.invoke(request.stdout)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(direct.stdout), json.loads(result.stdout))

"""Real JSON file -> executable policy -> common guard/PSI without DB or model mocks."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from math_fixtures import build_math, reseal_math

ROOT = Path(__file__).resolve().parents[2]


class MathematicalCliOfflineTests(unittest.TestCase):
    def invoke(self, path, *, company="DSE", environment="DEVELOPMENT", extra=()):
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "dsio_inventory_engine",
                "recommend-mathematical",
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
            timeout=15,
            check=False,
        )

    def test_actual_example_file_computes_policy_and_psi(self):
        process = self.invoke(ROOT / "examples/mathematical_input.json")
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["contract_version"], "1.1.0")
        self.assertEqual(
            result["mathematical_policy_report"]["policy_evidence"][0]["effective_policy"],
            {"safety_stock_qty": "4", "rop_qty": "14", "target_inventory_qty": "24"},
        )
        self.assertEqual([r["eoh_qty"] for r in result["psi_rows"]], ["0", "4", "4"])
        self.assertEqual([r["quantity"] for r in result["recommended_orders"]], ["14", "10"])
        self.assertFalse(
            result["database_writes"]
            or result["model_artifact_verified"]
            or result["approval_authenticated"]
        )

    def test_actual_unsealed_and_future_data_files_fail_without_psi(self):
        for code, mutation in (
            ("UNSEALED_POLICY_INPUT", lambda d: d["policy_input"].update(status="COLLECTING")),
            (
                "FUTURE_POLICY_INFORMATION",
                lambda d: d["policy_input"]["context"].update(available_at="2026-09-28T00:00:00Z"),
            ),
        ):
            data = build_math()
            mutation(data)
            with tempfile.TemporaryDirectory(prefix="io-math-test-") as directory:
                path = Path(directory) / "invalid.json"
                path.write_text(json.dumps(reseal_math(data)), encoding="utf-8")
                process = self.invoke(path)
            self.assertEqual(process.returncode, 2, process.stderr)
            result = json.loads(process.stdout)
            self.assertEqual(result["error_code"], code)
            self.assertNotIn("psi_rows", result)

    def test_company_production_and_db_read_are_blocked(self):
        for kwargs, code in (
            ({"company": "OTHER"}, "DEPLOYMENT_COMPANY_MISMATCH"),
            ({"environment": "PRODUCTION"}, "LOCAL_RECOMMENDATION_ONLY"),
            ({"extra": ["--read-postgres"]}, "MATHEMATICAL_DATABASE_ACCESS_FORBIDDEN"),
        ):
            process = self.invoke(ROOT / "examples/mathematical_input.json", **kwargs)
            self.assertEqual(process.returncode, 2)
            self.assertEqual(json.loads(process.stdout)["error_code"], code)


if __name__ == "__main__":
    unittest.main()

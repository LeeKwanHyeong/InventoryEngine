"""Actual CLI file-to-PSI flow; no external services or DB writes."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class BaselineCliOfflineTests(unittest.TestCase):
    def invoke(self, path: Path, company="DSE"):
        env = {
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "IO_COMPANY_CD": company,
            "IO_ENVIRONMENT": "DEVELOPMENT",
            "IO_POSTGRES_DSN": "deliberately-invalid-and-unused",
        }
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "dsio_inventory_engine",
                "simulate-baseline",
                "--request",
                str(path),
            ],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )

    def test_actual_example_file_to_baseline(self):
        result = self.invoke(ROOT / "examples/baseline_input.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual([r["eoh_qty"] for r in output["psi_rows"]], ["30", "10", "10"])
        self.assertEqual(output["status"], "BASELINE_PSI_COMPUTED_LOCALLY")
        self.assertFalse(
            output["run_claimed"] or output["database_writes"] or output["artifact_sealed"]
        )

    def test_actual_unsealed_file_rejected_without_psi(self):
        request = json.loads((ROOT / "examples/baseline_input.json").read_text())
        request["snapshots"]["inventory"]["status"] = "COLLECTING"
        with tempfile.TemporaryDirectory(prefix="io-baseline-test-") as directory:
            path = Path(directory) / "unsealed.json"
            path.write_text(json.dumps(request), encoding="utf-8")
            result = self.invoke(path)
        self.assertEqual(result.returncode, 2)
        output = json.loads(result.stdout)
        self.assertEqual(output["error_code"], "UNSEALED_INPUT")
        self.assertNotIn("psi_rows", output)

    def test_actual_cli_company_scope_rejection(self):
        result = self.invoke(ROOT / "examples/baseline_input.json", company="OTHER")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["error_code"], "DEPLOYMENT_COMPANY_MISMATCH")


if __name__ == "__main__":
    unittest.main()

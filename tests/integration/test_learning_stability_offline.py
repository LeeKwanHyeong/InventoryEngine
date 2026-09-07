"""Local process only: pinned selection, error redaction, preserve existing evidence."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SELECTION = ROOT / "docs/architecture/evidence/learning-stability-selection-20260903.json"
PINNED = "db4d8f24a85e65876b7eba60811208900bbebf3b714f883c7a787b1cb3bbaa76"


class StabilityCliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "examples/learning_stability.py"), *args],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def test_request_and_saved_selection_verification(self):
        result = self.run_cli("request")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["training_seeds"], [101, 202, 303])
        result = self.run_cli(
            "verify-selection", "--selection", str(SELECTION), "--selection-hash", PINNED
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["selected_model_count"], 6)

    def test_hash_required_and_wrong_pin_is_redacted(self):
        result = self.run_cli("holdout", "--selection", str(SELECTION))
        self.assertEqual(result.returncode, 2)
        result = self.run_cli(
            "holdout", "--selection", str(SELECTION), "--selection-hash", "0" * 64
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            json.loads(result.stderr), {"status": "REJECTED", "code": "SELECTION_HASH_MISMATCH"}
        )
        self.assertNotIn("Traceback", result.stderr)

    def test_explicit_artifact_output_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "request.json"
            first = self.run_cli("request", "--output", str(target))
            self.assertEqual(first.returncode, 0, first.stderr)
            original = target.read_bytes()
            second = self.run_cli("request", "--output", str(target))
            self.assertEqual(second.returncode, 2)
            self.assertEqual(target.read_bytes(), original)

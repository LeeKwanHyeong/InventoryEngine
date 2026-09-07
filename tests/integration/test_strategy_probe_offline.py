"""Real subprocess, no database/model training. Exercises only the common contract."""

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]


class StrategyProbeOfflineTests(unittest.TestCase):
    def test_all_three_probe_bindings_execute_without_database_or_training(self):
        for family in ("MATHEMATICAL", "PREDICTIVE_ML", "DEEP_RL"):
            with self.subTest(family=family):
                env = {
                    **os.environ,
                    "PYTHONPATH": str(ROOT / "src"),
                    "PYTHONDONTWRITEBYTECODE": "1",
                }
                result = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "examples/strategy_contract_probe.py"),
                        "--strategy",
                        family,
                    ],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=True,
                )
                output = json.loads(result.stdout)
                self.assertEqual(output["status"], "RECOMMENDED_PSI_COMPUTED_LOCALLY")
                self.assertEqual(output["strategy"]["strategy_type"], family)
                self.assertTrue(output["probe_only_not_a_trained_strategy"])
                self.assertFalse(output["database_writes"] or output["model_artifact_verified"])
                self.assertEqual(len(output["decision_evidence"]), 3)
                self.assertTrue(output["recommended_orders"])


if __name__ == "__main__":
    unittest.main()

"""Separate-process inference and real optional CPU learning; no DB clients."""

import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from learned_fixtures import inference_request, model_fixture
from math_fixtures import build_math
from dsio_inventory_engine.fit_replenishment.demo import build_learning_request

ROOT = Path(__file__).resolve().parents[2]


class LearnedCliTests(unittest.TestCase):
    def invoke(
        self, data, action="recommend-learned", environment="DEVELOPMENT", extra=(), code=None
    ):
        command = [
            sys.executable,
            "-m",
            "dsio_inventory_engine",
            action,
            "--request",
            "/dev/stdin",
            *extra,
        ]
        if code is not None:
            command = [sys.executable, "-c", code]
        return subprocess.run(
            command,
            input=json.dumps(data),
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
            cwd=ROOT,
            env={
                **os.environ,
                "PYTHONPATH": str(ROOT / "src"),
                "PYTHONDONTWRITEBYTECODE": "1",
                "IO_COMPANY_CD": "DSE",
                "IO_ENVIRONMENT": environment,
            },
        )

    def test_inference_does_not_import_torch_or_unpickle(self):
        anchor = build_math()
        code = """import builtins, json, sys
original=builtins.__import__
def guard(name,*args,**kwargs):
    if name.split('.')[0] in ('torch','numpy','pickle','asyncpg'): raise RuntimeError('Forbidden import '+name)
    return original(name,*args,**kwargs)
builtins.__import__=guard
from dsio_inventory_engine.entrypoints.cli import main
raise SystemExit(main(['recommend-learned','--request','/dev/stdin']))
"""
        for family in ("PREDICTIVE_ML", "DEEP_RL"):
            result = self.invoke(
                inference_request(anchor, model_fixture(anchor, family)).to_dict(), code=code
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(json.loads(result.stdout)["model_artifact_hash_verified"])

    def test_database_production_and_bad_hash_fail_closed(self):
        anchor = build_math()
        data = inference_request(anchor, model_fixture(anchor)).to_dict()
        for options in ({"environment": "PRODUCTION"}, {"extra": ["--read-postgres"]}):
            result = self.invoke(data, **options)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        data["model_reference"]["content_hash"] = "0" * 64
        result = self.invoke(data)
        self.assertEqual(json.loads(result.stdout)["error_code"], "MODEL_REFERENCE_MISMATCH")

    @unittest.skipUnless(
        importlib.util.find_spec("torch"), "optional learning extra requires torch"
    )
    def test_real_training_cli(self):
        result = self.invoke(build_learning_request(quick=True).to_dict(), action="train-learned")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["status"], "TRAINED_LOCALLY")
        self.assertGreater(data["ppo_training"]["optimizer_steps"], 0)
        self.assertFalse(data["artifact_sealed"] or data["database_writes"])

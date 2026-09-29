"""One immutable prepared input can feed independent PSI child executions."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from learned_fixtures import inference_request, model_fixture
from math_fixtures import build_math

from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    canonical_json,
    digest,
)
from dsio_inventory_engine.learned.application import RunLearnedPsiUseCase
from dsio_inventory_engine.prepare_inventory.application.inventory_input import (
    PreparedInventoryInput,
    PrepareInventoryInputUseCase,
)
from dsio_inventory_engine.recommend_replenishment.application.mathematical import (
    RunMathematicalReplenishmentUseCase,
)
from dsio_inventory_engine.simulate_inventory.application.baseline import (
    RunPsiSimulationUseCase,
)


DEPLOYMENT = DeploymentScope("DSE", "DEVELOPMENT")


def _reseal_prepared(value: dict) -> PreparedInventoryInput:
    unsigned_manifest = {
        key: row for key, row in value["manifest"].items() if key != "prepared_content_hash"
    }
    unsigned_document = {
        "manifest": unsigned_manifest,
        **{key: row for key, row in value.items() if key != "manifest"},
    }
    value["manifest"]["prepared_content_hash"] = digest(unsigned_document)
    return PreparedInventoryInput(canonical_json(value))


class SharedPreparedInputTests(unittest.TestCase):
    def test_one_prepared_input_feeds_fresh_baseline_math_and_learned_clones(self) -> None:
        mathematical = MathematicalPolicyRequest.from_dict(build_math())
        canonical = CanonicalInputRequest.from_dict(
            mathematical.to_dict()["recommendation"]["canonical_input"]
        )
        prepared = PrepareInventoryInputUseCase(DEPLOYMENT).execute(canonical)
        model = model_fixture(mathematical.to_dict())
        learned = inference_request(mathematical.to_dict(), model)

        with patch.object(
            PrepareInventoryInputUseCase,
            "execute",
            side_effect=AssertionError("shared input must not be prepared again"),
        ):
            baseline_result = RunPsiSimulationUseCase(DEPLOYMENT).execute(
                canonical,
                prepared_input=prepared,
            )
            mathematical_result = RunMathematicalReplenishmentUseCase(DEPLOYMENT).execute(
                mathematical,
                prepared_input=prepared,
            )
            learned_result = RunLearnedPsiUseCase(DEPLOYMENT).execute(
                learned,
                prepared_input=prepared,
            )

        baseline_result["prepared_input"]["positions"][0]["available_qty"] = "999"
        self.assertNotEqual(
            mathematical_result["prepared_input"]["positions"][0]["available_qty"],
            "999",
        )
        self.assertNotEqual(
            learned_result["prepared_input"]["positions"][0]["available_qty"],
            "999",
        )
        self.assertNotEqual(prepared.to_dict()["positions"][0]["available_qty"], "999")

    def test_prepared_input_must_match_the_canonical_hash(self) -> None:
        mathematical = MathematicalPolicyRequest.from_dict(build_math())
        canonical = CanonicalInputRequest.from_dict(
            mathematical.to_dict()["recommendation"]["canonical_input"]
        )
        value = PrepareInventoryInputUseCase(DEPLOYMENT).execute(canonical).to_dict()
        value["manifest"]["input_content_hash"] = "f" * 64
        mismatched = _reseal_prepared(value)

        with self.assertRaisesRegex(InventoryInputError, "PREPARED_INPUT_BINDING_MISMATCH"):
            RunPsiSimulationUseCase(DEPLOYMENT).execute(
                canonical,
                prepared_input=mismatched,
            )

    def test_prepared_input_body_tampering_is_rejected(self) -> None:
        mathematical = MathematicalPolicyRequest.from_dict(build_math())
        canonical = CanonicalInputRequest.from_dict(
            mathematical.to_dict()["recommendation"]["canonical_input"]
        )
        value = PrepareInventoryInputUseCase(DEPLOYMENT).execute(canonical).to_dict()
        value["demands"][0]["net_forecast_qty"] = "1010"
        tampered = PreparedInventoryInput(canonical_json(value))

        with self.assertRaisesRegex(InventoryInputError, "PREPARED_INPUT_CONTENT_HASH_MISMATCH"):
            RunPsiSimulationUseCase(DEPLOYMENT).execute(
                canonical,
                prepared_input=tampered,
            )

    def test_prepared_input_manifest_tampering_is_rejected(self) -> None:
        mathematical = MathematicalPolicyRequest.from_dict(build_math())
        canonical = CanonicalInputRequest.from_dict(
            mathematical.to_dict()["recommendation"]["canonical_input"]
        )
        value = PrepareInventoryInputUseCase(DEPLOYMENT).execute(canonical).to_dict()
        value["manifest"]["quantity_rules"][0]["scale"] = 6
        tampered = PreparedInventoryInput(canonical_json(value))

        with self.assertRaisesRegex(InventoryInputError, "PREPARED_INPUT_CONTENT_HASH_MISMATCH"):
            RunMathematicalReplenishmentUseCase(DEPLOYMENT).execute(
                mathematical,
                prepared_input=tampered,
            )


if __name__ == "__main__":
    unittest.main()

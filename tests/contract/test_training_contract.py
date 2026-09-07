"""Training wire contract and generated BOH -> existing Canonical/PSI compatibility."""

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from fixtures import build_request, reseal
from training_fixtures import training_fixture
from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.training import (
    CALENDAR,
    CONTEXT,
    COST,
    DEMAND,
    FIELDS,
    ITEM,
    SCENARIO,
    TrainingRequest,
)
from dsio_inventory_engine.simulate_inventory.application.baseline import RunPsiSimulationUseCase
from dsio_inventory_engine.training.reference import generate_warmup

try:
    from jsonschema import Draft202012Validator, FormatChecker, ValidationError
except ImportError:
    Draft202012Validator = None

ROOT = Path(__file__).resolve().parents[2]


class TrainingContractTests(unittest.TestCase):
    def test_schema_field_parity(self):
        schema = json.loads((ROOT / "schemas/training_foundation.schema.json").read_text())
        self.assertEqual(set(schema["required"]), set(FIELDS))
        for name, fields in (
            ("context", CONTEXT),
            ("calendar", CALENDAR),
            ("item", ITEM),
            ("demand", DEMAND),
            ("scenario", SCENARIO),
            ("cost", COST),
        ):
            self.assertEqual(set(schema["$defs"][name]["required"]), set(fields))

    @unittest.skipIf(Draft202012Validator is None, "jsonschema is a development dependency")
    def test_schema_accepts_request_and_rejects_bad_shapes(self):
        schema = json.loads((ROOT / "schemas/training_foundation.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        data = TrainingRequest.from_dict(training_fixture()).to_dict()
        validator.validate(data)
        handoff_schema = json.loads((ROOT / "schemas/synthetic_boh.schema.json").read_text())
        Draft202012Validator.check_schema(handoff_schema)
        warmup = generate_warmup(data, 78, data["scenarios"][0])
        Draft202012Validator(handoff_schema, format_checker=FormatChecker()).validate(
            {k: warmup[k] for k in ("positions", "open_orders")}
        )
        for mutate in (
            lambda d: d.update(extra=1),
            lambda d: d.update(warmup_weeks=51),
            lambda d: d["items"][0].update(service_level_code="SL_80"),
            lambda d: d["scenarios"][0]["cost"].update(currency="UNKNOWN"),
        ):
            value = copy.deepcopy(data)
            mutate(value)
            with self.assertRaises(ValidationError):
                validator.validate(value)

    def test_generated_boh_and_pipeline_fit_existing_canonical_psi(self):
        data = TrainingRequest.from_dict(training_fixture()).to_dict()
        warmup = generate_warmup(data, 78, data["scenarios"][0])
        position = warmup["positions"][0]
        # Test-only envelope assembly; no claim of Source approval, Artifact sealing or DB write.
        source = build_request(
            {
                "id": "independent-generator",
                "plan_type": "TGSM",
                "source_type": "SYNTHETIC_BOH",
                "start_date": data["calendar"][78]["start_date"],
                "weeks": [c["yyyyww"] for c in data["calendar"][78:81]],
                "items": [
                    {
                        "id": "A",
                        "boh": position["on_hand_qty"],
                        "prior": position["prior_eoh_qty"],
                        "backorder": position["backorder_qty"],
                        "forecast": ["10"] * 3,
                        "receipts": [
                            {
                                "week": o["planned_due_index"] - 78,
                                "qty": o["quantity"],
                                "status": "CONFIRMED",
                            }
                            for o in warmup["open_orders"]
                        ],
                    }
                ],
            }
        )
        for name in ("inventory", "prior_inventory", "receipts"):
            source["snapshots"][name]["metadata"].update(
                simulation_run_id=data["simulation_run_id"], generator_version="1.0.0"
            )
        result = RunPsiSimulationUseCase(DeploymentScope("DSE", "DEVELOPMENT")).execute(
            CanonicalInputRequest.from_dict(reseal(source))
        )
        self.assertEqual([r["eoh_qty"] for r in result["psi_rows"]], ["10", "10", "0"])
        self.assertFalse(result["database_writes"])


if __name__ == "__main__":
    unittest.main()

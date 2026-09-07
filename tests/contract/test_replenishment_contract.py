"""Wire field parity and the same method contract for all three strategy families."""

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from strategy_fixtures import ContractProbe, request

from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.replenishment import (
    CONTROL_FIELDS,
    DESCRIPTOR_FIELDS,
    EXECUTION_FIELDS,
    POLICY_FIELDS,
    PROPOSAL_FIELDS,
    STRATEGY_TYPES,
)
from dsio_inventory_engine.recommend_replenishment.application.run import RunRecommendedPsiUseCase

try:
    from jsonschema import Draft202012Validator, FormatChecker, ValidationError
except ImportError:
    Draft202012Validator = None

ROOT = Path(__file__).resolve().parents[2]


class ReplenishmentContractTests(unittest.TestCase):
    def test_field_parity(self):
        schema = json.loads((ROOT / "schemas/replenishment.schema.json").read_text())
        for name, fields in (
            ("execution", EXECUTION_FIELDS),
            ("strategy", DESCRIPTOR_FIELDS),
            ("control", CONTROL_FIELDS),
            ("policy", POLICY_FIELDS),
            ("proposal", PROPOSAL_FIELDS),
        ):
            self.assertEqual(set(schema["$defs"][name]["required"]), set(fields))

    @unittest.skipIf(Draft202012Validator is None, "jsonschema is a dev-only dependency")
    def test_all_strategy_configs_and_proposals_validate(self):
        schema = json.loads((ROOT / "schemas/replenishment.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        for family in STRATEGY_TYPES:
            req = request(family=family)
            validator.validate(req.to_dict()["execution"])
            output = RunRecommendedPsiUseCase(DeploymentScope("DSE", "DEVELOPMENT")).execute(
                req, ContractProbe(family)
            )
            for evidence in output["decision_evidence"]:
                validator.validate(evidence["proposal"])
        invalid = copy.deepcopy(req.to_dict()["execution"])
        invalid["strategy"]["model"] = None
        with self.assertRaises(ValidationError):
            validator.validate(invalid)


if __name__ == "__main__":
    unittest.main()

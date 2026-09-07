"""Optional cross-repository parity for the common Inventory Run identity."""

import ast
import os
import unittest
from pathlib import Path


EXPECTED = {
    "INVENTORY_RUN_CLAIM_CONTRACT_ID": "inventory-engine-run-claim-v1",
    "INVENTORY_RUN_CLAIM_CONTRACT_VERSION": "1.0.0",
    "INVENTORY_PLAN_SOURCE_KEY": "inventory.planning_cycle",
    "INVENTORY_PLAN_SOURCE_CONTRACT_VERSION": "1.0.0",
}


@unittest.skipUnless(
    os.environ.get("DSAI_PLATFORM_ROOT"),
    "platform source not explicitly provided",
)
class PlatformInventoryRunContractTests(unittest.TestCase):
    def test_platform_contract_identity_matches_inventory_runtime(self):
        path = (
            Path(os.environ["DSAI_PLATFORM_ROOT"])
            / "backend/platform_api/inventory_engine_run_contract.py"
        )
        tree = ast.parse(path.read_text(encoding="utf-8"))
        values = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name) and target.id in EXPECTED:
                    values[target.id] = ast.literal_eval(node.value)

        self.assertEqual(values, EXPECTED)


if __name__ == "__main__":
    unittest.main()

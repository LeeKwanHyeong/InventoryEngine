"""Optional cross-repository parity for Inventory Config 2.0.0 identity."""

import ast
import hashlib
import os
import unittest
from pathlib import Path

from dsio_inventory_engine.classify_inventory.application import (
    CONFIG_SCHEMA_V2_HASH,
    CONFIG_SCHEMA_V2_ID,
    CONFIG_SCHEMA_V2_VERSION,
)


@unittest.skipUnless(
    os.environ.get("DSAI_PLATFORM_ROOT"),
    "platform source not explicitly provided",
)
class PlatformInventoryConfigV2ContractTests(unittest.TestCase):
    def test_platform_schema_identity_matches_inventory_consumer(self):
        root = Path(os.environ["DSAI_PLATFORM_ROOT"])
        module_path = root / "backend/platform_api/inventory_engine_config.py"
        schema_path = root / "backend/platform_api/contracts/inventory-engine-config-v2.schema.json"
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        expected_names = {
            "INVENTORY_ENGINE_CONFIG_SCHEMA_ID",
            "INVENTORY_ENGINE_CONFIG_SCHEMA_VERSION",
        }
        values = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name) and target.id in expected_names:
                    values[target.id] = ast.literal_eval(node.value)

        self.assertEqual(values["INVENTORY_ENGINE_CONFIG_SCHEMA_ID"], CONFIG_SCHEMA_V2_ID)
        self.assertEqual(
            values["INVENTORY_ENGINE_CONFIG_SCHEMA_VERSION"],
            CONFIG_SCHEMA_V2_VERSION,
        )
        self.assertEqual(
            hashlib.sha256(schema_path.read_bytes()).hexdigest(),
            CONFIG_SCHEMA_V2_HASH,
        )


if __name__ == "__main__":
    unittest.main()

"""Platform-to-Inventory Runtime closed-contract tests."""

import copy
import unittest

from dsio_inventory_engine.inventory_contracts.runtime import (
    InventoryRuntimeExecutionRequest,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from tests.support.runtime_fixtures import runtime_dispatch


class RuntimeExecutionContractTests(unittest.TestCase):
    def test_claim_is_normalized_and_hash_is_order_independent(self):
        first = InventoryRuntimeExecutionRequest.from_dict(runtime_dispatch())
        reordered = copy.deepcopy(runtime_dispatch())
        reordered["claim"]["input_bindings"].reverse()
        replay = InventoryRuntimeExecutionRequest.from_dict(reordered)

        self.assertEqual(first.canonical_hash, replay.canonical_hash)
        self.assertEqual(first.tenant_id, "tenant-a")
        self.assertEqual(first.value["claim"]["scope"]["plant_cd"], "V100")

    def test_missing_or_changed_binding_fails_closed(self):
        missing = runtime_dispatch()
        missing["claim"]["input_bindings"] = missing["claim"]["input_bindings"][:-1]
        with self.assertRaises(InventoryInputError):
            InventoryRuntimeExecutionRequest.from_dict(missing)

        changed = runtime_dispatch()
        policy = next(
            item
            for item in changed["claim"]["input_bindings"]
            if item["input_type"] == "INVENTORY_POLICY"
        )
        policy["source_content_hash"] = "f" * 64
        with self.assertRaisesRegex(
            InventoryInputError,
            "INVENTORY_POLICY_BINDING_MISMATCH",
        ):
            InventoryRuntimeExecutionRequest.from_dict(changed)

    def test_unknown_field_fails_closed(self):
        value = runtime_dispatch()
        value["callback_url"] = "https://attacker.invalid"
        with self.assertRaisesRegex(InventoryInputError, "CONTRACT_FIELDS"):
            InventoryRuntimeExecutionRequest.from_dict(value)


if __name__ == "__main__":
    unittest.main()

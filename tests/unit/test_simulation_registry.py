"""Development Cost Profile and Stress Registry contract tests."""

import unittest

from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from dsio_inventory_engine.run_inventory.simulation_registry import (
    DevelopmentSimulationRegistry,
    validate_cost_profile_payload,
    validate_stress_payload,
)


class DevelopmentSimulationRegistryTests(unittest.TestCase):
    def test_registry_identity_matches_the_platform_golden_contract(self) -> None:
        registry = DevelopmentSimulationRegistry()

        cost = registry.cost_profile_binding()
        stresses = registry.stress_scenario_bindings()

        self.assertEqual(
            cost["profile_content_hash"],
            "840da9f8e8443197aaf9036b8a8dd68c94609cf4612b150b82360ac19fd0319b",
        )
        self.assertEqual(
            {row["scenario_id"]: row["scenario_content_hash"] for row in stresses},
            {
                "CONFIRMED_RECEIPT_DELAY_7_DAYS": (
                    "d4f3456c69b11761ae02bc5cbb084bfde5443512cab2aba9b255c4629a67d72f"
                ),
                "DEMAND_SURGE_20_PERCENT": (
                    "0080e25b529addc1fbdb4e9729e711cae03ad7d144e9b6948576558d1ba8558d"
                ),
            },
        )

    def test_payload_contracts_fail_closed(self) -> None:
        with self.assertRaisesRegex(InventoryInputError, "COST_PROFILE_RATE_INVALID"):
            validate_cost_profile_payload(
                {
                    "currency": "KRW",
                    "holding_cost_per_unit_week": "-1",
                    "backorder_cost_per_unit_week": "100",
                    "fixed_order_cost": "1000",
                    "variable_order_cost_per_unit": "2",
                }
            )
        with self.assertRaisesRegex(InventoryInputError, "STRESS_SCENARIO_PAYLOAD_INVALID"):
            validate_stress_payload(
                {
                    "forecast_multiplier": "1",
                    "customer_order_multiplier": "1",
                    "confirmed_receipt_delay_days": 366,
                }
            )


if __name__ == "__main__":
    unittest.main()

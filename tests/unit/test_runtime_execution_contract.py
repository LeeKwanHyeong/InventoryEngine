"""Platform-to-Inventory Runtime closed-contract tests."""

import copy
import unittest

from dsio_inventory_engine.inventory_contracts.runtime import (
    InventoryRuntimeExecutionRequest,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from tests.support.runtime_fixtures import runtime_dispatch
from tests.support.runtime_fixtures import runtime_dispatch_v2
from tests.support.runtime_fixtures import runtime_plan_key_hash


class RuntimeExecutionContractTests(unittest.TestCase):
    def test_claim_is_normalized_and_hash_is_order_independent(self):
        first = InventoryRuntimeExecutionRequest.from_dict(runtime_dispatch())
        reordered = copy.deepcopy(runtime_dispatch())
        reordered["claim"]["input_bindings"].reverse()
        replay = InventoryRuntimeExecutionRequest.from_dict(reordered)

        self.assertEqual(first.canonical_hash, replay.canonical_hash)
        self.assertEqual(first.tenant_id, "tenant-a")
        self.assertEqual(first.value["claim"]["scope"]["plant_cd"], "V100")
        self.assertEqual(
            first.value["claim"]["demand_run_id"],
            "50000000-0000-4000-8000-000000000001",
        )
        self.assertIsNone(first.value["claim"]["expected_automatic_publish_allowed"])
        self.assertIsNone(first.value["claim"]["expected_automatic_order_allowed"])

        explicit_null = runtime_dispatch()
        explicit_null["claim"].update(
            expected_automatic_publish_allowed=None,
            expected_automatic_order_allowed=None,
        )
        self.assertEqual(
            first.canonical_hash,
            InventoryRuntimeExecutionRequest.from_dict(explicit_null).canonical_hash,
        )

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

    def test_demand_run_id_is_required_and_must_be_a_uuid(self):
        missing = runtime_dispatch()
        del missing["claim"]["demand_run_id"]
        with self.assertRaisesRegex(InventoryInputError, "CONTRACT_FIELDS"):
            InventoryRuntimeExecutionRequest.from_dict(missing)

        malformed = runtime_dispatch()
        malformed["claim"]["demand_run_id"] = "DEMAND-LATEST"
        with self.assertRaisesRegex(InventoryInputError, "INVALID_UUID"):
            InventoryRuntimeExecutionRequest.from_dict(malformed)

    def test_demand_run_id_is_bound_into_v1_and_v2_dispatch_hashes(self):
        documents = (
            runtime_dispatch(),
            runtime_dispatch_v2(
                config_hash="a" * 64,
                effective_policy_content_hash="c" * 64,
            ),
        )
        for document in documents:
            with self.subTest(binding_count=len(document["claim"]["input_bindings"])):
                changed = copy.deepcopy(document)
                changed["claim"]["demand_run_id"] = "50000000-0000-4000-8000-000000000099"
                self.assertNotEqual(
                    InventoryRuntimeExecutionRequest.from_dict(document).canonical_hash,
                    InventoryRuntimeExecutionRequest.from_dict(changed).canonical_hash,
                )

    def test_plan_id_is_required_and_bound_into_v1_and_v2_dispatch_hashes(self):
        documents = (
            runtime_dispatch(),
            runtime_dispatch_v2(
                config_hash="a" * 64,
                effective_policy_content_hash="c" * 64,
            ),
        )
        for document in documents:
            with self.subTest(binding_count=len(document["claim"]["input_bindings"])):
                missing = copy.deepcopy(document)
                del missing["claim"]["plan_id"]
                with self.assertRaisesRegex(InventoryInputError, "CONTRACT_FIELDS"):
                    InventoryRuntimeExecutionRequest.from_dict(missing)

                changed = copy.deepcopy(document)
                changed["claim"]["plan_id"] = "PLAN-TAMPERED"
                with self.assertRaisesRegex(
                    InventoryInputError,
                    "RUNTIME_PLAN_KEY_BINDING_MISMATCH",
                ):
                    InventoryRuntimeExecutionRequest.from_dict(changed)

                changed["claim"]["plan_key_hash"] = runtime_plan_key_hash(changed)
                self.assertNotEqual(
                    InventoryRuntimeExecutionRequest.from_dict(document).canonical_hash,
                    InventoryRuntimeExecutionRequest.from_dict(changed).canonical_hash,
                )

    def test_plan_key_hash_seals_revision_plan_and_scope(self):
        for mutate in (
            lambda claim: claim.update(planning_cycle_revision_id="PCR-OTHER"),
            lambda claim: claim.update(plan_id="PLAN-OTHER"),
            lambda claim: claim["scope"].update(site_cd="V999"),
        ):
            with self.subTest(mutate=mutate):
                value = runtime_dispatch()
                mutate(value["claim"])
                with self.assertRaisesRegex(
                    InventoryInputError,
                    "RUNTIME_PLAN_KEY_BINDING_MISMATCH",
                ):
                    InventoryRuntimeExecutionRequest.from_dict(value)

    def test_required_binding_contract_keys_and_versions_are_exact(self):
        expected = {
            "DEMAND_FORECAST": "demand.forecast_snapshot",
            "INVENTORY_POSITION": "inventory.position",
            "INVENTORY_POLICY": "inventory.policy",
            "REPLENISHMENT_POLICY": "inventory.replenishment_policy",
            "SUPPLY_RECEIPTS": "inventory.supply_receipts",
            "CUSTOMER_ORDERS": "inventory.customer_orders",
            "PRIOR_INVENTORY": "inventory.prior_inventory",
            "CALENDAR": "demand_io.calendar",
            "MASTER": "demand_io.master",
        }
        for input_type in expected:
            for field, replacement in (
                ("source_contract_key", "inventory.wrong_contract"),
                ("source_contract_version", "9.9.9"),
            ):
                with self.subTest(input_type=input_type, field=field):
                    value = runtime_dispatch()
                    row = next(
                        item
                        for item in value["claim"]["input_bindings"]
                        if item["input_type"] == input_type
                    )
                    row[field] = replacement
                    with self.assertRaises(InventoryInputError):
                        InventoryRuntimeExecutionRequest.from_dict(value)

    def test_unknown_field_fails_closed(self):
        value = runtime_dispatch()
        value["callback_url"] = "https://attacker.invalid"
        with self.assertRaisesRegex(InventoryInputError, "CONTRACT_FIELDS"):
            InventoryRuntimeExecutionRequest.from_dict(value)

    def test_v2_classification_binding_seals_snapshot_and_effective_policy_hash(self):
        value = runtime_dispatch_v2(
            config_hash="a" * 64,
            effective_policy_content_hash="c" * 64,
        )

        request = InventoryRuntimeExecutionRequest.from_dict(value)

        self.assertEqual(
            request.classification_binding,
            {
                "input_type": "INVENTORY_CLASSIFICATION",
                "source_contract_key": "inventory.classification_effective_policy",
                "source_snapshot_id": "40000000-0000-4000-8000-000000000001",
                "source_content_hash": "c" * 64,
                "source_contract_version": "2.0.0",
            },
        )
        self.assertFalse(request.value["claim"]["expected_automatic_publish_allowed"])
        self.assertFalse(request.value["claim"]["expected_automatic_order_allowed"])

    def test_v2_requires_both_sealed_policy_admission_flags(self):
        for field, value in (
            ("expected_automatic_publish_allowed", None),
            ("expected_automatic_order_allowed", None),
            ("expected_automatic_publish_allowed", "false"),
        ):
            with self.subTest(field=field, value=value):
                claim = runtime_dispatch_v2(
                    config_hash="a" * 64,
                    effective_policy_content_hash="c" * 64,
                )
                claim["claim"][field] = value
                with self.assertRaises(InventoryInputError):
                    InventoryRuntimeExecutionRequest.from_dict(claim)

    def test_v1_rejects_v2_policy_admission_flags(self):
        value = runtime_dispatch()
        value["claim"].update(
            expected_automatic_publish_allowed=True,
            expected_automatic_order_allowed=True,
        )

        with self.assertRaisesRegex(
            InventoryInputError,
            "RUNTIME_V1_POLICY_ADMISSION_FORBIDDEN",
        ):
            InventoryRuntimeExecutionRequest.from_dict(value)

    def test_classification_binding_requires_exact_v2_contract(self):
        for mutate in (
            lambda row: row.update(source_contract_version="1.0.0"),
            lambda row: row.update(source_contract_key="inventory.policy"),
            lambda row: row.update(source_snapshot_id="not-a-uuid"),
        ):
            with self.subTest(mutate=mutate):
                value = runtime_dispatch_v2(
                    config_hash="a" * 64,
                    effective_policy_content_hash="c" * 64,
                )
                row = next(
                    item
                    for item in value["claim"]["input_bindings"]
                    if item["input_type"] == "INVENTORY_CLASSIFICATION"
                )
                mutate(row)
                with self.assertRaises(InventoryInputError):
                    InventoryRuntimeExecutionRequest.from_dict(value)

    def test_policy_contract_version_tracks_classification_binding(self):
        v1 = runtime_dispatch()
        v1_policy = next(
            row for row in v1["claim"]["input_bindings"] if row["input_type"] == "INVENTORY_POLICY"
        )
        v1_policy["source_contract_version"] = "2.0.0"
        with self.assertRaisesRegex(
            InventoryInputError,
            "INVENTORY_POLICY_BINDING_VERSION_MISMATCH",
        ):
            InventoryRuntimeExecutionRequest.from_dict(v1)

        v2 = runtime_dispatch_v2(
            config_hash="a" * 64,
            effective_policy_content_hash="c" * 64,
        )
        v2_policy = next(
            row for row in v2["claim"]["input_bindings"] if row["input_type"] == "INVENTORY_POLICY"
        )
        v2_policy["source_contract_version"] = "1.0.0"
        with self.assertRaisesRegex(
            InventoryInputError,
            "INVENTORY_POLICY_BINDING_VERSION_MISMATCH",
        ):
            InventoryRuntimeExecutionRequest.from_dict(v2)


if __name__ == "__main__":
    unittest.main()

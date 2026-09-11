"""Deterministic classification policy and replenishment admission tests."""

import copy
import unittest

from dsio_inventory_engine.inventory_contracts.classification import (
    derive_effective_item_policy,
    validate_effective_item_policy,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from dsio_inventory_engine.recommend_replenishment.application.effective_policy import (
    admit_effective_item_policy,
)


CONFIG_HASH = "a" * 64
FLOORS = {"V": 0.99, "E": 0.97, "D": 0.9}


def classified(*, strategy="MATHEMATICAL", ved_class="V") -> dict:
    item = {
        "item_id": "ITEM-A",
        "classification_status": "CLASSIFIED",
        "segment_key": "AX",
        "ved_class": ved_class,
        "unclassified_reason_code": None,
    }
    return {
        **item,
        **derive_effective_item_policy(
            **item,
            policy_cell={
                "segment_key": "AX",
                "target_service_level": 0.95,
                "review_cycle_weeks": 2,
                "strategy": strategy,
            },
            ved_service_level_floor=FLOORS,
            config_hash=CONFIG_HASH,
        ),
    }


class EffectiveItemPolicyTests(unittest.TestCase):
    def test_ved_floor_and_matrix_values_are_deterministic(self):
        first = classified()
        replay = classified()

        self.assertEqual(first, replay)
        self.assertEqual(first["effective_target_service_level"], "0.99")
        self.assertEqual(first["effective_review_cycle_weeks"], 2)
        self.assertEqual(first["effective_strategy"], "MATHEMATICAL")
        self.assertEqual(first["policy_source"], "ABC_XYZ_POLICY_MATRIX_WITH_VED_FLOOR")
        self.assertEqual(first["policy_adjustment_reason"], "VED_SERVICE_LEVEL_FLOOR_APPLIED")
        self.assertTrue(first["operational_io_eligible"])
        self.assertEqual(
            validate_effective_item_policy(first, config_hash=CONFIG_HASH)["effective_policy_hash"],
            first["effective_policy_hash"],
        )

    def test_unclassified_item_has_no_silent_default_policy(self):
        item = {
            "item_id": "ITEM-ZERO",
            "classification_status": "UNCLASSIFIED",
            "segment_key": None,
            "ved_class": "D",
            "unclassified_reason_code": "ZERO_MEAN_DEMAND",
        }
        policy = derive_effective_item_policy(
            **item,
            policy_cell=None,
            ved_service_level_floor=FLOORS,
            config_hash=CONFIG_HASH,
        )

        self.assertIsNone(policy["effective_strategy"])
        self.assertEqual(policy["policy_source"], "UNCLASSIFIED")
        self.assertFalse(policy["operational_io_eligible"])

    def test_policy_hash_tampering_fails_closed(self):
        item = classified()
        item["effective_review_cycle_weeks"] = 3

        with self.assertRaisesRegex(InventoryInputError, "ITEM_POLICY_HASH_MISMATCH"):
            validate_effective_item_policy(item, config_hash=CONFIG_HASH)

    def test_mathematical_is_operational_and_learned_strategy_is_shadow_only(self):
        math = classified()
        mathematical = {
            "strategy_type": "MATHEMATICAL",
            "implementation_id": "math-policy",
            "version": "1.0.0",
            "model": None,
        }
        admitted = admit_effective_item_policy(
            math,
            config_hash=CONFIG_HASH,
            execution_purpose="OPERATIONAL",
            strategy_descriptor=mathematical,
        )
        self.assertEqual(admitted["effective_target_service_level"], "0.99")

        learned = classified(strategy="PREDICTIVE_ML", ved_class=None)
        approved_model = {
            "strategy_type": "PREDICTIVE_ML",
            "implementation_id": "quantile-policy",
            "version": "1.0.0",
            "model": {
                "model_id": "ML-APPROVED-1",
                "version": "1.0.0",
                "content_hash": "b" * 64,
            },
        }
        shadow = admit_effective_item_policy(
            learned,
            config_hash=CONFIG_HASH,
            execution_purpose="SHADOW",
            strategy_descriptor=approved_model,
            model_approval={
                "approval_reference": "MODEL-APPROVAL-1",
                "status": "APPROVED",
                **approved_model["model"],
            },
        )
        self.assertEqual(shadow["effective_strategy"], "PREDICTIVE_ML")
        self.assertEqual(shadow["model_approval_reference"], "MODEL-APPROVAL-1")
        self.assertFalse(learned["operational_io_eligible"])

        with self.assertRaisesRegex(InventoryInputError, "ITEM_POLICY_NOT_OPERATIONALLY_ELIGIBLE"):
            admit_effective_item_policy(
                learned,
                config_hash=CONFIG_HASH,
                execution_purpose="OPERATIONAL",
                strategy_descriptor=approved_model,
                model_approval={
                    "approval_reference": "MODEL-APPROVAL-1",
                    "status": "APPROVED",
                    **approved_model["model"],
                },
            )

    def test_strategy_and_model_binding_are_never_silently_replaced(self):
        learned = classified(strategy="DEEP_RL", ved_class=None)
        mismatched = {
            "strategy_type": "PREDICTIVE_ML",
            "implementation_id": "quantile-policy",
            "version": "1.0.0",
            "model": {
                "model_id": "ML-APPROVED-1",
                "version": "1.0.0",
                "content_hash": "b" * 64,
            },
        }
        with self.assertRaisesRegex(InventoryInputError, "ITEM_POLICY_STRATEGY_MISMATCH"):
            admit_effective_item_policy(
                learned,
                config_hash=CONFIG_HASH,
                execution_purpose="SHADOW",
                strategy_descriptor=mismatched,
            )

        with self.assertRaisesRegex(
            InventoryInputError, "ITEM_POLICY_SHADOW_MODEL_APPROVAL_REQUIRED"
        ):
            admit_effective_item_policy(
                learned,
                config_hash=CONFIG_HASH,
                execution_purpose="SHADOW",
                strategy_descriptor={
                    **mismatched,
                    "strategy_type": "DEEP_RL",
                },
            )

        with self.assertRaisesRegex(InventoryInputError, "ITEM_POLICY_MODEL_APPROVAL_MISMATCH"):
            admit_effective_item_policy(
                learned,
                config_hash=CONFIG_HASH,
                execution_purpose="SHADOW",
                strategy_descriptor={
                    **mismatched,
                    "strategy_type": "DEEP_RL",
                },
                model_approval={
                    "approval_reference": "MODEL-APPROVAL-OTHER",
                    "status": "APPROVED",
                    "model_id": "RL-OTHER",
                    "version": "1.0.0",
                    "content_hash": "c" * 64,
                },
            )

        missing_model = copy.deepcopy(mismatched)
        missing_model.update(strategy_type="DEEP_RL", model=None)
        with self.assertRaisesRegex(InventoryInputError, "STRATEGY_MODEL_BINDING_REQUIRED"):
            admit_effective_item_policy(
                learned,
                config_hash=CONFIG_HASH,
                execution_purpose="SHADOW",
                strategy_descriptor=missing_model,
            )


if __name__ == "__main__":
    unittest.main()

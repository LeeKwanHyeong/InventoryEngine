"""Effective Policy V2 overlay, hash isolation and admission tests."""

import copy
import unittest

from dsio_inventory_engine.inventory_contracts.effective_policy_v2 import (
    derive_effective_item_policy_v2,
    validate_effective_item_policy_v2,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from dsio_inventory_engine.recommend_replenishment.application.effective_policy import (
    admit_effective_item_policy,
)


FLOORS = {"V": 0.99, "E": 0.97, "D": 0.9}
CONFIG_HASH = "a" * 64
MATH_STRATEGY = {
    "strategy_type": "MATHEMATICAL",
    "implementation_id": "math-policy",
    "version": "2.0.0",
    "model": None,
}


def axis(
    name: str,
    class_code: str | None,
    *,
    mode: str = "OPERATIONAL",
    status: str = "CLASSIFIED",
    evidence: dict | None = None,
) -> dict:
    return {
        "axis": name,
        "status": status,
        "class_code": class_code,
        "application_mode": mode,
        "policy_effective": mode == "OPERATIONAL" and status == "CLASSIFIED",
        "source_contract_key": f"{name}_SOURCE_V1",
        "evidence": evidence or {},
        "reason_code": None if status == "CLASSIFIED" else "SOURCE_NOT_VERIFIED",
    }


def axes(*, sde_mode: str = "OPERATIONAL", hml_mode: str = "OPERATIONAL") -> list[dict]:
    return [
        axis("ABC", "A"),
        axis("XYZ", "X"),
        axis("VED", "V"),
        axis("FSN", "S"),
        axis(
            "SDE",
            "S",
            mode=sde_mode,
            evidence={"p50_lead_time_days": "14", "p90_lead_time_days": "30"},
        ),
        axis("HML", "H", mode=hml_mode),
        axis("PLC", "DECLINE"),
    ]


def overlays() -> dict:
    return {
        "fsn_order_action": {"F": "ALLOW", "S": "ALLOW", "N": "REVIEW"},
        "sde_lead_time_basis": {"S": "P90", "D": "P90", "E": "P50"},
        "hml_approval_level": {"H": "HIGH_VALUE", "M": "STANDARD", "L": "AUTO"},
        "plc_order_action": {
            "PRE_LAUNCH": "REVIEW",
            "INTRODUCTION": "ALLOW",
            "GROWTH": "ALLOW",
            "MATURE": "ALLOW",
            "DECLINE": "REVIEW",
            "SERVICE_ONLY": "REVIEW",
            "DISCONTINUED": "BLOCK",
        },
    }


def derive(
    *,
    item_id: str = "ITEM-A",
    axis_results: list[dict] | None = None,
    policy_overlays: dict | None = None,
) -> dict:
    item = {
        "item_id": item_id,
        "classification_status": "CLASSIFIED",
        "segment_key": "AX",
        "unclassified_reason_code": None,
    }
    return {
        **item,
        **derive_effective_item_policy_v2(
            **item,
            classification_config_hash=CONFIG_HASH,
            policy_cell={
                "segment_key": "AX",
                "target_service_level": 0.95,
                "review_cycle_weeks": 2,
                "strategy": "MATHEMATICAL",
            },
            ved_service_level_floor=FLOORS,
            axis_results=axis_results or axes(),
            policy_overlays=policy_overlays or overlays(),
        ),
    }


class EffectiveItemPolicyV2Tests(unittest.TestCase):
    def test_opaque_business_item_id_is_preserved_in_policy_binding(self):
        item = derive(item_id="ITEM/01")

        self.assertEqual(item["item_id"], "ITEM/01")
        self.assertEqual(
            validate_effective_item_policy_v2(item)["effective_policy_hash"],
            item["effective_policy_hash"],
        )

    def test_operational_overlays_form_one_deterministic_policy(self):
        item = derive()

        self.assertEqual(item["effective_target_service_level"], "0.99")
        self.assertEqual(item["effective_order_action"], "REVIEW")
        self.assertEqual(item["effective_protection_lead_time_basis"], "P90")
        self.assertEqual(item["effective_protection_lead_time_days"], "30")
        self.assertEqual(item["effective_approval_level"], "HIGH_VALUE")
        self.assertTrue(item["recommendation_calculation_allowed"])
        self.assertTrue(item["evidence_storage_allowed"])
        self.assertFalse(item["order_execution_allowed"])
        self.assertFalse(item["automatic_publish_allowed"])
        self.assertFalse(item["automatic_order_allowed"])
        self.assertEqual(
            item["policy_gate_reason_codes"],
            ["ORDER_ACTION_REVIEW", "HML_APPROVAL_HIGH_VALUE"],
        )
        self.assertEqual(
            [row["axis"] for row in item["effective_axis_projection"]],
            ["ABC", "XYZ", "VED", "FSN", "SDE", "HML", "PLC"],
        )
        self.assertEqual(
            validate_effective_item_policy_v2(item)["effective_policy_hash"],
            item["effective_policy_hash"],
        )

    def test_block_precedes_review_and_sets_no_order_gate(self):
        rows = axes()
        rows[-1] = axis("PLC", "DISCONTINUED")
        item = derive(axis_results=rows)

        self.assertEqual(item["effective_order_action"], "BLOCK")
        self.assertTrue(item["operational_io_eligible"])
        self.assertFalse(item["recommendation_calculation_allowed"])
        self.assertFalse(item["order_execution_allowed"])
        self.assertTrue(item["no_order_gate"])
        self.assertFalse(item["automatic_publish_allowed"])
        self.assertEqual(
            item["policy_gate_reason_codes"],
            ["ORDER_ACTION_BLOCK", "HML_APPROVAL_HIGH_VALUE"],
        )

    def test_allow_p50_and_auto_approval_enable_automatic_order(self):
        rows = axes()
        rows[3] = axis("FSN", "F")
        rows[4] = axis(
            "SDE",
            "E",
            evidence={"p50_lead_time_days": "14", "p90_lead_time_days": "30"},
        )
        rows[5] = axis("HML", "L")
        rows[6] = axis("PLC", "MATURE")
        item = derive(axis_results=rows)

        self.assertEqual(item["effective_order_action"], "ALLOW")
        self.assertEqual(item["effective_protection_lead_time_basis"], "P50")
        self.assertEqual(item["effective_protection_lead_time_days"], "14")
        self.assertEqual(item["effective_approval_level"], "AUTO")
        self.assertTrue(item["recommendation_calculation_allowed"])
        self.assertTrue(item["automatic_publish_allowed"])
        self.assertTrue(item["automatic_order_allowed"])
        self.assertTrue(item["order_execution_allowed"])
        self.assertEqual(item["policy_gate_reason_codes"], [])

    def test_unverified_operational_axis_fails_closed_but_keeps_evidence_path(self):
        rows = axes()
        rows[3] = axis("FSN", None, status="UNVERIFIED")
        item = derive(axis_results=rows)

        self.assertFalse(item["operational_io_eligible"])
        self.assertEqual(item["effective_order_action"], "BLOCK")
        self.assertFalse(item["recommendation_calculation_allowed"])
        self.assertTrue(item["evidence_storage_allowed"])
        self.assertNotIn("FSN", [row["axis"] for row in item["effective_axis_projection"]])
        self.assertEqual(
            item["policy_gate_reason_codes"],
            [
                "OPERATIONAL_FSN_UNVERIFIED",
                "ORDER_ACTION_BLOCK",
                "HML_APPROVAL_HIGH_VALUE",
            ],
        )

    def test_synthetic_plc_shadow_is_evidence_only(self):
        rows = axes()
        rows[-1] = axis(
            "PLC",
            "MATURE",
            mode="SHADOW",
            status="SYNTHETIC",
            evidence={"source_profile_hash": "f" * 64},
        )

        item = derive(axis_results=rows)

        self.assertTrue(item["operational_io_eligible"])
        self.assertNotIn("PLC", [row["axis"] for row in item["effective_axis_projection"]])
        self.assertTrue(item["evidence_storage_allowed"])

    def test_shadow_axis_and_shadow_overlay_changes_do_not_change_effective_hash(self):
        first_rows = axes(sde_mode="SHADOW", hml_mode="SHADOW")
        first = derive(axis_results=first_rows)

        replay_rows = copy.deepcopy(first_rows)
        replay_rows[4]["class_code"] = "E"
        replay_rows[4]["evidence"] = {
            "p50_lead_time_days": "2",
            "p90_lead_time_days": "8",
            "shadow_experiment": "changed",
        }
        replay_rows[5]["class_code"] = "L"
        replay_overlays = overlays()
        replay_overlays["sde_lead_time_basis"] = {"S": "P50", "D": "P50", "E": "P90"}
        replay_overlays["hml_approval_level"] = {
            "H": "AUTO",
            "M": "HIGH_VALUE",
            "L": "STANDARD",
        }
        replay = derive(axis_results=replay_rows, policy_overlays=replay_overlays)

        self.assertEqual(first["effective_policy_hash"], replay["effective_policy_hash"])
        self.assertEqual(first["effective_axis_projection"], replay["effective_axis_projection"])
        self.assertIsNone(first["effective_protection_lead_time_basis"])
        self.assertIsNone(first["effective_approval_level"])

    def test_operational_sde_requires_ordered_p50_p90_evidence(self):
        rows = axes()
        rows[4]["evidence"] = {"p50_lead_time_days": "30", "p90_lead_time_days": "14"}

        with self.assertRaisesRegex(InventoryInputError, "SDE_LEAD_TIME_QUANTILES_REQUIRED"):
            derive(axis_results=rows)

    def test_review_is_admitted_for_calculation_but_block_is_not(self):
        reviewed = admit_effective_item_policy(
            derive(),
            config_hash=CONFIG_HASH,
            execution_purpose="OPERATIONAL",
            strategy_descriptor=MATH_STRATEGY,
        )
        self.assertTrue(reviewed["recommendation_calculation_allowed"])
        self.assertFalse(reviewed["automatic_publish_allowed"])

        rows = axes()
        rows[-1] = axis("PLC", "DISCONTINUED")
        with self.assertRaisesRegex(
            InventoryInputError, "ITEM_POLICY_RECOMMENDATION_CALCULATION_BLOCKED"
        ):
            admit_effective_item_policy(
                derive(axis_results=rows),
                config_hash=CONFIG_HASH,
                execution_purpose="OPERATIONAL",
                strategy_descriptor=MATH_STRATEGY,
            )

    def test_effective_hash_tampering_fails_closed(self):
        item = derive()
        item["effective_order_action"] = "ALLOW"

        with self.assertRaisesRegex(InventoryInputError, "ITEM_POLICY_SEMANTICS_INVALID"):
            validate_effective_item_policy_v2(item)

    def test_admission_still_validates_run_config_hash_without_hashing_it_into_policy(self):
        with self.assertRaisesRegex(InventoryInputError, "INVALID_HASH"):
            admit_effective_item_policy(
                derive(),
                config_hash="not-a-hash",
                execution_purpose="OPERATIONAL",
                strategy_descriptor=MATH_STRATEGY,
            )

    def test_admission_binds_policy_to_exact_classification_config(self):
        item = derive()

        with self.assertRaisesRegex(InventoryInputError, "ITEM_POLICY_CONFIG_HASH_MISMATCH"):
            admit_effective_item_policy(
                item,
                config_hash="b" * 64,
                execution_purpose="OPERATIONAL",
                strategy_descriptor=MATH_STRATEGY,
            )

    def test_config_binding_field_cannot_be_relabelled_for_another_run(self):
        item = derive()
        item["classification_config_hash"] = "b" * 64

        with self.assertRaisesRegex(
            InventoryInputError, "ITEM_POLICY_CONFIG_BINDING_HASH_MISMATCH"
        ):
            admit_effective_item_policy(
                item,
                config_hash="b" * 64,
                execution_purpose="OPERATIONAL",
                strategy_descriptor=MATH_STRATEGY,
            )


if __name__ == "__main__":
    unittest.main()

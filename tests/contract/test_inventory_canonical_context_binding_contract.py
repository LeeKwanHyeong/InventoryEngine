"""Python and JSON Schema parity for the sealed Canonical Context Binding."""

import copy
import json
import unittest
from pathlib import Path

from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.runtime import (
    CANONICAL_CONTEXT_BINDING_FIELDS,
    seal_canonical_context_binding,
    validate_canonical_context_binding,
)
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    digest,
)
from tests.support.fixtures import build_request, golden_case

try:
    from jsonschema import Draft202012Validator, FormatChecker, ValidationError
except ImportError:
    Draft202012Validator = None
    ValidationError = Exception


ROOT = Path(__file__).resolve().parents[2]


@unittest.skipIf(Draft202012Validator is None, "jsonschema is a dev-only dependency")
class InventoryCanonicalContextBindingContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(
            (ROOT / "schemas/inventory_canonical_context_binding.schema.json").read_text()
        )
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(
            cls.schema,
            format_checker=FormatChecker(),
        )

    def test_python_and_schema_fields_match_and_sealed_value_validates(self) -> None:
        canonical = CanonicalInputRequest.from_dict(build_request(golden_case()))
        binding = seal_canonical_context_binding(canonical)

        self.assertEqual(set(self.schema["required"]), set(CANONICAL_CONTEXT_BINDING_FIELDS))
        self.assertEqual(
            binding["quantity_rules_content_hash"],
            digest(canonical.to_dict()["quantity_rules"]),
        )
        self.assertEqual(validate_canonical_context_binding(binding), binding)
        self.validator.validate(binding)

    def test_cross_repository_v2_quantity_and_context_golden_hashes(self) -> None:
        value = build_request(golden_case())
        value.update(
            contract_id="io-canonical-input-v2",
            contract_version="2.0.0",
        )
        value["context"].update(
            plan_type="POSM",
            plan_yyyyww="202601",
            plan_start_date="2025-12-29",
            plan_end_date="2026-01-25",
            master_as_of_date="2025-12-29",
            master_snapshot_revision="MST-R1",
            business_timezone="Asia/Seoul",
            inventory_cutoff_at="2025-12-29T09:00:00+09:00",
            inventory_source_watermark="ERP-WATERMARK-1",
        )
        value["quantity_rules"] = [
            {
                "uom": "KG",
                "planning_scale": 3,
                "physical_scale": 3,
                "tolerance_qty": "0.0010",
                "approval_reference": "UOM-RULE-KG-1",
            },
            {
                "uom": "EA",
                "planning_scale": 6,
                "physical_scale": 0,
                "tolerance_qty": "0.000000",
                "approval_reference": "UOM-RULE-1",
            },
        ]

        binding = seal_canonical_context_binding(CanonicalInputRequest.from_dict(value))

        self.assertEqual(binding["inventory_cutoff_at"], "2025-12-29T00:00:00Z")
        self.assertEqual(
            binding["quantity_rules_content_hash"],
            "5443675253729cc7df4b93bcc650073d61b60793d28b256caa4565d45639eb17",
        )
        self.assertEqual(
            binding["content_hash"],
            "5abd4a70320e2ccfd0cb14f15e38d411d988aa8225f0a3a3348ced6cd04639a9",
        )

    def test_tampered_hash_and_unknown_field_fail_closed(self) -> None:
        canonical = CanonicalInputRequest.from_dict(build_request(golden_case()))
        binding = seal_canonical_context_binding(canonical)

        tampered = copy.deepcopy(binding)
        tampered["inventory_source_watermark"] = "TAMPERED"
        with self.assertRaisesRegex(
            InventoryInputError,
            "CANONICAL_CONTEXT_BINDING_HASH_MISMATCH",
        ):
            validate_canonical_context_binding(tampered)

        unknown = copy.deepcopy(binding)
        unknown["unsealed_context"] = "forbidden"
        with self.assertRaises(ValidationError):
            self.validator.validate(unknown)

    def test_unstable_text_invalid_timezone_and_reversed_horizon_fail_closed(self) -> None:
        base = build_request(golden_case())
        cases = (
            (
                "inventory_source_watermark",
                " PADDED-WATERMARK ",
                "INVALID_STABLE_TEXT",
            ),
            (
                "inventory_source_watermark",
                "CONTROL\x1fWATERMARK",
                "INVALID_STABLE_TEXT",
            ),
            (
                "business_timezone",
                "Mars/Olympus_Mons",
                "INVALID_BUSINESS_TIMEZONE",
            ),
            (
                "plan_start_date",
                "2026-10-19",
                "CANONICAL_CONTEXT_BINDING_HORIZON_INVALID",
            ),
        )
        for field, replacement, code in cases:
            with self.subTest(field=field, replacement=replacement):
                changed = copy.deepcopy(base)
                changed["context"][field] = replacement
                canonical = CanonicalInputRequest.from_dict(changed)
                with self.assertRaisesRegex(InventoryInputError, code):
                    seal_canonical_context_binding(canonical)


if __name__ == "__main__":
    unittest.main()

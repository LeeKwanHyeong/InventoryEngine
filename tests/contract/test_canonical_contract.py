"""Wire schema drift and independent Golden packaging contract."""

import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fixtures import GOLDEN, ROOT, build_request

from dsio_inventory_engine.inventory_contracts.canonical import (
    CONTEXT_FIELDS,
    METADATA_FIELDS,
    ROW_FIELDS,
)

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError:
    Draft202012Validator = None


class CanonicalContractTests(unittest.TestCase):
    def test_wire_field_sets_and_golden_content_are_pinned(self):
        schema = json.loads((ROOT / "schemas/canonical_input.schema.json").read_text())
        for kind in ROW_FIELDS:
            self.assertEqual(set(schema["$defs"][kind + "_row"]["required"]), set(ROW_FIELDS[kind]))
            self.assertEqual(
                set(schema["$defs"][kind + "_metadata"]["required"]), set(METADATA_FIELDS[kind])
            )
        self.assertEqual(set(schema["properties"]["context"]["required"]), set(CONTEXT_FIELDS))
        actual = hashlib.sha256(
            (ROOT / "tests/fixtures/golden_baseline.json").read_bytes()
        ).hexdigest()
        self.assertEqual(actual, "14d7ea7343f59a125aa37154c3a2689cf353744c5930a40ff66b150ed75c226c")
        self.assertEqual(len(GOLDEN["cases"]), 14)
        example = json.loads((ROOT / "examples/baseline_input.json").read_text())
        self.assertEqual(example, build_request(GOLDEN["cases"][0]))

    @unittest.skipIf(
        Draft202012Validator is None, "JSON Schema validation requires optional dev tool jsonschema"
    )
    def test_all_golden_inputs_validate_against_json_schema(self):
        schema = json.loads((ROOT / "schemas/canonical_input.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        for case in GOLDEN["cases"]:
            with self.subTest(case=case["id"]):
                validator.validate(build_request(case))


if __name__ == "__main__":
    unittest.main()

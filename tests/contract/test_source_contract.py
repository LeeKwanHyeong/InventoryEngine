"""Source Schema, DTO and Demand physical query projection compatibility."""

import copy
import json
import sys
import unittest
from pathlib import Path

import jsonschema
from referencing import Registry, Resource

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from source_fixtures import mapped
from dsio_inventory_engine.inventory_contracts.source import SourceInputRequest, SourceSnapshot
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError

ROOT = Path(__file__).resolve().parents[2]


class SourceContractTests(unittest.TestCase):
    def validator(self, name):
        canonical = json.loads((ROOT / "schemas/canonical_input.schema.json").read_text())
        registry = Registry().with_resource(
            "canonical_input.schema.json", Resource.from_contents(canonical)
        )
        schema = json.loads((ROOT / f"schemas/{name}.schema.json").read_text())
        jsonschema.Draft202012Validator.check_schema(schema)
        return jsonschema.Draft202012Validator(schema, registry=registry)

    def test_source_schema_dto_roundtrip(self):
        req, sources = mapped()
        self.validator("source_input").validate(req)
        self.assertEqual(SourceInputRequest.from_dict(req).to_dict(), req)
        for source in sources.values():
            self.validator("source_snapshot").validate(source)
            self.assertEqual(
                SourceSnapshot.from_dict(source).to_dict()["content_hash"], source["content_hash"]
            )

    def test_missing_binding_extra_field_and_invalid_hash_rejected(self):
        for mutation in (
            lambda d: d["source_bindings"].pop("inventory"),
            lambda d: d.update(latest=True),
            lambda d: d["source_bindings"]["forecast"].update(content_hash="unknown"),
        ):
            req, _ = mapped()
            mutation(req)
            with self.assertRaises(jsonschema.ValidationError):
                self.validator("source_input").validate(req)
            with self.assertRaises(InventoryInputError):
                SourceInputRequest.from_dict(req)

    def test_source_row_limit(self):
        _, sources = mapped()
        source = copy.deepcopy(sources["forecast"])
        source["rows"] = [{}] * 100001
        with self.assertRaises(jsonschema.ValidationError):
            self.validator("source_snapshot").validate(source)
        with self.assertRaisesRegex(InventoryInputError, "SOURCE_ROW_LIMIT"):
            SourceSnapshot.from_dict(source)

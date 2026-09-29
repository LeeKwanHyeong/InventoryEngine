"""Trade Cost authoritative-source ownership and collection contract tests."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

import jsonschema

from dsio_inventory_engine.inventory_contracts.trade_cost_sources import (
    TradeCostSourceCollectionManifest,
    seal_source_collection_manifest,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError


ROOT = Path(__file__).resolve().parents[2]
REGISTER = ROOT / "docs/architecture/evidence/trade-cost-source-owner-register-c100-20260916.json"
SCHEMA = ROOT / "schemas/trade_cost_source_collection_manifest.schema.json"
HASH = "c" * 64


def draft_manifest() -> dict:
    return json.loads(REGISTER.read_text())


def approved_manifest() -> dict:
    value = draft_manifest()
    value.pop("content_hash")
    value["manifest_status"] = "APPROVED"
    value["approved_by"] = "trade-cost-governance"
    value["approved_at"] = "2026-09-16T01:00:00Z"
    for row in value["entries"]:
        row.update(
            {
                "assignment_status": "CONFIRMED",
                "source_owner": f"owner:{row['source_domain'].lower()}",
                "source_system": f"system:{row['source_domain'].lower()}",
                "collection_method": "BATCH_API",
                "collection_cadence": "ON_CHANGE",
                "expected_freshness_hours": 24,
                "raw_content_hash": HASH,
                "evidence_reference": f"evidence:{row['entry_id']}",
                "license_review_status": "APPROVED",
            }
        )
    return seal_source_collection_manifest(value)


class TradeCostSourceCollectionContractTests(unittest.TestCase):
    def test_draft_owner_register_is_hash_sealed_and_schema_valid(self):
        value = draft_manifest()
        self.assertEqual(TradeCostSourceCollectionManifest.from_dict(value).to_dict(), value)
        schema = json.loads(SCHEMA.read_text())
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.Draft202012Validator(schema).validate(value)

    def test_unassigned_source_cannot_be_approved(self):
        value = draft_manifest()
        value.pop("content_hash")
        value["manifest_status"] = "APPROVED"
        value["approved_by"] = "trade-cost-governance"
        value["approved_at"] = "2026-09-16T01:00:00Z"
        with self.assertRaisesRegex(InventoryInputError, "SOURCE_OWNER_NOT_CONFIRMED"):
            seal_source_collection_manifest(value)

    def test_approved_manifest_requires_raw_evidence_and_license_review(self):
        value = approved_manifest()
        value.pop("content_hash")
        value["entries"][0]["raw_content_hash"] = None
        with self.assertRaisesRegex(
            InventoryInputError, "SOURCE_APPROVAL_RAW_CONTENT_HASH_REQUIRED"
        ):
            seal_source_collection_manifest(value)

    def test_approved_manifest_is_deterministic(self):
        first = approved_manifest()
        second = approved_manifest()
        self.assertEqual(first["content_hash"], second["content_hash"])
        schema = json.loads(SCHEMA.read_text())
        jsonschema.Draft202012Validator(schema).validate(first)

    def test_destination_country_coverage_is_required(self):
        value = draft_manifest()
        value.pop("content_hash")
        changed = copy.deepcopy(value)
        changed["entries"][0]["jurisdiction_country_codes"] = ["AE", "CN", "JP"]
        with self.assertRaisesRegex(InventoryInputError, "DESTINATION_SOURCE_COVERAGE_INVALID"):
            seal_source_collection_manifest(changed)


if __name__ == "__main__":
    unittest.main()

"""Closed and tamper-evident Trade Cost projection contract tests."""

from __future__ import annotations

import copy
import unittest

from dsio_inventory_engine.inventory_contracts.trade_cost_projection import (
    TradeCostRevisionSetProjection,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from tests.support.trade_cost_fixtures import sealed_trade_cost_projection


class TradeCostProjectionContractTest(unittest.TestCase):
    def test_accepts_sealed_development_projection(self) -> None:
        projection = TradeCostRevisionSetProjection.from_dict(sealed_trade_cost_projection())

        value = projection.to_dict()
        self.assertTrue(value["development_eligible"])
        self.assertFalse(value["operational_eligible"])
        self.assertEqual(1, len(value["sources"]))

    def test_rejects_tampered_source_record(self) -> None:
        value = sealed_trade_cost_projection()
        value["sources"][0]["records"][0]["ad_valorem_rate"] = "0.99"

        with self.assertRaisesRegex(
            InventoryInputError,
            "TRADE_COST_SOURCE_PROJECTION_HASH_MISMATCH",
        ):
            TradeCostRevisionSetProjection.from_dict(value)

    def test_rejects_tampered_revision_set_member(self) -> None:
        value = sealed_trade_cost_projection()
        value["members"][0]["content_hash"] = "f" * 64

        with self.assertRaisesRegex(
            InventoryInputError,
            "TRADE_COST_PROJECTION_SOURCE_MEMBERSHIP_MISMATCH",
        ):
            TradeCostRevisionSetProjection.from_dict(value)

    def test_rejects_tampered_projection_hash(self) -> None:
        value = sealed_trade_cost_projection()
        value["projection_content_hash"] = "f" * 64

        with self.assertRaisesRegex(
            InventoryInputError,
            "TRADE_COST_PROJECTION_CONTENT_HASH_MISMATCH",
        ):
            TradeCostRevisionSetProjection.from_dict(value)

    def test_rejects_source_from_another_site_even_when_resealed(self) -> None:
        value = sealed_trade_cost_projection()
        source = value["sources"][0]
        source["header"]["origin_site_cd"] = "V101"
        source["content_hash"] = digest(
            {
                "header": source["header"],
                "documents": source["documents"],
                "records": source["records"],
            }
        )
        value["members"][0]["content_hash"] = source["content_hash"]
        value["revision_set_content_hash"] = digest(
            {"header": value["revision_set"], "members": value["members"]}
        )
        body = {key: item for key, item in value.items() if key != "projection_content_hash"}
        value["projection_content_hash"] = digest(body)

        with self.assertRaisesRegex(
            InventoryInputError,
            "TRADE_COST_PROJECTION_SOURCE_SCOPE_MISMATCH",
        ):
            TradeCostRevisionSetProjection.from_dict(value)

    def test_rejects_false_development_eligibility(self) -> None:
        value = copy.deepcopy(sealed_trade_cost_projection())
        value["development_eligible"] = False

        with self.assertRaisesRegex(
            InventoryInputError,
            "TRADE_COST_PROJECTION_ELIGIBILITY_INVALID",
        ):
            TradeCostRevisionSetProjection.from_dict(value)


if __name__ == "__main__":
    unittest.main()

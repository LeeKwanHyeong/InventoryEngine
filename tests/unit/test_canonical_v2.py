"""V2 fractional planning never relaxes physical stock, cut-off or order integrity."""

import sys
import json
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from fixtures import reseal
from strategy_fixtures import ContractProbe, canonical, request
from math_fixtures import build_math, reseal_math
from learned_fixtures import model_fixture, inference_request

from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from dsio_inventory_engine.prepare_inventory.application.inventory_input import (
    PrepareInventoryInputUseCase,
)
from dsio_inventory_engine.simulate_inventory.application.baseline import RunPsiSimulationUseCase
from dsio_inventory_engine.recommend_replenishment.application.run import RunRecommendedPsiUseCase
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.recommend_replenishment.application.mathematical import (
    RunMathematicalReplenishmentUseCase,
)
from dsio_inventory_engine.learned.application import RunLearnedPsiUseCase

DEPLOYMENT = DeploymentScope("DSE", "DEVELOPMENT")


def v2(value):
    value["contract_id"], value["contract_version"] = "io-canonical-input-v2", "2.0.0"
    value["quantity_rules"] = [
        {
            "uom": "EA",
            "planning_scale": 6,
            "physical_scale": 0,
            "tolerance_qty": "0",
            "approval_reference": "APPROVED-V2",
        }
    ]
    return value


class CanonicalV2Tests(unittest.TestCase):
    def test_versioned_schema(self):
        from jsonschema import Draft202012Validator

        root = Path(__file__).resolve().parents[2]
        schema = json.loads((root / "schemas/canonical_input_v2.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        data = v2(canonical(forecast=["0.4"] * 3))
        Draft202012Validator(schema).validate(data)
        data["quantity_rules"][0]["scale"] = 0
        self.assertFalse(Draft202012Validator(schema).is_valid(data))

    def test_real_mathematical_and_analytic_ml_ppo_adapters(self):
        # Analytic fixture weights exercise real inference, not a training quality claim.
        anchor = build_math()
        source = v2(anchor["recommendation"]["canonical_input"])
        for row in source["snapshots"]["forecast"]["rows"]:
            row["forecast_qty"] = "0.4"
        source = CanonicalInputRequest.from_dict(reseal(source))
        anchor["recommendation"]["canonical_input"] = source.to_dict()
        anchor["recommendation"]["execution"]["canonical_input_hash"] = source.input_hash
        anchor["policy_input"]["context"]["canonical_input_hash"] = source.input_hash
        anchor = reseal_math(anchor)
        results = [
            RunMathematicalReplenishmentUseCase(DEPLOYMENT).execute(
                MathematicalPolicyRequest.from_dict(anchor)
            )
        ]
        for family in ("PREDICTIVE_ML", "DEEP_RL"):
            results.append(
                RunLearnedPsiUseCase(DEPLOYMENT).execute(
                    inference_request(anchor, model_fixture(anchor, family))
                )
            )
        for result in results:
            self.assertTrue(
                all(Decimal(r["quantity"]) % 1 == 0 for r in result["recommended_orders"])
            )
            self.assertEqual(len(result["psi_rows"]), 3)

    def test_literal_fractional_psi(self):
        data = v2(canonical(boh="2", forecast=["0.4", "0.4", "0.4"]))
        result = RunPsiSimulationUseCase(DEPLOYMENT).execute(CanonicalInputRequest.from_dict(data))
        self.assertEqual([r["eoh_qty"] for r in result["psi_rows"]], ["1.6", "1.2", "0.8"])
        self.assertEqual(
            sum(Decimal(r["fulfilled_forecast_qty"]) for r in result["psi_rows"]), Decimal("1.2")
        )

    def test_v1_still_rejects_fractional_forecast(self):
        data = CanonicalInputRequest.from_dict(canonical(forecast=["0.4"] * 3))
        with self.assertRaisesRegex(InventoryInputError, "UOM_QUANTITY_PRECISION"):
            PrepareInventoryInputUseCase(DEPLOYMENT).execute(data)

    def test_physical_fields_and_cutoff_adjustments_remain_integer(self):
        for kind, key in (
            ("inventory", "on_hand_qty"),
            ("prior_inventory", "eoh_qty"),
            ("policies", "order_multiple"),
        ):
            with self.subTest(kind=kind):
                data = v2(canonical())
                data["snapshots"][kind]["rows"][0][key] = "0.4"
                with self.assertRaisesRegex(InventoryInputError, "UOM_QUANTITY_PRECISION"):
                    PrepareInventoryInputUseCase(DEPLOYMENT).execute(
                        CanonicalInputRequest.from_dict(reseal(data))
                    )

    def test_v2_cannot_enable_fractional_physical_ea(self):
        data = v2(canonical())
        data["quantity_rules"][0]["physical_scale"] = 1
        with self.assertRaisesRegex(InventoryInputError, "EA_REQUIRES_EXACT_INTEGER"):
            PrepareInventoryInputUseCase(DEPLOYMENT).execute(CanonicalInputRequest.from_dict(data))

    def test_each_strategy_uses_same_integer_lot_guard(self):
        for family in ("MATHEMATICAL", "PREDICTIVE_ML", "DEEP_RL"):
            with self.subTest(strategy=family):
                source = v2(canonical(boh="0", forecast=["0.4"] * 3))
                config = request(
                    source, family=family, lead_time_days=0, order_multiple="1", moq="1"
                )
                probe = ContractProbe(family=family, action="ORDER_UP_TO", quantity="1.3")
                result = RunRecommendedPsiUseCase(DEPLOYMENT).execute(config, probe)
                for row in result["recommended_orders"]:
                    self.assertEqual(Decimal(row["quantity"]) % 1, 0)

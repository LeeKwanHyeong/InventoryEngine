"""Offline interface probe. Not a mathematical policy calculator or trained ML/PPO model.

PYTHONPATH=src python examples/strategy_contract_probe.py --strategy DEEP_RL
"""

import argparse
import json
from pathlib import Path

from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.replenishment import (
    STRATEGY_TYPES,
    RecommendationRequest,
    ReplenishmentProposal,
)
from dsio_inventory_engine.inventory_contracts.values import read_json
from dsio_inventory_engine.recommend_replenishment.application.run import RunRecommendedPsiUseCase


class FixedTargetContractProbe:
    def __init__(self, family: str):
        self.descriptor = {
            "strategy_type": family,
            "implementation_id": "fixed-target-contract-probe",
            "version": "probe-v1",
            "model": None
            if family == "MATHEMATICAL"
            else {
                "model_id": "PROBE-NOT-A-TRAINED-MODEL",
                "version": "probe-v1",
                "content_hash": "a" * 64,
            },
        }

    def decide(self, observation):
        return ReplenishmentProposal.from_dict(
            {
                "contract_id": "io-replenishment-decision-v1",
                "decision_id": observation.to_dict()["decision_id"],
                "observation_hash": observation.content_hash,
                "strategy": self.descriptor,
                "action_type": "ORDER_UP_TO",
                "quantity": "200",
                "calculated_policy": {
                    "safety_stock_qty": None,
                    "rop_qty": None,
                    "target_inventory_qty": None,
                },
                "reason_codes": ["FIXED_TARGET_CONTRACT_PROBE"],
            }
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", choices=STRATEGY_TYPES, default="MATHEMATICAL")
    args = parser.parse_args()
    source = CanonicalInputRequest.from_dict(
        read_json(Path(__file__).with_name("baseline_input.json").read_text(encoding="utf-8"))
    )
    data = source.to_dict()
    strategy = FixedTargetContractProbe(args.strategy)
    request = RecommendationRequest.from_dict(
        {
            "canonical_input": data,
            "execution": {
                "contract_id": "io-replenishment-v1",
                "contract_version": "1.0.0",
                "psi_scenario_type": "RECOMMENDED",
                "execution_mode": "LOCAL_SHADOW",
                "configuration_revision": data["context"]["configuration_revision"],
                "canonical_input_hash": source.input_hash,
                "strategy": strategy.descriptor,
                "approval_reference": "PROBE-NOT-AN-OPERATIONAL-APPROVAL",
                "allowed_action_types": ["HOLD", "ORDER_UP_TO"],
                "decision_timing": "BUCKET_START_BEFORE_RECEIPTS",
                "receipt_mapping": "NEXT_BUCKET_START_ON_OR_AFTER_DUE_DATE",
                "capacity_mode": "CONSERVATIVE_NO_DEMAND_CREDIT",
                "item_controls": [
                    {
                        "item_id": r["item_id"],
                        "uom": r["uom"],
                        "max_order_qty": "1000",
                        "min_target_qty": "0",
                        "max_target_qty": "1000",
                        "order_dates": [
                            b["start_date"] for b in data["snapshots"]["calendar"]["rows"]
                        ],
                    }
                    for r in data["snapshots"]["master"]["rows"]
                ],
            },
        }
    )
    result = RunRecommendedPsiUseCase(DeploymentScope("DSE", "DEVELOPMENT")).execute(
        request, strategy
    )
    result["probe_only_not_a_trained_strategy"] = True
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

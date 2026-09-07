"""Contract probes only: these are NOT mathematical, ML or PPO implementations."""

from fixtures import build_request, reseal

from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.replenishment import (
    RecommendationRequest,
    ReplenishmentProposal,
)


def binding(family="MATHEMATICAL"):
    return {
        "strategy_type": family,
        "implementation_id": "contract-probe",
        "version": "test-v1",
        "model": None
        if family == "MATHEMATICAL"
        else {
            "model_id": "TEST-ONLY-NO-TRAINED-MODEL",
            "version": "test-v1",
            "content_hash": "a" * 64,
        },
    }


def canonical(*, boh="10", forecast=None, receipts=None, reserved="0", backorder="0", uom="EA"):
    return build_request(
        {
            "id": "strategy-contract",
            "items": [
                {
                    "id": "ITEM-A",
                    "uom": uom,
                    "boh": boh,
                    "reserved": reserved,
                    "backorder": backorder,
                    "forecast": forecast or ["0", "0", "0"],
                    "receipts": receipts or [],
                }
            ],
        }
    )


def request(source=None, family="MATHEMATICAL", **policy):
    source = source or canonical()
    source["snapshots"]["policies"]["rows"][0].update(policy)
    source = reseal(source)
    data = CanonicalInputRequest.from_dict(source)
    return RecommendationRequest.from_dict(
        {
            "canonical_input": data.to_dict(),
            "execution": {
                "contract_id": "io-replenishment-v1",
                "contract_version": "1.0.0",
                "psi_scenario_type": "RECOMMENDED",
                "execution_mode": "LOCAL_SHADOW",
                "configuration_revision": source["context"]["configuration_revision"],
                "canonical_input_hash": data.input_hash,
                "strategy": binding(family),
                "approval_reference": "TEST-FIXTURE-NOT-OPERATIONAL-APPROVAL",
                "allowed_action_types": ["HOLD", "ORDER_QTY", "ORDER_UP_TO"],
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
                            b["start_date"] for b in source["snapshots"]["calendar"]["rows"]
                        ],
                    }
                    for r in source["snapshots"]["master"]["rows"]
                ],
            },
        }
    )


class ContractProbe:
    def __init__(self, family="MATHEMATICAL", action="ORDER_UP_TO", quantity="100"):
        self.descriptor = binding(family)
        self.action = action
        self.quantity = quantity
        self.observations = []

    def decide(self, observation):
        self.observations.append(observation)
        return ReplenishmentProposal.from_dict(
            {
                "contract_id": "io-replenishment-decision-v1",
                "decision_id": observation.to_dict()["decision_id"],
                "observation_hash": observation.content_hash,
                "strategy": self.descriptor,
                "action_type": self.action,
                "quantity": self.quantity,
                "calculated_policy": {
                    "safety_stock_qty": None,
                    "rop_qty": None,
                    "target_inventory_qty": None,
                },
                "reason_codes": ["CONTRACT_PROBE_ONLY"],
            }
        )

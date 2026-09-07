"""Analytic JSON weights for hand-calculated inference goldens; no training import."""

import math

from dsio_inventory_engine.inventory_contracts.model import FAMILIES, SHAPES, ModelArtifact
from dsio_inventory_engine.inventory_contracts.values import digest


def reseal_model(data):
    data = {k: v for k, v in data.items() if k != "content_hash"}
    data["content_hash"] = digest(data)
    return data


def model_fixture(anchor, family="PREDICTIVE_ML", action=3):
    def zeros(dimensions):
        return [
            "0" if len(dimensions) == 1 else zeros(dimensions[1:]) for _ in range(dimensions[0])
        ]

    weights = {k: zeros(d) for k, d in SHAPES[family].items()}
    if family == "PREDICTIVE_ML":
        weights["linear.bias"] = [format(math.log(math.expm1(0.5)), ".17g")]
    else:
        weights["actor.bias"][action] = "1"
    context = anchor["recommendation"]["canonical_input"]["context"]
    profile = anchor["policy_input"]["profile"]
    return ModelArtifact.from_dict(
        reseal_model(
            {
                "contract_id": "io-replenishment-model-v1",
                "contract_version": "1.0.0",
                "model_id": "GOLDEN-" + family,
                "version": "1",
                "strategy_type": family,
                "algorithm": FAMILIES[family],
                "feature_contract_id": "io-learned-observation-v1",
                "scope": {
                    k: context[k]
                    for k in (
                        "company_cd",
                        "subs_cd",
                        "site_cd",
                        "plan_type",
                        "configuration_revision",
                    )
                },
                "training_data_hash": "1" * 64,
                "training_profile_hash": "2" * 64,
                "trained_through_date": "2020-01-01",
                "training_run_id": "GOLDEN-NOT-TRAINED",
                "runtime_version": "analytic-v1",
                "policy_contract": {
                    k: profile[k]
                    for k in ("lookback_weeks", "stddev_ddof", "replenishment_cycle_weeks")
                },
                "normalizer": {"mean": ["0"] * 14, "std": ["1"] * 14},
                "weights": weights,
            }
        )
    )


def inference_request(anchor, model):
    from dsio_inventory_engine.learned.application import LearnedInferenceRequest

    return LearnedInferenceRequest.from_dict(
        {
            "mathematical_request": anchor,
            "model": model.to_dict(),
            "model_reference": model.reference,
        }
    )

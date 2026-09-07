"""Bounded CPU learning + paired validation/test evaluation; no database access."""

import argparse
import json

from dsio_inventory_engine.fit_replenishment.demo import build_learning_request
from dsio_inventory_engine.inventory_contracts.model import ModelArtifact
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.training import TrainingRequest
from dsio_inventory_engine.inventory_contracts.values import digest
from dsio_inventory_engine.learned.comparison import compare_strategies


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-only", action="store_true")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--train-only", action="store_true")
    args = parser.parse_args()
    request = build_learning_request(quick=args.quick)
    if args.request_only:
        result = request.to_dict()
    else:
        from dsio_inventory_engine.fit_replenishment.train import TrainLearnedStrategiesUseCase

        scope = DeploymentScope("DSE", "DEVELOPMENT")
        result = TrainLearnedStrategiesUseCase(scope).execute(request)
        if not args.train_only:
            models = [ModelArtifact.from_dict(m) for m in result["models"]]
            training = TrainingRequest.from_dict(request.to_dict()["training_request"])
            result["validation"] = compare_strategies(training, models, scope, split="VALIDATION")
            result["test"] = compare_strategies(training, models, scope, split="TEST")
            result.pop("content_hash")
            result["content_hash"] = digest(result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

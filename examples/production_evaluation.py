"""Print a synthetic production-adapter request, or run it without database access."""

import argparse
import json

from dsio_inventory_engine.evaluate_replenishment.application import (
    EvaluateMathematicalStrategyUseCase,
)
from dsio_inventory_engine.evaluate_replenishment.demo import build_evaluation_request
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.training.demo import build_demo_request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-only", action="store_true")
    parser.add_argument("--split", choices=["TRAIN", "VALIDATION", "TEST"], default="TEST")
    parser.add_argument(
        "--scenario",
        default="BASE",
        choices=["BASE", "HIGH_SHORTAGE_COST", "SERVICE_99", "DELAY_1W", "DELAY_2W", "DISRUPTION"],
    )
    args = parser.parse_args()
    scope = DeploymentScope("DSE", "DEVELOPMENT")
    request = build_evaluation_request(
        build_demo_request(), scope, split=args.split, scenario_id=args.scenario
    )
    result = (
        request.to_dict()
        if args.request_only
        else EvaluateMathematicalStrategyUseCase(scope).execute(request)
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

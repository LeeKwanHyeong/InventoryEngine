"""Run deterministic research data preparation without DB access or model training."""

import argparse
import json

from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.training.demo import build_demo_request
from dsio_inventory_engine.training.prepare import PrepareTrainingFoundationUseCase


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-only", action="store_true")
    args = parser.parse_args()
    request = build_demo_request()
    result = (
        request.to_dict()
        if args.request_only
        else PrepareTrainingFoundationUseCase(DeploymentScope("DSE", "DEVELOPMENT")).execute(
            request
        )
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

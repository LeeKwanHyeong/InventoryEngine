"""Evaluate sealed local policy simulations; no database, deployment or order publication."""

import argparse
import json
from pathlib import Path

from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.policy_sensitivity import PolicySensitivityRequest
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, read_json
from dsio_inventory_engine.infrastructure.policy_search_artifacts import (
    LocalPolicySearchArtifactStore,
)
from dsio_inventory_engine.infrastructure.policy_sensitivity_artifacts import (
    LocalPolicySensitivityArtifactStore,
)
from dsio_inventory_engine.recommend_replenishment.application.policy_sensitivity import (
    RunPolicySensitivityUseCase,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--company-cd", default="DSE")
    args = parser.parse_args(argv)
    deployment = DeploymentScope(args.company_cd, "DEVELOPMENT")
    try:
        with args.request.open("rb") as stream:
            data = read_json(stream.read(128_000_001).decode(), max_bytes=128_000_000)
        request = PolicySensitivityRequest.from_dict(data)
        result = RunPolicySensitivityUseCase(deployment).execute(request)
        if args.artifact_root is not None:
            for case in request.to_dict()["cases"]:
                LocalPolicySearchArtifactStore(args.artifact_root).write(case["search_result"])
            receipt = LocalPolicySensitivityArtifactStore(args.artifact_root, deployment).write(
                result
            )
            print(
                json.dumps(
                    {
                        "status": result["status"],
                        "artifact": receipt,
                        "automatic_order_allowed": False,
                    },
                    sort_keys=True,
                )
            )
        else:
            print(json.dumps(result, sort_keys=True))
        return 0
    except InventoryInputError as exc:
        print(json.dumps({"status": "REJECTED", "error_code": str(exc)}))
        return 2
    except (OSError, ValueError, TypeError, KeyError):
        print(json.dumps({"status": "FAILED", "error_code": "POLICY_SENSITIVITY_EXECUTION_FAILED"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

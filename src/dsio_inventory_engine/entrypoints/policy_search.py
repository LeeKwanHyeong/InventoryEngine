"""Offline per-item policy search; JSON output or explicit append-only local artifact root."""

import argparse
import json
from pathlib import Path

from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, read_json, require
from dsio_inventory_engine.recommend_replenishment.application.policy_search import (
    RunPolicySearchUseCase,
)
from dsio_inventory_engine.infrastructure.policy_search_artifacts import (
    LocalPolicySearchArtifactStore,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--company-cd", default="DSE")
    parser.add_argument("--artifact-root", type=Path)
    args = parser.parse_args(argv)
    try:
        with args.request.open("rb") as stream:
            payload = stream.read(8_000_001)
        request = read_json(payload.decode())
        require(
            set(request) == {"mathematical_request", "config_values", "classification_snapshot"},
            "POLICY_SEARCH_REQUEST_INVALID",
        )
        result = RunPolicySearchUseCase(DeploymentScope(args.company_cd, "DEVELOPMENT")).execute(
            MathematicalPolicyRequest.from_dict(request["mathematical_request"]),
            config_values=request["config_values"],
            classification_snapshot=request["classification_snapshot"],
        )
        if args.artifact_root is not None:
            receipt = LocalPolicySearchArtifactStore(args.artifact_root).write(result)
            print(
                json.dumps(
                    {
                        "status": result["status"],
                        "candidate_count": len(result["candidate_evaluations"]),
                        "artifact": receipt,
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
        print(json.dumps({"status": "FAILED", "error_code": "POLICY_SEARCH_EXECUTION_FAILED"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

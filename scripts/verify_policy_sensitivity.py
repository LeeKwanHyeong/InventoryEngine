"""Repository-only development harness; explicit test fixtures, not an ERP Source Producer.

Run from InventoryEngine with PYTHONPATH=.:src:tests:tests/support.
An existing --artifact-root stores all simulation parents and replay-verified PSENS results.
"""

import argparse
import json
from pathlib import Path

from dsio_inventory_engine.infrastructure.policy_search_artifacts import (
    LocalPolicySearchArtifactStore,
)
from dsio_inventory_engine.infrastructure.policy_sensitivity_artifacts import (
    LocalPolicySensitivityArtifactStore,
)
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.policy_sensitivity import PolicySensitivityRequest
from dsio_inventory_engine.recommend_replenishment.application.policy_sensitivity import (
    RunPolicySensitivityUseCase,
)
from tests.support.policy_sensitivity_fixtures import long_experiment


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path)
    args = parser.parse_args()
    data = long_experiment()
    deployment = DeploymentScope("DSE", "DEVELOPMENT")
    if args.artifact_root is not None:
        for case in data["cases"]:
            LocalPolicySearchArtifactStore(args.artifact_root).write(case["search_result"])
    policy_by_id = {
        c["candidate"]["candidate_id"]: c["candidate"]
        for c in data["cases"][0]["search_result"]["candidate_evaluations"]
    }
    summaries = []
    for anchor in ("validation-13", "validation-52"):
        data["reference_case_id"] = anchor
        result = RunPolicySensitivityUseCase(deployment).execute(
            PolicySensitivityRequest.from_dict(data)
        )
        receipt = None
        if args.artifact_root is not None:
            store = LocalPolicySensitivityArtifactStore(args.artifact_root, deployment)
            receipt = store.write(result)
            assert store.read(result["content_hash"]) == result
        summaries.append(
            {
                "reference_case_id": anchor,
                "input_hash": result["input_hash"],
                "content_hash": result["content_hash"],
                "artifact": receipt,
                "validation_selections": [
                    {
                        "case_id": r["case_id"],
                        "cost_profile_id": r["cost_profile_id"],
                        "policy": policy_by_id.get(r["selected_candidate_id"]),
                    }
                    for r in result["matrix"]
                    if r["split"] == "VALIDATION" and r["cost_profile_id"] in ("base", "order-x20")
                ],
                "holdout": [
                    {
                        "case_id": r["case_id"],
                        "locked_policy": policy_by_id.get(r["locked_candidate_id"]),
                        **{
                            k: r["metrics"][k]
                            for k in (
                                "current_demand_fill_rate",
                                "current_demand_qty",
                                "current_demand_fulfilled_qty",
                                "average_on_hand_eoh_qty",
                                "order_count",
                                "total_cost",
                                "cost_per_week",
                                "service_feasible",
                            )
                        },
                    }
                    for r in result["holdout_evaluations"]
                    if r["cost_profile_id"] == "base"
                ],
                "terminal_comparisons": len(result["terminal_diagnostics"]),
                "terminal_horizon_dependent_comparisons": sum(
                    d.get("horizon_dependent_actions", False)
                    for d in result["terminal_diagnostics"]
                ),
                "database_writes": False,
                "automatic_order_allowed": False,
            }
        )
    print(
        json.dumps(
            {
                "source_type": "SYNTHETIC_DEVELOPMENT_FIXTURE",
                "sku_count": 1,
                "case_count": len(data["cases"]),
                "cost_profile_count": len(data["cost_profiles"]),
                "evaluations": summaries,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

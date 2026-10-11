"""Offline item policy selection; never change a source artifact or operating policy."""

import argparse
import json
from pathlib import Path

from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, read_json, require
from dsio_inventory_engine.infrastructure.policy_search_artifacts import (
    LocalPolicySearchArtifactStore,
)
from dsio_inventory_engine.infrastructure.policy_selection_artifacts import (
    LocalPolicySelectionArtifactStore,
)
from dsio_inventory_engine.recommend_replenishment.application.policy_selection import (
    RunPolicySelectionUseCase,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--search-result", type=Path)
    source.add_argument("--search-content-hash")
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--company-cd", default="DSE")
    args = parser.parse_args(argv)
    deployment = DeploymentScope(args.company_cd, "DEVELOPMENT")
    try:
        if args.search_result is not None:
            with args.search_result.open("rb") as stream:
                payload = stream.read(128_000_001)
            search = read_json(payload.decode(), max_bytes=128_000_000)
        else:
            require(args.artifact_root is not None, "POLICY_SELECTION_ARTIFACT_ROOT_REQUIRED")
            search = LocalPolicySearchArtifactStore(args.artifact_root).read(
                args.search_content_hash
            )
        selection = RunPolicySelectionUseCase(deployment).execute(search)
        if args.artifact_root is not None:
            # Publish the source first; the selection never refers to an unpersisted parent.
            LocalPolicySearchArtifactStore(args.artifact_root).write(search)
            receipt = LocalPolicySelectionArtifactStore(args.artifact_root, deployment).write(
                selection
            )
            print(
                json.dumps(
                    {
                        "status": selection["status"],
                        "selected_item_count": selection["selected_item_count"],
                        "not_selected_item_count": selection["not_selected_item_count"],
                        "automatic_order_allowed": False,
                        "artifact": receipt,
                    },
                    sort_keys=True,
                )
            )
        else:
            print(json.dumps(selection, sort_keys=True))
        return 0
    except InventoryInputError as exc:
        print(json.dumps({"status": "REJECTED", "error_code": str(exc)}))
        return 2
    except (OSError, ValueError, TypeError, KeyError):
        print(json.dumps({"status": "FAILED", "error_code": "POLICY_SELECTION_EXECUTION_FAILED"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

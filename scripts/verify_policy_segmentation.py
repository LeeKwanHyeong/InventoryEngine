"""Run the synthetic temporal policy-profile experiment without DB, HTTP or Runtime.

Repository-only harness. Requires PYTHONPATH=.:src:tests:tests/support:tests/unit.
Artifacts use the existing append-only PSR/PSEL stores, not a new publication path.
"""

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from dsio_inventory_engine.infrastructure.policy_search_artifacts import (
    LocalPolicySearchArtifactStore,
)
from dsio_inventory_engine.infrastructure.policy_selection_artifacts import (
    LocalPolicySelectionArtifactStore,
)
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.values import canonical_json, digest, require
from dsio_inventory_engine.evaluate_replenishment.policy_segmentation import (
    run_experiment,
    summarize_report,
)
from tests.support.policy_segmentation_fixtures import ABC_PROFILES, PROFILES, experiment_cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument(
        "--report", type=Path, help="New JSON file; an existing file is never overwritten"
    )
    args = parser.parse_args()
    if args.report is not None and (args.report.exists() or not args.report.parent.is_dir()):
        parser.error("--report must be a new file in an existing directory")
    if args.artifact_root is not None:
        LocalPolicySearchArtifactStore(args.artifact_root)
    artifacts = []
    cases = experiment_cases()
    source_archive = None
    if args.artifact_root is not None:
        source = {
            "contract_id": "inventory-policy-segmentation-synthetic-fixture-inputs",
            "contract_version": "1.0.0",
            "source_type": "SYNTHETIC_DEVELOPMENT_FIXTURE",
            "cases": json.loads(json.dumps([asdict(case) for case in cases], default=str)),
            "profiles": PROFILES,
            "abc_profiles": ABC_PROFILES,
            "operational_approved": False,
        }
        source_hash = digest(source)
        path = args.artifact_root / f"INPUTS-{source_hash}.json"
        payload = canonical_json(source) + "\n"
        try:
            with path.open("x", encoding="utf-8") as stream:
                stream.write(payload)
        except FileExistsError:
            require(path.is_file() and not path.is_symlink(), "SEGMENTATION_INPUT_ARCHIVE_PATH")
            require(
                path.read_text(encoding="utf-8") == payload, "SEGMENTATION_INPUT_ARCHIVE_CONFLICT"
            )
        source_archive = {"path": str(path), "content_hash": source_hash}

    def persist(search, selection):
        search_store = LocalPolicySearchArtifactStore(args.artifact_root)
        selection_store = LocalPolicySelectionArtifactStore(
            args.artifact_root, DeploymentScope("DSE", "DEVELOPMENT")
        )
        search_receipt = search_store.write(search)
        selection_receipt = selection_store.write(selection)
        assert search_store.read(search["content_hash"]) == search
        assert selection_store.read(selection["content_hash"]) == selection
        artifacts.append({"search": search_receipt, "selection": selection_receipt})

    result = run_experiment(
        cases,
        PROFILES,
        ABC_PROFILES,
        artifact_sink=persist if args.artifact_root is not None else None,
    )
    summary = summarize_report(result)
    if args.report is not None:
        with args.report.open("x", encoding="utf-8") as stream:
            json.dump(
                {
                    "result": result,
                    "summary": summary,
                    "artifacts": artifacts,
                    "source_archive": source_archive,
                },
                stream,
                indent=2,
                sort_keys=True,
            )
            stream.write("\n")
    print(
        json.dumps(
            {
                "source_type": result["source_type"],
                "semantic_content_hash": result["semantic_content_hash"],
                "model_hash": result["model"]["content_hash"],
                "model_depth": result["model"]["max_depth"],
                "training_rows": result["training_row_count"],
                "validation_rows": result["validation_row_count"],
                "learning_exclusions": len(result["learning_exclusions"]),
                "summary": summary,
                "training_and_validation_seconds": result["timing"][
                    "training_and_validation_elapsed_ns"
                ]
                / 10**9,
                "artifact_pairs_verified": len(artifacts),
                "source_archive": source_archive,
                "database_writes": False,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

"""Development-only comparison; writes local evidence, never contacts a database or Runtime."""

import argparse
from decimal import localcontext
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "src", ROOT / "tests", ROOT / "tests/support"):
    sys.path.insert(0, str(path))

from dsio_inventory_engine.classify_inventory.application import build_snapshot  # noqa: E402
from dsio_inventory_engine.classify_inventory.legacy_features import LegacyFeatureProducer  # noqa: E402
from dsio_inventory_engine.classify_inventory.legacy_rules import LegacyStyleRules  # noqa: E402
from dsio_inventory_engine.classify_inventory.legacy_style import LegacyFactorSegmentation  # noqa: E402
from dsio_inventory_engine.evaluate_replenishment.catalog_comparison import (  # noqa: E402
    OFFICIAL_PROTOCOL_HASH,
    run_catalog_comparison,
    validate_protocol,
)
from dsio_inventory_engine.infrastructure.catalog_selection_artifacts import (  # noqa: E402
    LocalCatalogPolicySelectionArtifactStore,
    LocalCatalogPsiArtifactStore,
)
from dsio_inventory_engine.inventory_contracts.values import canonical_json, digest, require  # noqa: E402
from dsio_inventory_engine.recommend_replenishment.application.policy_search import NUMBERS  # noqa: E402
from tests.support.catalog_comparison_fixtures import comparison_case  # noqa: E402

PROTOCOL_PATH = ROOT / "docs/architecture/data/catalog-holdout-comparison-v1.json"
RULES_PATH = ROOT / "docs/architecture/data/legacy-style-development-rules-v1.json"
MATH_PATH = ROOT / "src/dsio_inventory_engine/recommend_replenishment/application/math_policy.py"


def implementation_manifest():
    paths = sorted(
        set(ROOT.glob("src/**/*.py"))
        | set(ROOT.glob("schemas/*.json"))
        | set(ROOT.glob("tests/**/*.py"))
        | {
            ROOT / "tests/unit/test_seven_axis_segmentation.py",
            Path(__file__).resolve(),
            RULES_PATH,
        }
    )
    files = {str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest() for path in paths}
    return {"files": files, "content_hash": digest(files)}


def write_new(path, value):
    """Never overwrite inputs/results from another attempt, even on an interrupted run."""
    data = canonical_json(value).encode()
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    require(path.read_bytes() == data, "COMPARISON_EVIDENCE_READBACK")
    return {"path": path.name, "sha256": sha256(data).hexdigest(), "byte_count": len(data)}


def execute(protocol, output, *, expected_protocol_hash):
    with localcontext(NUMBERS):
        return _execute(protocol, output, expected_protocol_hash=expected_protocol_hash)


def _execute(protocol, output, *, expected_protocol_hash):
    validate_protocol(protocol, expected_protocol_hash=expected_protocol_hash)
    require(output.is_dir() and not output.is_symlink(), "COMPARISON_OUTPUT_DIRECTORY")
    require(not list(output.iterdir()), "COMPARISON_OUTPUT_NOT_EMPTY")
    (output / "artifacts").mkdir()
    before = implementation_manifest()
    build_hash = sha256(MATH_PATH.read_bytes()).hexdigest()
    rules = LegacyStyleRules.from_document(json.loads(RULES_PATH.read_text()))
    feature_producer = LegacyFeatureProducer(rules, reference_calculator_build_hash=build_hash)
    classifier = LegacyFactorSegmentation(rules, reference_calculator_build_hash=build_hash)
    cases = [
        comparison_case(protocol, period, reference_build_hash=build_hash)
        for period in protocol["periods"]
    ]
    # Archive all inputs before executing a single candidate. Future draws are evaluation-only.
    inputs = {
        "protocol": protocol,
        "protocol_hash": expected_protocol_hash,
        "implementation_manifest": before,
        "reference_calculator_build_hash": build_hash,
        "cases": [
            {
                "case_id": case.case_id,
                "planning_request": case.planning_request,
                "realized_replay_request": case.replay_request,
                "original_classification_snapshot": build_snapshot(case.classification_inputs),
                "config_values": case.classification_inputs.config_values,
                "feature_request": feature,
            }
            for case, feature in cases
        ],
        "source_type": "SYNTHETIC_DEVELOPMENT_HOLDOUT",
        "database_writes": False,
    }
    input_receipt = write_new(output / "pinned-inputs.json", inputs)
    receipts = []

    def progress(message):
        print(json.dumps(message, ensure_ascii=False), file=sys.stderr, flush=True)

    def sink(case_id, mode, source, selection, selector, request, bindings):
        progress({"phase": "STORE_AND_RESTART_VERIFY", "case_id": case_id, "mode": mode})
        parent = LocalCatalogPsiArtifactStore(output / "artifacts", selector, request, **bindings)
        source_receipt = parent.write(source)
        require(parent.write(source) == source_receipt, "COMPARISON_SOURCE_IDEMPOTENCY")
        # New instance models restart; exact source inputs are passed, never 'latest'.
        restarted = LocalCatalogPsiArtifactStore(
            output / "artifacts", selector, request, **bindings
        )
        require(restarted.read(source["content_hash"]) == source, "COMPARISON_SOURCE_RECOVERY")
        selection_receipt = None
        if selection is not None:
            store = LocalCatalogPolicySelectionArtifactStore(output / "artifacts", parent)
            selection_receipt = store.write(selection)
            require(store.write(selection) == selection_receipt, "COMPARISON_SELECTION_IDEMPOTENCY")
            recovered = LocalCatalogPolicySelectionArtifactStore(output / "artifacts", restarted)
            require(
                recovered.read(selection["content_hash"]) == selection,
                "COMPARISON_SELECTION_RECOVERY",
            )
        receipts.append(
            {
                "case_id": case_id,
                "mode": mode,
                "source": source_receipt,
                "selection": selection_receipt,
            }
        )

    result = run_catalog_comparison(
        cases,
        protocol,
        expected_protocol_hash=expected_protocol_hash,
        feature_producer=feature_producer,
        classifier=classifier,
        artifact_sink=sink,
        progress=progress,
    )
    require(implementation_manifest() == before, "COMPARISON_IMPLEMENTATION_CHANGED")
    require(
        sha256((output / "pinned-inputs.json").read_bytes()).hexdigest() == input_receipt["sha256"],
        "COMPARISON_INPUT_ARCHIVE_CHANGED",
    )
    report = {
        "result": result,
        "input_receipt": input_receipt,
        "artifact_receipts": receipts,
        "implementation_manifest_hash": before["content_hash"],
        "git_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "uncommitted_implementation_pinned_by_manifest": True,
        "timing_includes_artifact_io": False,
        "statistical_superiority_claimed": False,
    }
    receipt = write_new(output / "comparison-report.json", report)
    print(
        json.dumps(
            {
                "report": receipt,
                "semantic_content_hash": result["semantic_content_hash"],
                "summary": result["summary"],
                "timing": result["timing"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="Existing empty local directory")
    args = parser.parse_args()
    execute(
        json.loads(PROTOCOL_PATH.read_text()),
        args.output,
        expected_protocol_hash=OFFICIAL_PROTOCOL_HASH,
    )


if __name__ == "__main__":
    main()

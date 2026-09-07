"""Replay a pinned local smoke-study model against its synthetic TEST Canonical input."""

import argparse
import json
from pathlib import Path

from dsio_inventory_engine.evaluate_replenishment.demo import build_evaluation_request
from dsio_inventory_engine.inventory_contracts.model import ModelArtifact
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.values import read_json, require
from dsio_inventory_engine.learned.application import LearnedInferenceRequest, RunLearnedPsiUseCase
from dsio_inventory_engine.training.demo import build_demo_request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", choices=["PREDICTIVE_ML", "DEEP_RL"], default="PREDICTIVE_ML")
    parser.add_argument(
        "--study",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "docs/architecture/evidence/learned-strategies-smoke-20260903.json",
    )
    parser.add_argument("--request-only", action="store_true")
    args = parser.parse_args()
    require(args.study.stat().st_size <= 8_000_000, "INPUT_SIZE_LIMIT")
    study = read_json(args.study.read_text(encoding="utf-8"))
    model = ModelArtifact.from_dict(
        next(m for m in study["models"] if m["strategy_type"] == args.strategy)
    )
    expected = next(
        r for r in study["model_references"] if r["model_id"] == model.reference["model_id"]
    )
    scope = DeploymentScope("DSE", "DEVELOPMENT")
    anchor = build_evaluation_request(build_demo_request(), scope, split="TEST").to_dict()[
        "mathematical_request"
    ]
    request = LearnedInferenceRequest.from_dict(
        {"mathematical_request": anchor, "model": model.to_dict(), "model_reference": expected}
    )
    result = (
        request.to_dict() if args.request_only else RunLearnedPsiUseCase(scope).execute(request)
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

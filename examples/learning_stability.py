"""Select locally, then evaluate an independently pinned selection. No DB access."""

import argparse
import json
import sys
from pathlib import Path

from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.stability import StabilityRequest, default_request
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, read_json
from dsio_inventory_engine.stability.application import LearningStabilityUseCase


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("request", "select", "holdout", "verify-selection"))
    parser.add_argument("--request", type=Path)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--selection-hash")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.phase in ("holdout", "verify-selection") and (
        not args.selection or not args.selection_hash or args.request
    ):
        parser.error(
            "holdout/verify-selection require --selection and --selection-hash, not --request"
        )
    if args.phase in ("request", "select") and (args.selection or args.selection_hash):
        parser.error("selection arguments apply only to holdout/verify-selection")
    if args.output and args.output.exists():
        parser.error("output already exists; use a new path to preserve evidence")
    engine = LearningStabilityUseCase(
        DeploymentScope("DSE", "DEVELOPMENT"),
        lambda event: print(json.dumps(event), file=sys.stderr, flush=True),
    )
    try:
        if args.phase in ("request", "select"):
            req = (
                StabilityRequest.from_dict(read_json(args.request.read_text()))
                if args.request
                else default_request()
            )
            result = req.to_dict() if args.phase == "request" else engine.select(req)
        else:
            selection = read_json(args.selection.read_text())
            if args.phase == "verify-selection":
                _, models = engine.validate_selection(selection, args.selection_hash)
                result = {
                    "status": "HASH_AND_BINDINGS_VERIFIED",
                    "selection_content_hash": args.selection_hash,
                    "selected_model_count": len(models),
                    "operational_approval": False,
                }
            else:
                result = engine.holdout(selection, args.selection_hash)
        if args.output:
            # Exclusive local artifact creation; never overwrite prior observations.
            with args.output.open("x", encoding="utf-8") as stream:
                json.dump(result, stream, sort_keys=True, ensure_ascii=False)
                stream.write("\n")
            print(
                json.dumps({"output": str(args.output), "content_hash": result.get("content_hash")})
            )
        else:
            print(json.dumps(result, sort_keys=True, ensure_ascii=False))
        return 0
    except InventoryInputError as exc:
        print(json.dumps({"status": "REJECTED", "code": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

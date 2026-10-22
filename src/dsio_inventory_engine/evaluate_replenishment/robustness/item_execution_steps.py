"""Shared deterministic leaf execution; no I/O, Source discovery or new policies.

The existing full-memory runner and durable runner share these exact steps.
Only a verified full Catalog determines candidate membership and leaf boundaries.
"""

from dataclasses import dataclass
import json

from dsio_inventory_engine.inventory_contracts.replenishment import RecommendationRequest
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    canonical_json,
    digest,
    hash_value,
    require,
)
from .contracts import PROFILES, clone, input_errors, sealed
from .item_execution import (
    MAX_LEAF_CANDIDATES,
    MAX_LEAF_BYTES,
    CANDIDATE_FAILURES,
    CandidateStrategy,
    RunCausalPsiUseCase,
    _admission,
    _policy,
    _trace,
)
from .scoring import score_trace
from .source_binding import RealizedDemandPath


@dataclass(frozen=True)
class ItemExecutionContext:
    assembly: dict
    external: RealizedDemandPath
    recommendation: RecommendationRequest
    internal: RealizedDemandPath
    admission: dict
    dates: tuple[str, ...]
    runner: RunCausalPsiUseCase
    profile_id: str

    @property
    def leaf_count(self) -> int:
        return (
            len(self.assembly["complete_candidates"]) + MAX_LEAF_CANDIDATES - 1
        ) // MAX_LEAF_CANDIDATES


@input_errors
def prepare_context(
    preparer,
    deployment,
    assembly,
    *,
    expected_assembly_hash,
    realization,
    expected_realization_hash,
    profile_id="PRICE_BASE",
    reuse_preparation=True,
) -> ItemExecutionContext:
    value = preparer.verify(assembly, expected_assembly_hash=expected_assembly_hash)
    require(profile_id in PROFILES, "ROBUSTNESS_COST_PROFILE_INVALID")
    external = RealizedDemandPath.from_dict(realization.to_dict())
    hash_value(expected_realization_hash)
    require(external.content_hash == expected_realization_hash, "ITEM_REALIZATION_HASH_MISMATCH")
    original = RecommendationRequest.from_dict(value["mathematical_request"]["recommendation"])
    world = external.to_dict()
    require(
        world["planning_request_hash"] == original.input_hash, "ITEM_REALIZATION_PLANNING_MISMATCH"
    )
    require(len(world["rows"]) == 65, "ITEM_CONTINUOUS_65_WEEKS_REQUIRED")
    rec = RecommendationRequest.from_dict(value["candidate_recommendation"])
    internal = RealizedDemandPath.from_dict({**world, "planning_request_hash": rec.input_hash})
    dates = tuple(
        r["start_date"]
        for r in sorted(
            rec.to_dict()["canonical_input"]["snapshots"]["calendar"]["rows"],
            key=lambda r: r["seq"],
        )
    )
    return ItemExecutionContext(
        value,
        external,
        rec,
        internal,
        _admission(value),
        dates,
        RunCausalPsiUseCase(deployment, reuse_preparation=reuse_preparation),
        profile_id,
    )


@input_errors
def execute_leaf(context: ItemExecutionContext, index: int) -> dict:
    require(type(index) is int and 0 <= index < context.leaf_count, "ITEM_LEAF_INDEX_INVALID")
    value, rec = context.assembly, context.recommendation
    candidates = value["complete_candidates"]
    start = index * MAX_LEAF_CANDIDATES
    children = []
    for candidate in candidates[start : start + MAX_LEAF_CANDIDATES]:
        strategy = CandidateStrategy(
            clone(candidate),
            clone(value["history_statistics"]),
            clone(value["mathematical_request"]["policy_input"]["profile"]),
            context.dates,
            rec.input_hash,
            rec.to_dict()["execution"]["strategy_input_binding"],
            clone(context.admission),
        )
        try:
            result = context.runner.execute(
                rec,
                strategy,
                source=value["development_source"],
                source_request=value["source_request"],
                expected_source_hash=value["development_source"]["content_hash"],
                realization=context.internal,
                expected_realization_hash=context.internal.content_hash,
            )
            child = {
                "candidate": candidate,
                "status": "SUCCEEDED",
                "failure_reason": None,
                "causal_result": result,
            }
        except InventoryInputError as error:
            if str(error) not in CANDIDATE_FAILURES:
                raise
            child = {
                "candidate": candidate,
                "status": "FAILED",
                "failure_reason": str(error),
                "causal_result": None,
                "failure_evidence": json.loads(error.evidence_json),
            }
        children.append(sealed("inventory-robustness-candidate-execution", child))
    leaf = sealed(
        "inventory-robustness-item-leaf",
        {
            "assembly_hash": value["content_hash"],
            "realization_hash": context.external.content_hash,
            "leaf_index": index,
            "complete_candidate_count": len(candidates),
            "candidate_ids": [r["candidate"]["candidate_id"] for r in children],
            "candidate_executions": children,
        },
    )
    require(len(canonical_json(leaf).encode()) <= MAX_LEAF_BYTES, "ITEM_LEAF_RESOURCE_LIMIT")
    return leaf


@input_errors
def verify_leaf(
    context: ItemExecutionContext, leaf: dict, *, index: int, expected_hash: str
) -> dict:
    value = clone(leaf)
    hash_value(expected_hash)
    require(
        value.get("content_hash") == expected_hash
        and digest({k: v for k, v in value.items() if k != "content_hash"}) == expected_hash,
        "ITEM_LEAF_HASH_MISMATCH",
    )
    rebuilt = execute_leaf(context, index)
    require(rebuilt["content_hash"] == expected_hash, "ITEM_LEAF_REPLAY_MISMATCH")
    return rebuilt


@input_errors
def finish_result(context: ItemExecutionContext, leaves: list[dict]) -> dict:
    """Internal assembly of freshly executed or actual-replay-verified leaves only."""
    require(
        type(leaves) is list and len(leaves) == context.leaf_count,
        "ITEM_CANDIDATE_EXECUTION_INCOMPLETE",
    )
    value, world = context.assembly, context.external.to_dict()
    candidates = value["complete_candidates"]
    for index, leaf in enumerate(leaves):
        require(
            leaf["leaf_index"] == index
            and leaf["assembly_hash"] == value["content_hash"]
            and leaf["realization_hash"] == context.external.content_hash,
            "ITEM_LEAF_BINDING_MISMATCH",
        )
    children = [r for leaf in leaves for r in leaf["candidate_executions"]]
    require([r["candidate"] for r in children] == candidates, "ITEM_CANDIDATE_EXECUTION_INCOMPLETE")
    provenance = sealed(
        "inventory-robustness-item-source-manifest",
        {
            "assembly_hash": value["content_hash"],
            "parent_classification_hash": value["parent_classification"]["content_hash"],
            "item_projection_hash": value["item_projection"]["content_hash"],
            "complete_catalog_hash": value["candidate_catalog"]["content_hash"],
            "realization_hash": context.external.content_hash,
            "leaf_hashes": [leaf["content_hash"] for leaf in leaves],
            "candidate_execution_hashes": [r["content_hash"] for r in children],
            "durable_artifact_persisted": False,
        },
    )
    path = {
        **{k: world[k] for k in ("path_id", "split", "seed")},
        "path_content_hash": context.external.content_hash,
    }
    traces = [
        _trace(value, path, child, provenance["content_hash"], context.admission)
        for child in children
    ]
    scorecards = [score_trace(t, profile_id=context.profile_id) for t in traces]
    return sealed(
        "inventory-robustness-item-evaluation",
        {
            "assembly": value,
            "realization": world,
            "realization_hash": context.external.content_hash,
            "profile_id": context.profile_id,
            "source_manifest": provenance,
            "leaves": leaves,
            "evaluator_bank_fragment": {
                "binding": value["evaluation_binding"],
                "expected_paths": [path],
                "candidates": [_policy(c) for c in candidates],
                "traces": traces,
            },
            "path_scorecards": scorecards,
            "risk_aggregation_status": "NOT_RUN_REQUIRES_COMPLETE_SPLIT",
            "selection_status": "NOT_RUN",
            "official_paths_generated": False,
            "durable_artifact_persisted": False,
        },
    )

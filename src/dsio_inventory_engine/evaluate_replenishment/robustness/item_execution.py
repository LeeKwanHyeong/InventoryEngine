"""Execute every Catalog candidate with the real strategy, Guard and causal PSI.

One item and one supplied realization per invocation. The complete Catalog stays
intact; at most 16 candidates are held in each in-memory leaf. No path generator,
durable storage, authority lookup or operational publication is introduced here.
"""

from dsio_inventory_engine.classify_inventory.candidate_catalog import CandidateCatalogProducer
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.values import (
    digest,
    hash_value,
    require,
)
from dsio_inventory_engine.recommend_replenishment.application.effective_policy import (
    admit_effective_item_policy,
)
from dsio_inventory_engine.recommend_replenishment.application.policy_search import (
    CandidateStrategy as CandidateStrategy,
    DESCRIPTOR,
)
from .causal_simulation import RunCausalPsiUseCase as RunCausalPsiUseCase
from .contracts import POLICY_FIELDS, ROW_FIELDS, clone, input_errors
from .item_projection import PrepareItemCatalogUseCase
from .source_binding import RealizedDemandPath

MAX_LEAF_CANDIDATES = 16
MAX_LEAF_BYTES = 128_000_000
CANDIDATE_FAILURES = frozenset(
    {
        "POLICY_SEARCH_TARGET_OUTSIDE_APPROVED_BOUNDS",
        "POLICY_SEARCH_QUANTITY_RANGE",
        "STRATEGY_EXECUTION_FAILED",
    }
)


def _policy(candidate):
    return {k: candidate[k] for k in POLICY_FIELDS}


def _admission(assembly):
    original = assembly["item_projection"]["original_item"]
    parent = assembly["parent_classification"]
    admission = admit_effective_item_policy(
        original,
        config_hash=parent["config_hash"],
        execution_purpose="SHADOW",
        strategy_descriptor=DESCRIPTOR,
    )
    ved = (
        original.get("ved_class")
        if assembly["config_values"]["segmentation"]["ved"]["enabled"]
        else None
    )
    admission["search_service_level_floor"] = (
        str(assembly["config_values"]["policy_matrix"]["ved_service_level_floor"][ved])
        if ved is not None
        else "0.5"
    )
    return admission


def _trace(assembly, path, child, source_hash, admission):
    result = child["causal_result"]
    source = assembly["development_source"]
    return {
        "binding": assembly["evaluation_binding"],
        "path": path,
        "candidate": _policy(child["candidate"]),
        "source_result_hash": source_hash,
        "status": child["status"],
        "failure_reason": child["failure_reason"],
        "gate": {
            k: admission[k]
            for k in ("effective_order_action", "recommendation_calculation_allowed")
        },
        "rows": [{k: r[k] for k in ROW_FIELDS} for r in result["psi_rows"]] if result else None,
        "orders": [
            {k: r[k] for k in ("order_id", "order_date", "receipt_date", "quantity")}
            for r in result["recommended_orders"]
        ]
        if result
        else None,
        "initial_receipts": [
            {k: r[k] for k in ("receipt_id", "receipt_date", "quantity")}
            for r in source["w0_due_in"]
        ],
    }


class RunItemCatalogUseCase:
    def __init__(
        self,
        deployment: DeploymentScope,
        producer: CandidateCatalogProducer,
        *,
        reuse_preparation: bool = True,
    ):
        self.deployment = deployment
        self.preparer = PrepareItemCatalogUseCase(deployment, producer)
        require(type(reuse_preparation) is bool, "CAUSAL_REUSE_FLAG_INVALID")
        self.reuse_preparation = reuse_preparation

    @input_errors
    def execute(
        self,
        assembly: dict,
        *,
        expected_assembly_hash: str,
        realization: RealizedDemandPath,
        expected_realization_hash: str,
        profile_id: str = "PRICE_BASE",
    ) -> dict:
        from .item_execution_steps import prepare_context, execute_leaf, finish_result

        context = prepare_context(
            self.preparer,
            self.deployment,
            assembly,
            expected_assembly_hash=expected_assembly_hash,
            realization=realization,
            expected_realization_hash=expected_realization_hash,
            profile_id=profile_id,
            reuse_preparation=self.reuse_preparation,
        )
        return finish_result(context, [execute_leaf(context, i) for i in range(context.leaf_count)])

    @input_errors
    def verify(
        self,
        result: dict,
        *,
        expected_result_hash: str,
        expected_assembly_hash: str,
        expected_realization_hash: str,
    ) -> None:
        """Rebuild actual Catalog/strategy/Guard/PSI, not caller-supplied score constants."""
        hash_value(expected_result_hash)
        require(type(result) is dict, "ITEM_RESULT_FIELDS_INVALID")
        leaves = result.get("leaves")
        require(type(leaves) is list and 1 <= len(leaves) <= 6, "ITEM_RESULT_LEAF_LIMIT")
        # A full 88-candidate envelope can exceed 128MB. That is NOT permission
        # to relax the existing flat Reader/clone limit: validate bounded headers
        # and each <=16-candidate leaf separately before doing the actual replay.
        value = clone({k: v for k, v in result.items() if k != "leaves"})
        value["leaves"] = []
        for leaf in leaves:
            checked = clone(leaf)
            require(
                type(checked.get("candidate_executions")) is list
                and 1 <= len(checked["candidate_executions"]) <= MAX_LEAF_CANDIDATES,
                "ITEM_RESULT_LEAF_LIMIT",
            )
            value["leaves"].append(checked)
        require(
            value.get("content_hash") == expected_result_hash
            and digest({k: v for k, v in value.items() if k != "content_hash"})
            == expected_result_hash,
            "ITEM_RESULT_HASH_MISMATCH",
        )
        rebuilt = self.execute(
            value["assembly"],
            expected_assembly_hash=expected_assembly_hash,
            realization=RealizedDemandPath.from_dict(value["realization"]),
            expected_realization_hash=expected_realization_hash,
            profile_id=value["profile_id"],
        )
        require(expected_result_hash == rebuilt["content_hash"], "ITEM_RESULT_REPLAY_MISMATCH")

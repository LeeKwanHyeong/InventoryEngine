"""Replay exact Catalog PSI before per-item selection; no approval or source mutation."""

from decimal import Decimal, DecimalException, localcontext

from dsio_inventory_engine.classify_inventory.candidate_catalog import (
    CandidateCatalogProducer,
    OfflineCandidateCatalog,
)
from dsio_inventory_engine.inventory_contracts.catalog_selection import (
    CONTRACT_ID,
    INPUT_HASH_FIELDS,
    SOURCE_PREFIX,
    VERSION,
    compare_baseline_metrics,
    verify_catalog_selection_result,
)
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.policy_selection import RANK_FIELDS, SELECTION_RULE
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    canonical_json,
    digest,
    hash_value,
    read_json,
    require,
)
from .catalog_search import RunCatalogPolicySearchUseCase
from .policy_search import NUMBERS
from .policy_selection import (
    assess_candidate,
    rank_candidate_assessments,
    verify_candidate_evaluation,
)


class RunCatalogPolicySelectionUseCase:
    def __init__(
        self, deployment: DeploymentScope, producer: CandidateCatalogProducer, *, numerics=None
    ):
        self.deployment = deployment
        self.numerics = numerics
        self.search = RunCatalogPolicySearchUseCase(deployment, producer, numerics=numerics)

    def _source(
        self,
        source: dict,
        request: MathematicalPolicyRequest,
        *,
        expected_source_hash: str,
        catalog: OfflineCandidateCatalog,
        expected_catalog_hash: str,
        config_values: dict,
        classification_snapshot: dict,
    ) -> dict:
        require(self.deployment.environment == "DEVELOPMENT", "CATALOG_SELECTION_DEVELOPMENT_ONLY")
        hash_value(expected_source_hash)
        # A fresh bounded JSON value prevents mutation of the caller's Source during assessment.
        value = read_json(canonical_json(source), max_bytes=128_000_000)
        require(
            value.get("contract_id") == "inventory-offline-catalog-psi-result"
            and value.get("contract_version") == "1.0.0",
            "CATALOG_SELECTION_SOURCE_CONTRACT_INVALID",
        )
        self.search.verify_result(
            value,
            request,
            expected_result_hash=expected_source_hash,
            catalog=catalog,
            expected_catalog_hash=expected_catalog_hash,
            config_values=config_values,
            classification_snapshot=classification_snapshot,
        )
        # Replay also authenticates exclusions, failed candidates and Baseline metrics.
        # Resealing a changed candidate set, gate, cost or failure cannot authorize selection.
        return value

    def verify_source(self, source: dict, request: MathematicalPolicyRequest, **bindings) -> None:
        """Verify held-out intake without ranking or creating a new selection."""
        try:
            with localcontext(NUMBERS):
                self._source(source, request, **bindings)
        except InventoryInputError:
            raise
        except (KeyError, TypeError, ValueError, DecimalException):
            raise InventoryInputError("CATALOG_SELECTION_SOURCE_INVALID") from None

    def execute(self, source: dict, request: MathematicalPolicyRequest, **bindings) -> dict:
        try:
            with localcontext(NUMBERS):
                return self._select(self._source(source, request, **bindings))
        except InventoryInputError:
            raise
        except (KeyError, TypeError, ValueError, DecimalException):
            raise InventoryInputError("CATALOG_SELECTION_SOURCE_INVALID") from None

    def _select(self, source: dict) -> dict:
        excluded = {row["item_id"]: row["reason_code"] for row in source["excluded_items"]}
        grouped = {row["item_id"]: [] for row in source["baseline_items"]}
        for child in source["candidate_evaluations"]:
            grouped[child["candidate"]["item_id"]].append(child)
        items = []
        for baseline in sorted(source["baseline_items"], key=lambda row: row["item_id"]):
            item = baseline["item_id"]
            children = sorted(grouped[item], key=lambda row: row["candidate"]["candidate_id"])
            assessments, reviews = [], {}
            for child in children:
                verified = (
                    (
                        verify_candidate_evaluation(child, baseline, source)
                        if self.numerics is None
                        else self.numerics.verify_candidate(child, baseline, source)
                    )
                    if child["status"] == "SUCCEEDED"
                    else None
                )
                if verified is not None:
                    verified["minimum_fill_rate"] = Decimal(
                        source["search_config"]["minimum_fill_rate"]
                    )
                    reviews[child["candidate"]["candidate_id"]] = verified["review"]
                assessment = assess_candidate(child, verified, candidate_prefix="OCCP-")
                assessment.update(
                    verified_metrics=verified["metrics"] if verified is not None else None,
                    baseline_comparison=compare_baseline_metrics(
                        verified["metrics"], baseline["metrics"]
                    )
                    if verified is not None
                    else None,
                )
                assessments.append(assessment)
            eligible, ranked_reasons = rank_candidate_assessments(
                assessments, candidate_prefix="OCCP-"
            )
            selected = eligible[0] if eligible else None
            chosen = next(
                (
                    child
                    for child in children
                    if selected and child["candidate"]["candidate_id"] == selected["candidate_id"]
                ),
                None,
            )
            if selected:
                reasons = ranked_reasons
            elif item in excluded:
                reasons = ["SEARCH_ITEM_EXCLUDED"]
            elif not children:
                reasons = ["NO_CANDIDATES_EVALUATED"]
            elif all(row["status"] == "FAILED" for row in assessments):
                reasons = ["ALL_CANDIDATES_FAILED"]
            else:
                reasons = sorted({reason for row in assessments for reason in row["reason_codes"]})
            items.append(
                {
                    "item_id": item,
                    "status": "SELECTED" if selected else "NOT_SELECTED",
                    "reason_codes": reasons,
                    "search_exclusion_reason": excluded.get(item),
                    "baseline_metrics": baseline["metrics"],
                    "selected_candidate_id": selected["candidate_id"] if selected else None,
                    "selected_candidate_result_hash": selected["candidate_result_hash"]
                    if selected
                    else None,
                    "selected_policy": chosen["candidate"] if chosen else None,
                    "selected_score": selected["score"] if selected else None,
                    "source_review_required": reviews[selected["candidate_id"]]
                    if selected
                    else None,
                    "candidate_decisions": assessments,
                }
            )
        result = {
            "contract_id": CONTRACT_ID,
            "contract_version": VERSION,
            "status": "OFFLINE_CATALOG_POLICY_SELECTION_COMPUTED",
            "selection_algorithm": "SERVICE_COST_INVENTORY_ORDER_LEXICOGRAPHIC_V1",
            "selection_rule": SELECTION_RULE,
            "ranking_fields": list(RANK_FIELDS),
            "evaluation_mode": "DEVELOPMENT_SHADOW",
            "optimality_scope": "EVALUATED_CANDIDATE_SET_ONLY",
            "verification_profile": "EXACT_SOURCE_REPLAY_GUARD_PSI_METRICS_V1",
            "context": source["context"],
            "source_result_reference": SOURCE_PREFIX + source["content_hash"],
            "source_result_content_hash": source["content_hash"],
            "candidate_catalog_reference": "OCC-" + source["candidate_catalog_hash"],
            "candidate_catalog_hash": source["candidate_catalog_hash"],
            "candidate_mode": source["candidate_mode"],
            "minimum_fill_rate": source["search_config"]["minimum_fill_rate"],
            "terminal_condition": source["terminal_condition"],
            "classification_snapshot_id": source["classification_snapshot_id"],
            **{key: source[key] for key in INPUT_HASH_FIELDS},
            "item_selections": items,
            "selected_item_count": sum(row["status"] == "SELECTED" for row in items),
            "not_selected_item_count": sum(row["status"] == "NOT_SELECTED" for row in items),
            "automatic_publish_allowed": False,
            "automatic_order_allowed": False,
            "operational_policy_approved": False,
            "database_writes": False,
        }
        result["content_hash"] = digest(result)
        return verify_catalog_selection_result(result)

    def verify_result(
        self,
        result: dict,
        source: dict,
        request: MathematicalPolicyRequest,
        *,
        expected_selection_hash: str,
        **bindings,
    ) -> None:
        hash_value(expected_selection_hash)
        verify_catalog_selection_result(result)
        require(
            result["content_hash"] == expected_selection_hash, "CATALOG_SELECTION_HASH_MISMATCH"
        )
        expected = self.execute(source, request, **bindings)
        require(
            canonical_json(result) == canonical_json(expected), "CATALOG_SELECTION_REPLAY_MISMATCH"
        )

"""Execute verified, selected policies on the same sealed input, without publication.

This opt-in local view is not a new persisted Artifact or an operational order.
Selection provenance and candidate execution identities are reused verbatim.
"""

from __future__ import annotations

import copy
from decimal import DecimalException, localcontext

from dsio_inventory_engine.classify_inventory.candidate_catalog import (
    CandidateCatalogProducer,
    OfflineCandidateCatalog,
)
from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.order_planning import SelectedPolicyOrderPlanBinding
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    canonical_json,
    digest,
    read_json,
    require,
)
from dsio_inventory_engine.prepare_inventory.application.inventory_input import (
    PrepareInventoryInputUseCase,
)
from dsio_inventory_engine.simulate_inventory.application.baseline import _roll_forward

from . import run as common_psi
from .catalog_selection import RunCatalogPolicySelectionUseCase
from .effective_policy import admit_effective_item_policy
from .mathematical import prepare_mathematical_strategy
from .policy_search import NUMBERS, CandidateStrategy, DESCRIPTOR, RunPolicySearchUseCase
from .policy_selection import RunPolicySelectionUseCase


class RunSelectedPolicyOrderPlanUseCase:
    """No strategy injection, fallback selection, DB writes, or ERP side effects."""

    def __init__(
        self,
        deployment: DeploymentScope,
        producer: CandidateCatalogProducer | None = None,
        *,
        numerics=None,
    ) -> None:
        self.deployment = deployment
        self.producer = producer
        self.numerics = numerics

    def execute(
        self,
        binding: SelectedPolicyOrderPlanBinding,
        selection_result: dict,
        source: dict,
        request: MathematicalPolicyRequest,
        *,
        config_values: dict,
        classification_snapshot: dict,
        catalog: OfflineCandidateCatalog | None = None,
        expected_catalog_hash: str | None = None,
    ) -> dict:
        require(self.deployment.environment == "DEVELOPMENT", "ORDER_PLAN_DEVELOPMENT_ONLY")
        try:
            with localcontext(NUMBERS):
                # Freeze caller-owned values once before any verification or calculation.
                request = MathematicalPolicyRequest.from_dict(request.to_dict())
                binding = SelectedPolicyOrderPlanBinding.from_dict(binding.to_dict())
                selection = read_json(canonical_json(selection_result), max_bytes=128_000_000)
                source = read_json(canonical_json(source), max_bytes=128_000_000)
                config, classification = copy.deepcopy((config_values, classification_snapshot))
                value = binding.verify_sources(
                    selection,
                    request,
                    expected_selection_hash=binding.to_dict()["selection_result_content_hash"],
                )
                self._verify_selection(
                    value,
                    selection,
                    source,
                    request,
                    config,
                    classification,
                    catalog,
                    expected_catalog_hash,
                )
                return self._run(value, selection, source, request, classification)
        except InventoryInputError:
            raise
        except (KeyError, TypeError, ValueError, DecimalException):
            raise InventoryInputError("ORDER_PLAN_SOURCE_INVALID") from None

    def _verify_selection(
        self,
        binding: dict,
        selection: dict,
        source: dict,
        request: MathematicalPolicyRequest,
        config: dict,
        classification: dict,
        catalog: OfflineCandidateCatalog | None,
        expected_catalog_hash: str | None,
    ) -> None:
        if selection["contract_id"] == "inventory-policy-selection-result":
            require(
                catalog is None and expected_catalog_hash is None,
                "ORDER_PLAN_UNEXPECTED_CATALOG",
            )
            # V1's row verifier alone does not authenticate all exclusions/failed
            # candidates. Rebuild the original bounded Source before ranking it.
            rebuilt = RunPolicySearchUseCase(self.deployment, numerics=self.numerics).execute(
                request, config_values=config, classification_snapshot=classification
            )
            require(
                canonical_json(rebuilt) == canonical_json(source),
                "ORDER_PLAN_SOURCE_REPLAY_MISMATCH",
            )
            expected = RunPolicySelectionUseCase(self.deployment, numerics=self.numerics).execute(
                source
            )
            require(
                canonical_json(expected) == canonical_json(selection),
                "ORDER_PLAN_SELECTION_REPLAY_MISMATCH",
            )
        else:
            require(
                self.producer is not None
                and catalog is not None
                and expected_catalog_hash is not None,
                "ORDER_PLAN_CATALOG_BINDING_REQUIRED",
            )
            RunCatalogPolicySelectionUseCase(
                self.deployment, self.producer, numerics=self.numerics
            ).verify_result(
                selection,
                source,
                request,
                expected_selection_hash=binding["selection_result_content_hash"],
                expected_source_hash=selection["source_result_content_hash"],
                catalog=catalog,
                expected_catalog_hash=expected_catalog_hash,
                config_values=config,
                classification_snapshot=classification,
            )

    def _run(
        self,
        binding: dict,
        selection: dict,
        source: dict,
        request: MathematicalPolicyRequest,
        classification: dict,
    ) -> dict:
        raw = request.to_dict()
        sealed_prepared = PrepareInventoryInputUseCase(self.deployment).execute(
            CanonicalInputRequest.from_dict(raw["recommendation"]["canonical_input"])
        )
        _, prepared, report, _ = prepare_mathematical_strategy(
            request,
            self.deployment,
            prepared_input=sealed_prepared,
        )
        require(
            prepared["manifest"]["prepared_content_hash"] == source["prepared_input_hash"],
            "ORDER_PLAN_PREPARED_INPUT_MISMATCH",
        )
        baseline = (
            _roll_forward(prepared) if self.numerics is None else self.numerics.baseline(prepared)
        )
        require(
            baseline == [r for item in source["baseline_items"] for r in item["psi_rows"]],
            "ORDER_PLAN_BASELINE_REPLAY_MISMATCH",
        )
        stats = {row["item_id"]: row for row in report["history_statistics"]}
        assignments = {row["item_id"]: row for row in classification["items"]}
        chosen = {row["item_id"]: row for row in selection["item_selections"]}
        children = {
            row["candidate"]["candidate_id"]: row for row in source["candidate_evaluations"]
        }
        rows, decisions, orders, plans = [], [], [], []
        for master in sorted(prepared["master"], key=lambda row: row["item_id"]):
            item = master["item_id"]
            choice = chosen[item]
            admission = None
            if choice["status"] == "SELECTED":
                child = children[choice["selected_candidate_id"]]
                require(
                    child["status"] == "SUCCEEDED"
                    and child["content_hash"] == choice["selected_candidate_result_hash"]
                    and child["candidate"] == choice["selected_policy"],
                    "ORDER_PLAN_SELECTED_CANDIDATE_MISMATCH",
                )
                # These immutable admissions were authenticated by exact Source
                # replay; never upgrade a Shadow/REVIEW permission for this plan.
                admission = copy.deepcopy(
                    child["decision_evidence"][0]["effective_policy_admission"]
                )
                require(
                    not admission.get("no_order_gate", False)
                    and admission.get("recommendation_calculation_allowed", True),
                    "ORDER_PLAN_SELECTED_POLICY_BLOCKED",
                )
                item_rows, item_decisions, item_orders = self._run_item(
                    master,
                    child,
                    admission,
                    prepared,
                    raw,
                    stats[item],
                    numerics=self.numerics,
                )
                rows.extend(item_rows)
                decisions.extend(item_decisions)
                orders.extend(item_orders)
            else:
                # Preserve Baseline and exclusion reasons, not a made-up zero policy.
                rows.extend(copy.deepcopy([r for r in baseline if r["item_id"] == item]))
                if assignments[item].get("classification_status") == "CLASSIFIED":
                    admission = admit_effective_item_policy(
                        assignments[item],
                        config_hash=classification["config_hash"],
                        execution_purpose="SHADOW",
                        strategy_descriptor=DESCRIPTOR,
                    )
            plans.append(
                {
                    **{
                        key: copy.deepcopy(choice[key])
                        for key in (
                            "item_id",
                            "selected_candidate_id",
                            "selected_candidate_result_hash",
                            "reason_codes",
                            "search_exclusion_reason",
                            "source_review_required",
                        )
                    },
                    "status": "NOT_SELECTED"
                    if choice["status"] != "SELECTED"
                    else ("REVIEW_REQUIRED" if choice["source_review_required"] else "COMPUTED"),
                    "psi_scenario_type": "RECOMMENDED"
                    if choice["status"] == "SELECTED"
                    else "BASELINE",
                    "effective_policy_admission": admission,
                }
            )
        return {
            "order_plan_binding": binding,
            "context": copy.deepcopy(prepared["context"]),
            "status": "SELECTED_POLICY_ORDER_PLAN_COMPUTED_LOCALLY"
            if selection["selected_item_count"]
            else "NO_POLICY_SELECTED",
            # The effective-policy projection is execution-local. Keep the
            # original sealed Prepared document and its existing Hash intact.
            "prepared_input": sealed_prepared.to_dict(),
            "baseline_psi_rows": baseline,
            "item_plans": plans,
            "selected_item_count": selection["selected_item_count"],
            "not_selected_item_count": selection["not_selected_item_count"],
            "psi_rows": rows,
            "psi_content_hash": digest(rows),
            "decision_evidence": decisions,
            "decision_content_hash": digest(decisions),
            "recommended_orders": orders,
            "orders_content_hash": digest(orders),
            "automatic_publish_allowed": False,
            "automatic_order_allowed": False,
            "operational_policy_approved": False,
            "database_writes": False,
            "artifact_sealed": False,
            "evidence_persisted": False,
            "run_claimed": False,
        }

    @staticmethod
    def _run_item(
        master: dict,
        child: dict,
        admission: dict,
        prepared: dict,
        raw: dict,
        stats: dict,
        *,
        numerics=None,
    ) -> tuple[list, list, list]:
        item = master["item_id"]
        view = {
            **prepared,
            "master": [master],
            **{
                key: [r for r in prepared[key] if r["item_id"] == item]
                for key in ("demands", "policies", "receipt_decisions")
            },
        }
        original = child["decision_evidence"]
        candidate_binding = original[0]["observation"]["strategy_input_binding"]
        execution = {
            **raw["recommendation"]["execution"],
            "strategy": dict(DESCRIPTOR),
            "strategy_input_binding": candidate_binding,
        }
        strategy = CandidateStrategy(
            copy.deepcopy(child["candidate"]),
            stats,
            raw["policy_input"]["profile"],
            tuple(row["start_date"] for row in prepared["calendar"]),
            child["candidate_execution_hash"],
            candidate_binding,
            admission,
        )
        common_psi.validate_controls(
            view,
            {
                **execution,
                "item_controls": [r for r in execution["item_controls"] if r["item_id"] == item],
            },
        )
        run_simulation = common_psi.simulate if numerics is None else numerics.simulate
        rows, decisions, orders = run_simulation(
            view,
            execution,
            child["candidate_execution_hash"],
            strategy,
            admissions={item: admission},
        )
        require(
            rows == child["psi_rows"]
            and orders == child["orders"]
            and len(decisions) == len(original),
            "ORDER_PLAN_PSI_REPLAY_MISMATCH",
        )
        for current, previous in zip(decisions, original, strict=True):
            require(
                current == {k: v for k, v in previous.items() if k != "candidate_policy_evidence"},
                "ORDER_PLAN_ACTION_REPLAY_MISMATCH",
            )
            # Keep formula provenance only after freshly calculated proposal,
            # Guard, observation/state and permissions match. Never copy orders.
            current["candidate_policy_evidence"] = copy.deepcopy(
                previous["candidate_policy_evidence"]
            )
        return rows, decisions, orders

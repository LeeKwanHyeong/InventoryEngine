"""Opt-in, calculation-only C# target orchestration; no Runtime registration.

Reuse the existing strategy, admission, Guard, supply ledger, evaluation and
ranking code. Only the numerical stock transition changes. No Legacy replay,
global monkeypatch, backend fallback, new IDs or extra result hashes.
"""

from contextlib import contextmanager
import copy
from decimal import Decimal

from dsio_inventory_engine.inventory_contracts.calculation_boundary import (
    CalculationBoundaryBinding,
)
from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.order_planning import SelectedPolicyOrderPlanBinding
from dsio_inventory_engine.inventory_contracts.target_result_parts import verify_partitioned_result
from dsio_inventory_engine.inventory_contracts.replenishment import (
    RecommendationRequest,
    ReplenishmentProposal,
)
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    canonical_json,
    quantity_text,
    require,
)
from dsio_inventory_engine.prepare_inventory.application.calculation_adapter import (
    CalculationKernel,
    target_number,
    verify_target_result,
)
from dsio_inventory_engine.prepare_inventory.application.inventory_input import (
    PrepareInventoryInputUseCase,
    validate_prepared_projection,
)
from dsio_inventory_engine.simulate_inventory.application.baseline import _roll_forward
from dsio_inventory_engine.simulate_inventory.application.step import InventoryState
from . import run as common
from .catalog_search import RunCatalogPolicySearchUseCase
from .catalog_selection import RunCatalogPolicySelectionUseCase
from .policy_search import DESCRIPTOR, RunPolicySearchUseCase
from .policy_selection import RunPolicySelectionUseCase, verify_candidate_evaluation
from .selected_order_plan import RunSelectedPolicyOrderPlanUseCase
from .mathematical import RunMathematicalReplenishmentUseCase


class _AdmittedStrategy:
    """Enforce existing no-order admission before a hostile proposal reaches the ledger."""

    def __init__(self, original, admissions):
        self.original, self.admissions = original, admissions or {}
        self.descriptor, self.input_binding = original.descriptor, original.input_binding

    def decide(self, observation):
        proposal = self.original.decide(observation)
        require(isinstance(proposal, ReplenishmentProposal), "INVALID_STRATEGY_RESULT")
        proposal = ReplenishmentProposal.from_dict(proposal.to_dict())
        admission = self.admissions.get(observation.to_dict()["item_id"], {})
        require(
            not (
                admission.get("no_order_gate", False)
                or not admission.get("recommendation_calculation_allowed", True)
            )
            or proposal.to_dict()["action_type"] == "HOLD",
            "TARGET_STRATEGY_GATE_VIOLATION",
        )
        return proposal


class _TargetNumerics:
    """Request-local, explicitly wired trusted numerical dependency, not a Gate."""

    def __init__(self, deployment, kernel, recommendation, binding, *, prepared_input=None):
        require(deployment.environment == "DEVELOPMENT", "TARGET_EXECUTION_DEVELOPMENT_ONLY")
        self.recommendation = RecommendationRequest.from_dict(recommendation.to_dict())
        raw = self.recommendation.to_dict()
        self.execution = raw["execution"]
        require(self.execution["execution_mode"] == "LOCAL_SHADOW", "TARGET_EXECUTION_LOCAL_ONLY")
        self.canonical = CanonicalInputRequest.from_dict(raw["canonical_input"])
        supplied = prepared_input or PrepareInventoryInputUseCase(deployment).execute(
            self.canonical
        )
        self.prepared = validate_prepared_projection(self.canonical, supplied, deployment)
        self.base = self.prepared.to_dict()
        self.kernel = kernel
        self.build_hash = kernel.dll_sha256
        self.binding = CalculationBoundaryBinding.from_dict(binding.to_dict())
        self.binding.verify_sources(self.canonical, **self._binding_arguments(self.execution))
        self._canonical_document = self.canonical.document_json
        self._fixed_controls = copy.deepcopy(
            {
                k: v
                for k, v in self.execution.items()
                if k not in ("strategy", "strategy_input_binding")
            }
        )
        # This instance belongs to one invocation and one immutable Canonical
        # source. Only the strategy input pointer may vary between candidates;
        # it is independently normalized by CalculationBoundaryBinding below.
        self._bindings_by_strategy = {}
        self.projected = copy.deepcopy(self.base)
        # Preserve the same admission failures and effective Source policy projection.
        common.apply_effective_policy_controls(
            self.projected, self.execution, canonical_input=self.canonical
        )
        common.validate_controls(self.projected, self.execution)
        self.stage = "PREPARE"
        self.calls = {}
        self.bound = {}

    def _binding_arguments(self, execution, *, baseline=False):
        return {
            "prepared_content_hash": self.base["manifest"]["prepared_content_hash"],
            "psi_simulator": {
                "implementation_id": "inventory.psi.target_guarded.csharp",
                "version": "1.0.0",
                "implementation_content_hash": self.build_hash,
            },
            "calculation_backend": "CSHARP_PYTHONNET",
            "semantics_profile": "TARGET_GUARDED",
            "psi_scenario_type": "BASELINE" if baseline else "RECOMMENDED",
            "execution": None if baseline else execution,
        }

    def _bind(self, execution=None, *, baseline=False):
        require(self.kernel.dll_sha256 == self.build_hash, "TARGET_EXECUTION_BUILD_CHANGED")
        require(
            self.canonical.document_json == self._canonical_document,
            "TARGET_EXECUTION_INPUT_CHANGED",
        )
        if not baseline:
            require(
                {
                    k: v
                    for k, v in execution.items()
                    if k not in ("strategy", "strategy_input_binding")
                }
                == self._fixed_controls,
                "TARGET_EXECUTION_CONTROLS_CHANGED",
            )
        key = None if baseline else canonical_json(execution["strategy"])
        if key not in self._bindings_by_strategy:
            self._bindings_by_strategy[key] = CalculationBoundaryBinding.from_sources(
                self.canonical, **self._binding_arguments(execution, baseline=baseline)
            ).to_dict()
        value = copy.deepcopy(self._bindings_by_strategy[key])
        if not baseline:
            value["replenishment_execution_binding"]["strategy_input_binding"] = copy.deepcopy(
                execution["strategy_input_binding"]
            )
        value = CalculationBoundaryBinding.from_dict(value).to_dict()
        self.bound[canonical_json(value)] = copy.deepcopy(value)
        return value

    def _view(self, prepared):
        items = {r["item_id"] for r in prepared["master"]}
        require(
            bool(items) and items <= {r["item_id"] for r in self.base["master"]},
            "TARGET_EXECUTION_VIEW_MISMATCH",
        )
        for key in ("context", "calendar", "manifest", "positions"):
            require(prepared[key] == self.projected[key], "TARGET_EXECUTION_VIEW_MISMATCH")
        for key in ("master", "demands", "policies", "receipt_decisions"):
            require(
                prepared[key] == [r for r in self.projected[key] if r["item_id"] in items],
                "TARGET_EXECUTION_VIEW_MISMATCH",
            )

    @contextmanager
    def phase(self, stage):
        previous, self.stage = self.stage, stage
        try:
            yield
        finally:
            self.stage = previous

    def advance(self, state, **quantities):
        require(self.kernel.dll_sha256 == self.build_hash, "TARGET_EXECUTION_BUILD_CHANGED")
        given = {
            "available_qty": quantity_text(state.available_qty),
            "reserved_qty": quantity_text(state.reserved_qty),
            "backorder_qty": quantity_text(state.backorder_qty),
            **{k: quantity_text(v) for k, v in quantities.items()},
        }
        given.setdefault("recommended_receipt_qty", "0")
        for value in given.values():
            target_number(value)
        try:
            result = self.kernel.advance_target(given)
        except InventoryInputError:
            raise
        except Exception:
            raise InventoryInputError("TARGET_TRANSITION_FAILED") from None
        verify_target_result(given, result)
        self.calls[self.stage] = self.calls.get(self.stage, 0) + 1
        return InventoryState(
            *(Decimal(result[k]) for k in ("eoh_qty", "reserved_qty", "backorder_close_qty"))
        ), result

    def baseline(self, prepared):
        self._view(prepared)
        self._bind(baseline=True)
        return _roll_forward(prepared, advance=self.advance)

    def simulate(self, prepared, execution, input_hash, strategy, *, admissions=None):
        self._view(prepared)
        self._bind(execution)
        require(strategy.descriptor == execution["strategy"], "STRATEGY_BINDING_MISMATCH")
        require(
            getattr(strategy, "input_binding", None) == execution["strategy_input_binding"],
            "STRATEGY_INPUT_BINDING_MISMATCH",
        )
        return common.simulate(
            prepared,
            execution,
            input_hash,
            _AdmittedStrategy(strategy, admissions),
            admissions=admissions,
            advance=self.advance,
        )

    def verify_candidate(self, child, baseline, source):
        require(
            source["context"] == self.base["context"]
            and source["canonical_input_hash"] == self.canonical.input_hash
            and source["prepared_input_hash"] == self.base["manifest"]["prepared_content_hash"],
            "TARGET_EXECUTION_SOURCE_MISMATCH",
        )
        execution = {
            **self.execution,
            "strategy": dict(DESCRIPTOR),
            "strategy_input_binding": child["decision_evidence"][0]["observation"][
                "strategy_input_binding"
            ],
        }
        self._bind(execution)
        return verify_candidate_evaluation(child, baseline, source, advance=self.advance)

    def evidence(self):
        return {
            "calculation_binding": self.binding.to_dict(),
            "execution_bindings": [self.bound[k] for k in sorted(self.bound)],
            "completed_transition_calls": dict(sorted(self.calls.items())),
            "automatic_publish_allowed": False,
            "automatic_order_allowed": False,
            "database_writes": False,
            "run_claimed": False,
            "runtime_registered": False,
        }


class RunTargetRecommendedPsiUseCase:
    """Explicit local entrypoint, not a Runtime/Plan strategy or backend selector."""

    def __init__(self, deployment, kernel: CalculationKernel):
        self.deployment, self.kernel = deployment, kernel

    def execute(self, request: RecommendationRequest, strategy, binding, *, prepared_input=None):
        numerics = _TargetNumerics(
            self.deployment, self.kernel, request, binding, prepared_input=prepared_input
        )
        with numerics.phase("RECOMMENDED"):
            result = common.RunRecommendedPsiUseCase(self.deployment, numerics=numerics).execute(
                numerics.recommendation, strategy, prepared_input=numerics.prepared
            )
        return {**numerics.evidence(), "recommended_result": result}


class RunTargetMathematicalReplenishmentUseCase:
    def __init__(self, deployment, kernel: CalculationKernel):
        self.deployment, self.kernel = deployment, kernel

    def execute(self, request: MathematicalPolicyRequest, binding):
        request = MathematicalPolicyRequest.from_dict(request.to_dict())
        rec = RecommendationRequest.from_dict(request.to_dict()["recommendation"])
        numerics = _TargetNumerics(self.deployment, self.kernel, rec, binding)
        with numerics.phase("MATHEMATICAL_RECOMMENDED"):
            result = RunMathematicalReplenishmentUseCase(
                self.deployment, numerics=numerics
            ).execute(request, prepared_input=numerics.prepared)
        return {**numerics.evidence(), "recommended_result": result}


class RunTargetPolicyPipelineUseCase:
    """Closed Source -> candidate evaluation -> selection -> fresh order-plan replay."""

    def __init__(self, deployment, kernel: CalculationKernel, producer=None):
        self.deployment, self.kernel, self.producer = deployment, kernel, producer

    def execute(
        self,
        request: MathematicalPolicyRequest,
        binding,
        *,
        config_values,
        classification_snapshot,
        catalog=None,
        expected_catalog_hash=None,
    ):
        request = MathematicalPolicyRequest.from_dict(request.to_dict())
        config_values, classification_snapshot = copy.deepcopy(
            (config_values, classification_snapshot)
        )
        recommendation = RecommendationRequest.from_dict(request.to_dict()["recommendation"])
        numerics = _TargetNumerics(self.deployment, self.kernel, recommendation, binding)
        extra = {"config_values": config_values, "classification_snapshot": classification_snapshot}
        if catalog is None:
            require(
                self.producer is None and expected_catalog_hash is None,
                "TARGET_PIPELINE_UNEXPECTED_CATALOG",
            )
            search = RunPolicySearchUseCase(self.deployment, numerics=numerics)
            selector = RunPolicySelectionUseCase(self.deployment, numerics=numerics)
        else:
            require(
                self.producer is not None and expected_catalog_hash is not None,
                "TARGET_PIPELINE_CATALOG_REQUIRED",
            )
            search = RunCatalogPolicySearchUseCase(
                self.deployment, self.producer, numerics=numerics
            )
            selector = RunCatalogPolicySelectionUseCase(
                self.deployment, self.producer, numerics=numerics
            )
            extra.update(catalog=catalog, expected_catalog_hash=expected_catalog_hash)
        with numerics.phase("CANDIDATE_EVALUATION"):
            source = search.execute(request, **extra)
        with numerics.phase("POLICY_SELECTION"):
            selection = (
                selector.execute(source)
                if catalog is None
                else selector.execute(
                    source, request, expected_source_hash=source["content_hash"], **extra
                )
            )
        order_binding = SelectedPolicyOrderPlanBinding.from_sources(
            selection, request, expected_selection_hash=selection["content_hash"]
        )
        with numerics.phase("ORDER_PLAN_REPLAY"):
            order_plan = RunSelectedPolicyOrderPlanUseCase(
                self.deployment, self.producer, numerics=numerics
            ).execute(order_binding, selection, source, request, **extra)
        return {
            **numerics.evidence(),
            "search_result": source,
            "selection_result": selection,
            "order_plan": order_plan,
        }

    def verify_result(self, result, request, binding, **sources):
        # Recompute every stage, including failed/excluded candidates, with the
        # pinned C# Build. Re-sealing a Python/mixed/changed result cannot admit it.
        expected = self.execute(request, binding, **sources)
        verify_partitioned_result(result, expected)

"""Deterministic PSI child artifacts and row-derived Bundle comparisons.

This module is deliberately storage-neutral.  It produces immutable JSON-shaped
documents and stable logical references; a later Artifact Writer is responsible
for persisting the exact documents at those references.
"""

from __future__ import annotations

import copy
from datetime import date, timedelta
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from typing import Any, Mapping

from dsio_inventory_engine.inventory_contracts.replenishment import (
    STRATEGY_INPUT_BINDING_FIELDS,
    ReplenishmentObservation,
    ReplenishmentProposal,
    descriptor,
)
from dsio_inventory_engine.inventory_contracts.result_bundle import (
    derive_psi_child_result_id as derive_contract_psi_child_result_id,
    validate_optional_child_failure_semantics,
)
from dsio_inventory_engine.inventory_contracts.values import (
    day,
    decimal_string,
    digest,
    hash_value,
    identifier,
    integer,
    item_identifier,
    quantity_text,
    require,
    shape,
    yyyyww,
)
from dsio_inventory_engine.recommend_replenishment.application.guard import validate_action
from dsio_inventory_engine.recommend_replenishment.application.effective_policy import (
    admit_effective_item_policy,
)


PSI_RESULT_ARTIFACT_CONTRACT_ID = "inventory-psi-result-v1"
PSI_RESULT_ARTIFACT_CONTRACT_KEY = "inventory.psi_result"
PSI_RESULT_ARTIFACT_CONTRACT_VERSION = "1.0.0"
ACTION_EVIDENCE_ARTIFACT_CONTRACT_ID = "inventory-action-evidence-v1"
ACTION_EVIDENCE_ARTIFACT_CONTRACT_VERSION = "1.0.0"
ACTION_OBSERVATION_SOURCE_CONTRACT_ID = "inventory-action-observation-source-v1"

_NUMBERS = Context(prec=40, rounding=ROUND_HALF_EVEN)
_SERVICE_QUANTUM = Decimal("0.000001")
# A row remains capped at 1e12 by ``decimal_string``.  Aggregate metrics may
# legitimately sum two demand fields across the 100 million row contract cap.
_MAX_METRIC_QUANTITY = Decimal("200000000000000000000")
_RESULT_KINDS = {"BASELINE_PSI", "RECOMMENDED_PSI", "STRESS_PSI"}
_EXECUTION_ROLES = {"OPERATIONAL", "SHADOW", "EVIDENCE_ONLY"}
_STRATEGY_TYPES = {"NONE", "MATHEMATICAL", "DEEP_RL"}
_REQUIRED_QUANTITIES = (
    "boh_qty",
    "reserved_qty",
    "on_hand_boh_qty",
    "confirmed_supplier_receipt_qty",
    "recommended_receipt_qty",
    "available_qty",
    "backorder_open_qty",
    "confirmed_customer_order_qty",
    "net_forecast_qty",
    "fulfilled_backorder_qty",
    "fulfilled_confirmed_customer_order_qty",
    "fulfilled_forecast_qty",
    "eoh_qty",
    "on_hand_eoh_qty",
    "backorder_close_qty",
    "forecast_shortage_qty",
)
_PSI_ROW_COMMON_FIELDS = {
    "company_cd",
    "subs_cd",
    "site_cd",
    "item_id",
    "uom",
    "yyyyww",
    "seq",
    "start_date",
    "end_date",
    "base_month",
    "gross_forecast_qty",
    "forecast_consumed_qty",
    "forecast_netting_mode",
    "source_snapshot_id",
    "source_content_hash",
    "psi_scenario_type",
    *_REQUIRED_QUANTITIES,
}
_PSI_ROW_STRATEGY_FIELDS = {
    "strategy_type",
    "physical_capacity_excess_qty",
}
_NO_ACTIONS = {
    "raw_action_reference": None,
    "raw_action_content_hash": None,
    "constrained_action_reference": None,
    "constrained_action_content_hash": None,
    "adjustment_reasons_reference": None,
    "adjustment_reasons_content_hash": None,
}
_PSI_ARTIFACT_BODY_FIELDS = {
    "contract_id",
    "contract_version",
    "artifact_contract_key",
    "engine_run_id",
    "attempt_no",
    "child_result_id",
    "canonical_input_hash",
    "result_kind",
    "execution_role",
    "strategy_type",
    "scenario_id",
    "scenario_content_hash",
    "challenger_id",
    "model_content_hash",
    "row_count",
    "metrics",
    "action_evidence",
    "psi_rows",
}
_ACTION_ARTIFACT_BODY_FIELDS = {
    "contract_id",
    "contract_version",
    "evidence_kind",
    "child_result_id",
    "canonical_input_hash",
    "result_kind",
    "execution_role",
    "strategy_type",
    "scenario_id",
    "scenario_content_hash",
    "challenger_id",
    "model_content_hash",
    "row_count",
    "observation_source",
    "rows",
}
_ACTION_VALIDATION_FIELDS = {
    "decision_id",
    "item_id",
    "uom",
    "decision_date",
    "action_type",
    "requested_qty",
    "inventory_position_qty",
    "raw_order_qty",
    "lot_rounded_qty",
    "accepted_order_qty",
    "uncovered_order_qty",
    "due_date",
    "receipt_date",
    "receipt_bucket",
    "capacity_headroom_qty",
    "status",
    "reason_codes",
    "source_policy_id",
    "source_policy",
    "calculated_policy",
    "effective_target_inventory_qty",
    "approval_reference",
    "capacity_mode",
}
_ACTION_STATUSES = {"NO_ORDER", "REJECTED", "ACCEPTED", "ADJUSTED"}
_EARLY_REJECTION_REASONS = {
    "ACTION_NOT_APPROVED",
    "TARGET_OUTSIDE_APPROVED_BOUNDS",
    "ORDER_CALENDAR_CLOSED",
}
_ARRIVAL_REJECTION_REASON = "ARRIVAL_OUTSIDE_PLAN_HORIZON"
_FEASIBILITY_REJECTION_REASON = "NO_FEASIBLE_ORDER_QUANTITY"
_ADJUSTMENT_REASON_FIELDS = {
    "decision_id",
    "item_id",
    "uom",
    "decision_date",
    "status",
    "reason_codes",
    "requested_qty",
    "raw_order_qty",
    "lot_rounded_qty",
    "accepted_order_qty",
}


def derive_psi_child_result_id(
    *,
    engine_run_id: str,
    attempt_no: int,
    canonical_input_hash: str,
    result_kind: str,
    execution_role: str,
    strategy_type: str,
    scenario_id: str,
    scenario_content_hash: str,
    challenger_id: str | None = None,
    model_content_hash: str | None = None,
) -> str:
    """Derive a replay-stable identity without depending on artifact content."""

    identity = _semantic_identity(
        engine_run_id=engine_run_id,
        attempt_no=attempt_no,
        canonical_input_hash=canonical_input_hash,
        result_kind=result_kind,
        execution_role=execution_role,
        strategy_type=strategy_type,
        scenario_id=scenario_id,
        scenario_content_hash=scenario_content_hash,
        challenger_id=challenger_id,
        model_content_hash=model_content_hash,
    )
    return derive_contract_psi_child_result_id(
        **{key: value for key, value in identity.items() if key != "contract_id"}
    )


def summarize_psi_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate auditable service, ending-stock and backorder facts from PSI rows."""

    _, metrics = _normalize_and_summarize(rows)
    return metrics


def seal_action_observation_source(
    *,
    prepared_input: Mapping[str, Any],
    execution: Mapping[str, Any],
    observation_input_hash: str,
    admissions_by_item: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Seal the authoritative Prepared/Execution projection used by action evidence."""

    require(
        type(prepared_input) is dict and type(execution) is dict,
        "ACTION_OBSERVATION_SOURCE_INVALID",
    )
    manifest = prepared_input.get("manifest")
    require(type(manifest) is dict, "ACTION_OBSERVATION_SOURCE_INVALID")
    normalized_admissions = (
        None
        if admissions_by_item is None
        else {
            item_identifier(item_id): copy.deepcopy(admission)
            for item_id, admission in sorted(admissions_by_item.items())
        }
    )
    allowed_actions = copy.deepcopy(execution.get("allowed_action_types"))
    require(
        type(allowed_actions) is list
        and allowed_actions == sorted(allowed_actions)
        and len(allowed_actions) == len(set(allowed_actions))
        and "HOLD" in allowed_actions
        and set(allowed_actions) <= {"HOLD", "ORDER_QTY", "ORDER_UP_TO"},
        "ACTION_OBSERVATION_SOURCE_INVALID",
    )
    body = {
        "contract_id": ACTION_OBSERVATION_SOURCE_CONTRACT_ID,
        "canonical_input_hash": hash_value(manifest.get("input_content_hash")),
        "prepared_input_content_hash": hash_value(manifest.get("prepared_content_hash")),
        "observation_input_hash": hash_value(observation_input_hash),
        "context": copy.deepcopy(prepared_input.get("context")),
        "calendar": copy.deepcopy(prepared_input.get("calendar")),
        "demands": copy.deepcopy(prepared_input.get("demands")),
        "positions": copy.deepcopy(prepared_input.get("positions")),
        "policies": copy.deepcopy(prepared_input.get("policies")),
        "receipt_decisions": copy.deepcopy(prepared_input.get("receipt_decisions")),
        "quantity_rules": copy.deepcopy(manifest.get("quantity_rules")),
        "item_controls": copy.deepcopy(execution.get("item_controls")),
        "strategy": descriptor(dict(execution.get("strategy", {}))),
        "execution_approval_reference": identifier(execution.get("approval_reference")),
        "allowed_action_types": allowed_actions,
        "capacity_mode": identifier(execution.get("capacity_mode")),
        "strategy_input_binding": copy.deepcopy(execution.get("strategy_input_binding")),
        "execution_purpose": identifier(execution.get("execution_purpose")),
        "effective_policy_binding": copy.deepcopy(execution.get("effective_policy_binding")),
        "effective_item_policies": copy.deepcopy(execution.get("effective_item_policies")),
        "model_approval": copy.deepcopy(execution.get("model_approval")),
        "admissions_by_item": normalized_admissions,
    }
    require(
        body["capacity_mode"] == "CONSERVATIVE_NO_DEMAND_CREDIT",
        "ACTION_OBSERVATION_SOURCE_INVALID",
    )
    for key in (
        "context",
        "calendar",
        "demands",
        "positions",
        "policies",
        "receipt_decisions",
        "quantity_rules",
        "item_controls",
    ):
        require(
            type(body[key]) is dict if key == "context" else type(body[key]) is list,
            "ACTION_OBSERVATION_SOURCE_INVALID",
        )
    return {**body, "content_hash": digest(body)}


def build_psi_child_artifact(
    *,
    engine_run_id: str,
    attempt_no: int,
    canonical_input_hash: str,
    result_kind: str,
    execution_role: str,
    strategy_type: str,
    scenario_id: str,
    scenario_content_hash: str,
    psi_rows: list[dict[str, Any]],
    decision_evidence: list[dict[str, Any]] | None = None,
    challenger_id: str | None = None,
    model_content_hash: str | None = None,
    expected_strategy_descriptor: Mapping[str, Any] | None = None,
    expected_execution_approval_reference: str | None = None,
    expected_observation_input_hash: str | None = None,
    expected_strategy_input_binding: Mapping[str, Any] | None = None,
    expected_admissions_by_item: Mapping[str, Mapping[str, Any]] | None = None,
    expected_model_approval_reference: str | None = None,
    expected_observation_source: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one successful Child Result plus its in-memory sealed artifacts."""

    identity = _semantic_identity(
        engine_run_id=engine_run_id,
        attempt_no=attempt_no,
        canonical_input_hash=canonical_input_hash,
        result_kind=result_kind,
        execution_role=execution_role,
        strategy_type=strategy_type,
        scenario_id=scenario_id,
        scenario_content_hash=scenario_content_hash,
        challenger_id=challenger_id,
        model_content_hash=model_content_hash,
    )
    child_result_id = f"IOC-{digest(identity)}"
    normalized_rows, metrics = _normalize_and_summarize(psi_rows)
    _validate_row_semantics(normalized_rows, result_kind=result_kind, strategy_type=strategy_type)

    base_reference = _artifact_base_reference(identity, child_result_id)
    action_bindings, action_artifacts = _build_action_artifacts(
        child_result_id=child_result_id,
        canonical_input_hash=identity["canonical_input_hash"],
        result_kind=identity["result_kind"],
        execution_role=identity["execution_role"],
        strategy_type=identity["strategy_type"],
        scenario_id=identity["scenario_id"],
        scenario_content_hash=identity["scenario_content_hash"],
        challenger_id=identity["challenger_id"],
        model_content_hash=identity["model_content_hash"],
        base_reference=base_reference,
        decision_evidence=decision_evidence,
        expected_decisions={
            (row["item_id"], row["uom"], row["start_date"]) for row in normalized_rows
        },
        psi_rows=normalized_rows,
        expected_strategy_descriptor=expected_strategy_descriptor,
        expected_execution_approval_reference=expected_execution_approval_reference,
        expected_observation_input_hash=expected_observation_input_hash,
        expected_strategy_input_binding=expected_strategy_input_binding,
        expected_admissions_by_item=expected_admissions_by_item,
        expected_model_approval_reference=expected_model_approval_reference,
        expected_observation_source=expected_observation_source,
    )
    artifact_reference = f"{base_reference}:psi"
    artifact_body = {
        "contract_id": PSI_RESULT_ARTIFACT_CONTRACT_ID,
        "contract_version": PSI_RESULT_ARTIFACT_CONTRACT_VERSION,
        "artifact_contract_key": PSI_RESULT_ARTIFACT_CONTRACT_KEY,
        "engine_run_id": identity["engine_run_id"],
        "attempt_no": identity["attempt_no"],
        "child_result_id": child_result_id,
        "canonical_input_hash": identity["canonical_input_hash"],
        "result_kind": identity["result_kind"],
        "execution_role": identity["execution_role"],
        "strategy_type": identity["strategy_type"],
        "scenario_id": identity["scenario_id"],
        "scenario_content_hash": identity["scenario_content_hash"],
        "challenger_id": identity["challenger_id"],
        "model_content_hash": identity["model_content_hash"],
        "row_count": len(normalized_rows),
        "metrics": metrics,
        "action_evidence": action_bindings,
        "psi_rows": normalized_rows,
    }
    artifact = {**artifact_body, "content_hash": digest(artifact_body)}
    child_result = {
        "child_result_id": child_result_id,
        "result_kind": identity["result_kind"],
        "execution_role": identity["execution_role"],
        "strategy_type": identity["strategy_type"],
        "scenario_id": identity["scenario_id"],
        "scenario_content_hash": identity["scenario_content_hash"],
        "challenger_id": identity["challenger_id"],
        "model_content_hash": identity["model_content_hash"],
        "status": "SUCCEEDED",
        "artifact_reference": artifact_reference,
        "artifact_contract_key": PSI_RESULT_ARTIFACT_CONTRACT_KEY,
        "artifact_contract_version": PSI_RESULT_ARTIFACT_CONTRACT_VERSION,
        "child_content_hash": artifact["content_hash"],
        "row_count": len(normalized_rows),
        "failure_reason_code": None,
        "action_evidence": action_bindings,
    }
    result = {
        "child_result": child_result,
        "artifact_reference": artifact_reference,
        "artifact": artifact,
        "metrics": metrics,
        "action_artifacts": action_artifacts,
    }
    _validate_successful_build(result)
    return copy.deepcopy(result)


def build_failed_psi_child(
    *,
    engine_run_id: str,
    attempt_no: int,
    canonical_input_hash: str,
    result_kind: str,
    execution_role: str,
    strategy_type: str,
    scenario_id: str,
    scenario_content_hash: str,
    failure_reason_code: str,
    status: str = "FAILED",
    challenger_id: str | None = None,
    model_content_hash: str | None = None,
) -> dict[str, Any]:
    """Build a failed/skipped optional Child without fabricating artifacts or metrics."""

    identity = _semantic_identity(
        engine_run_id=engine_run_id,
        attempt_no=attempt_no,
        canonical_input_hash=canonical_input_hash,
        result_kind=result_kind,
        execution_role=execution_role,
        strategy_type=strategy_type,
        scenario_id=scenario_id,
        scenario_content_hash=scenario_content_hash,
        challenger_id=challenger_id,
        model_content_hash=model_content_hash,
    )
    require(
        not (
            result_kind == "BASELINE_PSI"
            or (
                result_kind == "RECOMMENDED_PSI"
                and execution_role == "OPERATIONAL"
                and strategy_type == "MATHEMATICAL"
            )
        ),
        "REQUIRED_PSI_CHILD_CANNOT_FAIL",
    )
    require(status in {"FAILED", "SKIPPED"}, "PSI_CHILD_FAILURE_STATUS_INVALID")
    validate_optional_child_failure_semantics(
        result_kind=identity["result_kind"],
        execution_role=identity["execution_role"],
        strategy_type=identity["strategy_type"],
        status=status,
        failure_reason_code=failure_reason_code,
    )
    child_result_id = f"IOC-{digest(identity)}"
    result = {
        "child_result": {
            "child_result_id": child_result_id,
            "result_kind": identity["result_kind"],
            "execution_role": identity["execution_role"],
            "strategy_type": identity["strategy_type"],
            "scenario_id": identity["scenario_id"],
            "scenario_content_hash": identity["scenario_content_hash"],
            "challenger_id": identity["challenger_id"],
            "model_content_hash": identity["model_content_hash"],
            "status": status,
            "artifact_reference": None,
            "artifact_contract_key": None,
            "artifact_contract_version": None,
            "child_content_hash": None,
            "row_count": None,
            "failure_reason_code": identifier(failure_reason_code),
            "action_evidence": dict(_NO_ACTIONS),
        },
        "artifact_reference": None,
        "artifact": None,
        "metrics": None,
        "action_artifacts": {},
    }
    return copy.deepcopy(result)


def compare_psi_children(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    cost_profile: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Derive Result Bundle V1 deltas; callers cannot inject metric values."""

    baseline_build = _validate_successful_build(baseline)
    baseline_child = baseline_build["child_result"]
    require(
        baseline_child["result_kind"] == "BASELINE_PSI"
        and baseline_child["execution_role"] == "EVIDENCE_ONLY"
        and baseline_child["strategy_type"] == "NONE",
        "PSI_COMPARISON_BASELINE_REQUIRED",
    )
    require(type(candidate) is dict, "PSI_CHILD_BUILD_INVALID")
    candidate_child = candidate.get("child_result")
    require(type(candidate_child) is dict, "PSI_CHILD_BUILD_INVALID")
    status = candidate_child.get("status")
    require(status in {"SUCCEEDED", "FAILED", "SKIPPED"}, "PSI_CHILD_STATUS_INVALID")
    if status != "SUCCEEDED":
        require(
            candidate.get("artifact") is None
            and candidate.get("artifact_reference") is None
            and candidate.get("metrics") is None
            and candidate.get("action_artifacts") == {},
            "FAILED_PSI_CHILD_ARTIFACT_FORBIDDEN",
        )
        reason = "CANDIDATE_RESULT_FAILED" if status == "FAILED" else "CANDIDATE_RESULT_SKIPPED"
        unavailable = _unavailable(reason)
        return {
            "baseline_child_result_id": baseline_child["child_result_id"],
            "candidate_child_result_id": identifier(candidate_child.get("child_result_id")),
            "cost_delta": dict(unavailable),
            "service_level_delta": dict(unavailable),
            "backorder_qty_delta": dict(unavailable),
        }

    candidate_build = _validate_successful_build(candidate)
    candidate_child = candidate_build["child_result"]
    require(
        candidate_child["result_kind"] in {"RECOMMENDED_PSI", "STRESS_PSI"},
        "PSI_COMPARISON_CANDIDATE_INVALID",
    )
    require(
        candidate_build["artifact"]["engine_run_id"] == baseline_build["artifact"]["engine_run_id"]
        and candidate_build["artifact"]["attempt_no"] == baseline_build["artifact"]["attempt_no"]
        and candidate_build["artifact"]["canonical_input_hash"]
        == baseline_build["artifact"]["canonical_input_hash"],
        "PSI_COMPARISON_CANONICAL_INPUT_MISMATCH",
    )
    require(
        _psi_universe(candidate_build) == _psi_universe(baseline_build),
        "PSI_COMPARISON_UNIVERSE_MISMATCH",
    )
    if candidate_child["result_kind"] == "RECOMMENDED_PSI":
        _validate_base_scenario_exogenous_inputs(baseline_build, candidate_build)
    baseline_metrics = baseline_build["artifact"]["metrics"]
    candidate_metrics = candidate_build["artifact"]["metrics"]
    require(
        baseline_metrics["uoms"] == candidate_metrics["uoms"],
        "PSI_COMPARISON_UOM_UNIVERSE_MISMATCH",
    )
    require(len(baseline_metrics["uoms"]) == 1, "PSI_COMPARISON_MIXED_UOM_UNSUPPORTED")

    with localcontext(_NUMBERS):
        baseline_demand = Decimal(baseline_metrics["horizon_demand_qty"])
        candidate_demand = Decimal(candidate_metrics["horizon_demand_qty"])
        service_delta = (
            _unavailable("NO_DEMAND_IN_HORIZON")
            if baseline_demand == 0 or candidate_demand == 0
            else _available(
                _service_delta_text(
                    Decimal(baseline_metrics["on_time_fulfilled_qty"]),
                    baseline_demand,
                    Decimal(candidate_metrics["on_time_fulfilled_qty"]),
                    candidate_demand,
                )
            )
        )
        backorder_delta = Decimal(candidate_metrics["ending_backorder_qty"]) - Decimal(
            baseline_metrics["ending_backorder_qty"]
        )
    cost_delta = _unavailable("COST_PROFILE_NOT_BOUND")
    if cost_profile is not None:
        cost_delta = _available(
            _signed_quantity_text(
                _psi_cost(candidate_build, cost_profile) - _psi_cost(baseline_build, cost_profile)
            )
        )
    return {
        "baseline_child_result_id": baseline_child["child_result_id"],
        "candidate_child_result_id": candidate_child["child_result_id"],
        "cost_delta": cost_delta,
        "service_level_delta": service_delta,
        "backorder_qty_delta": _available(_signed_quantity_text(backorder_delta)),
    }


def compare_psi_children_detailed(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    cost_profile: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return the V1 Bundle projection plus ending-inventory comparison evidence."""

    bundle_comparison = compare_psi_children(
        baseline,
        candidate,
        cost_profile=cost_profile,
    )
    candidate_child = candidate["child_result"]
    if candidate_child["status"] != "SUCCEEDED":
        reason = (
            "CANDIDATE_RESULT_FAILED"
            if candidate_child["status"] == "FAILED"
            else "CANDIDATE_RESULT_SKIPPED"
        )
        available_delta = _unavailable(reason)
        on_hand_delta = _unavailable(reason)
    else:
        baseline_build = _validate_successful_build(baseline)
        candidate_build = _validate_successful_build(candidate)
        baseline_metrics = baseline_build["metrics"]
        candidate_metrics = candidate_build["metrics"]
        require(
            baseline_metrics["uoms"] == candidate_metrics["uoms"],
            "PSI_COMPARISON_UOM_UNIVERSE_MISMATCH",
        )
        require(
            len(baseline_metrics["uoms"]) == 1,
            "PSI_COMPARISON_MIXED_UOM_UNSUPPORTED",
        )
        with localcontext(_NUMBERS):
            available_delta = _available(
                _signed_quantity_text(
                    Decimal(candidate_metrics["ending_available_inventory_qty"])
                    - Decimal(baseline_metrics["ending_available_inventory_qty"])
                )
            )
            on_hand_delta = _available(
                _signed_quantity_text(
                    Decimal(candidate_metrics["ending_on_hand_inventory_qty"])
                    - Decimal(baseline_metrics["ending_on_hand_inventory_qty"])
                )
            )
    return {
        "bundle_comparison": bundle_comparison,
        "ending_available_inventory_qty_delta": available_delta,
        "ending_on_hand_inventory_qty_delta": on_hand_delta,
    }


def _psi_cost(build: Mapping[str, Any], profile: Mapping[str, str]) -> Decimal:
    return calculate_psi_operating_cost(build, profile)


def calculate_psi_operating_cost(build: Mapping[str, Any], profile: Mapping[str, str]) -> Decimal:
    """Calculate one auditable horizon cost from PSI and constrained actions."""

    require(
        type(profile) is dict
        and set(profile)
        == {
            "currency",
            "holding_cost_per_unit_week",
            "backorder_cost_per_unit_week",
            "fixed_order_cost",
            "variable_order_cost_per_unit",
        },
        "COST_PROFILE_PAYLOAD_INVALID",
    )
    with localcontext(_NUMBERS):
        holding_units = sum(
            (Decimal(row["on_hand_eoh_qty"]) for row in build["artifact"]["psi_rows"]),
            Decimal(0),
        )
        backorder_units = sum(
            (Decimal(row["backorder_close_qty"]) for row in build["artifact"]["psi_rows"]),
            Decimal(0),
        )
        order_count = Decimal(0)
        order_units = Decimal(0)
        constrained = build["action_artifacts"].get("constrained_action")
        if constrained is not None:
            for row in constrained["artifact"]["rows"]:
                accepted = Decimal(row["accepted_order_qty"])
                if accepted > 0:
                    order_count += 1
                    order_units += accepted
        return (
            holding_units * Decimal(profile["holding_cost_per_unit_week"])
            + backorder_units * Decimal(profile["backorder_cost_per_unit_week"])
            + order_count * Decimal(profile["fixed_order_cost"])
            + order_units * Decimal(profile["variable_order_cost_per_unit"])
        )


def _semantic_identity(
    *,
    engine_run_id: str,
    attempt_no: int,
    canonical_input_hash: str,
    result_kind: str,
    execution_role: str,
    strategy_type: str,
    scenario_id: str,
    scenario_content_hash: str,
    challenger_id: str | None,
    model_content_hash: str | None,
) -> dict[str, Any]:
    run = identifier(engine_run_id)
    attempt = integer(attempt_no)
    require(attempt > 0, "INVALID_POSITIVE_INTEGER")
    canonical_hash = hash_value(canonical_input_hash)
    scenario = identifier(scenario_id)
    scenario_hash = hash_value(scenario_content_hash)
    require(result_kind in _RESULT_KINDS, "INVALID_RESULT_KIND")
    require(execution_role in _EXECUTION_ROLES, "INVALID_EXECUTION_ROLE")
    require(strategy_type in _STRATEGY_TYPES, "INVALID_RESULT_STRATEGY_TYPE")
    challenger = None if challenger_id is None else identifier(challenger_id)
    model_hash = None if model_content_hash is None else hash_value(model_content_hash)
    if strategy_type == "DEEP_RL":
        require(
            challenger is not None and model_hash is not None,
            "PSI_CHILD_MODEL_BINDING_REQUIRED",
        )
    else:
        require(challenger is None and model_hash is None, "PSI_CHILD_MODEL_BINDING_FORBIDDEN")
    if result_kind == "BASELINE_PSI":
        require(
            execution_role == "EVIDENCE_ONLY" and strategy_type == "NONE" and scenario == "BASE",
            "BASELINE_RESULT_SEMANTICS_INVALID",
        )
    elif result_kind == "RECOMMENDED_PSI":
        require(
            (
                execution_role == "OPERATIONAL"
                and strategy_type == "MATHEMATICAL"
                and scenario == "BASE"
            )
            or (execution_role == "SHADOW" and strategy_type == "DEEP_RL" and scenario == "BASE"),
            "RECOMMENDED_RESULT_SEMANTICS_INVALID",
        )
    else:
        require(
            execution_role == "EVIDENCE_ONLY" and scenario != "BASE",
            "STRESS_RESULT_ROLE_INVALID",
        )
    return {
        "contract_id": "inventory-psi-child-identity-v1",
        "engine_run_id": run,
        "attempt_no": attempt,
        "canonical_input_hash": canonical_hash,
        "result_kind": result_kind,
        "execution_role": execution_role,
        "strategy_type": strategy_type,
        "scenario_id": scenario,
        "scenario_content_hash": scenario_hash,
        "challenger_id": challenger,
        "model_content_hash": model_hash,
    }


def _normalize_and_summarize(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    require(type(rows) is list and 0 < len(rows) <= 100_000_000, "PSI_ARTIFACT_ROWS_INVALID")
    normalized = [_normalize_row(row) for row in rows]
    normalized.sort(key=_row_key)
    keys = [_row_key(row) for row in normalized]
    require(len(set(keys)) == len(keys), "PSI_ARTIFACT_ROW_DUPLICATE")
    scopes = {(row["company_cd"], row["subs_cd"], row["site_cd"]) for row in normalized}
    require(len(scopes) == 1, "PSI_ARTIFACT_SCOPE_MISMATCH")

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in normalized:
        grouped.setdefault((row["item_id"], row["uom"]), []).append(row)
    signatures = []
    for item_rows in grouped.values():
        ordered = sorted(item_rows, key=lambda row: (row["seq"], row["yyyyww"]))
        sequence = [row["seq"] for row in ordered]
        require(sequence == list(range(len(sequence))), "PSI_ARTIFACT_BUCKET_SEQUENCE_INVALID")
        _validate_bucket_calendar(ordered)
        _validate_item_roll_forward(ordered)
        signatures.append(
            tuple(
                (
                    row["seq"],
                    row["yyyyww"],
                    row["start_date"],
                    row["end_date"],
                )
                for row in ordered
            )
        )
    require(len(set(signatures)) == 1, "PSI_ARTIFACT_HORIZON_MISMATCH")

    with localcontext(_NUMBERS):
        by_item = [
            _summarize_rows(item_rows, item_id=item_id, uom=uom)
            for (item_id, uom), item_rows in sorted(grouped.items())
        ]
        by_uom = []
        for uom in sorted({row["uom"] for row in normalized}):
            uom_rows = [row for row in normalized if row["uom"] == uom]
            by_uom.append(_summarize_rows(uom_rows, item_id=None, uom=uom))
    uoms = [row["uom"] for row in by_uom]
    scalar = by_uom[0] if len(by_uom) == 1 else None
    metrics = {
        "uoms": uoms,
        "horizon_demand_qty": None if scalar is None else scalar["horizon_demand_qty"],
        "on_time_fulfilled_qty": None if scalar is None else scalar["on_time_fulfilled_qty"],
        "projected_service_level": None if scalar is None else scalar["projected_service_level"],
        "ending_available_inventory_qty": (
            None if scalar is None else scalar["ending_available_inventory_qty"]
        ),
        "ending_on_hand_inventory_qty": (
            None if scalar is None else scalar["ending_on_hand_inventory_qty"]
        ),
        "ending_backorder_qty": None if scalar is None else scalar["ending_backorder_qty"],
        "forecast_shortage_qty": None if scalar is None else scalar["forecast_shortage_qty"],
        "by_uom": by_uom,
        "by_item": by_item,
    }
    return normalized, metrics


def _normalize_row(value: Any) -> dict[str, Any]:
    require(type(value) is dict, "PSI_ARTIFACT_ROW_INVALID")
    scenario_type = value.get("psi_scenario_type")
    expected_fields = (
        _PSI_ROW_COMMON_FIELDS
        if scenario_type == "BASELINE"
        else _PSI_ROW_COMMON_FIELDS | _PSI_ROW_STRATEGY_FIELDS
    )
    require(set(value) == expected_fields, "PSI_ARTIFACT_ROW_CONTRACT_INVALID")
    row = copy.deepcopy(value)
    for key in (
        "company_cd",
        "subs_cd",
        "site_cd",
        "uom",
        "psi_scenario_type",
    ):
        row[key] = identifier(row.get(key))
    row["item_id"] = item_identifier(row.get("item_id"))
    row["yyyyww"] = yyyyww(row.get("yyyyww"))
    row["seq"] = integer(row.get("seq"))
    row["base_month"] = identifier(row.get("base_month"))
    row["forecast_netting_mode"] = identifier(row.get("forecast_netting_mode"))
    row["source_snapshot_id"] = identifier(row.get("source_snapshot_id"))
    row["source_content_hash"] = hash_value(row.get("source_content_hash"))
    if "strategy_type" in row:
        row["strategy_type"] = identifier(row.get("strategy_type"))
    for key in ("start_date", "end_date"):
        row[key] = day(row.get(key))
    for key, value in list(row.items()):
        if key.endswith("_qty") and value is not None:
            row[key] = decimal_string(value)
    for key in _REQUIRED_QUANTITIES:
        require(key in row, "PSI_ARTIFACT_METRIC_FIELD_MISSING")
    return row


def _row_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        row["company_cd"],
        row["subs_cd"],
        row["site_cd"],
        row["item_id"],
        row["uom"],
        row["seq"],
        row["yyyyww"],
    )


def _summarize_rows(rows: list[dict[str, Any]], *, item_id: str | None, uom: str) -> dict[str, Any]:
    with localcontext(_NUMBERS):
        return _summarize_rows_in_context(rows, item_id=item_id, uom=uom)


def _summarize_rows_in_context(
    rows: list[dict[str, Any]], *, item_id: str | None, uom: str
) -> dict[str, Any]:
    demand = Decimal(0)
    fulfilled = Decimal(0)
    shortage = Decimal(0)
    terminal: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        confirmed = Decimal(row["confirmed_customer_order_qty"])
        forecast = Decimal(row["net_forecast_qty"])
        fulfilled_confirmed = Decimal(row["fulfilled_confirmed_customer_order_qty"])
        fulfilled_forecast = Decimal(row["fulfilled_forecast_qty"])
        require(
            fulfilled_confirmed <= confirmed and fulfilled_forecast <= forecast,
            "PSI_ARTIFACT_FULFILLMENT_INVALID",
        )
        demand += confirmed + forecast
        fulfilled += fulfilled_confirmed + fulfilled_forecast
        shortage += Decimal(row["forecast_shortage_qty"])
        key = (row["item_id"], row["uom"])
        if key not in terminal or row["seq"] > terminal[key]["seq"]:
            terminal[key] = row
    ending_available = sum((Decimal(row["eoh_qty"]) for row in terminal.values()), Decimal(0))
    ending_on_hand = sum((Decimal(row["on_hand_eoh_qty"]) for row in terminal.values()), Decimal(0))
    ending_backorder = sum(
        (Decimal(row["backorder_close_qty"]) for row in terminal.values()), Decimal(0)
    )
    result = {
        "uom": uom,
        "horizon_demand_qty": _metric_quantity_text(demand),
        "on_time_fulfilled_qty": _metric_quantity_text(fulfilled),
        "projected_service_level": _service_level_text(fulfilled, demand) if demand else None,
        "ending_available_inventory_qty": _metric_quantity_text(ending_available),
        "ending_on_hand_inventory_qty": _metric_quantity_text(ending_on_hand),
        "ending_backorder_qty": _metric_quantity_text(ending_backorder),
        "forecast_shortage_qty": _metric_quantity_text(shortage),
    }
    if item_id is not None:
        result = {"item_id": item_id, **result}
    return result


def _validate_bucket_calendar(rows: list[dict[str, Any]]) -> None:
    """Validate the pinned business buckets without deriving ISO week keys."""

    require(
        len({row["yyyyww"] for row in rows}) == len(rows),
        "PSI_ARTIFACT_BUCKET_SEQUENCE_INVALID",
    )
    previous_end: date | None = None
    for row in rows:
        start = date.fromisoformat(row["start_date"])
        end = date.fromisoformat(row["end_date"])
        require(
            0 <= (end - start).days <= 6,
            "PSI_ARTIFACT_BUCKET_RANGE_INVALID",
        )
        require(
            previous_end is None or start == previous_end + timedelta(days=1),
            "PSI_ARTIFACT_BUCKET_CONTINUITY_INVALID",
        )
        previous_end = end


def _validate_item_roll_forward(rows: list[dict[str, Any]]) -> None:
    with localcontext(_NUMBERS):
        _validate_item_roll_forward_in_context(rows)


def _validate_item_roll_forward_in_context(rows: list[dict[str, Any]]) -> None:
    previous: dict[str, Any] | None = None
    for row in rows:
        quantities = {key: Decimal(row[key]) for key in _REQUIRED_QUANTITIES}
        require(
            all(value.is_finite() and value >= 0 for value in quantities.values()),
            "PSI_ARTIFACT_NEGATIVE_OR_NONFINITE_QUANTITY",
        )
        require(
            quantities["on_hand_boh_qty"] == quantities["boh_qty"] + quantities["reserved_qty"]
            and quantities["available_qty"]
            == quantities["boh_qty"]
            + quantities["confirmed_supplier_receipt_qty"]
            + quantities["recommended_receipt_qty"]
            and quantities["available_qty"]
            == quantities["eoh_qty"]
            + quantities["fulfilled_backorder_qty"]
            + quantities["fulfilled_confirmed_customer_order_qty"]
            + quantities["fulfilled_forecast_qty"]
            and quantities["on_hand_eoh_qty"] == quantities["eoh_qty"] + quantities["reserved_qty"],
            "PSI_ARTIFACT_STOCK_CONSERVATION_INVALID",
        )
        require(
            quantities["backorder_open_qty"] + quantities["confirmed_customer_order_qty"]
            == quantities["backorder_close_qty"]
            + quantities["fulfilled_backorder_qty"]
            + quantities["fulfilled_confirmed_customer_order_qty"],
            "PSI_ARTIFACT_ORDER_CONSERVATION_INVALID",
        )
        require(
            quantities["forecast_shortage_qty"]
            == quantities["net_forecast_qty"] - quantities["fulfilled_forecast_qty"],
            "PSI_ARTIFACT_FORECAST_CONSERVATION_INVALID",
        )
        fulfilled_backorder = min(
            quantities["available_qty"],
            quantities["backorder_open_qty"],
        )
        after_backorder = quantities["available_qty"] - fulfilled_backorder
        fulfilled_confirmed = min(
            after_backorder,
            quantities["confirmed_customer_order_qty"],
        )
        after_confirmed = after_backorder - fulfilled_confirmed
        fulfilled_forecast = min(
            after_confirmed,
            quantities["net_forecast_qty"],
        )
        require(
            quantities["fulfilled_backorder_qty"] == fulfilled_backorder
            and quantities["fulfilled_confirmed_customer_order_qty"] == fulfilled_confirmed
            and quantities["fulfilled_forecast_qty"] == fulfilled_forecast,
            "PSI_ARTIFACT_FULFILLMENT_PRIORITY_INVALID",
        )
        if previous is not None:
            require(
                quantities["boh_qty"] == Decimal(previous["eoh_qty"])
                and quantities["backorder_open_qty"] == Decimal(previous["backorder_close_qty"])
                and quantities["reserved_qty"] == Decimal(previous["reserved_qty"]),
                "PSI_ARTIFACT_ROLL_FORWARD_INVALID",
            )
        previous = row


def _validate_row_semantics(
    rows: list[dict[str, Any]], *, result_kind: str, strategy_type: str
) -> None:
    if result_kind == "BASELINE_PSI":
        require(
            all(
                row["psi_scenario_type"] == "BASELINE"
                and Decimal(row["recommended_receipt_qty"]) == 0
                for row in rows
            ),
            "BASELINE_PSI_ROW_SEMANTICS_INVALID",
        )
    elif result_kind == "RECOMMENDED_PSI":
        require(
            all(
                row["psi_scenario_type"] == "RECOMMENDED"
                and row.get("strategy_type") == strategy_type
                for row in rows
            ),
            "RECOMMENDED_PSI_ROW_SEMANTICS_INVALID",
        )
    else:
        require(
            all(
                row["psi_scenario_type"] == "STRESS" and row.get("strategy_type") == strategy_type
                for row in rows
            ),
            "STRESS_PSI_ROW_SEMANTICS_INVALID",
        )


def _build_action_artifacts(
    *,
    child_result_id: str,
    canonical_input_hash: str,
    result_kind: str,
    execution_role: str,
    strategy_type: str,
    scenario_id: str,
    scenario_content_hash: str,
    challenger_id: str | None,
    model_content_hash: str | None,
    base_reference: str,
    decision_evidence: list[dict[str, Any]] | None,
    expected_decisions: set[tuple[str, str, str]],
    psi_rows: list[dict[str, Any]],
    expected_strategy_descriptor: Mapping[str, Any] | None,
    expected_execution_approval_reference: str | None,
    expected_observation_input_hash: str | None,
    expected_strategy_input_binding: Mapping[str, Any] | None,
    expected_admissions_by_item: Mapping[str, Mapping[str, Any]] | None,
    expected_model_approval_reference: str | None,
    expected_observation_source: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    decisions = [] if decision_evidence is None else copy.deepcopy(decision_evidence)
    require(type(decisions) is list, "ACTION_EVIDENCE_INVALID")
    if strategy_type == "NONE":
        require(
            not decisions
            and expected_strategy_descriptor is None
            and expected_execution_approval_reference is None
            and expected_observation_input_hash is None
            and expected_strategy_input_binding is None
            and expected_admissions_by_item is None
            and expected_model_approval_reference is None
            and expected_observation_source is None,
            "ACTION_EVIDENCE_FORBIDDEN",
        )
        return dict(_NO_ACTIONS), {}
    require(
        expected_strategy_descriptor is not None
        and expected_execution_approval_reference is not None,
        "ACTION_EVIDENCE_EXPECTED_BINDING_REQUIRED",
    )
    require(
        expected_observation_input_hash is not None,
        "ACTION_EVIDENCE_EXPECTED_BINDING_REQUIRED",
    )
    require(
        expected_observation_source is not None,
        "ACTION_EVIDENCE_OBSERVATION_SOURCE_REQUIRED",
    )
    require(len(decisions) == len(expected_decisions), "ACTION_EVIDENCE_ROW_COUNT_MISMATCH")
    raw_rows, constrained_rows, adjustment_rows = _normalize_action_pairs(
        decisions,
        expected_decisions=expected_decisions,
        psi_rows=psi_rows,
        canonical_input_hash=canonical_input_hash,
        expected_observation_input_hash=expected_observation_input_hash,
        strategy_type=strategy_type,
        model_content_hash=model_content_hash,
        expected_strategy_descriptor=expected_strategy_descriptor,
        expected_execution_approval_reference=expected_execution_approval_reference,
        expected_strategy_input_binding=expected_strategy_input_binding,
        expected_admissions_by_item=expected_admissions_by_item,
        expected_model_approval_reference=expected_model_approval_reference,
        expected_observation_source=expected_observation_source,
        execution_role=execution_role,
    )
    normalized_source = _normalize_observation_source(expected_observation_source)
    specs = (
        ("raw_action", "RAW_ACTION", raw_rows, normalized_source),
        ("constrained_action", "CONSTRAINED_ACTION", constrained_rows, None),
        ("adjustment_reasons", "ADJUSTMENT_REASONS", adjustment_rows, None),
    )
    bindings: dict[str, Any] = {}
    artifacts: dict[str, dict[str, Any]] = {}
    for prefix, evidence_kind, evidence_rows, observation_source in specs:
        reference = f"{base_reference}:{prefix.replace('_', '-')}"
        body = {
            "contract_id": ACTION_EVIDENCE_ARTIFACT_CONTRACT_ID,
            "contract_version": ACTION_EVIDENCE_ARTIFACT_CONTRACT_VERSION,
            "evidence_kind": evidence_kind,
            "child_result_id": child_result_id,
            "canonical_input_hash": canonical_input_hash,
            "result_kind": result_kind,
            "execution_role": execution_role,
            "strategy_type": strategy_type,
            "scenario_id": scenario_id,
            "scenario_content_hash": scenario_content_hash,
            "challenger_id": challenger_id,
            "model_content_hash": model_content_hash,
            "row_count": len(evidence_rows),
            "observation_source": observation_source,
            "rows": evidence_rows,
        }
        artifact = {**body, "content_hash": digest(body)}
        bindings[f"{prefix}_reference"] = reference
        bindings[f"{prefix}_content_hash"] = artifact["content_hash"]
        artifacts[prefix] = {"reference": reference, "artifact": artifact}
    return bindings, artifacts


def _normalize_action_pairs(
    decisions: list[dict[str, Any]],
    *,
    expected_decisions: set[tuple[str, str, str]],
    psi_rows: list[dict[str, Any]],
    canonical_input_hash: str,
    expected_observation_input_hash: str,
    strategy_type: str,
    model_content_hash: str | None,
    expected_strategy_descriptor: Mapping[str, Any],
    expected_execution_approval_reference: str,
    expected_strategy_input_binding: Mapping[str, Any] | None,
    expected_admissions_by_item: Mapping[str, Mapping[str, Any]] | None,
    expected_model_approval_reference: str | None,
    expected_observation_source: Mapping[str, Any],
    execution_role: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    expected_descriptor = descriptor(dict(expected_strategy_descriptor))
    execution_approval = identifier(expected_execution_approval_reference)
    observation_input_hash = hash_value(expected_observation_input_hash)
    expected_binding = (
        None
        if expected_strategy_input_binding is None
        else shape(dict(expected_strategy_input_binding), STRATEGY_INPUT_BINDING_FIELDS)
    )
    admissions = (
        None
        if expected_admissions_by_item is None
        else {
            item_identifier(key): copy.deepcopy(value)
            for key, value in expected_admissions_by_item.items()
        }
    )
    if expected_model_approval_reference is not None:
        expected_model_approval_reference = identifier(expected_model_approval_reference)
        require(admissions is not None, "ACTION_EVIDENCE_ADMISSION_REQUIRED")
    source = _normalize_observation_source(expected_observation_source)
    require(
        source["canonical_input_hash"] == canonical_input_hash
        and source["observation_input_hash"] == observation_input_hash
        and source["strategy"] == expected_descriptor
        and source["execution_approval_reference"] == execution_approval
        and source["strategy_input_binding"] == expected_binding
        and source["admissions_by_item"] == admissions,
        "ACTION_OBSERVATION_SOURCE_BINDING_MISMATCH",
    )
    _validate_psi_source_binding(psi_rows, source)
    expected_purpose = "OPERATIONAL" if execution_role == "OPERATIONAL" else "SHADOW"
    require(
        source["execution_purpose"] == expected_purpose,
        "ACTION_EXECUTION_PURPOSE_MISMATCH",
    )
    if admissions is not None:
        require(
            set(admissions) == {item for item, _uom, _date in expected_decisions},
            "ACTION_EVIDENCE_ADMISSION_UNIVERSE_MISMATCH",
        )
    _validate_admissions_authority(admissions, source)

    indexed: list[tuple[str, dict[str, Any], dict[str, Any], dict[str, Any] | None]] = []
    actual_decisions: set[tuple[str, str, str]] = set()
    for decision in decisions:
        expected_fields = {"observation", "observation_hash", "proposal", "validation"}
        if admissions is not None:
            expected_fields.add("effective_policy_admission")
        require(
            type(decision) is dict and set(decision) == expected_fields, "ACTION_EVIDENCE_INVALID"
        )
        observation = ReplenishmentObservation.from_dict(decision["observation"]).to_dict()
        observation_hash = hash_value(decision["observation_hash"])
        proposal = ReplenishmentProposal.from_dict(decision["proposal"]).to_dict()
        require(
            digest(observation) == observation_hash == proposal["observation_hash"],
            "ACTION_OBSERVATION_HASH_MISMATCH",
        )
        require(
            observation["input_content_hash"] == observation_input_hash,
            "ACTION_OBSERVATION_INPUT_BINDING_MISMATCH",
        )
        require(
            observation["strategy"] == expected_descriptor
            and proposal["strategy"] == expected_descriptor,
            "ACTION_PROPOSAL_STRATEGY_MISMATCH",
        )
        require(
            observation["approval_reference"] == execution_approval,
            "ACTION_EXECUTION_APPROVAL_MISMATCH",
        )
        actual_binding = observation.get("strategy_input_binding")
        require(
            actual_binding == expected_binding,
            "ACTION_STRATEGY_INPUT_BINDING_MISMATCH",
        )
        _validate_observation_source_binding(observation, source)
        validation = _normalize_action_validation(decision["validation"], proposal=proposal)
        replayed_validation = _normalize_action_validation(
            validate_action(copy.deepcopy(observation), proposal), proposal=proposal
        )
        require(
            validation == replayed_validation,
            "ACTION_GUARD_REPLAY_MISMATCH",
        )
        decision_id = proposal["decision_id"]
        require(validation["decision_id"] == decision_id, "ACTION_EVIDENCE_DECISION_MISMATCH")
        require(
            decision_id
            == "DEC-"
            + digest(
                [
                    observation_input_hash,
                    observation["item_id"],
                    observation["bucket"]["yyyyww"],
                ]
            ),
            "ACTION_EVIDENCE_DECISION_ID_MISMATCH",
        )
        _validate_proposal_binding(
            proposal,
            strategy_type=strategy_type,
            model_content_hash=model_content_hash,
            expected_strategy_descriptor=expected_descriptor,
        )
        action_key = (
            validation["item_id"],
            validation["uom"],
            validation["decision_date"],
        )
        _validate_observation_psi_binding(observation, validation, psi_rows)
        admission = None
        if admissions is not None:
            admission = copy.deepcopy(admissions[validation["item_id"]])
            require(
                decision["effective_policy_admission"] == admission,
                "ACTION_EFFECTIVE_POLICY_ADMISSION_MISMATCH",
            )
            _validate_admission_binding(
                admission,
                observation=observation,
                expected_strategy_descriptor=expected_descriptor,
                expected_model_approval_reference=expected_model_approval_reference,
            )
        actual_decisions.add(action_key)
        raw_evidence = {
            "observation": observation,
            "observation_hash": observation_hash,
            "proposal": proposal,
        }
        if admission is not None:
            raw_evidence["effective_policy_admission"] = admission
        indexed.append((decision_id, raw_evidence, validation, admission))
    indexed.sort(key=lambda item: item[0])
    require(
        len({decision_id for decision_id, *_ in indexed}) == len(indexed),
        "ACTION_EVIDENCE_DECISION_DUPLICATE",
    )
    require(actual_decisions == expected_decisions, "ACTION_EVIDENCE_PSI_UNIVERSE_MISMATCH")
    _validate_action_sequence_authority(indexed, source)
    constrained_rows = [validation for _, _, validation, _ in indexed]
    _validate_action_receipt_reconciliation(constrained_rows, psi_rows)
    adjustment_rows = [_adjustment_row(validation) for validation in constrained_rows]
    return [raw for _, raw, _, _ in indexed], constrained_rows, adjustment_rows


def _normalize_action_validation(value: Any, *, proposal: Mapping[str, Any]) -> dict[str, Any]:
    require(
        type(value) is dict and set(value) == _ACTION_VALIDATION_FIELDS,
        "ACTION_VALIDATION_CONTRACT_INVALID",
    )
    reasons = value.get("reason_codes")
    require(
        type(reasons) is list and 0 < len(reasons) <= 20,
        "ACTION_VALIDATION_REASON_CODES_INVALID",
    )
    normalized_reasons = [identifier(reason) for reason in reasons]
    require(
        len(set(normalized_reasons)) == len(normalized_reasons),
        "ACTION_VALIDATION_REASON_CODES_INVALID",
    )
    source_policy = value.get("source_policy")
    require(type(source_policy) is dict, "ACTION_VALIDATION_SOURCE_POLICY_INVALID")
    result = {
        "decision_id": identifier(value.get("decision_id")),
        "item_id": item_identifier(value.get("item_id")),
        "uom": identifier(value.get("uom")),
        "decision_date": day(value.get("decision_date")),
        "action_type": identifier(value.get("action_type")),
        "requested_qty": decimal_string(value.get("requested_qty")),
        "inventory_position_qty": decimal_string(value.get("inventory_position_qty"), signed=True),
        "raw_order_qty": decimal_string(value.get("raw_order_qty")),
        "lot_rounded_qty": decimal_string(value.get("lot_rounded_qty")),
        "accepted_order_qty": decimal_string(value.get("accepted_order_qty")),
        "uncovered_order_qty": decimal_string(value.get("uncovered_order_qty")),
        "due_date": None if value.get("due_date") is None else day(value.get("due_date")),
        "receipt_date": (
            None if value.get("receipt_date") is None else day(value.get("receipt_date"))
        ),
        "receipt_bucket": (
            None if value.get("receipt_bucket") is None else yyyyww(value.get("receipt_bucket"))
        ),
        "capacity_headroom_qty": (
            None
            if value.get("capacity_headroom_qty") is None
            else decimal_string(value.get("capacity_headroom_qty"))
        ),
        "status": identifier(value.get("status")),
        "reason_codes": normalized_reasons,
        "source_policy_id": identifier(value.get("source_policy_id")),
        "source_policy": copy.deepcopy(source_policy),
        "calculated_policy": copy.deepcopy(value.get("calculated_policy")),
        "effective_target_inventory_qty": (
            None
            if value.get("effective_target_inventory_qty") is None
            else decimal_string(value.get("effective_target_inventory_qty"))
        ),
        "approval_reference": identifier(value.get("approval_reference")),
        "capacity_mode": identifier(value.get("capacity_mode")),
    }
    require(
        result["action_type"] == proposal["action_type"]
        and result["requested_qty"] == proposal["quantity"]
        and result["calculated_policy"] == proposal["calculated_policy"],
        "ACTION_VALIDATION_PROPOSAL_MISMATCH",
    )
    require(
        result["source_policy"].get("policy_id") == result["source_policy_id"],
        "ACTION_VALIDATION_SOURCE_POLICY_INVALID",
    )
    for key in ("item_id", "uom"):
        if key in result["source_policy"]:
            require(
                result["source_policy"][key] == result[key],
                "ACTION_VALIDATION_SOURCE_POLICY_INVALID",
            )
    _validate_action_status_semantics(result)
    return result


def _validate_proposal_binding(
    proposal: Mapping[str, Any],
    *,
    strategy_type: str,
    model_content_hash: str | None,
    expected_strategy_descriptor: Mapping[str, Any],
) -> None:
    strategy = proposal["strategy"]
    require(
        strategy["strategy_type"] == strategy_type and strategy == expected_strategy_descriptor,
        "ACTION_PROPOSAL_STRATEGY_MISMATCH",
    )
    model = strategy["model"]
    require(
        (model is None and model_content_hash is None)
        or (model is not None and model["content_hash"] == model_content_hash),
        "ACTION_PROPOSAL_MODEL_MISMATCH",
    )


def _validate_admission_binding(
    admission: Mapping[str, Any],
    *,
    observation: Mapping[str, Any],
    expected_strategy_descriptor: Mapping[str, Any],
    expected_model_approval_reference: str | None,
) -> None:
    require(type(admission) is dict, "ACTION_EFFECTIVE_POLICY_ADMISSION_INVALID")
    require(
        admission.get("item_id") == observation["item_id"]
        and admission.get("strategy") == expected_strategy_descriptor
        and admission.get("model_approval_reference") == expected_model_approval_reference,
        "ACTION_EFFECTIVE_POLICY_ADMISSION_MISMATCH",
    )
    policy = observation["policy"]
    require(
        policy.get("classification_effective_policy_hash") == admission.get("effective_policy_hash")
        and policy.get("classification_config_hash") == admission.get("classification_config_hash")
        and policy.get("approved_service_level") == admission.get("effective_target_service_level")
        and policy.get("effective_review_cycle_weeks")
        == admission.get("effective_review_cycle_weeks"),
        "ACTION_EFFECTIVE_POLICY_OBSERVATION_MISMATCH",
    )
    effective_lead_time = admission.get("effective_protection_lead_time_days")
    if effective_lead_time is not None:
        require(
            policy.get("effective_protection_lead_time_basis")
            == admission.get("effective_protection_lead_time_basis")
            and Decimal(effective_lead_time)
            == Decimal(policy["effective_protection_lead_time_days"]),
            "ACTION_EFFECTIVE_POLICY_OBSERVATION_MISMATCH",
        )


def _validate_admissions_authority(
    admissions: Mapping[str, Mapping[str, Any]] | None,
    source: Mapping[str, Any],
) -> None:
    if admissions is None:
        return
    binding = source["effective_policy_binding"]
    policies = {item["item_id"]: item for item in source["effective_item_policies"]}
    require(
        set(policies) == set(admissions),
        "ACTION_EFFECTIVE_POLICY_ADMISSION_UNIVERSE_MISMATCH",
    )
    for item_id, admission in admissions.items():
        expected = admit_effective_item_policy(
            policies[item_id],
            config_hash=binding["classification_config_hash"],
            execution_purpose=source["execution_purpose"],
            strategy_descriptor=source["strategy"],
            model_approval=source["model_approval"],
        )
        require(
            admission == expected,
            "ACTION_EFFECTIVE_POLICY_ADMISSION_AUTHORITY_MISMATCH",
        )


def _normalize_observation_source(value: Mapping[str, Any]) -> dict[str, Any]:
    fields = {
        "contract_id",
        "canonical_input_hash",
        "prepared_input_content_hash",
        "observation_input_hash",
        "context",
        "calendar",
        "demands",
        "positions",
        "policies",
        "receipt_decisions",
        "quantity_rules",
        "item_controls",
        "strategy",
        "execution_approval_reference",
        "allowed_action_types",
        "capacity_mode",
        "strategy_input_binding",
        "execution_purpose",
        "effective_policy_binding",
        "effective_item_policies",
        "model_approval",
        "admissions_by_item",
    }
    require(
        type(value) is dict and set(value) == fields | {"content_hash"},
        "ACTION_OBSERVATION_SOURCE_INVALID",
    )
    body = {key: copy.deepcopy(value[key]) for key in fields}
    require(
        body["contract_id"] == ACTION_OBSERVATION_SOURCE_CONTRACT_ID
        and value["content_hash"] == digest(body),
        "ACTION_OBSERVATION_SOURCE_HASH_MISMATCH",
    )
    hash_value(body["canonical_input_hash"])
    hash_value(body["prepared_input_content_hash"])
    hash_value(body["observation_input_hash"])
    descriptor(dict(body["strategy"]))
    identifier(body["execution_approval_reference"])
    require(
        body["execution_purpose"] in {"OPERATIONAL", "SHADOW"},
        "ACTION_OBSERVATION_SOURCE_INVALID",
    )
    require(
        type(body["context"]) is dict
        and all(
            type(body[key]) is list
            for key in (
                "calendar",
                "demands",
                "positions",
                "policies",
                "receipt_decisions",
                "quantity_rules",
                "item_controls",
                "allowed_action_types",
            )
        )
        and body["capacity_mode"] == "CONSERVATIVE_NO_DEMAND_CREDIT"
        and (body["admissions_by_item"] is None or type(body["admissions_by_item"]) is dict),
        "ACTION_OBSERVATION_SOURCE_INVALID",
    )
    if body["admissions_by_item"] is None:
        require(
            body["effective_policy_binding"] is None and body["effective_item_policies"] is None,
            "ACTION_OBSERVATION_SOURCE_INVALID",
        )
    else:
        require(
            type(body["effective_policy_binding"]) is dict
            and type(body["effective_item_policies"]) is list,
            "ACTION_OBSERVATION_SOURCE_INVALID",
        )
    return {**body, "content_hash": value["content_hash"]}


def _validate_observation_source_binding(
    observation: Mapping[str, Any], source: Mapping[str, Any]
) -> None:
    item_id = observation["item_id"]
    uom = observation["uom"]
    require(observation["context"] == source["context"], "ACTION_OBSERVATION_CONTEXT_MISMATCH")
    controls = [row for row in source["item_controls"] if row["item_id"] == item_id]
    rules = [row for row in source["quantity_rules"] if row["uom"] == uom]
    policies = sorted(
        (row for row in source["policies"] if row["item_id"] == item_id),
        key=lambda row: (row["effective_from"], row["effective_to"], row["policy_id"]),
    )
    require(
        len(controls) == 1
        and len(rules) == 1
        and observation["control"] == controls[0]
        and observation["quantity_rule"] == rules[0]
        and observation["policy_schedule"] == policies
        and observation["allowed_action_types"] == source["allowed_action_types"]
        and observation["capacity_mode"] == source["capacity_mode"],
        "ACTION_OBSERVATION_AUTHORITY_MISMATCH",
    )
    calendar = source["calendar"]
    indexes = [
        index
        for index, bucket in enumerate(calendar)
        if bucket["start_date"] == observation["decision_date"]
    ]
    require(len(indexes) == 1, "ACTION_OBSERVATION_AUTHORITY_MISMATCH")
    index = indexes[0]
    demands = {(row["item_id"], row["yyyyww"]): row for row in source["demands"]}
    expected_demands = [demands[(item_id, bucket["yyyyww"])] for bucket in calendar[index:]]
    require(
        observation["bucket"] == calendar[index]
        and observation["calendar"] == calendar[index:]
        and observation["future_demand"] == expected_demands,
        "ACTION_OBSERVATION_AUTHORITY_MISMATCH",
    )


def _validate_psi_source_binding(psi_rows: list[dict[str, Any]], source: Mapping[str, Any]) -> None:
    calendar = source["calendar"]
    positions = {(row["item_id"], row["uom"]): row for row in source["positions"]}
    demands = {(row["item_id"], row["uom"], row["yyyyww"]): row for row in source["demands"]}
    expected_universe = {
        (item_id, uom, bucket["yyyyww"]) for item_id, uom in positions for bucket in calendar
    }
    actual_universe = {(row["item_id"], row["uom"], row["yyyyww"]) for row in psi_rows}
    require(actual_universe == expected_universe, "PSI_CHILD_UNIVERSE_MISMATCH")
    confirmed: dict[tuple[str, str, str], Decimal] = {}
    with localcontext(_NUMBERS):
        for receipt in source["receipt_decisions"]:
            if Decimal(receipt["included_qty"]) > 0:
                key = (receipt["item_id"], receipt["uom"], receipt["yyyyww"])
                confirmed[key] = confirmed.get(key, Decimal(0)) + Decimal(receipt["included_qty"])
        for row in psi_rows:
            key = (row["item_id"], row["uom"], row["yyyyww"])
            demand = demands[key]
            bucket = next(item for item in calendar if item["yyyyww"] == row["yyyyww"])
            require(
                all(
                    row[field] == source["context"][field]
                    for field in ("company_cd", "subs_cd", "site_cd")
                )
                and all(
                    row[field] == bucket[field]
                    for field in ("seq", "start_date", "end_date", "base_month")
                )
                and all(
                    row[field] == demand[field]
                    for field in (
                        "gross_forecast_qty",
                        "forecast_consumed_qty",
                        "net_forecast_qty",
                        "confirmed_customer_order_qty",
                        "forecast_netting_mode",
                        "source_snapshot_id",
                        "source_content_hash",
                    )
                )
                and Decimal(row["confirmed_supplier_receipt_qty"])
                == confirmed.get(key, Decimal(0)),
                "PSI_SOURCE_PROJECTION_MISMATCH",
            )
            if row["seq"] == calendar[0]["seq"]:
                position = positions[(row["item_id"], row["uom"])]
                require(
                    Decimal(row["boh_qty"]) == Decimal(position["available_qty"])
                    and Decimal(row["reserved_qty"]) == Decimal(position["reserved_qty"])
                    and Decimal(row["on_hand_boh_qty"]) == Decimal(position["on_hand_qty"])
                    and Decimal(row["backorder_open_qty"]) == Decimal(position["backorder_qty"]),
                    "PSI_SOURCE_INITIAL_STATE_MISMATCH",
                )
            if row["psi_scenario_type"] != "BASELINE":
                applicable = [
                    policy
                    for policy in source["policies"]
                    if policy["item_id"] == row["item_id"]
                    and policy["uom"] == row["uom"]
                    and policy["effective_from"] <= row["start_date"] < policy["effective_to"]
                ]
                require(len(applicable) == 1, "PSI_CAPACITY_POLICY_BINDING_MISMATCH")
                capacity = applicable[0]["physical_max_capacity"]
                expected_excess = (
                    Decimal(0)
                    if capacity is None
                    else max(
                        Decimal(0),
                        Decimal(row["available_qty"])
                        + Decimal(row["reserved_qty"])
                        - Decimal(capacity),
                    )
                )
                require(
                    Decimal(row["physical_capacity_excess_qty"]) == expected_excess,
                    "PSI_PHYSICAL_CAPACITY_EXCESS_MISMATCH",
                )


def _validate_action_sequence_authority(
    indexed: list[tuple[str, dict[str, Any], dict[str, Any], dict[str, Any] | None]],
    source: Mapping[str, Any],
) -> None:
    by_item: dict[str, list[tuple[str, dict[str, Any], dict[str, Any], dict[str, Any] | None]]] = {}
    for row in indexed:
        by_item.setdefault(row[1]["observation"]["item_id"], []).append(row)
    for item_id, decisions in by_item.items():
        pending = [
            {
                "supply_id": "CONFIRMED:" + receipt["receipt_id"],
                "supply_kind": "CONFIRMED",
                "due_date": receipt["due_date"],
                "receipt_bucket": receipt["yyyyww"],
                "quantity": receipt["included_qty"],
            }
            for receipt in source["receipt_decisions"]
            if receipt["item_id"] == item_id and Decimal(receipt["included_qty"]) > 0
        ]
        decisions.sort(key=lambda row: row[1]["observation"]["bucket"]["seq"])
        for _decision_id, raw, validation, _admission in decisions:
            observation = raw["observation"]
            require(
                observation["pending_supply"] == sorted(pending, key=lambda row: row["supply_id"]),
                "ACTION_OBSERVATION_PENDING_SUPPLY_MISMATCH",
            )
            if Decimal(validation["accepted_order_qty"]) > 0:
                pending.append(
                    {
                        "supply_id": "REC-" + validation["decision_id"],
                        "supply_kind": "RECOMMENDED",
                        "due_date": validation["due_date"],
                        "receipt_bucket": validation["receipt_bucket"],
                        "quantity": validation["accepted_order_qty"],
                    }
                )
            pending = [
                row for row in pending if row["receipt_bucket"] != observation["bucket"]["yyyyww"]
            ]


def _validate_observation_psi_binding(
    observation: Mapping[str, Any],
    validation: Mapping[str, Any],
    psi_rows: list[dict[str, Any]],
) -> None:
    matching = [
        row
        for row in psi_rows
        if row["item_id"] == observation["item_id"]
        and row["uom"] == observation["uom"]
        and row["start_date"] == observation["decision_date"]
    ]
    require(len(matching) == 1, "ACTION_OBSERVATION_PSI_ROW_MISMATCH")
    current = matching[0]
    require(
        all(
            observation["context"][key] == current[key]
            for key in ("company_cd", "subs_cd", "site_cd")
        ),
        "ACTION_OBSERVATION_SCOPE_MISMATCH",
    )
    state = observation["state"]
    require(
        state["available_qty"] == current["boh_qty"]
        and state["reserved_qty"] == current["reserved_qty"]
        and state["on_hand_qty"] == current["on_hand_boh_qty"]
        and state["backorder_qty"] == current["backorder_open_qty"],
        "ACTION_OBSERVATION_PSI_STATE_MISMATCH",
    )
    future_rows = sorted(
        (
            row
            for row in psi_rows
            if row["item_id"] == observation["item_id"]
            and row["uom"] == observation["uom"]
            and row["seq"] >= current["seq"]
        ),
        key=lambda row: row["seq"],
    )
    expected_calendar = [
        {key: row[key] for key in ("yyyyww", "seq", "start_date", "end_date", "base_month")}
        for row in future_rows
    ]
    require(
        observation["bucket"] == expected_calendar[0]
        and observation["calendar"] == expected_calendar,
        "ACTION_OBSERVATION_PSI_CALENDAR_MISMATCH",
    )
    expected_demands = [
        {
            key: row[key]
            for key in (
                "item_id",
                "uom",
                "yyyyww",
                "gross_forecast_qty",
                "forecast_consumed_qty",
                "net_forecast_qty",
                "confirmed_customer_order_qty",
                "forecast_netting_mode",
                "source_snapshot_id",
                "source_content_hash",
            )
        }
        for row in future_rows
    ]
    require(
        observation["future_demand"] == expected_demands,
        "ACTION_OBSERVATION_PSI_DEMAND_MISMATCH",
    )
    with localcontext(_NUMBERS):
        confirmed = sum(
            (
                Decimal(row["quantity"])
                for row in observation["pending_supply"]
                if row["supply_kind"] == "CONFIRMED" and row["receipt_bucket"] == current["yyyyww"]
            ),
            Decimal(0),
        )
        recommended = sum(
            (
                Decimal(row["quantity"])
                for row in observation["pending_supply"]
                if row["supply_kind"] == "RECOMMENDED"
                and row["receipt_bucket"] == current["yyyyww"]
            ),
            Decimal(0),
        )
        if validation["receipt_bucket"] == current["yyyyww"]:
            recommended += Decimal(validation["accepted_order_qty"])
        require(
            confirmed == Decimal(current["confirmed_supplier_receipt_qty"])
            and recommended == Decimal(current["recommended_receipt_qty"]),
            "ACTION_OBSERVATION_PSI_RECEIPT_MISMATCH",
        )


def _validate_action_status_semantics(value: Mapping[str, Any]) -> None:
    with localcontext(_NUMBERS):
        _validate_action_status_semantics_in_context(value)


def _validate_action_status_semantics_in_context(value: Mapping[str, Any]) -> None:
    status = value["status"]
    action_type = value["action_type"]
    require(status in _ACTION_STATUSES, "ACTION_VALIDATION_STATUS_INVALID")
    require(
        value["capacity_mode"] == "CONSERVATIVE_NO_DEMAND_CREDIT",
        "ACTION_VALIDATION_CAPACITY_MODE_INVALID",
    )
    requested = Decimal(value["requested_qty"])
    position = Decimal(value["inventory_position_qty"])
    raw = Decimal(value["raw_order_qty"])
    rounded = Decimal(value["lot_rounded_qty"])
    accepted = Decimal(value["accepted_order_qty"])
    uncovered = Decimal(value["uncovered_order_qty"])
    headroom = (
        None if value["capacity_headroom_qty"] is None else Decimal(value["capacity_headroom_qty"])
    )
    expected_raw = (
        max(Decimal(0), requested - position) if action_type == "ORDER_UP_TO" else requested
    )
    require(raw == expected_raw, "ACTION_VALIDATION_RAW_QUANTITY_INVALID")
    require(
        accepted <= rounded and uncovered == max(Decimal(0), raw - accepted),
        "ACTION_VALIDATION_QUANTITY_CONSERVATION_INVALID",
    )
    require(
        headroom is None or (headroom >= 0 and accepted <= headroom),
        "ACTION_VALIDATION_CAPACITY_HEADROOM_INVALID",
    )
    require(
        (value["receipt_date"] is None) == (value["receipt_bucket"] is None),
        "ACTION_VALIDATION_RECEIPT_MAPPING_INVALID",
    )
    if value["receipt_date"] is not None:
        require(
            value["due_date"] is not None
            and value["decision_date"] <= value["due_date"] <= value["receipt_date"],
            "ACTION_VALIDATION_RECEIPT_MAPPING_INVALID",
        )
    elif value["due_date"] is not None:
        require(
            value["decision_date"] <= value["due_date"],
            "ACTION_VALIDATION_RECEIPT_MAPPING_INVALID",
        )
    target = value["effective_target_inventory_qty"]
    expected_target = value["requested_qty"] if action_type == "ORDER_UP_TO" else None
    if status == "NO_ORDER":
        require(
            raw == 0
            and rounded == 0
            and accepted == 0
            and value["due_date"] is None
            and value["receipt_date"] is None
            and value["receipt_bucket"] is None
            and target == expected_target
            and value["reason_codes"]
            == (["HOLD"] if action_type == "HOLD" else ["NO_NET_REQUIREMENT"]),
            "ACTION_VALIDATION_NO_ORDER_INVALID",
        )
    elif status == "REJECTED":
        reason = value["reason_codes"][0] if len(value["reason_codes"]) == 1 else None
        require(
            accepted == 0
            and target is None
            and reason
            in {
                *_EARLY_REJECTION_REASONS,
                _ARRIVAL_REJECTION_REASON,
                _FEASIBILITY_REJECTION_REASON,
            },
            "ACTION_VALIDATION_REJECTION_INVALID",
        )
        if reason in _EARLY_REJECTION_REASONS:
            require(
                rounded == 0
                and value["due_date"] is None
                and value["receipt_date"] is None
                and value["receipt_bucket"] is None
                and value["capacity_headroom_qty"] is None
                and (reason != "TARGET_OUTSIDE_APPROVED_BOUNDS" or action_type == "ORDER_UP_TO")
                and (reason != "ORDER_CALENDAR_CLOSED" or raw > 0),
                "ACTION_VALIDATION_REJECTION_INVALID",
            )
        elif reason == _ARRIVAL_REJECTION_REASON:
            require(
                raw > 0
                and rounded == 0
                and value["due_date"] is not None
                and value["receipt_date"] is None
                and value["receipt_bucket"] is None
                and value["capacity_headroom_qty"] is None,
                "ACTION_VALIDATION_REJECTION_INVALID",
            )
        else:
            require(
                raw > 0
                and rounded >= raw
                and rounded > 0
                and value["due_date"] is not None
                and value["receipt_date"] is not None
                and value["receipt_bucket"] is not None,
                "ACTION_VALIDATION_REJECTION_INVALID",
            )
    else:
        require(
            accepted > 0
            and rounded >= raw
            and rounded > 0
            and value["due_date"] is not None
            and value["receipt_date"] is not None
            and value["receipt_bucket"] is not None
            and target == expected_target,
            "ACTION_VALIDATION_ACCEPTANCE_INVALID",
        )
        expected_reasons = []
        if rounded != raw:
            expected_reasons.append("MOQ_OR_MULTIPLE_ROUND_UP")
        if accepted != rounded:
            expected_reasons.append("APPROVED_LIMIT_OR_CAPACITY_REDUCTION")
        if value["due_date"] != value["receipt_date"]:
            expected_reasons.append("ARRIVAL_ROUNDED_FORWARD")
        require(
            (
                status == "ACCEPTED"
                and not expected_reasons
                and value["reason_codes"] == ["CONSTRAINTS_PASSED"]
            )
            or (status == "ADJUSTED" and value["reason_codes"] == expected_reasons),
            "ACTION_VALIDATION_ADJUSTMENT_INVALID",
        )


def _validate_action_receipt_reconciliation(
    constrained_rows: list[dict[str, Any]], psi_rows: list[dict[str, Any]]
) -> None:
    with localcontext(_NUMBERS):
        _validate_action_receipt_reconciliation_in_context(constrained_rows, psi_rows)


def _validate_action_receipt_reconciliation_in_context(
    constrained_rows: list[dict[str, Any]], psi_rows: list[dict[str, Any]]
) -> None:
    expected: dict[tuple[str, str, str], Decimal] = {}
    receipt_dates: dict[tuple[str, str, str], str] = {}
    for row in psi_rows:
        key = (row["item_id"], row["uom"], row["yyyyww"])
        expected[key] = expected.get(key, Decimal(0)) + Decimal(row["recommended_receipt_qty"])
        receipt_dates[key] = row["start_date"]
    actual: dict[tuple[str, str, str], Decimal] = {}
    for row in constrained_rows:
        if row["receipt_bucket"] is not None:
            key = (row["item_id"], row["uom"], row["receipt_bucket"])
            require(
                key in expected and row["receipt_date"] == receipt_dates[key],
                "ACTION_PSI_RECEIPT_MAPPING_MISMATCH",
            )
        accepted = Decimal(row["accepted_order_qty"])
        if accepted == 0:
            continue
        actual[key] = actual.get(key, Decimal(0)) + accepted
    require(
        all(actual.get(key, Decimal(0)) == quantity for key, quantity in expected.items())
        and set(actual) <= set(expected),
        "ACTION_PSI_RECEIPT_QUANTITY_MISMATCH",
    )


def _adjustment_row(validation: Mapping[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(validation[key]) for key in _ADJUSTMENT_REASON_FIELDS}


def _validate_successful_build(value: Mapping[str, Any]) -> dict[str, Any]:
    require(type(value) is dict, "PSI_CHILD_BUILD_INVALID")
    require(
        set(value)
        == {
            "child_result",
            "artifact_reference",
            "artifact",
            "metrics",
            "action_artifacts",
        },
        "PSI_CHILD_BUILD_INVALID",
    )
    child = value["child_result"]
    artifact = value["artifact"]
    require(
        type(child) is dict and child.get("status") == "SUCCEEDED" and type(artifact) is dict,
        "PSI_CHILD_BUILD_INVALID",
    )
    require(
        set(artifact) == _PSI_ARTIFACT_BODY_FIELDS | {"content_hash"}
        and artifact.get("contract_id") == PSI_RESULT_ARTIFACT_CONTRACT_ID
        and artifact.get("contract_version") == PSI_RESULT_ARTIFACT_CONTRACT_VERSION
        and artifact.get("artifact_contract_key") == PSI_RESULT_ARTIFACT_CONTRACT_KEY,
        "PSI_CHILD_ARTIFACT_CONTRACT_INVALID",
    )
    body = {key: copy.deepcopy(item) for key, item in artifact.items() if key != "content_hash"}
    require(
        artifact.get("content_hash") == digest(body),
        "PSI_CHILD_ARTIFACT_HASH_MISMATCH",
    )
    normalized_rows, metrics = _normalize_and_summarize(artifact.get("psi_rows"))
    require(
        artifact.get("row_count") == len(normalized_rows),
        "PSI_CHILD_ARTIFACT_ROW_COUNT_MISMATCH",
    )
    require(
        artifact.get("psi_rows") == normalized_rows and artifact.get("metrics") == metrics,
        "PSI_CHILD_ARTIFACT_METRICS_MISMATCH",
    )
    _validate_row_semantics(
        normalized_rows,
        result_kind=artifact["result_kind"],
        strategy_type=artifact["strategy_type"],
    )
    require(value["metrics"] == metrics, "PSI_CHILD_BUILD_METRICS_MISMATCH")
    identity = _semantic_identity(
        engine_run_id=artifact["engine_run_id"],
        attempt_no=artifact["attempt_no"],
        canonical_input_hash=artifact["canonical_input_hash"],
        result_kind=artifact["result_kind"],
        execution_role=artifact["execution_role"],
        strategy_type=artifact["strategy_type"],
        scenario_id=artifact["scenario_id"],
        scenario_content_hash=artifact["scenario_content_hash"],
        challenger_id=artifact["challenger_id"],
        model_content_hash=artifact["model_content_hash"],
    )
    expected_child_result_id = f"IOC-{digest(identity)}"
    expected_reference = f"{_artifact_base_reference(identity, expected_child_result_id)}:psi"
    require(
        artifact["child_result_id"] == expected_child_result_id
        and value["artifact_reference"] == expected_reference,
        "PSI_CHILD_ID_OR_REFERENCE_MISMATCH",
    )
    for key in (
        "child_result_id",
        "result_kind",
        "execution_role",
        "strategy_type",
        "scenario_id",
        "scenario_content_hash",
        "challenger_id",
        "model_content_hash",
        "row_count",
    ):
        require(child.get(key) == artifact.get(key), "PSI_CHILD_ARTIFACT_BINDING_MISMATCH")
    require(
        child.get("child_content_hash") == artifact["content_hash"]
        and child.get("artifact_reference") == value["artifact_reference"]
        and child.get("artifact_contract_key") == PSI_RESULT_ARTIFACT_CONTRACT_KEY
        and child.get("artifact_contract_version") == PSI_RESULT_ARTIFACT_CONTRACT_VERSION
        and child.get("failure_reason_code") is None
        and child.get("action_evidence") == artifact.get("action_evidence"),
        "PSI_CHILD_ARTIFACT_BINDING_MISMATCH",
    )
    action_artifacts = value["action_artifacts"]
    require(type(action_artifacts) is dict, "ACTION_EVIDENCE_INVALID")
    if child["strategy_type"] == "NONE":
        require(
            not action_artifacts and child["action_evidence"] == _NO_ACTIONS,
            "ACTION_EVIDENCE_FORBIDDEN",
        )
    else:
        require(
            set(action_artifacts) == {"raw_action", "constrained_action", "adjustment_reasons"},
            "ACTION_EVIDENCE_REQUIRED",
        )
        for prefix, value_and_artifact in action_artifacts.items():
            require(type(value_and_artifact) is dict, "ACTION_EVIDENCE_INVALID")
            action_artifact = value_and_artifact.get("artifact")
            require(type(action_artifact) is dict, "ACTION_EVIDENCE_INVALID")
            action_body = {
                key: copy.deepcopy(item)
                for key, item in action_artifact.items()
                if key != "content_hash"
            }
            expected_kind = {
                "raw_action": "RAW_ACTION",
                "constrained_action": "CONSTRAINED_ACTION",
                "adjustment_reasons": "ADJUSTMENT_REASONS",
            }[prefix]
            expected_action_reference = (
                f"{_artifact_base_reference(identity, expected_child_result_id)}:"
                f"{prefix.replace('_', '-')}"
            )
            require(
                set(action_artifact) == _ACTION_ARTIFACT_BODY_FIELDS | {"content_hash"}
                and action_artifact.get("content_hash") == digest(action_body)
                and action_artifact.get("contract_id") == ACTION_EVIDENCE_ARTIFACT_CONTRACT_ID
                and action_artifact.get("contract_version")
                == ACTION_EVIDENCE_ARTIFACT_CONTRACT_VERSION
                and action_artifact.get("evidence_kind") == expected_kind
                and value_and_artifact.get("reference") == expected_action_reference
                and value_and_artifact.get("reference")
                == child["action_evidence"][f"{prefix}_reference"]
                and action_artifact["content_hash"]
                == child["action_evidence"][f"{prefix}_content_hash"]
                and action_artifact.get("child_result_id") == child["child_result_id"]
                and action_artifact.get("canonical_input_hash") == artifact["canonical_input_hash"]
                and action_artifact.get("result_kind") == artifact["result_kind"]
                and action_artifact.get("execution_role") == artifact["execution_role"]
                and action_artifact.get("strategy_type") == artifact["strategy_type"]
                and action_artifact.get("scenario_id") == artifact["scenario_id"]
                and action_artifact.get("scenario_content_hash")
                == artifact["scenario_content_hash"]
                and action_artifact.get("challenger_id") == artifact["challenger_id"]
                and action_artifact.get("model_content_hash") == artifact["model_content_hash"]
                and action_artifact.get("row_count") == len(action_artifact.get("rows", [])),
                "ACTION_EVIDENCE_HASH_MISMATCH",
            )
            require(
                (prefix == "raw_action" and action_artifact["observation_source"] is not None)
                or (prefix != "raw_action" and action_artifact["observation_source"] is None),
                "ACTION_OBSERVATION_SOURCE_PERSISTENCE_INVALID",
            )
        raw_rows = action_artifacts["raw_action"]["artifact"]["rows"]
        constrained_rows = action_artifacts["constrained_action"]["artifact"]["rows"]
        require(
            type(raw_rows) is list
            and type(constrained_rows) is list
            and len(raw_rows) == len(constrained_rows),
            "ACTION_EVIDENCE_INVALID",
        )
        require(bool(raw_rows), "ACTION_EVIDENCE_INVALID")
        persisted_decisions = [
            {**copy.deepcopy(raw), "validation": copy.deepcopy(validation)}
            for raw, validation in zip(raw_rows, constrained_rows, strict=True)
        ]
        first_raw = raw_rows[0]
        require(
            type(first_raw) is dict
            and type(first_raw.get("observation")) is dict
            and type(first_raw.get("proposal")) is dict,
            "ACTION_EVIDENCE_INVALID",
        )
        first_observation = first_raw["observation"]
        persisted_observation_source = _normalize_observation_source(
            action_artifacts["raw_action"]["artifact"]["observation_source"]
        )
        has_admissions = "effective_policy_admission" in first_raw
        require(
            all(("effective_policy_admission" in row) == has_admissions for row in raw_rows),
            "ACTION_EVIDENCE_ADMISSION_INCONSISTENT",
        )
        persisted_admissions = (
            {row["observation"]["item_id"]: row["effective_policy_admission"] for row in raw_rows}
            if has_admissions
            else None
        )
        persisted_model_approval = (
            first_raw["effective_policy_admission"].get("model_approval_reference")
            if has_admissions
            else None
        )
        normalized_raw, normalized_constrained, derived_adjustments = _normalize_action_pairs(
            persisted_decisions,
            expected_decisions={
                (row["item_id"], row["uom"], row["start_date"]) for row in normalized_rows
            },
            psi_rows=normalized_rows,
            canonical_input_hash=artifact["canonical_input_hash"],
            expected_observation_input_hash=first_observation["input_content_hash"],
            strategy_type=artifact["strategy_type"],
            model_content_hash=artifact["model_content_hash"],
            expected_strategy_descriptor=first_observation["strategy"],
            expected_execution_approval_reference=first_observation["approval_reference"],
            expected_strategy_input_binding=first_observation.get("strategy_input_binding"),
            expected_admissions_by_item=persisted_admissions,
            expected_model_approval_reference=persisted_model_approval,
            expected_observation_source=persisted_observation_source,
            execution_role=artifact["execution_role"],
        )
        require(
            raw_rows == normalized_raw
            and constrained_rows == normalized_constrained
            and action_artifacts["adjustment_reasons"]["artifact"]["rows"] == derived_adjustments,
            "ACTION_EVIDENCE_SEMANTICS_MISMATCH",
        )
    return copy.deepcopy(value)


def validate_psi_child_build(value: Mapping[str, Any]) -> dict[str, Any]:
    """Independently validate one read-back PSI Child and its action artifacts."""

    return _validate_successful_build(value)


def _psi_universe(value: Mapping[str, Any]) -> set[tuple[Any, ...]]:
    return {
        (
            row["company_cd"],
            row["subs_cd"],
            row["site_cd"],
            row["item_id"],
            row["uom"],
            row["seq"],
            row["yyyyww"],
            row["start_date"],
            row["end_date"],
        )
        for row in value["artifact"]["psi_rows"]
    }


def _validate_base_scenario_exogenous_inputs(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any]
) -> None:
    """Ensure a strategy changes actions, never the BASE scenario's outside world."""

    baseline_rows = {_row_key(row): row for row in baseline["artifact"]["psi_rows"]}
    candidate_rows = {_row_key(row): row for row in candidate["artifact"]["psi_rows"]}
    invariant_fields = (
        "confirmed_customer_order_qty",
        "net_forecast_qty",
        "confirmed_supplier_receipt_qty",
    )
    optional_invariant_fields = (
        "gross_forecast_qty",
        "forecast_consumed_qty",
        "forecast_netting_mode",
        "source_snapshot_id",
        "source_content_hash",
    )
    initial_fields = (
        "boh_qty",
        "reserved_qty",
        "on_hand_boh_qty",
        "backorder_open_qty",
    )
    for key, baseline_row in baseline_rows.items():
        candidate_row = candidate_rows[key]
        require(
            all(baseline_row[field] == candidate_row[field] for field in invariant_fields),
            "PSI_BASE_SCENARIO_EXOGENOUS_INPUT_MISMATCH",
        )
        for field in optional_invariant_fields:
            if field in baseline_row or field in candidate_row:
                require(
                    baseline_row.get(field) == candidate_row.get(field),
                    "PSI_BASE_SCENARIO_EXOGENOUS_INPUT_MISMATCH",
                )
        if baseline_row["seq"] == 0:
            require(
                all(baseline_row[field] == candidate_row[field] for field in initial_fields),
                "PSI_BASE_SCENARIO_INITIAL_STATE_MISMATCH",
            )


def _artifact_base_reference(identity: Mapping[str, Any], child_result_id: str) -> str:
    return (
        f"artifact:inventory:{identity['engine_run_id']}:{identity['attempt_no']}:{child_result_id}"
    )


def _metric_quantity_text(value: Decimal) -> str:
    require(
        value.is_finite() and Decimal(0) <= value <= _MAX_METRIC_QUANTITY,
        "PSI_METRIC_QUANTITY_RANGE",
    )
    return quantity_text(value)


def _signed_quantity_text(value: Decimal) -> str:
    require(
        value.is_finite() and value.copy_abs() <= _MAX_METRIC_QUANTITY,
        "PSI_METRIC_QUANTITY_RANGE",
    )
    return quantity_text(value)


def _service_level_text(fulfilled: Decimal, demand: Decimal) -> str:
    with localcontext(_NUMBERS):
        require(Decimal(0) <= fulfilled <= demand and demand > 0, "PSI_SERVICE_METRIC_INVALID")
        return quantity_text(
            (fulfilled / demand).quantize(_SERVICE_QUANTUM, rounding=ROUND_HALF_EVEN)
        )


def _service_delta_text(
    baseline_fulfilled: Decimal,
    baseline_demand: Decimal,
    candidate_fulfilled: Decimal,
    candidate_demand: Decimal,
) -> str:
    with localcontext(_NUMBERS):
        value = candidate_fulfilled / candidate_demand - baseline_fulfilled / baseline_demand
        return _signed_quantity_text(value.quantize(_SERVICE_QUANTUM, rounding=ROUND_HALF_EVEN))


def _available(value: str) -> dict[str, str | None]:
    return {"status": "AVAILABLE", "value": value, "reason_code": None}


def _unavailable(reason: str) -> dict[str, str | None]:
    return {"status": "NOT_AVAILABLE", "value": None, "reason_code": reason}


__all__ = [
    "ACTION_EVIDENCE_ARTIFACT_CONTRACT_ID",
    "ACTION_EVIDENCE_ARTIFACT_CONTRACT_VERSION",
    "PSI_RESULT_ARTIFACT_CONTRACT_ID",
    "PSI_RESULT_ARTIFACT_CONTRACT_KEY",
    "PSI_RESULT_ARTIFACT_CONTRACT_VERSION",
    "build_failed_psi_child",
    "build_psi_child_artifact",
    "validate_psi_child_build",
    "compare_psi_children",
    "compare_psi_children_detailed",
    "derive_psi_child_result_id",
    "summarize_psi_rows",
]

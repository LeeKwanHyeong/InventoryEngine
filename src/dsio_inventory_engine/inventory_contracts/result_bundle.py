"""Deterministic execution-plan and PSI result-bundle contracts.

Scenario, execution role and strategy are independent axes.  The contract keeps
the mathematical recommendation as the only operational result while allowing
approved PPO challengers and stress simulations to remain immutable evidence.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .values import (
    InventoryInputError,
    boolean,
    choice,
    digest,
    hash_value,
    identifier,
    integer,
    optional,
    quantity_text,
    require,
    shape,
)
from .run_cost import run_cost_binding


STRATEGY_EXECUTION_PLAN_CONTRACT_ID = "inventory-strategy-execution-plan-v1"
STRATEGY_EXECUTION_PLAN_CONTRACT_VERSION = "1.1.0"
LANDED_COST_STRATEGY_PLAN_VERSION = "1.2.0"
RESULT_BUNDLE_CONTRACT_ID = "inventory-result-bundle-v1"
RESULT_BUNDLE_CONTRACT_VERSION = "1.0.0"
RESULT_BUNDLE_SOURCE_CONTRACT_KEY = "inventory.result_bundle"
PSI_CHILD_IDENTITY_CONTRACT_ID = "inventory-psi-child-identity-v1"
PSI_RESULT_ARTIFACT_CONTRACT_KEY = "inventory.psi_result"
PSI_RESULT_ARTIFACT_CONTRACT_VERSION = "1.0.0"
BASE_SCENARIO_CONTRACT_KEY = "inventory.scenario.base"
BASE_SCENARIO_CONTRACT_VERSION = "1.0.0"
BASE_SCENARIO_DESCRIPTOR = {
    "contract_key": BASE_SCENARIO_CONTRACT_KEY,
    "contract_version": BASE_SCENARIO_CONTRACT_VERSION,
    "scenario_id": "BASE",
    "calendar_source": "CANONICAL_PINNED_BUSINESS_CALENDAR",
    "initial_state_source": "CANONICAL_SEALED_BOH",
    "demand_source": "CONFIRMED_CUSTOMER_ORDER_PLUS_NET_FORECAST",
    "committed_supply_source": "CONFIRMED_SUPPLIER_RECEIPTS_ONLY",
}
BASE_SCENARIO_CONTENT_HASH = digest(BASE_SCENARIO_DESCRIPTOR)
PSI_SIMULATOR_IMPLEMENTATION_ID = "inventory.psi.weekly_roll_forward"
PSI_SIMULATOR_IMPLEMENTATION_VERSION = "1.0.0"
_MAX_AGGREGATE_METRIC_QUANTITY = Decimal("200000000000000000000")

RESULT_KINDS = ("BASELINE_PSI", "RECOMMENDED_PSI", "STRESS_PSI")
EXECUTION_ROLES = ("OPERATIONAL", "SHADOW", "EVIDENCE_ONLY")
RESULT_STRATEGY_TYPES = ("NONE", "MATHEMATICAL", "DEEP_RL")
RESULT_STATUSES = ("SUCCEEDED", "FAILED", "SKIPPED")
METRIC_STATUSES = ("AVAILABLE", "NOT_AVAILABLE")
METRIC_UNAVAILABLE_REASONS = (
    "COST_PROFILE_NOT_BOUND",
    "CANDIDATE_RESULT_FAILED",
    "CANDIDATE_RESULT_SKIPPED",
    "NO_DEMAND_IN_HORIZON",
)


def derive_result_bundle_id(engine_run_id: Any, attempt_no: Any) -> str:
    """Derive the replay-stable Bundle identity without a circular content hash."""

    identity = {
        "contract_id": RESULT_BUNDLE_CONTRACT_ID,
        "engine_run_id": identifier(engine_run_id),
        "attempt_no": _positive(attempt_no),
    }
    return f"IOB-{digest(identity)}"


def derive_psi_child_result_id(
    *,
    engine_run_id: Any,
    attempt_no: Any,
    canonical_input_hash: Any,
    result_kind: Any,
    execution_role: Any,
    strategy_type: Any,
    scenario_id: Any,
    scenario_content_hash: Any,
    challenger_id: Any = None,
    model_content_hash: Any = None,
) -> str:
    """Derive the immutable Child identity used by PSI and Bundle producers."""

    identity = {
        "contract_id": PSI_CHILD_IDENTITY_CONTRACT_ID,
        "engine_run_id": identifier(engine_run_id),
        "attempt_no": _positive(attempt_no),
        "canonical_input_hash": hash_value(canonical_input_hash),
        "result_kind": choice(*RESULT_KINDS)(result_kind),
        "execution_role": choice(*EXECUTION_ROLES)(execution_role),
        "strategy_type": choice(*RESULT_STRATEGY_TYPES)(strategy_type),
        "scenario_id": identifier(scenario_id),
        "scenario_content_hash": hash_value(scenario_content_hash),
        "challenger_id": None if challenger_id is None else identifier(challenger_id),
        "model_content_hash": (
            None if model_content_hash is None else hash_value(model_content_hash)
        ),
    }
    return f"IOC-{digest(identity)}"


def derive_psi_artifact_references(
    *,
    engine_run_id: Any,
    attempt_no: Any,
    child_result_id: Any,
) -> dict[str, str]:
    """Derive logical PSI and action locations; byte existence is a Reader concern."""

    base = (
        f"artifact:inventory:{identifier(engine_run_id)}:{_positive(attempt_no)}:"
        f"{identifier(child_result_id)}"
    )
    return {
        "psi": f"{base}:psi",
        "raw_action": f"{base}:raw-action",
        "constrained_action": f"{base}:constrained-action",
        "adjustment_reasons": f"{base}:adjustment-reasons",
    }


def derive_stress_scenario_content_hash(
    *,
    scenario_id: Any,
    scenario_contract_key: Any,
    scenario_contract_version: Any,
    scenario_payload_content_hash: Any,
    deterministic_seed: Any,
) -> str:
    """Bind one resolved Stress payload and seed to its semantic identity."""

    descriptor = {
        "scenario_id": identifier(scenario_id),
        "scenario_contract_key": identifier(scenario_contract_key),
        "scenario_contract_version": _contract_version(scenario_contract_version),
        "scenario_payload_content_hash": hash_value(scenario_payload_content_hash),
        "deterministic_seed": _deterministic_seed(deterministic_seed),
    }
    require(descriptor["scenario_id"] != "BASE", "STRESS_SCENARIO_ID_RESERVED")
    return digest(descriptor)


def derive_cost_profile_content_hash(
    *,
    cost_profile_id: Any,
    profile_contract_key: Any,
    profile_contract_version: Any,
    profile_payload_content_hash: Any,
) -> str:
    """Bind an approved cost payload to its immutable business identity."""

    return digest(
        {
            "cost_profile_id": identifier(cost_profile_id),
            "profile_contract_key": identifier(profile_contract_key),
            "profile_contract_version": _contract_version(profile_contract_version),
            "profile_payload_content_hash": hash_value(profile_payload_content_hash),
        }
    )


def _reference(value: Any) -> str:
    require(
        isinstance(value, str)
        and value == value.strip()
        and bool(value)
        and len(value) <= 512
        and all(ord(character) > 0x1F and ord(character) != 0x7F for character in value)
        and re.fullmatch(r"\S(?:.*\S)?", value) is not None,
        "INVALID_EVIDENCE_REFERENCE",
    )
    return value


def _opaque(value: Any) -> str:
    require(
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= 160
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*", value) is not None,
        "INVALID_OPAQUE_IDENTIFIER",
    )
    return value


def _contract_version(value: Any) -> str:
    require(
        isinstance(value, str) and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value) is not None,
        "INVALID_CONTRACT_VERSION",
    )
    return value


def _signed_decimal(value: Any) -> str:
    require(isinstance(value, str) and len(value) <= 40, "RESULT_METRIC_DECIMAL_REQUIRED")
    try:
        number = Decimal(value)
        require(
            number.is_finite() and number.copy_abs() <= _MAX_AGGREGATE_METRIC_QUANTITY,
            "RESULT_METRIC_QUANTITY_RANGE",
        )
        exponent = number.as_tuple().exponent
        require(
            isinstance(exponent, int) and exponent >= -6,
            "RESULT_METRIC_QUANTITY_PRECISION",
        )
        require(
            re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]{1,6})?", value) is not None,
            "RESULT_METRIC_DECIMAL_FORMAT",
        )
        return quantity_text(number)
    except InvalidOperation:
        raise InventoryInputError("RESULT_METRIC_INVALID_DECIMAL") from None


def _positive(value: Any) -> int:
    result = integer(value)
    require(result > 0, "INVALID_POSITIVE_INTEGER")
    return result


def _deterministic_seed(value: Any) -> int:
    require(
        type(value) is int and 0 <= value <= 9_223_372_036_854_775_807,
        "INVALID_DETERMINISTIC_SEED",
    )
    return value


def _row_count(value: Any) -> int:
    require(type(value) is int and 0 <= value <= 100_000_000, "INVALID_ROW_COUNT")
    return value


def _scope(value: Any) -> dict[str, str]:
    return shape(
        value,
        {
            "company_cd": identifier,
            "subs_cd": identifier,
            "plant_cd": identifier,
            "site_cd": identifier,
        },
    )


def _model(value: Any) -> dict[str, str]:
    return shape(
        value,
        {
            "model_id": identifier,
            "version": identifier,
            "content_hash": hash_value,
        },
    )


def _strategy_input_binding(value: Any) -> dict[str, str]:
    return shape(
        value,
        {
            "contract_id": choice("io-mathematical-policy-input-v1"),
            "contract_version": choice("1.0.0"),
            "snapshot_id": identifier,
            "content_hash": hash_value,
        },
    )


def _operational_strategy(value: Any) -> dict[str, Any]:
    return shape(
        value,
        {
            "strategy_type": choice("MATHEMATICAL"),
            "implementation_id": choice("historical-normal-r-s"),
            "version": choice("1.0.0"),
            "implementation_content_hash": hash_value,
            "replenishment_config_content_hash": hash_value,
            "strategy_input_binding": _strategy_input_binding,
        },
    )


def _psi_simulator(value: Any) -> dict[str, str]:
    return shape(
        value,
        {
            "implementation_id": choice(PSI_SIMULATOR_IMPLEMENTATION_ID),
            "version": choice(PSI_SIMULATOR_IMPLEMENTATION_VERSION),
            "implementation_content_hash": hash_value,
        },
    )


def _challenger(value: Any) -> dict[str, Any]:
    return shape(
        value,
        {
            "challenger_id": identifier,
            "strategy_type": choice("DEEP_RL"),
            "algorithm": choice("PPO"),
            "implementation_id": identifier,
            "version": identifier,
            "implementation_content_hash": hash_value,
            "model": _model,
            "approval_reference": identifier,
        },
    )


def _challengers(value: Any) -> list[dict[str, Any]]:
    require(type(value) is list and len(value) <= 32, "SHADOW_CHALLENGERS_INVALID")
    result = [_challenger(item) for item in value]
    require(
        len({item["challenger_id"] for item in result}) == len(result),
        "SHADOW_CHALLENGER_DUPLICATE",
    )
    require(
        len(
            {
                (
                    item["implementation_content_hash"],
                    item["model"]["content_hash"],
                    item["approval_reference"],
                )
                for item in result
            }
        )
        == len(result),
        "SHADOW_CHALLENGER_BINDING_DUPLICATE",
    )
    return sorted(
        result,
        key=lambda item: (
            item["challenger_id"],
            item["model"]["model_id"],
            item["model"]["version"],
            item["model"]["content_hash"],
        ),
    )


def _stress_scenario(value: Any) -> dict[str, Any]:
    result = shape(
        value,
        {
            "scenario_id": identifier,
            "scenario_type": choice("STRESS"),
            "scenario_contract_key": identifier,
            "scenario_contract_version": _contract_version,
            "scenario_payload_content_hash": hash_value,
            "deterministic_seed": _deterministic_seed,
            "scenario_content_hash": hash_value,
            "runner_implementation_id": identifier,
            "runner_implementation_version": identifier,
            "runner_implementation_content_hash": hash_value,
        },
    )
    require(result["scenario_id"] != "BASE", "STRESS_SCENARIO_ID_RESERVED")
    require(
        result["scenario_content_hash"]
        == derive_stress_scenario_content_hash(
            scenario_id=result["scenario_id"],
            scenario_contract_key=result["scenario_contract_key"],
            scenario_contract_version=result["scenario_contract_version"],
            scenario_payload_content_hash=result["scenario_payload_content_hash"],
            deterministic_seed=result["deterministic_seed"],
        ),
        "STRESS_SCENARIO_CONTENT_HASH_MISMATCH",
    )
    return result


def _stress_scenarios(value: Any) -> list[dict[str, Any]]:
    require(type(value) is list and len(value) <= 64, "STRESS_SCENARIOS_INVALID")
    result = [_stress_scenario(item) for item in value]
    require(
        len({item["scenario_id"] for item in result}) == len(result),
        "STRESS_SCENARIO_DUPLICATE",
    )
    return sorted(result, key=lambda item: (item["scenario_id"], item["scenario_content_hash"]))


def _cost_profile(value: Any) -> dict[str, Any]:
    result = shape(
        value,
        {
            "cost_profile_id": identifier,
            "profile_contract_key": choice("inventory.cost_profile"),
            "profile_contract_version": _contract_version,
            "profile_payload_content_hash": hash_value,
            "profile_content_hash": hash_value,
            "source_type": choice("DEVELOPMENT_SYNTHETIC", "AUTHORIZED_SOURCE"),
            "approval_reference": identifier,
        },
    )
    require(
        result["profile_content_hash"]
        == derive_cost_profile_content_hash(
            cost_profile_id=result["cost_profile_id"],
            profile_contract_key=result["profile_contract_key"],
            profile_contract_version=result["profile_contract_version"],
            profile_payload_content_hash=result["profile_payload_content_hash"],
        ),
        "COST_PROFILE_CONTENT_HASH_MISMATCH",
    )
    return result


STRATEGY_EXECUTION_PLAN_BODY_FIELDS = {
    "contract_id": choice(STRATEGY_EXECUTION_PLAN_CONTRACT_ID),
    "contract_version": choice(
        STRATEGY_EXECUTION_PLAN_CONTRACT_VERSION, LANDED_COST_STRATEGY_PLAN_VERSION
    ),
    "strategy_execution_plan_id": identifier,
    "classification_config_hash": hash_value,
    "effective_policy_content_hash": hash_value,
    "base_scenario_contract_key": choice(BASE_SCENARIO_CONTRACT_KEY),
    "base_scenario_contract_version": choice(BASE_SCENARIO_CONTRACT_VERSION),
    "base_scenario_content_hash": choice(BASE_SCENARIO_CONTENT_HASH),
    "psi_simulator": _psi_simulator,
    "operational_strategy": _operational_strategy,
    "shadow_challenger_bindings": _challengers,
    "stress_scenario_bindings": _stress_scenarios,
    "cost_profile_binding": optional(_cost_profile),
    "result_bundle_contract_key": choice(RESULT_BUNDLE_SOURCE_CONTRACT_KEY),
    "result_bundle_contract_version": choice(RESULT_BUNDLE_CONTRACT_VERSION),
}
STRATEGY_EXECUTION_PLAN_FIELDS = {
    **STRATEGY_EXECUTION_PLAN_BODY_FIELDS,
    "content_hash": hash_value,
}


def seal_strategy_execution_plan(value: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize ordering and seal an immutable run-level strategy plan."""

    candidate = dict(value)
    candidate.setdefault("cost_profile_binding", None)
    fields = _plan_fields(candidate, include_hash=False)
    body = shape(candidate, fields)
    hash_body = dict(body)
    if hash_body["cost_profile_binding"] is None:
        # Preserve the 1.1.0 hash of plans produced before cost profiles were
        # bindable. A bound profile remains part of the immutable plan hash.
        hash_body.pop("cost_profile_binding")
    return {**body, "content_hash": digest(hash_body)}


def validate_strategy_execution_plan(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a sealed plan, including its canonical SHA-256."""

    candidate = dict(value)
    candidate.setdefault("cost_profile_binding", None)
    fields = _plan_fields(candidate, include_hash=True)
    normalized = shape(candidate, fields)
    body = {key: row for key, row in normalized.items() if key != "content_hash"}
    hash_body = dict(body)
    if hash_body["cost_profile_binding"] is None:
        hash_body.pop("cost_profile_binding")
    require(
        normalized["content_hash"] == digest(hash_body),
        "STRATEGY_EXECUTION_PLAN_HASH_MISMATCH",
    )
    return normalized


def _plan_fields(candidate: dict, *, include_hash: bool) -> dict:
    fields = dict(
        STRATEGY_EXECUTION_PLAN_FIELDS if include_hash else STRATEGY_EXECUTION_PLAN_BODY_FIELDS
    )
    if candidate.get("contract_version") == LANDED_COST_STRATEGY_PLAN_VERSION:
        fields["landed_cost_binding"] = run_cost_binding
        require(candidate.get("cost_profile_binding") is not None, "RUN_COST_PROFILE_REQUIRED")
    else:
        require(candidate.get("landed_cost_binding") is None, "RUN_COST_PLAN_VERSION_INVALID")
        candidate.pop("landed_cost_binding", None)
    return fields


def _action_evidence(value: Any) -> dict[str, str | None]:
    result = shape(
        value,
        {
            "raw_action_reference": optional(_reference),
            "raw_action_content_hash": optional(hash_value),
            "constrained_action_reference": optional(_reference),
            "constrained_action_content_hash": optional(hash_value),
            "adjustment_reasons_reference": optional(_reference),
            "adjustment_reasons_content_hash": optional(hash_value),
        },
    )
    for prefix in ("raw_action", "constrained_action", "adjustment_reasons"):
        require(
            (result[f"{prefix}_reference"] is None) == (result[f"{prefix}_content_hash"] is None),
            "ACTION_EVIDENCE_BINDING_INCOMPLETE",
        )
    return result


def _child_result(value: Any) -> dict[str, Any]:
    result = shape(
        value,
        {
            "child_result_id": identifier,
            "result_kind": choice(*RESULT_KINDS),
            "execution_role": choice(*EXECUTION_ROLES),
            "strategy_type": choice(*RESULT_STRATEGY_TYPES),
            "scenario_id": identifier,
            "scenario_content_hash": hash_value,
            "challenger_id": optional(identifier),
            "model_content_hash": optional(hash_value),
            "status": choice(*RESULT_STATUSES),
            "artifact_reference": optional(_reference),
            "artifact_contract_key": optional(identifier),
            "artifact_contract_version": optional(_contract_version),
            "child_content_hash": optional(hash_value),
            "row_count": optional(_row_count),
            "failure_reason_code": optional(identifier),
            "action_evidence": _action_evidence,
        },
    )
    artifact_values = (
        result["artifact_reference"],
        result["artifact_contract_key"],
        result["artifact_contract_version"],
        result["child_content_hash"],
        result["row_count"],
    )
    require(
        all(value is None for value in artifact_values)
        or all(value is not None for value in artifact_values),
        "CHILD_RESULT_ARTIFACT_BINDING_INCOMPLETE",
    )
    succeeded = result["status"] == "SUCCEEDED"
    require(
        (
            succeeded
            and result["artifact_reference"] is not None
            and result["failure_reason_code"] is None
        )
        or (not succeeded and result["failure_reason_code"] is not None),
        "CHILD_RESULT_STATUS_EVIDENCE_INVALID",
    )
    if succeeded:
        require(
            result["row_count"] is not None and result["row_count"] > 0,
            "CHILD_RESULT_EMPTY_SUCCESS_FORBIDDEN",
        )
    if not succeeded:
        validate_optional_child_failure_semantics(
            result_kind=result["result_kind"],
            execution_role=result["execution_role"],
            strategy_type=result["strategy_type"],
            status=result["status"],
            failure_reason_code=result["failure_reason_code"],
        )
    if result["strategy_type"] == "DEEP_RL":
        require(
            result["challenger_id"] is not None and result["model_content_hash"] is not None,
            "CHILD_RESULT_MODEL_BINDING_REQUIRED",
        )
    else:
        require(
            result["challenger_id"] is None and result["model_content_hash"] is None,
            "CHILD_RESULT_MODEL_BINDING_FORBIDDEN",
        )

    if result["result_kind"] == "BASELINE_PSI":
        require(
            result["execution_role"] == "EVIDENCE_ONLY"
            and result["strategy_type"] == "NONE"
            and result["scenario_id"] == "BASE"
            and succeeded,
            "BASELINE_RESULT_SEMANTICS_INVALID",
        )
    elif result["result_kind"] == "RECOMMENDED_PSI":
        require(
            (
                result["execution_role"] == "OPERATIONAL"
                and result["strategy_type"] == "MATHEMATICAL"
                and result["scenario_id"] == "BASE"
                and succeeded
            )
            or (
                result["execution_role"] == "SHADOW"
                and result["strategy_type"] == "DEEP_RL"
                and result["scenario_id"] == "BASE"
            ),
            "RECOMMENDED_RESULT_SEMANTICS_INVALID",
        )
    else:
        require(
            result["execution_role"] == "EVIDENCE_ONLY"
            and result["strategy_type"] == "MATHEMATICAL"
            and result["scenario_id"] != "BASE"
            and result["challenger_id"] is None
            and result["model_content_hash"] is None,
            "STRESS_RESULT_ROLE_INVALID",
        )

    actions = result["action_evidence"]
    action_values = list(actions.values())
    if succeeded and result["strategy_type"] != "NONE":
        require(all(value is not None for value in action_values), "ACTION_EVIDENCE_REQUIRED")
    if not succeeded:
        require(
            all(value is None for value in action_values),
            "RESULT_BUNDLE_FAILED_CHILD_ARTIFACT_FORBIDDEN",
        )
    elif result["strategy_type"] == "NONE":
        require(all(value is None for value in action_values), "ACTION_EVIDENCE_FORBIDDEN")
    return result


def validate_optional_child_failure_semantics(
    *,
    result_kind: Any,
    execution_role: Any,
    strategy_type: Any,
    status: Any,
    failure_reason_code: Any,
) -> None:
    """Keep optional-child alerting semantics stable across all producers."""

    kind = choice(*RESULT_KINDS)(result_kind)
    role = choice(*EXECUTION_ROLES)(execution_role)
    strategy = choice(*RESULT_STRATEGY_TYPES)(strategy_type)
    outcome = choice("FAILED", "SKIPPED")(status)
    reason = identifier(failure_reason_code)
    if kind == "RECOMMENDED_PSI" and role == "SHADOW" and strategy == "DEEP_RL":
        require(outcome == "FAILED", "PPO_CHILD_SKIP_FORBIDDEN")
        return
    if kind == "STRESS_PSI" and role == "EVIDENCE_ONLY" and strategy == "MATHEMATICAL":
        require(
            (outcome == "SKIPPED" and reason == "STRESS_SCENARIO_UNAVAILABLE")
            or (outcome == "FAILED" and reason != "STRESS_SCENARIO_UNAVAILABLE"),
            "STRESS_CHILD_FAILURE_STATUS_INVALID",
        )
        return
    raise InventoryInputError("REQUIRED_PSI_CHILD_CANNOT_FAIL")


def _child_results(value: Any) -> list[dict[str, Any]]:
    require(type(value) is list and 2 <= len(value) <= 512, "CHILD_RESULTS_INVALID")
    result = [_child_result(item) for item in value]
    require(
        len({item["child_result_id"] for item in result}) == len(result),
        "CHILD_RESULT_ID_DUPLICATE",
    )
    semantic_keys = {
        (
            item["result_kind"],
            item["execution_role"],
            item["strategy_type"],
            item["scenario_id"],
            item["challenger_id"],
        )
        for item in result
    }
    require(len(semantic_keys) == len(result), "CHILD_RESULT_SEMANTIC_DUPLICATE")
    kind_order = {name: index for index, name in enumerate(RESULT_KINDS)}
    role_order = {name: index for index, name in enumerate(EXECUTION_ROLES)}
    strategy_order = {name: index for index, name in enumerate(RESULT_STRATEGY_TYPES)}
    return sorted(
        result,
        key=lambda item: (
            kind_order[item["result_kind"]],
            role_order[item["execution_role"]],
            strategy_order[item["strategy_type"]],
            item["scenario_id"],
            item["challenger_id"] or "",
            item["child_result_id"],
        ),
    )


def _metric_delta(value: Any) -> dict[str, str | None]:
    result = shape(
        value,
        {
            "status": choice(*METRIC_STATUSES),
            "value": optional(_signed_decimal),
            "reason_code": optional(choice(*METRIC_UNAVAILABLE_REASONS)),
        },
    )
    require(
        (
            result["status"] == "AVAILABLE"
            and result["value"] is not None
            and result["reason_code"] is None
        )
        or (
            result["status"] == "NOT_AVAILABLE"
            and result["value"] is None
            and result["reason_code"] is not None
        ),
        "METRIC_DELTA_STATUS_INVALID",
    )
    return result


def _comparison(value: Any) -> dict[str, Any]:
    return shape(
        value,
        {
            "baseline_child_result_id": identifier,
            "candidate_child_result_id": identifier,
            "cost_delta": _metric_delta,
            "service_level_delta": _metric_delta,
            "backorder_qty_delta": _metric_delta,
        },
    )


def _comparisons(value: Any) -> list[dict[str, Any]]:
    require(type(value) is list and len(value) <= 511, "RESULT_COMPARISONS_INVALID")
    result = [_comparison(item) for item in value]
    require(
        len({item["candidate_child_result_id"] for item in result}) == len(result),
        "RESULT_COMPARISON_DUPLICATE",
    )
    return sorted(result, key=lambda item: item["candidate_child_result_id"])


RESULT_BUNDLE_BODY_FIELDS = {
    "contract_id": choice(RESULT_BUNDLE_CONTRACT_ID),
    "contract_version": choice(RESULT_BUNDLE_CONTRACT_VERSION),
    "source_contract_key": choice(RESULT_BUNDLE_SOURCE_CONTRACT_KEY),
    "result_bundle_id": identifier,
    "engine_run_id": identifier,
    "attempt_no": _positive,
    "tenant_id": identifier,
    "project_id": identifier,
    "planning_cycle_id": identifier,
    "planning_cycle_revision_id": identifier,
    "cycle_site_execution_id": identifier,
    "plan_id": _opaque,
    "scope": _scope,
    "canonical_input_hash": hash_value,
    "site_binding_hash": hash_value,
    "strategy_execution_plan_id": identifier,
    "strategy_execution_plan_content_hash": hash_value,
    "classification_config_hash": hash_value,
    "effective_policy_content_hash": hash_value,
    "cost_profile_content_hash": optional(hash_value),
    "result_children": _child_results,
    "effective_child_result_id": identifier,
    "comparisons": _comparisons,
    "automatic_publish_allowed": boolean,
    "automatic_order_allowed": boolean,
}
RESULT_BUNDLE_FIELDS = {**RESULT_BUNDLE_BODY_FIELDS, "content_hash": hash_value}


def seal_inventory_result_bundle(
    value: Mapping[str, Any],
    *,
    strategy_execution_plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Normalize, cross-check and seal a Result Bundle against its pinned plan."""

    plan = validate_strategy_execution_plan(strategy_execution_plan)
    body = shape(dict(value), RESULT_BUNDLE_BODY_FIELDS)
    _validate_result_bundle_id(body)
    _validate_bundle_semantics(body, plan)
    return {**body, "content_hash": digest(body)}


def validate_inventory_result_bundle(
    value: Mapping[str, Any],
    *,
    strategy_execution_plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the Bundle hash and all plan/result/effective-pointer bindings."""

    plan = validate_strategy_execution_plan(strategy_execution_plan)
    normalized = shape(dict(value), RESULT_BUNDLE_FIELDS)
    body = {key: normalized[key] for key in RESULT_BUNDLE_BODY_FIELDS}
    _validate_result_bundle_id(body)
    _validate_bundle_semantics(body, plan)
    require(normalized["content_hash"] == digest(body), "RESULT_BUNDLE_HASH_MISMATCH")
    return normalized


def result_display_code(result: Mapping[str, Any]) -> str:
    """Return a UI label derived from the three orthogonal result axes."""

    normalized = _child_result(dict(result))
    if normalized["result_kind"] == "BASELINE_PSI":
        return "BASELINE_PSI"
    if normalized["result_kind"] == "RECOMMENDED_PSI":
        if normalized["execution_role"] == "OPERATIONAL":
            return "MATHEMATICAL_RECOMMENDED_PSI"
        return "PPO_SHADOW_RECOMMENDED_PSI"
    scenario = re.sub(r"[^A-Za-z0-9]+", "_", normalized["scenario_id"]).strip("_").upper()
    return f"STRESS_{scenario}_PSI"


def _validate_result_bundle_id(body: Mapping[str, Any]) -> None:
    require(
        body["result_bundle_id"]
        == derive_result_bundle_id(body["engine_run_id"], body["attempt_no"]),
        "RESULT_BUNDLE_ID_DERIVATION_MISMATCH",
    )


def _validate_bundle_semantics(body: Mapping[str, Any], plan: Mapping[str, Any]) -> None:
    require(
        body["strategy_execution_plan_id"] == plan["strategy_execution_plan_id"]
        and body["strategy_execution_plan_content_hash"] == plan["content_hash"],
        "RESULT_BUNDLE_PLAN_BINDING_MISMATCH",
    )
    require(
        body["classification_config_hash"] == plan["classification_config_hash"]
        and body["effective_policy_content_hash"] == plan["effective_policy_content_hash"],
        "RESULT_BUNDLE_POLICY_BINDING_MISMATCH",
    )
    require(
        not body["automatic_order_allowed"] or body["automatic_publish_allowed"],
        "RESULT_BUNDLE_PUBLICATION_GATE_INVALID",
    )
    cost_binding = plan["cost_profile_binding"]
    if cost_binding is None and body["cost_profile_content_hash"] is not None:
        raise InventoryInputError("RESULT_BUNDLE_COST_PROFILE_BINDING_UNSUPPORTED")
    require(
        body["cost_profile_content_hash"]
        == (None if cost_binding is None else cost_binding["profile_content_hash"]),
        "RESULT_BUNDLE_COST_PROFILE_BINDING_MISMATCH",
    )

    children = body["result_children"]
    baseline = [item for item in children if item["result_kind"] == "BASELINE_PSI"]
    mathematical = [
        item
        for item in children
        if item["result_kind"] == "RECOMMENDED_PSI"
        and item["execution_role"] == "OPERATIONAL"
        and item["strategy_type"] == "MATHEMATICAL"
    ]
    require(len(baseline) == 1, "RESULT_BUNDLE_BASELINE_REQUIRED")
    require(len(mathematical) == 1, "RESULT_BUNDLE_MATHEMATICAL_RESULT_REQUIRED")
    baseline_result = baseline[0]
    mathematical_result = mathematical[0]
    require(
        (
            baseline_result["scenario_id"],
            baseline_result["scenario_content_hash"],
        )
        == (
            mathematical_result["scenario_id"],
            mathematical_result["scenario_content_hash"],
        ),
        "RESULT_BUNDLE_BASE_SCENARIO_MISMATCH",
    )
    require(
        baseline_result["scenario_content_hash"] == plan["base_scenario_content_hash"],
        "RESULT_BUNDLE_BASE_SCENARIO_PLAN_MISMATCH",
    )
    require(
        body["effective_child_result_id"] == mathematical_result["child_result_id"],
        "RESULT_BUNDLE_EFFECTIVE_POINTER_INVALID",
    )

    challengers = {item["challenger_id"]: item for item in plan["shadow_challenger_bindings"]}
    shadow_results = [
        item
        for item in children
        if item["result_kind"] == "RECOMMENDED_PSI" and item["execution_role"] == "SHADOW"
    ]
    require(
        {item["challenger_id"] for item in shadow_results} == set(challengers),
        "RESULT_BUNDLE_SHADOW_RESULT_SET_MISMATCH",
    )
    for item in children:
        expected_child_result_id = derive_psi_child_result_id(
            engine_run_id=body["engine_run_id"],
            attempt_no=body["attempt_no"],
            canonical_input_hash=body["canonical_input_hash"],
            result_kind=item["result_kind"],
            execution_role=item["execution_role"],
            strategy_type=item["strategy_type"],
            scenario_id=item["scenario_id"],
            scenario_content_hash=item["scenario_content_hash"],
            challenger_id=item["challenger_id"],
            model_content_hash=item["model_content_hash"],
        )
        require(
            item["child_result_id"] == expected_child_result_id,
            "RESULT_BUNDLE_CHILD_ID_DERIVATION_MISMATCH",
        )
        references = derive_psi_artifact_references(
            engine_run_id=body["engine_run_id"],
            attempt_no=body["attempt_no"],
            child_result_id=expected_child_result_id,
        )
        if item["status"] == "SUCCEEDED":
            require(
                item["artifact_reference"] == references["psi"]
                and item["artifact_contract_key"] == PSI_RESULT_ARTIFACT_CONTRACT_KEY
                and item["artifact_contract_version"] == PSI_RESULT_ARTIFACT_CONTRACT_VERSION,
                "RESULT_BUNDLE_CHILD_ARTIFACT_REFERENCE_MISMATCH",
            )
            if item["strategy_type"] != "NONE":
                require(
                    all(
                        item["action_evidence"][f"{key}_reference"] == references[key]
                        for key in (
                            "raw_action",
                            "constrained_action",
                            "adjustment_reasons",
                        )
                    ),
                    "RESULT_BUNDLE_ACTION_REFERENCE_MISMATCH",
                )
        else:
            require(
                item["artifact_reference"] is None
                and item["artifact_contract_key"] is None
                and item["artifact_contract_version"] is None
                and item["child_content_hash"] is None
                and item["row_count"] is None
                and all(value is None for value in item["action_evidence"].values()),
                "RESULT_BUNDLE_FAILED_CHILD_ARTIFACT_FORBIDDEN",
            )
        if item["strategy_type"] == "DEEP_RL":
            challenger = challengers.get(item["challenger_id"])
            require(
                challenger is not None
                and item["model_content_hash"] == challenger["model"]["content_hash"],
                "RESULT_BUNDLE_CHALLENGER_BINDING_MISMATCH",
            )
        if item["result_kind"] == "RECOMMENDED_PSI" and item["execution_role"] == "SHADOW":
            require(
                (item["scenario_id"], item["scenario_content_hash"])
                == (
                    baseline_result["scenario_id"],
                    baseline_result["scenario_content_hash"],
                ),
                "RESULT_BUNDLE_BASE_SCENARIO_MISMATCH",
            )

    stress_bindings = {item["scenario_id"]: item for item in plan["stress_scenario_bindings"]}
    stress_results = [item for item in children if item["result_kind"] == "STRESS_PSI"]
    require(
        len(stress_results) == len(stress_bindings)
        and {item["scenario_id"] for item in stress_results} == set(stress_bindings),
        "RESULT_BUNDLE_STRESS_RESULT_SET_MISMATCH",
    )
    for item in stress_results:
        binding = stress_bindings[item["scenario_id"]]
        require(
            item["scenario_content_hash"] == binding["scenario_content_hash"],
            "RESULT_BUNDLE_STRESS_SCENARIO_MISMATCH",
        )

    by_id = {item["child_result_id"]: item for item in children}
    comparisons = body["comparisons"]
    expected_candidates = set(by_id) - {baseline_result["child_result_id"]}
    require(
        {item["candidate_child_result_id"] for item in comparisons} == expected_candidates,
        "RESULT_BUNDLE_COMPARISON_SET_MISMATCH",
    )
    for comparison in comparisons:
        require(
            comparison["baseline_child_result_id"] == baseline_result["child_result_id"],
            "RESULT_BUNDLE_COMPARISON_BASELINE_MISMATCH",
        )
        candidate = by_id[comparison["candidate_child_result_id"]]
        _validate_comparison_metrics(
            comparison,
            candidate_status=candidate["status"],
            cost_profile_bound=cost_binding is not None,
        )


def _validate_comparison_metrics(
    comparison: Mapping[str, Any],
    *,
    candidate_status: str,
    cost_profile_bound: bool,
) -> None:
    metrics = (
        comparison["cost_delta"],
        comparison["service_level_delta"],
        comparison["backorder_qty_delta"],
    )
    if candidate_status != "SUCCEEDED":
        reason = (
            "CANDIDATE_RESULT_FAILED"
            if candidate_status == "FAILED"
            else "CANDIDATE_RESULT_SKIPPED"
        )
        require(
            all(
                metric == {"status": "NOT_AVAILABLE", "value": None, "reason_code": reason}
                for metric in metrics
            ),
            "RESULT_BUNDLE_FAILED_COMPARISON_INVALID",
        )
        return
    require(
        comparison["backorder_qty_delta"]["status"] == "AVAILABLE"
        and (
            comparison["service_level_delta"]["status"] == "AVAILABLE"
            or comparison["service_level_delta"]
            == {
                "status": "NOT_AVAILABLE",
                "value": None,
                "reason_code": "NO_DEMAND_IN_HORIZON",
            }
        ),
        "RESULT_BUNDLE_OPERATIONAL_METRICS_REQUIRED",
    )
    cost = comparison["cost_delta"]
    if cost_profile_bound:
        require(
            cost["status"] == "AVAILABLE"
            and cost["value"] is not None
            and cost["reason_code"] is None,
            "RESULT_BUNDLE_COST_PROFILE_STATUS_INVALID",
        )
    else:
        require(
            cost
            == {
                "status": "NOT_AVAILABLE",
                "value": None,
                "reason_code": "COST_PROFILE_NOT_BOUND",
            },
            "RESULT_BUNDLE_COST_PROFILE_STATUS_INVALID",
        )


__all__ = [
    "RESULT_BUNDLE_CONTRACT_ID",
    "RESULT_BUNDLE_CONTRACT_VERSION",
    "RESULT_BUNDLE_FIELDS",
    "RESULT_BUNDLE_SOURCE_CONTRACT_KEY",
    "STRATEGY_EXECUTION_PLAN_CONTRACT_ID",
    "STRATEGY_EXECUTION_PLAN_CONTRACT_VERSION",
    "STRATEGY_EXECUTION_PLAN_FIELDS",
    "derive_psi_artifact_references",
    "derive_psi_child_result_id",
    "derive_cost_profile_content_hash",
    "derive_result_bundle_id",
    "result_display_code",
    "seal_inventory_result_bundle",
    "seal_strategy_execution_plan",
    "validate_inventory_result_bundle",
    "validate_strategy_execution_plan",
]

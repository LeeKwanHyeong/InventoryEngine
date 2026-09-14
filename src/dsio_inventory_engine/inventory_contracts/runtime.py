"""Closed Platform-to-Inventory Runtime execution contract."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .canonical import CanonicalInputRequest
from .result_bundle import (
    validate_inventory_result_bundle,
    validate_strategy_execution_plan,
)
from .values import (
    boolean,
    canonical_json,
    choice,
    day,
    digest,
    hash_value,
    identifier,
    integer,
    optional,
    read_json,
    require,
    shape,
    timestamp,
    yyyyww,
)


CONTRACT_ID = "inventory-engine-execution-request-v1"
CONTRACT_VERSION = "1.0.0"
RECEIPT_ID = "inventory-engine-execution-receipt-v1"
CANONICAL_CONTEXT_BINDING_CONTRACT_ID = "inventory-canonical-context-binding-v1"
CANONICAL_CONTEXT_BINDING_CONTRACT_VERSION = "1.0.0"
REQUIRED_INPUT_TYPES = {
    "DEMAND_FORECAST",
    "INVENTORY_POSITION",
    "INVENTORY_POLICY",
    "REPLENISHMENT_POLICY",
    "SUPPLY_RECEIPTS",
    "CUSTOMER_ORDERS",
    "PRIOR_INVENTORY",
    "CALENDAR",
    "MASTER",
}
INPUT_TYPES = REQUIRED_INPUT_TYPES | {
    "INVENTORY_NETWORK",
    "INVENTORY_CLASSIFICATION",
}
CANONICAL_INPUT_BINDING_TYPES = {
    "DEMAND_FORECAST": ("forecast", "demand.forecast_snapshot", "1.0.0"),
    "INVENTORY_POSITION": ("inventory", "inventory.position", "1.0.0"),
    "REPLENISHMENT_POLICY": (
        "policies",
        "inventory.replenishment_policy",
        "1.0.0",
    ),
    "SUPPLY_RECEIPTS": (
        "receipts",
        "inventory.supply_receipts",
        "1.0.0",
    ),
    "CUSTOMER_ORDERS": (
        "customer_orders",
        "inventory.customer_orders",
        "1.0.0",
    ),
    "PRIOR_INVENTORY": (
        "prior_inventory",
        "inventory.prior_inventory",
        "1.0.0",
    ),
    "CALENDAR": ("calendar", "demand_io.calendar", "1.0.0"),
    "MASTER": ("master", "demand_io.master", "1.0.0"),
}

CANONICAL_CONTEXT_BINDING_BODY_FIELDS = {
    "contract_id": choice(CANONICAL_CONTEXT_BINDING_CONTRACT_ID),
    "contract_version": choice(CANONICAL_CONTEXT_BINDING_CONTRACT_VERSION),
    "plan_type": choice("POSM", "TGSM"),
    "plan_yyyyww": yyyyww,
    "plan_start_date": day,
    "plan_end_date": day,
    "master_as_of_date": day,
    "master_snapshot_revision": identifier,
    "business_timezone": lambda value: _business_timezone(value),
    "inventory_cutoff_at": timestamp,
    "inventory_source_watermark": lambda value: _stable_text(value),
    "quantity_rules_content_hash": hash_value,
}
CANONICAL_CONTEXT_BINDING_FIELDS = {
    **CANONICAL_CONTEXT_BINDING_BODY_FIELDS,
    "content_hash": hash_value,
}


def _stable_text(value: Any) -> str:
    require(
        isinstance(value, str)
        and value == value.strip()
        and bool(value)
        and len(value) <= 512
        and re.search(r"[\x00-\x1f\x7f]", value) is None,
        "INVALID_STABLE_TEXT",
    )
    return value


def _business_timezone(value: Any) -> str:
    normalized = _stable_text(value)
    try:
        ZoneInfo(normalized)
    except (ZoneInfoNotFoundError, ValueError):
        require(False, "INVALID_BUSINESS_TIMEZONE")
    return normalized


def _canonical_context_binding_body(
    context: Mapping[str, Any],
    quantity_rules: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Normalize the claimable, non-attempt Canonical admission context."""

    require(type(context) is dict, "CANONICAL_CONTEXT_BINDING_CONTEXT_INVALID")
    require(type(quantity_rules) is list, "CANONICAL_CONTEXT_BINDING_QUANTITY_RULES_INVALID")
    normalized_context = shape(
        {
            key: context.get(key)
            for key in CANONICAL_CONTEXT_BINDING_BODY_FIELDS
            if key
            not in {
                "contract_id",
                "contract_version",
                "quantity_rules_content_hash",
            }
        },
        {
            key: rule
            for key, rule in CANONICAL_CONTEXT_BINDING_BODY_FIELDS.items()
            if key not in {"contract_id", "contract_version", "quantity_rules_content_hash"}
        },
    )
    require(
        date.fromisoformat(normalized_context["plan_start_date"])
        <= date.fromisoformat(normalized_context["plan_end_date"]),
        "CANONICAL_CONTEXT_BINDING_HORIZON_INVALID",
    )
    return {
        "contract_id": CANONICAL_CONTEXT_BINDING_CONTRACT_ID,
        "contract_version": CANONICAL_CONTEXT_BINDING_CONTRACT_VERSION,
        **normalized_context,
        "quantity_rules_content_hash": digest(quantity_rules),
    }


def seal_canonical_context_binding(
    canonical_input: CanonicalInputRequest,
) -> dict[str, Any]:
    """Seal Context and normalized quantity rules from an actual Canonical value object."""

    canonical = CanonicalInputRequest.from_dict(canonical_input.to_dict()).to_dict()
    body = _canonical_context_binding_body(
        canonical["context"],
        canonical["quantity_rules"],
    )
    return {**body, "content_hash": digest(body)}


def validate_canonical_context_binding(value: Any) -> dict[str, Any]:
    """Validate the binding wire and its self-hash before it enters a Run claim."""

    normalized = shape(value, CANONICAL_CONTEXT_BINDING_FIELDS)
    body = {key: normalized[key] for key in CANONICAL_CONTEXT_BINDING_BODY_FIELDS}
    require(
        date.fromisoformat(body["plan_start_date"]) <= date.fromisoformat(body["plan_end_date"]),
        "CANONICAL_CONTEXT_BINDING_HORIZON_INVALID",
    )
    require(
        normalized["content_hash"] == digest(body),
        "CANONICAL_CONTEXT_BINDING_HASH_MISMATCH",
    )
    return normalized


def _uuid(value: Any) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError):
        require(False, "INVALID_UUID")
    raise AssertionError("unreachable")


def _positive(value: Any) -> int:
    parsed = integer(value)
    require(parsed > 0, "INVALID_POSITIVE_INTEGER")
    return parsed


def _opaque(value: Any, *, limit: int = 160) -> str:
    require(
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= limit
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*", value) is not None,
        "INVALID_OPAQUE_IDENTIFIER",
    )
    return value


def _optional_identifier(value: Any) -> str | None:
    return None if value is None else identifier(value)


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


def _input_binding(value: Any) -> dict[str, Any]:
    require(type(value) is dict, "INPUT_BINDING_INVALID")
    input_type = choice(*sorted(INPUT_TYPES))(value.get("input_type"))
    fields = {
        "input_type": choice(*sorted(INPUT_TYPES)),
        "source_contract_key": identifier,
        "source_snapshot_id": _opaque,
        "source_content_hash": hash_value,
        "source_contract_version": lambda item: _opaque(item, limit=32),
    }
    result = shape(value, fields)
    require(
        re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", result["source_contract_version"]) is not None,
        "INVALID_CONTRACT_VERSION",
    )
    if input_type == "INVENTORY_CLASSIFICATION":
        require(
            result["source_contract_key"] == "inventory.classification_effective_policy"
            and result["source_contract_version"] == "2.0.0",
            "INVENTORY_CLASSIFICATION_BINDING_INVALID",
        )
        _uuid(result["source_snapshot_id"])
    return result


def _claim(value: Any) -> dict[str, Any]:
    # Platform's v1 wire may omit these additive fields.  Normalize omission and
    # explicit JSON null to the same canonical v1 representation; v2 semantics
    # below require both values to be sealed booleans.
    if type(value) is dict:
        value = dict(value)
        value.setdefault("expected_automatic_publish_allowed", None)
        value.setdefault("expected_automatic_order_allowed", None)
        value.setdefault("strategy_execution_plan", None)
        value.setdefault("canonical_context_binding", None)
    result = shape(
        value,
        {
            "contract_id": choice("inventory-engine-run-claim-v1"),
            "contract_version": choice("1.0.0"),
            "tenant_id": identifier,
            "project_id": identifier,
            "planning_cycle_id": identifier,
            "planning_cycle_revision_id": identifier,
            "cycle_site_execution_id": identifier,
            "expected_site_row_version": _positive,
            "config_id": _uuid,
            "config_revision_id": _uuid,
            "config_hash": hash_value,
            "demand_run_id": _uuid,
            "plan_id": _opaque,
            "plan_key_hash": hash_value,
            "site_binding_hash": hash_value,
            "expected_automatic_publish_allowed": optional(boolean),
            "expected_automatic_order_allowed": optional(boolean),
            "strategy_execution_plan": optional(validate_strategy_execution_plan),
            "canonical_context_binding": optional(validate_canonical_context_binding),
            "trigger_type": choice("manual", "scheduled", "api", "recovery"),
            "idempotency_key_hash": hash_value,
            "request_id": _opaque,
            "correlation_id": _opaque,
            "requested_by": _optional_identifier,
            "scope": _scope,
            "input_bindings": lambda items: (
                [_input_binding(item) for item in items]
                if isinstance(items, list)
                else require(False, "INPUT_BINDINGS_INVALID")
            ),
        },
    )
    require(
        result["plan_key_hash"]
        == digest(
            {
                "planning_cycle_revision_id": result["planning_cycle_revision_id"],
                "plan_id": result["plan_id"],
                "scope": result["scope"],
            }
        ),
        "RUNTIME_PLAN_KEY_BINDING_MISMATCH",
    )
    bindings = result["input_bindings"]
    require(9 <= len(bindings) <= 11, "INPUT_BINDINGS_INVALID")
    by_type = {item["input_type"]: item for item in bindings}
    require(len(by_type) == len(bindings), "INPUT_BINDING_DUPLICATE")
    require(REQUIRED_INPUT_TYPES <= set(by_type), "INPUT_BINDING_INCOMPLETE")
    for input_type, (
        _canonical_kind,
        source_key,
        contract_version,
    ) in CANONICAL_INPUT_BINDING_TYPES.items():
        binding = by_type[input_type]
        require(
            binding["source_contract_key"] == source_key
            and binding["source_contract_version"] == contract_version,
            "RUNTIME_INPUT_BINDING_CONTRACT_MISMATCH",
        )
    policy = by_type["INVENTORY_POLICY"]
    require(
        policy["source_contract_key"] == "inventory.policy"
        and policy["source_snapshot_id"] == result["config_revision_id"]
        and policy["source_content_hash"] == result["config_hash"],
        "INVENTORY_POLICY_BINDING_MISMATCH",
    )
    classification = by_type.get("INVENTORY_CLASSIFICATION")
    strategy_execution_plan = result["strategy_execution_plan"]
    canonical_context_binding = result["canonical_context_binding"]
    require(
        (classification is None and policy["source_contract_version"] == "1.0.0")
        or (classification is not None and policy["source_contract_version"] == "2.0.0"),
        "INVENTORY_POLICY_BINDING_VERSION_MISMATCH",
    )
    require(
        (classification is None and strategy_execution_plan is None)
        or (classification is not None and strategy_execution_plan is not None),
        (
            "RUNTIME_STRATEGY_EXECUTION_PLAN_UNEXPECTED"
            if classification is None
            else "RUNTIME_STRATEGY_EXECUTION_PLAN_REQUIRED"
        ),
    )
    require(
        (classification is None and canonical_context_binding is None)
        or (classification is not None and canonical_context_binding is not None),
        (
            "RUNTIME_CANONICAL_CONTEXT_BINDING_UNEXPECTED"
            if classification is None
            else "RUNTIME_CANONICAL_CONTEXT_BINDING_REQUIRED"
        ),
    )
    expected_gates = (
        result["expected_automatic_publish_allowed"],
        result["expected_automatic_order_allowed"],
    )
    require(
        (classification is None and expected_gates == (None, None))
        or (
            classification is not None
            and all(type(value) is bool for value in expected_gates)
            and (
                bool(result["expected_automatic_publish_allowed"])
                or not bool(result["expected_automatic_order_allowed"])
            )
        ),
        (
            "RUNTIME_V1_POLICY_ADMISSION_FORBIDDEN"
            if classification is None
            else "RUNTIME_V2_POLICY_ADMISSION_REQUIRED"
        ),
    )
    if classification is not None:
        require(
            strategy_execution_plan["classification_config_hash"] == result["config_hash"]
            and strategy_execution_plan["effective_policy_content_hash"]
            == classification["source_content_hash"],
            "RUNTIME_STRATEGY_EXECUTION_PLAN_POLICY_MISMATCH",
        )
    else:
        # The additive V2 field must not change the normalized V1 wire or hash.
        del result["canonical_context_binding"]
    result["input_bindings"] = sorted(bindings, key=lambda item: item["input_type"])
    return result


def validate_effective_policy_runtime_binding(
    request: "InventoryRuntimeExecutionRequest",
    binding: Mapping[str, Any],
) -> dict[str, str]:
    """Match a replenishment V2 binding to the exact Platform-claimed inputs."""

    normalized = shape(
        dict(binding),
        {
            "contract_id": choice("inventory-effective-policy-run-binding-v2"),
            "contract_version": choice("2.0.0"),
            "classification_snapshot_id": _uuid,
            "classification_config_hash": hash_value,
            "effective_policy_content_hash": hash_value,
        },
    )
    claim = request.value["claim"]
    by_type = {row["input_type"]: row for row in claim["input_bindings"]}
    classification = by_type.get("INVENTORY_CLASSIFICATION")
    require(classification is not None, "RUNTIME_CLASSIFICATION_BINDING_REQUIRED")
    require(
        normalized["classification_snapshot_id"] == classification["source_snapshot_id"]
        and normalized["effective_policy_content_hash"] == classification["source_content_hash"],
        "RUNTIME_CLASSIFICATION_BINDING_MISMATCH",
    )
    require(
        normalized["classification_config_hash"] == claim["config_hash"],
        "RUNTIME_CLASSIFICATION_CONFIG_MISMATCH",
    )
    return normalized


def validate_canonical_runtime_binding(
    request: "InventoryRuntimeExecutionRequest",
    *,
    canonical_input: CanonicalInputRequest,
) -> None:
    """Bind a Platform claim to one canonical attempt and its Demand provenance."""

    canonical = CanonicalInputRequest.from_dict(canonical_input.to_dict()).to_dict()
    context = canonical["context"]
    input_bindings = canonical["input_bindings"]
    forecast_provenance = canonical["snapshots"]["forecast"]["metadata"]
    claim = request.value["claim"]
    require(
        request.engine_run_id == context.get("engine_run_id"),
        "RUNTIME_CANONICAL_RUN_ID_MISMATCH",
    )
    require(
        all(
            claim[key] == context.get(key)
            for key in (
                "planning_cycle_id",
                "planning_cycle_revision_id",
                "cycle_site_execution_id",
            )
        ),
        "RUNTIME_CANONICAL_CYCLE_MISMATCH",
    )
    require(
        claim["demand_run_id"]
        == context.get("demand_run_id")
        == forecast_provenance.get("demand_run_id"),
        "RUNTIME_CANONICAL_DEMAND_RUN_MISMATCH",
    )
    require(
        claim["plan_id"] == context.get("plan_id"),
        "RUNTIME_CANONICAL_PLAN_ID_MISMATCH",
    )
    require(
        all(claim["scope"][key] == context.get(key) for key in ("company_cd", "subs_cd", "site_cd"))
        and claim["scope"]["plant_cd"] == context.get("site_cd"),
        "RUNTIME_CANONICAL_SCOPE_MISMATCH",
    )
    require(
        claim["config_revision_id"] == context.get("configuration_revision"),
        "RUNTIME_CANONICAL_CONFIG_REVISION_MISMATCH",
    )
    require(
        claim["canonical_context_binding"] == seal_canonical_context_binding(canonical_input),
        "RUNTIME_CANONICAL_CONTEXT_BINDING_MISMATCH",
    )
    require(
        claim["plan_key_hash"]
        == digest(
            {
                "planning_cycle_revision_id": claim["planning_cycle_revision_id"],
                "plan_id": claim["plan_id"],
                "scope": claim["scope"],
            }
        ),
        "RUNTIME_PLAN_KEY_BINDING_MISMATCH",
    )

    by_type = {row["input_type"]: row for row in claim["input_bindings"]}
    for input_type, (
        canonical_kind,
        source_key,
        contract_version,
    ) in CANONICAL_INPUT_BINDING_TYPES.items():
        canonical = input_bindings.get(canonical_kind)
        claimed = by_type[input_type]
        require(
            isinstance(canonical, Mapping)
            and claimed["source_contract_key"] == source_key
            and claimed["source_contract_version"] == contract_version
            and claimed["source_snapshot_id"] == canonical.get("snapshot_id")
            and claimed["source_content_hash"] == canonical.get("content_hash"),
            "RUNTIME_CANONICAL_INPUT_BINDING_MISMATCH",
        )

    site_identity: dict[str, Any] = {
        "scope": claim["scope"],
        "input_bindings": claim["input_bindings"],
    }
    if "INVENTORY_CLASSIFICATION" in by_type:
        site_identity["canonical_context_binding_hash"] = claim["canonical_context_binding"][
            "content_hash"
        ]
        site_identity["effective_policy_admission"] = {
            "automatic_publish_allowed": claim["expected_automatic_publish_allowed"],
            "automatic_order_allowed": claim["expected_automatic_order_allowed"],
        }
        site_identity["strategy_execution_plan_content_hash"] = claim["strategy_execution_plan"][
            "content_hash"
        ]
    require(
        claim["site_binding_hash"] == digest(site_identity),
        "RUNTIME_SITE_BINDING_HASH_MISMATCH",
    )


def validate_result_bundle_runtime_binding(
    request: "InventoryRuntimeExecutionRequest",
    bundle: Mapping[str, Any],
    *,
    expected_canonical_input_hash: str,
) -> dict[str, Any]:
    """Bind a sealed Bundle back to the exact Platform-claimed Attempt."""

    expected_canonical_input_hash = hash_value(expected_canonical_input_hash)
    claim = request.value["claim"]
    strategy_plan = request.strategy_execution_plan
    require(strategy_plan is not None, "RUNTIME_RESULT_BUNDLE_PLAN_REQUIRED")
    normalized = validate_inventory_result_bundle(
        bundle,
        strategy_execution_plan=strategy_plan,
    )
    classification = request.classification_binding
    require(classification is not None, "RUNTIME_RESULT_BUNDLE_CLASSIFICATION_REQUIRED")
    require(
        normalized["engine_run_id"] == request.engine_run_id
        and normalized["attempt_no"] == request.value["attempt_no"],
        "RUNTIME_RESULT_BUNDLE_ATTEMPT_MISMATCH",
    )
    require(
        normalized["tenant_id"] == claim["tenant_id"]
        and normalized["project_id"] == claim["project_id"],
        "RUNTIME_RESULT_BUNDLE_OWNER_MISMATCH",
    )
    require(
        all(
            normalized[key] == claim[key]
            for key in (
                "planning_cycle_id",
                "planning_cycle_revision_id",
                "cycle_site_execution_id",
                "plan_id",
            )
        )
        and normalized["scope"] == claim["scope"],
        "RUNTIME_RESULT_BUNDLE_SCOPE_MISMATCH",
    )
    require(
        normalized["canonical_input_hash"] == expected_canonical_input_hash,
        "RUNTIME_RESULT_BUNDLE_CANONICAL_INPUT_MISMATCH",
    )
    require(
        normalized["site_binding_hash"] == claim["site_binding_hash"]
        and normalized["strategy_execution_plan_id"] == strategy_plan["strategy_execution_plan_id"]
        and normalized["strategy_execution_plan_content_hash"] == strategy_plan["content_hash"],
        "RUNTIME_RESULT_BUNDLE_INPUT_BINDING_MISMATCH",
    )
    require(
        normalized["classification_config_hash"] == claim["config_hash"]
        and normalized["effective_policy_content_hash"] == classification["source_content_hash"],
        "RUNTIME_RESULT_BUNDLE_POLICY_MISMATCH",
    )
    require(
        normalized["automatic_publish_allowed"] == claim["expected_automatic_publish_allowed"]
        and normalized["automatic_order_allowed"] == claim["expected_automatic_order_allowed"],
        "RUNTIME_RESULT_BUNDLE_GATE_MISMATCH",
    )
    return normalized


@dataclass(frozen=True, slots=True)
class InventoryRuntimeExecutionRequest:
    value: dict[str, Any]

    @classmethod
    def from_dict(cls, value: Any) -> "InventoryRuntimeExecutionRequest":
        normalized = shape(
            value,
            {
                "contract_id": choice(CONTRACT_ID),
                "contract_version": choice(CONTRACT_VERSION),
                "engine_run_id": _uuid,
                "attempt_no": _positive,
                "site_row_version": _positive,
                "claim": _claim,
            },
        )
        require(
            len(canonical_json(normalized).encode("utf-8")) <= 1_000_000,
            "RUNTIME_REQUEST_SIZE_LIMIT",
        )
        return cls(normalized)

    @classmethod
    def from_json(cls, document: str) -> "InventoryRuntimeExecutionRequest":
        return cls.from_dict(read_json(document, max_bytes=1_000_000))

    @property
    def engine_run_id(self) -> str:
        return self.value["engine_run_id"]

    @property
    def tenant_id(self) -> str:
        return self.value["claim"]["tenant_id"]

    @property
    def project_id(self) -> str:
        return self.value["claim"]["project_id"]

    @property
    def site_row_version(self) -> int:
        return self.value["site_row_version"]

    @property
    def canonical_hash(self) -> str:
        value = self.to_dict()
        if value["claim"]["expected_automatic_publish_allowed"] is None:
            del value["claim"]["expected_automatic_publish_allowed"]
            del value["claim"]["expected_automatic_order_allowed"]
            del value["claim"]["strategy_execution_plan"]
        return digest(value)

    @property
    def classification_binding(self) -> dict[str, Any] | None:
        return next(
            (
                dict(binding)
                for binding in self.value["claim"]["input_bindings"]
                if binding["input_type"] == "INVENTORY_CLASSIFICATION"
            ),
            None,
        )

    @property
    def strategy_execution_plan(self) -> dict[str, Any] | None:
        value = self.value["claim"]["strategy_execution_plan"]
        return None if value is None else dict(value)

    @property
    def canonical_context_binding(self) -> dict[str, Any] | None:
        value = self.value["claim"].get("canonical_context_binding")
        return None if value is None else dict(value)

    def to_dict(self) -> dict[str, Any]:
        return {**self.value, "claim": {**self.value["claim"]}}


@dataclass(frozen=True, slots=True)
class InventoryRuntimeExecutionReceipt:
    engine_run_id: str
    status: str
    replayed: bool

    @classmethod
    def from_dict(cls, value: Any) -> "InventoryRuntimeExecutionReceipt":
        normalized = shape(
            value,
            {
                "engine_run_id": _uuid,
                "status": choice("created", "running"),
                "replayed": boolean,
            },
        )
        return cls(**normalized)

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": RECEIPT_ID,
            "engine_run_id": self.engine_run_id,
            "status": self.status,
            "replayed": self.replayed,
        }


__all__ = [
    "CANONICAL_CONTEXT_BINDING_CONTRACT_ID",
    "CANONICAL_CONTEXT_BINDING_CONTRACT_VERSION",
    "CANONICAL_CONTEXT_BINDING_FIELDS",
    "CONTRACT_ID",
    "CONTRACT_VERSION",
    "InventoryRuntimeExecutionReceipt",
    "InventoryRuntimeExecutionRequest",
    "RECEIPT_ID",
    "seal_canonical_context_binding",
    "validate_canonical_context_binding",
    "validate_canonical_runtime_binding",
    "validate_effective_policy_runtime_binding",
    "validate_result_bundle_runtime_binding",
]

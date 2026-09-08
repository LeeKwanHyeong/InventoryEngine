"""Closed Platform-to-Inventory Runtime execution contract."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from .values import (
    boolean,
    canonical_json,
    choice,
    digest,
    hash_value,
    identifier,
    integer,
    read_json,
    require,
    shape,
)


CONTRACT_ID = "inventory-engine-execution-request-v1"
CONTRACT_VERSION = "1.0.0"
RECEIPT_ID = "inventory-engine-execution-receipt-v1"
REQUIRED_INPUT_TYPES = {
    "DEMAND_FORECAST",
    "INVENTORY_POSITION",
    "INVENTORY_POLICY",
    "CALENDAR",
    "MASTER",
}
INPUT_TYPES = REQUIRED_INPUT_TYPES | {"INVENTORY_NETWORK"}


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
    result = shape(
        value,
        {
            "input_type": choice(*sorted(INPUT_TYPES)),
            "source_contract_key": identifier,
            "source_snapshot_id": _opaque,
            "source_content_hash": hash_value,
            "source_contract_version": lambda item: _opaque(item, limit=32),
        },
    )
    require(
        re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", result["source_contract_version"])
        is not None,
        "INVALID_CONTRACT_VERSION",
    )
    return result


def _claim(value: Any) -> dict[str, Any]:
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
            "plan_id": _opaque,
            "plan_key_hash": hash_value,
            "site_binding_hash": hash_value,
            "trigger_type": choice("manual", "scheduled", "api", "recovery"),
            "idempotency_key_hash": hash_value,
            "request_id": _opaque,
            "correlation_id": _opaque,
            "requested_by": _optional_identifier,
            "scope": _scope,
            "input_bindings": lambda items: [_input_binding(item) for item in items]
            if isinstance(items, list)
            else require(False, "INPUT_BINDINGS_INVALID"),
        },
    )
    bindings = result["input_bindings"]
    require(5 <= len(bindings) <= 6, "INPUT_BINDINGS_INVALID")
    by_type = {item["input_type"]: item for item in bindings}
    require(len(by_type) == len(bindings), "INPUT_BINDING_DUPLICATE")
    require(REQUIRED_INPUT_TYPES <= set(by_type), "INPUT_BINDING_INCOMPLETE")
    policy = by_type["INVENTORY_POLICY"]
    require(
        policy["source_snapshot_id"] == result["config_revision_id"]
        and policy["source_content_hash"] == result["config_hash"],
        "INVENTORY_POLICY_BINDING_MISMATCH",
    )
    result["input_bindings"] = sorted(bindings, key=lambda item: item["input_type"])
    return result


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
        return digest(self.value)

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
    "CONTRACT_ID",
    "CONTRACT_VERSION",
    "InventoryRuntimeExecutionReceipt",
    "InventoryRuntimeExecutionRequest",
    "RECEIPT_ID",
]

"""Closed evidence-only parent contract, independent of calculator and storage."""

from __future__ import annotations

import re
from typing import Any, Mapping

from .landed_cost_artifact import currency, landed_cost_run_binding, positive
from .run_cost import MANIFEST_ID, MANIFEST_KEY, MODE, RECOGNITION, VERSION
from .values import (
    choice,
    day,
    decimal_string,
    digest,
    hash_value,
    identifier,
    integer,
    item_identifier,
    require,
    shape,
)


def reference(value: Any) -> str:
    require(
        isinstance(value, str)
        and 1 <= len(value) <= 512
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", value) is not None,
        "INVALID_EVIDENCE_REFERENCE",
    )
    return value


def run_manifest_reference(request) -> str:
    return f"artifact:inventory:{request.engine_run_id}:{request.value['attempt_no']}:run-result-manifest"


def _optional(rule):
    return lambda value: None if value is None else rule(value)


def _rows(value, rule, maximum=10_000):
    require(type(value) is list and len(value) <= maximum, "RUN_COST_ROW_LIMIT")
    return [rule(row) for row in value]


def _order(value):
    return shape(
        value,
        {
            "item_id": item_identifier,
            "decision_id": identifier,
            "decision_date": day,
            "accepted_order_qty": positive,
            "reason_code": choice("PRICING_TEMPLATE_MISSING"),
        },
    )


def _metric(value):
    result = shape(
        value,
        {
            "psi_child_result_id": identifier,
            "status": choice("AVAILABLE", "NOT_AVAILABLE"),
            "operating_cost": _optional(decimal_string),
            "net_landed_cost": _optional(decimal_string),
            "total_cost": _optional(decimal_string),
            "accepted_order_count": integer,
            "accepted_order_qty": decimal_string,
            "unpriced_orders": lambda rows: _rows(rows, _order),
            "reason_code": _optional(identifier),
        },
    )
    available = result["status"] == "AVAILABLE"
    require(result["accepted_order_count"] >= 0, "RUN_COST_ORDER_COUNT_INVALID")
    require(
        all(
            (result[key] is not None) == available
            for key in ("operating_cost", "net_landed_cost", "total_cost")
        )
        and (result["reason_code"] is None) == available,
        "RUN_COST_METRIC_STATUS_INVALID",
    )
    return result


def _comparison(value):
    result = shape(
        value,
        {
            "baseline_psi_child_result_id": identifier,
            "candidate_psi_child_result_id": identifier,
            "status": choice("AVAILABLE", "NOT_AVAILABLE"),
            "total_cost_delta": _optional(lambda item: decimal_string(item, signed=True)),
            "reason_code": _optional(identifier),
        },
    )
    available = result["status"] == "AVAILABLE"
    require(
        (result["total_cost_delta"] is not None) == available
        and (result["reason_code"] is None) == available,
        "RUN_COST_COMPARISON_STATUS_INVALID",
    )
    return result


def validate_manifest_document(value: Mapping[str, Any]) -> dict:
    result = shape(
        dict(value),
        {
            "contract_id": choice(MANIFEST_ID),
            "contract_version": choice(VERSION),
            "source_contract_key": choice(MANIFEST_KEY),
            "artifact_reference": reference,
            "engine_run_id": identifier,
            "attempt_no": integer,
            "tenant_id": identifier,
            "project_id": identifier,
            "runtime_request_content_hash": hash_value,
            "canonical_input_hash": hash_value,
            "site_binding_hash": hash_value,
            "strategy_execution_plan_content_hash": hash_value,
            "psi_result": lambda row: shape(
                row,
                {
                    "snapshot_id": identifier,
                    "content_hash": hash_value,
                    "artifact_reference": reference,
                },
            ),
            "input_reference": reference,
            "input_content_hash": hash_value,
            "projection_reference": reference,
            "projection_content_hash": hash_value,
            "profile_reference": reference,
            "profile_content_hash": hash_value,
            "cost_application_mode": choice(MODE),
            "recognition_rule": choice(RECOGNITION),
            "cost_currency": currency,
            "operational_cost_eligible": lambda flag: (
                flag if flag is False else _invalid_operational()
            ),
            "cost_children": lambda rows: _rows(
                rows,
                lambda row: shape(
                    row,
                    {
                        "psi_child_result_id": identifier,
                        "item_id": item_identifier,
                        "decision_id": identifier,
                        "decision_date": day,
                        "accepted_order_qty": positive,
                        "artifact_reference": reference,
                        "content_hash": hash_value,
                        "shipment_content_hash": hash_value,
                        "calculation_status": choice("CALCULABLE", "NOT_CALCULABLE"),
                        "binding": landed_cost_run_binding,
                    },
                ),
            ),
            "simulation_metrics": lambda rows: _rows(rows, _metric, 98),
            "simulation_comparisons": lambda rows: _rows(rows, _comparison, 97),
            "content_hash": hash_value,
        },
    )
    require(
        result["attempt_no"] > 0 and bool(result["simulation_metrics"]), "RUN_RESULT_MANIFEST_EMPTY"
    )
    require(
        result["content_hash"]
        == digest({key: row for key, row in result.items() if key != "content_hash"}),
        "RUN_RESULT_MANIFEST_HASH_MISMATCH",
    )
    require(
        len({row["psi_child_result_id"] for row in result["simulation_metrics"]})
        == len(result["simulation_metrics"]),
        "RUN_COST_METRIC_DUPLICATE",
    )
    require(
        len({row["artifact_reference"] for row in result["cost_children"]})
        == len(result["cost_children"]),
        "RUN_COST_CHILD_DUPLICATE",
    )
    return result


def _invalid_operational():
    require(False, "RUN_COST_DEVELOPMENT_ONLY")


def validate_run_result_manifest(
    request,
    manifest: Mapping[str, Any],
    *,
    canonical_input_hash: str,
    psi_snapshot_id: str,
    psi_content_hash: str,
) -> dict:
    value = validate_manifest_document(manifest)
    require(
        value["engine_run_id"] == request.engine_run_id
        and value["attempt_no"] == request.value["attempt_no"]
        and value["tenant_id"] == request.tenant_id
        and value["project_id"] == request.project_id
        and value["runtime_request_content_hash"] == request.canonical_hash,
        "RUN_RESULT_MANIFEST_CLAIM_MISMATCH",
    )
    root = run_manifest_reference(request)
    require(
        value["artifact_reference"] == root
        and value["input_reference"] == root + ":input"
        and value["projection_reference"] == root + ":projection"
        and value["profile_reference"] == root + ":profile"
        and value["canonical_input_hash"] == hash_value(canonical_input_hash)
        and value["site_binding_hash"] == request.value["claim"]["site_binding_hash"]
        and value["strategy_execution_plan_content_hash"]
        == request.strategy_execution_plan["content_hash"],
        "RUN_RESULT_MANIFEST_BINDING_MISMATCH",
    )
    require(
        value["psi_result"]
        == {
            "snapshot_id": psi_snapshot_id,
            "content_hash": psi_content_hash,
            "artifact_reference": f"artifact:inventory:{request.engine_run_id}:{request.value['attempt_no']}:bundle",
        },
        "RUN_RESULT_MANIFEST_PSI_MISMATCH",
    )
    require(
        value["input_content_hash"]
        == request.strategy_execution_plan["landed_cost_binding"]["input_content_hash"],
        "RUN_RESULT_MANIFEST_INPUT_MISMATCH",
    )
    return value

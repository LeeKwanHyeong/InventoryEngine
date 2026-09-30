"""Claim-pinned development costing of guarded PSI orders and parent evidence."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Any, Mapping, Protocol

from dsio_inventory_engine.calculate_landed_cost.artifacts import (
    CHILD_KEY,
    PROJECTION_KEY,
    SHIPMENT_KEY,
    build_landed_cost_child,
)
from dsio_inventory_engine.inventory_contracts.landed_cost_artifact import (
    LandedCostShipmentInput,
    seal_shipment_input,
)
from dsio_inventory_engine.inventory_contracts.run_cost import (
    INPUT_KEY,
    MANIFEST_ID,
    MANIFEST_KEY,
    MODE,
    RECOGNITION,
    VERSION,
    validate_run_cost_claim,
)
from dsio_inventory_engine.inventory_contracts.runtime import (
    InventoryRuntimeExecutionRequest,
    validate_canonical_runtime_binding,
    validate_result_bundle_runtime_binding,
)
from dsio_inventory_engine.inventory_contracts.trade_cost_projection import (
    TradeCostRevisionSetProjection,
    validate_trade_cost_projection_runtime_binding,
)
from dsio_inventory_engine.inventory_contracts.values import digest, quantity_text, require
from dsio_inventory_engine.inventory_contracts.run_result_manifest import run_manifest_reference
from dsio_inventory_engine.inventory_evidence.contracts import ArtifactObject

from .psi_orchestrator import PsiRunBundle, ResolvedCostProfile
from .result_artifacts import calculate_psi_operating_cost

_MAX_EVIDENCE_BYTES = 64 * 1024 * 1024


class RunCostInputReader(Protocol):
    async def read(
        self, *, snapshot_id: str, content_hash: str, tenant_id: str, project_id: str
    ) -> Mapping[str, Any]: ...


class RunCostProjectionResolver(Protocol):
    async def resolve(
        self, request: InventoryRuntimeExecutionRequest
    ) -> TradeCostRevisionSetProjection: ...


@dataclass(frozen=True, slots=True)
class ResolvedRunLandedCost:
    input_document: Mapping[str, Any]
    projection: TradeCostRevisionSetProjection


class RunLandedCostResolver(Protocol):
    async def resolve(self, request: InventoryRuntimeExecutionRequest) -> ResolvedRunLandedCost: ...


class SealedRunLandedCostResolver:
    """Read exact identities only; never discover the latest cost snapshot."""

    def __init__(self, *, inputs: RunCostInputReader, projection: RunCostProjectionResolver):
        self.inputs = inputs
        self.projection = projection

    async def resolve(self, request: InventoryRuntimeExecutionRequest) -> ResolvedRunLandedCost:
        plan = request.strategy_execution_plan
        require(
            plan is not None and plan.get("landed_cost_binding") is not None,
            "RUN_COST_CLAIM_BINDING_REQUIRED",
        )
        binding = plan["landed_cost_binding"]
        document = await self.inputs.read(
            snapshot_id=binding["input_snapshot_id"],
            content_hash=binding["input_content_hash"],
            tenant_id=request.tenant_id,
            project_id=request.project_id,
        )
        inputs = validate_run_cost_claim(request, document)
        projection = await self.projection.resolve(request)
        validate_trade_cost_projection_runtime_binding(
            request, projection, environment_scope="DEVELOPMENT"
        )
        return ResolvedRunLandedCost(inputs, projection)


@dataclass(frozen=True, slots=True)
class RunCostEvidence:
    manifest: Mapping[str, Any]
    artifacts: tuple[ArtifactObject, ...]


def _sealed(body: dict) -> dict:
    return {**body, "content_hash": digest(body)}


def _shipment(request, run, inputs, template, child_id, action) -> LandedCostShipmentInput:
    body = copy.deepcopy(template)
    body.pop("content_hash")
    qty = Decimal(action["accepted_order_qty"])
    body["binding"].update(
        engine_run_id=request.engine_run_id,
        attempt_no=request.value["attempt_no"],
        canonical_input_hash=run.canonical_input.input_hash,
    )
    identity = {
        "child_result_id": child_id,
        "item_id": action["item_id"],
        "decision_date": action["decision_date"],
        "decision_id": action["decision_id"],
    }
    body["shipment_id"] = "DEV-ORDER-" + digest(identity)[:40]
    line = body["lines"][0]
    line["shipment_line_id"] = "LINE-" + digest(identity)[:40]
    line["quantity"] = quantity_text(qty)
    line["transaction_value"] = quantity_text(Decimal(line["transaction_value"]) * qty)
    for charge in line["charges"]:
        charge["amount"] = quantity_text(Decimal(charge["amount"]) * qty)
    return LandedCostShipmentInput.from_dict(seal_shipment_input(body))


def _cost_objects(projection, shipment, child) -> list[ArtifactObject]:
    wrapped = _sealed(
        {
            "contract_id": "io-landed-cost-projection-source-v1",
            "contract_version": VERSION,
            "projection": projection.to_dict(),
        }
    )
    return [
        ArtifactObject.from_document(
            reference=child["shipment_reference"],
            contract_key=SHIPMENT_KEY,
            document=shipment.to_dict(),
        ),
        ArtifactObject.from_document(
            reference=child["projection_reference"], contract_key=PROJECTION_KEY, document=wrapped
        ),
        ArtifactObject.from_document(
            reference=child["artifact_reference"], contract_key=CHILD_KEY, document=child
        ),
    ]


def _builds(run: PsiRunBundle):
    for child in run.result_bundle["result_children"]:
        actions = None
        build = None
        if child["status"] == "SUCCEEDED":
            artifact = run.artifacts[child["artifact_reference"]]
            reference = child["action_evidence"]["constrained_action_reference"]
            if reference is not None:
                actions = run.artifacts[reference]
            build = {"artifact": artifact, "action_artifacts": {}}
            if actions is not None:
                build["action_artifacts"]["constrained_action"] = {"artifact": actions}
        yield child, build, actions


def build_run_cost_evidence(
    *,
    request: InventoryRuntimeExecutionRequest,
    run: PsiRunBundle,
    resolved: ResolvedRunLandedCost,
    cost_profile: ResolvedCostProfile,
) -> RunCostEvidence:
    """Use accepted actions only; costs never modify PSI or strategy decisions."""
    validate_canonical_runtime_binding(request, canonical_input=run.canonical_input)
    validate_result_bundle_runtime_binding(
        request, run.result_bundle, expected_canonical_input_hash=run.canonical_input.input_hash
    )
    inputs = validate_run_cost_claim(request, resolved.input_document)
    projection = TradeCostRevisionSetProjection.from_dict(resolved.projection.to_dict())
    validate_trade_cost_projection_runtime_binding(
        request, projection, environment_scope="DEVELOPMENT"
    )
    require(
        cost_profile.profile_content_hash == inputs["cost_profile_content_hash"],
        "RUN_COST_PROFILE_BINDING_MISMATCH",
    )
    require(
        all(
            getattr(cost_profile, key) == value
            for key, value in request.strategy_execution_plan["cost_profile_binding"].items()
        ),
        "RUN_COST_PROFILE_BINDING_MISMATCH",
    )
    require(cost_profile.source_type == "DEVELOPMENT_SYNTHETIC", "RUN_COST_DEVELOPMENT_ONLY")
    require(
        cost_profile.payload["currency"] == inputs["cost_currency"], "RUN_COST_CURRENCY_MISMATCH"
    )
    root = run_manifest_reference(request)
    wrapped = _sealed(
        {
            "contract_id": "io-landed-cost-projection-source-v1",
            "contract_version": VERSION,
            "projection": projection.to_dict(),
        }
    )
    profile = _sealed(
        {
            "contract_id": "inventory-run-cost-profile-evidence-v1",
            "contract_version": VERSION,
            "binding": request.strategy_execution_plan["cost_profile_binding"],
            "payload": cost_profile.payload,
        }
    )
    objects = [
        ArtifactObject.from_document(
            reference=root + ":input", contract_key=INPUT_KEY, document=inputs
        ),
        ArtifactObject.from_document(
            reference=root + ":projection", contract_key=PROJECTION_KEY, document=wrapped
        ),
        ArtifactObject.from_document(
            reference=root + ":profile",
            contract_key="inventory.run_cost_profile_evidence",
            document=profile,
        ),
    ]
    entries, metrics = [], []
    templates = {row["lines"][0]["item_id"]: row for row in inputs["shipment_templates"]}
    builds = list(_builds(run))
    order_count = sum(
        1
        for _, _, actions in builds
        if actions is not None
        for row in actions["rows"]
        if Decimal(row["accepted_order_qty"]) > 0
    )
    require(order_count <= 10_000, "RUN_COST_ORDER_LIMIT")
    require(
        sum(len(row.document_bytes) for row in objects)
        + order_count * len(objects[1].document_bytes)
        <= _MAX_EVIDENCE_BYTES,
        "RUN_COST_EVIDENCE_SIZE_LIMIT",
    )
    with localcontext() as numbers:
        numbers.prec = 48
        for child, build, actions in builds:
            metric = _evaluate_child(
                request,
                run,
                inputs,
                projection,
                templates,
                cost_profile,
                child,
                build,
                actions,
                objects,
                entries,
            )
            metrics.append(metric)
            require(
                sum(len(row.document_bytes) for row in objects) <= _MAX_EVIDENCE_BYTES,
                "RUN_COST_EVIDENCE_SIZE_LIMIT",
            )
    baseline_id = next(
        row["child_result_id"]
        for row in run.result_bundle["result_children"]
        if row["result_kind"] == "BASELINE_PSI"
    )
    baseline = next(row for row in metrics if row["psi_child_result_id"] == baseline_id)
    comparisons = _comparisons(baseline_id, baseline, metrics)
    bundle = run.result_bundle
    manifest = _sealed(
        {
            "contract_id": MANIFEST_ID,
            "contract_version": VERSION,
            "source_contract_key": MANIFEST_KEY,
            "artifact_reference": root,
            "engine_run_id": request.engine_run_id,
            "attempt_no": request.value["attempt_no"],
            "tenant_id": request.tenant_id,
            "project_id": request.project_id,
            "runtime_request_content_hash": request.canonical_hash,
            "canonical_input_hash": run.canonical_input.input_hash,
            "site_binding_hash": request.value["claim"]["site_binding_hash"],
            "strategy_execution_plan_content_hash": request.strategy_execution_plan["content_hash"],
            "psi_result": {
                "snapshot_id": bundle["result_bundle_id"],
                "content_hash": bundle["content_hash"],
                "artifact_reference": f"artifact:inventory:{request.engine_run_id}:{request.value['attempt_no']}:bundle",
            },
            "input_reference": root + ":input",
            "input_content_hash": inputs["content_hash"],
            "projection_reference": root + ":projection",
            "projection_content_hash": projection.to_dict()["projection_content_hash"],
            "profile_reference": root + ":profile",
            "profile_content_hash": profile["content_hash"],
            "cost_application_mode": MODE,
            "recognition_rule": RECOGNITION,
            "cost_currency": inputs["cost_currency"],
            "operational_cost_eligible": False,
            "cost_children": entries,
            "simulation_metrics": metrics,
            "simulation_comparisons": comparisons,
        }
    )
    objects.append(
        ArtifactObject.from_document(reference=root, contract_key=MANIFEST_KEY, document=manifest)
    )
    require(
        sum(len(row.document_bytes) for row in objects) <= _MAX_EVIDENCE_BYTES,
        "RUN_COST_EVIDENCE_SIZE_LIMIT",
    )
    require(len({row.reference for row in objects}) == len(objects), "ARTIFACT_REFERENCE_DUPLICATE")
    return RunCostEvidence(manifest, tuple(objects))


def _comparisons(baseline_id, baseline, metrics):
    comparisons = []
    with localcontext() as numbers:
        numbers.prec = 48
        for metric in metrics:
            if metric["psi_child_result_id"] == baseline_id:
                continue
            available = baseline["status"] == metric["status"] == "AVAILABLE"
            comparisons.append(
                {
                    "baseline_psi_child_result_id": baseline_id,
                    "candidate_psi_child_result_id": metric["psi_child_result_id"],
                    "status": "AVAILABLE" if available else "NOT_AVAILABLE",
                    "total_cost_delta": quantity_text(
                        Decimal(metric["total_cost"]) - Decimal(baseline["total_cost"])
                    )
                    if available
                    else None,
                    "reason_code": None
                    if available
                    else (metric["reason_code"] or baseline["reason_code"]),
                }
            )
    return comparisons


def _evaluate_child(
    request, run, inputs, projection, templates, profile, child, build, actions, objects, entries
):
    child_id = child["child_result_id"]
    result = {
        "psi_child_result_id": child_id,
        "status": "NOT_AVAILABLE",
        "operating_cost": None,
        "net_landed_cost": None,
        "total_cost": None,
        "accepted_order_count": 0,
        "accepted_order_qty": "0",
        "unpriced_orders": [],
        "reason_code": None,
    }
    if child["status"] != "SUCCEEDED":
        result["reason_code"] = "CANDIDATE_RESULT_" + child["status"]
        return result
    orders = (
        []
        if actions is None
        else [row for row in actions["rows"] if Decimal(row["accepted_order_qty"]) > 0]
    )
    require(len(orders) <= 10_000, "RUN_COST_ORDER_LIMIT")
    result["accepted_order_count"] = len(orders)
    result["accepted_order_qty"] = quantity_text(
        sum((Decimal(row["accepted_order_qty"]) for row in orders), Decimal(0))
    )
    total = Decimal(0)
    missing = False
    for action in orders:
        template = templates.get(action["item_id"])
        if template is None:
            missing = True
            result["unpriced_orders"].append(
                {
                    "item_id": action["item_id"],
                    "decision_id": action["decision_id"],
                    "decision_date": action["decision_date"],
                    "accepted_order_qty": action["accepted_order_qty"],
                    "reason_code": "PRICING_TEMPLATE_MISSING",
                }
            )
            continue
        shipment = _shipment(request, run, inputs, template, child_id, action)
        require(len(entries) < 10_000, "RUN_COST_ORDER_LIMIT")
        cost = build_landed_cost_child(projection, shipment)
        objects.extend(_cost_objects(projection, shipment, cost))
        entries.append(
            {
                "psi_child_result_id": child_id,
                "item_id": action["item_id"],
                "decision_id": action["decision_id"],
                "decision_date": action["decision_date"],
                "accepted_order_qty": action["accepted_order_qty"],
                "artifact_reference": cost["artifact_reference"],
                "content_hash": cost["content_hash"],
                "shipment_content_hash": shipment.to_dict()["content_hash"],
                "calculation_status": cost["calculation_status"],
                "binding": shipment.to_dict()["binding"],
            }
        )
        if cost["calculation_status"] != "CALCULABLE":
            missing = True
        else:
            total += Decimal(cost["totals"]["net_landed_cost_amount"])
    if missing:
        result["reason_code"] = "LANDED_COST_NOT_CALCULABLE_OR_TEMPLATE_MISSING"
        return result
    operating = calculate_psi_operating_cost(build, profile.payload)
    result.update(
        status="AVAILABLE",
        operating_cost=quantity_text(operating),
        net_landed_cost=quantity_text(total),
        total_cost=quantity_text(operating + total),
    )
    return result

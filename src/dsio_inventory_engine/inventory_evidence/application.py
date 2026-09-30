"""Persist and independently verify one sealed Inventory Result Bundle."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping, TYPE_CHECKING

from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.result_bundle import (
    RESULT_BUNDLE_SOURCE_CONTRACT_KEY,
    validate_inventory_result_bundle,
)
from dsio_inventory_engine.inventory_contracts.values import digest, require
from dsio_inventory_engine.prepare_inventory.application.inventory_input import (
    PreparedInventoryInput,
    validate_prepared_projection,
)
from dsio_inventory_engine.run_inventory.psi_orchestrator import PsiRunBundle
from dsio_inventory_engine.run_inventory.result_artifacts import (
    PSI_RESULT_ARTIFACT_CONTRACT_KEY,
    seal_action_observation_source,
    validate_psi_child_build,
)

from .contracts import (
    ArtifactObject,
    InventoryEvidenceUnitOfWork,
    OutboxMessage,
    OutboxRecord,
)

if TYPE_CHECKING:
    from dsio_inventory_engine.run_inventory.landed_cost_pipeline import RunCostEvidence


CANONICAL_INPUT_EVIDENCE_CONTRACT_KEY = "inventory.run_canonical_input_evidence"
PREPARED_INPUT_EVIDENCE_CONTRACT_KEY = "inventory.run_prepared_input_evidence"
EXECUTION_SOURCE_EVIDENCE_CONTRACT_KEY = "inventory.run_execution_source_evidence"
RESULT_READY_OUTBOX_CONTRACT_KEY = "inventory.result_ready"
_CONTRACT_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class PersistedInventoryResultBundle:
    bundle_reference: str
    bundle_content_hash: str
    outbox: OutboxRecord
    artifact_count: int
    replayed: bool


@dataclass(frozen=True, slots=True)
class VerifiedInventoryResultBundle:
    bundle_reference: str
    result_bundle: Mapping[str, Any]
    canonical_input: CanonicalInputRequest
    prepared_input: PreparedInventoryInput
    outbox: OutboxRecord
    artifact_count: int
    run_cost_evidence: RunCostEvidence | None = None


class PersistInventoryResultBundleUseCase:
    """Atomically store exact artifacts and a reusable result-ready pointer."""

    def __init__(
        self,
        deployment: DeploymentScope,
        repository: InventoryEvidenceUnitOfWork,
    ) -> None:
        self.deployment = deployment
        self.repository = repository

    def execute(
        self,
        run: PsiRunBundle,
        *,
        strategy_execution_plan: Mapping[str, Any],
        runtime_request=None,
        run_cost_evidence: RunCostEvidence | None = None,
    ) -> PersistedInventoryResultBundle:
        require(isinstance(run, PsiRunBundle), "PSI_RUN_BUNDLE_REQUIRED")
        plan = copy.deepcopy(dict(strategy_execution_plan))
        bundle = validate_inventory_result_bundle(
            run.result_bundle,
            strategy_execution_plan=plan,
        )
        canonical = CanonicalInputRequest.from_dict(run.canonical_input.to_dict())
        require(bundle["canonical_input_hash"] == canonical.input_hash, "BUNDLE_INPUT_MISMATCH")
        prepared = validate_prepared_projection(
            canonical,
            PreparedInventoryInput(run.prepared_input.document_json),
            self.deployment,
        )
        refs = _run_references(bundle)

        objects: list[ArtifactObject] = [
            ArtifactObject.from_document(
                reference=refs["bundle"],
                contract_key=RESULT_BUNDLE_SOURCE_CONTRACT_KEY,
                document=bundle,
            ),
            ArtifactObject.from_document(
                reference=refs["canonical"],
                contract_key=CANONICAL_INPUT_EVIDENCE_CONTRACT_KEY,
                document=_seal_source(
                    {
                        "contract_id": "inventory-run-canonical-input-evidence-v1",
                        "contract_version": _CONTRACT_VERSION,
                        "source_contract_key": CANONICAL_INPUT_EVIDENCE_CONTRACT_KEY,
                        "engine_run_id": bundle["engine_run_id"],
                        "attempt_no": bundle["attempt_no"],
                        "canonical_input_hash": canonical.input_hash,
                        "canonical_input": canonical.to_dict(),
                    }
                ),
            ),
            ArtifactObject.from_document(
                reference=refs["prepared"],
                contract_key=PREPARED_INPUT_EVIDENCE_CONTRACT_KEY,
                document=_seal_source(
                    {
                        "contract_id": "inventory-run-prepared-input-evidence-v1",
                        "contract_version": _CONTRACT_VERSION,
                        "source_contract_key": PREPARED_INPUT_EVIDENCE_CONTRACT_KEY,
                        "engine_run_id": bundle["engine_run_id"],
                        "attempt_no": bundle["attempt_no"],
                        "canonical_input_hash": canonical.input_hash,
                        "prepared_content_hash": prepared.to_dict()["manifest"][
                            "prepared_content_hash"
                        ],
                        "prepared_input": prepared.to_dict(),
                    }
                ),
            ),
        ]

        expected_in_memory: set[str] = set()
        execution_sources: list[dict[str, str]] = []
        for child in bundle["result_children"]:
            if child["status"] != "SUCCEEDED":
                continue
            built = _child_build(child, run.artifacts)
            validate_psi_child_build(built)
            for reference, document, contract_key in _child_documents(built):
                require(reference not in expected_in_memory, "ARTIFACT_REFERENCE_DUPLICATE")
                expected_in_memory.add(reference)
                objects.append(
                    ArtifactObject.from_document(
                        reference=reference,
                        contract_key=contract_key,
                        document=document,
                    )
                )
            if child["strategy_type"] != "NONE":
                source = _execution_source_document(run, child, built)
                source_reference = _execution_source_reference(bundle, child["child_result_id"])
                source_object = ArtifactObject.from_document(
                    reference=source_reference,
                    contract_key=EXECUTION_SOURCE_EVIDENCE_CONTRACT_KEY,
                    document=source,
                )
                objects.append(source_object)
                execution_sources.append(
                    {
                        "child_result_id": child["child_result_id"],
                        "artifact_reference": source_reference,
                        "content_hash": source_object.content_hash,
                    }
                )
        require(expected_in_memory == set(run.artifacts), "ORCHESTRATOR_ARTIFACT_SET_MISMATCH")
        cost_bound = plan.get("landed_cost_binding") is not None
        require(cost_bound == (run_cost_evidence is not None), "RUN_COST_EVIDENCE_BINDING_REQUIRED")
        if cost_bound:
            from .run_cost import revalidate_run_cost_evidence

            require(runtime_request is not None, "RUN_COST_RUNTIME_REQUEST_REQUIRED")
            verified_cost = revalidate_run_cost_evidence(
                evidence=run_cost_evidence, request=runtime_request, run=run
            )
            objects.extend(verified_cost.artifacts)
        require(
            len({artifact.reference for artifact in objects}) == len(objects),
            "ARTIFACT_REFERENCE_DUPLICATE",
        )
        outbox = _result_ready_outbox(
            bundle, objects, execution_sources, run_cost_evidence=run_cost_evidence
        )
        committed = self.repository.commit(objects, outbox)
        verified = ReadAndVerifyInventoryResultBundleUseCase(
            self.deployment,
            self.repository,
        ).execute(
            refs["bundle"],
            strategy_execution_plan=plan,
            runtime_request=runtime_request,
        )
        require(
            verified.result_bundle["content_hash"] == bundle["content_hash"]
            and verified.outbox.message.payload_content_hash
            == committed.outbox.message.payload_content_hash,
            "EVIDENCE_READ_BACK_MISMATCH",
        )
        return PersistedInventoryResultBundle(
            bundle_reference=refs["bundle"],
            bundle_content_hash=bundle["content_hash"],
            outbox=committed.outbox,
            artifact_count=committed.artifact_count,
            replayed=committed.replayed,
        )


class ReadAndVerifyInventoryResultBundleUseCase:
    """Read persisted bytes and rebuild all Bundle, Child and action-source bindings."""

    def __init__(
        self,
        deployment: DeploymentScope,
        repository: InventoryEvidenceUnitOfWork,
    ) -> None:
        self.deployment = deployment
        self.repository = repository

    def execute(
        self,
        bundle_reference: str,
        *,
        strategy_execution_plan: Mapping[str, Any],
        runtime_request=None,
    ) -> VerifiedInventoryResultBundle:
        bundle_object = self.repository.read_artifact(bundle_reference)
        require(
            bundle_object.contract_key == RESULT_BUNDLE_SOURCE_CONTRACT_KEY,
            "RESULT_BUNDLE_ARTIFACT_CONTRACT_MISMATCH",
        )
        bundle = validate_inventory_result_bundle(
            bundle_object.document(),
            strategy_execution_plan=strategy_execution_plan,
        )
        refs = _run_references(bundle)
        require(refs["bundle"] == bundle_reference, "RESULT_BUNDLE_REFERENCE_MISMATCH")

        canonical_object = self.repository.read_artifact(refs["canonical"])
        canonical_source = canonical_object.document()
        _validate_source_header(
            canonical_source,
            contract_key=CANONICAL_INPUT_EVIDENCE_CONTRACT_KEY,
            bundle=bundle,
        )
        canonical = CanonicalInputRequest.from_dict(canonical_source["canonical_input"])
        require(
            canonical.input_hash == bundle["canonical_input_hash"]
            and canonical_source["canonical_input_hash"] == canonical.input_hash,
            "CANONICAL_EVIDENCE_BINDING_MISMATCH",
        )

        prepared_object = self.repository.read_artifact(refs["prepared"])
        prepared_source = prepared_object.document()
        _validate_source_header(
            prepared_source,
            contract_key=PREPARED_INPUT_EVIDENCE_CONTRACT_KEY,
            bundle=bundle,
        )
        prepared = validate_prepared_projection(
            canonical,
            PreparedInventoryInput(_canonical_document(prepared_source["prepared_input"])),
            self.deployment,
        )
        require(
            prepared_source["canonical_input_hash"] == canonical.input_hash
            and prepared_source["prepared_content_hash"]
            == prepared.to_dict()["manifest"]["prepared_content_hash"],
            "PREPARED_EVIDENCE_BINDING_MISMATCH",
        )

        artifact_bindings = [bundle_object, canonical_object, prepared_object]
        in_memory_artifacts = {}
        execution_sources: list[dict[str, str]] = []
        for child in bundle["result_children"]:
            if child["status"] != "SUCCEEDED":
                continue
            persisted = _read_child_build(self.repository, child)
            validate_psi_child_build(persisted)
            for reference, document, _ in _child_documents(persisted):
                artifact_bindings.append(self.repository.read_artifact(reference))
                in_memory_artifacts[reference] = document
            if child["strategy_type"] != "NONE":
                source_reference = _execution_source_reference(bundle, child["child_result_id"])
                source_object = self.repository.read_artifact(source_reference)
                source = source_object.document()
                _validate_execution_source(
                    source,
                    bundle=bundle,
                    child=child,
                    built=persisted,
                    base_prepared=prepared.to_dict(),
                )
                artifact_bindings.append(source_object)
                execution_sources.append(
                    {
                        "child_result_id": child["child_result_id"],
                        "artifact_reference": source_reference,
                        "content_hash": source_object.content_hash,
                    }
                )

        cost_evidence = None
        if strategy_execution_plan.get("landed_cost_binding") is not None:
            from .run_cost import verify_run_cost_evidence

            require(runtime_request is not None, "RUN_COST_RUNTIME_REQUEST_REQUIRED")
            recovered_run = PsiRunBundle(
                canonical_input=canonical,
                prepared_input=prepared,
                result_bundle=bundle,
                artifacts=in_memory_artifacts,
                child_metrics={},
                detailed_comparisons=(),
                execution_results={},
            )
            cost_evidence = verify_run_cost_evidence(
                request=runtime_request, run=recovered_run, repository=self.repository
            )
            artifact_bindings.extend(cost_evidence.artifacts)
        outbox_id = _outbox_id(bundle)
        outbox = self.repository.read_outbox(outbox_id)
        expected_message = _result_ready_outbox(
            bundle, artifact_bindings, execution_sources, run_cost_evidence=cost_evidence
        )
        require(
            outbox.message == expected_message,
            "RESULT_READY_OUTBOX_BINDING_MISMATCH",
        )
        return VerifiedInventoryResultBundle(
            bundle_reference=bundle_reference,
            result_bundle=bundle,
            canonical_input=canonical,
            prepared_input=prepared,
            outbox=outbox,
            artifact_count=len(artifact_bindings),
            run_cost_evidence=cost_evidence,
        )


def _child_build(
    child: Mapping[str, Any],
    artifacts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    reference = child["artifact_reference"]
    require(reference in artifacts, "PSI_CHILD_ARTIFACT_MISSING")
    action_artifacts: dict[str, dict[str, Any]] = {}
    for prefix in ("raw_action", "constrained_action", "adjustment_reasons"):
        action_reference = child["action_evidence"][f"{prefix}_reference"]
        if action_reference is not None:
            require(action_reference in artifacts, "ACTION_EVIDENCE_ARTIFACT_MISSING")
            action_artifacts[prefix] = {
                "reference": action_reference,
                "artifact": copy.deepcopy(artifacts[action_reference]),
            }
    artifact = copy.deepcopy(artifacts[reference])
    return {
        "child_result": copy.deepcopy(dict(child)),
        "artifact_reference": reference,
        "artifact": artifact,
        "metrics": copy.deepcopy(artifact["metrics"]),
        "action_artifacts": action_artifacts,
    }


def _read_child_build(
    repository: InventoryEvidenceUnitOfWork,
    child: Mapping[str, Any],
) -> dict[str, Any]:
    psi = repository.read_artifact(child["artifact_reference"])
    require(psi.contract_key == PSI_RESULT_ARTIFACT_CONTRACT_KEY, "PSI_ARTIFACT_CONTRACT_MISMATCH")
    artifacts = {child["artifact_reference"]: psi.document()}
    for prefix in ("raw_action", "constrained_action", "adjustment_reasons"):
        reference = child["action_evidence"][f"{prefix}_reference"]
        if reference is not None:
            artifacts[reference] = repository.read_artifact(reference).document()
    return _child_build(child, artifacts)


def _child_documents(
    built: Mapping[str, Any],
) -> tuple[tuple[str, Mapping[str, Any], str], ...]:
    documents = [
        (
            built["artifact_reference"],
            built["artifact"],
            PSI_RESULT_ARTIFACT_CONTRACT_KEY,
        )
    ]
    documents.extend(
        (
            value["reference"],
            value["artifact"],
            "inventory.action_evidence",
        )
        for value in built["action_artifacts"].values()
    )
    return tuple(documents)


def _execution_source_document(
    run: PsiRunBundle,
    child: Mapping[str, Any],
    built: Mapping[str, Any],
) -> dict[str, Any]:
    result = run.execution_results.get(child["child_result_id"])
    require(isinstance(result, Mapping), "EXECUTION_SOURCE_RESULT_MISSING")
    prepared = result.get("prepared_input")
    execution = result.get("execution")
    admissions = result.get("effective_policy_admission_evidence")
    require(
        type(prepared) is dict and type(execution) is dict and type(admissions) is list,
        "EXECUTION_SOURCE_INVALID",
    )
    admissions_by_item = {row["item_id"]: copy.deepcopy(row) for row in admissions}
    require(len(admissions_by_item) == len(admissions), "EXECUTION_SOURCE_ADMISSION_DUPLICATE")
    raw_rows = built["action_artifacts"]["raw_action"]["artifact"]["rows"]
    require(bool(raw_rows), "EXECUTION_SOURCE_OBSERVATION_MISSING")
    observation_input_hash = raw_rows[0]["observation"]["input_content_hash"]
    return _seal_source(
        {
            "contract_id": "inventory-run-execution-source-evidence-v1",
            "contract_version": _CONTRACT_VERSION,
            "source_contract_key": EXECUTION_SOURCE_EVIDENCE_CONTRACT_KEY,
            "engine_run_id": run.result_bundle["engine_run_id"],
            "attempt_no": run.result_bundle["attempt_no"],
            "child_result_id": child["child_result_id"],
            "canonical_input_hash": run.result_bundle["canonical_input_hash"],
            "observation_input_hash": observation_input_hash,
            "prepared_input": copy.deepcopy(prepared),
            "execution": copy.deepcopy(execution),
            "admissions_by_item": dict(sorted(admissions_by_item.items())),
        }
    )


def _validate_execution_source(
    source: Mapping[str, Any],
    *,
    bundle: Mapping[str, Any],
    child: Mapping[str, Any],
    built: Mapping[str, Any],
    base_prepared: Mapping[str, Any],
) -> None:
    _validate_source_header(
        source,
        contract_key=EXECUTION_SOURCE_EVIDENCE_CONTRACT_KEY,
        bundle=bundle,
    )
    require(
        source.get("child_result_id") == child["child_result_id"]
        and source.get("canonical_input_hash") == bundle["canonical_input_hash"]
        and type(source.get("prepared_input")) is dict
        and type(source.get("execution")) is dict
        and type(source.get("admissions_by_item")) is dict,
        "EXECUTION_SOURCE_BINDING_MISMATCH",
    )
    if child["scenario_id"] == "BASE":
        require(
            all(
                source["prepared_input"].get(key) == value
                for key, value in base_prepared.items()
                if key != "policies"
            ),
            "BASE_EXECUTION_SOURCE_MISMATCH",
        )
    reconstructed = seal_action_observation_source(
        prepared_input=source["prepared_input"],
        execution=source["execution"],
        observation_input_hash=source["observation_input_hash"],
        admissions_by_item=source["admissions_by_item"] or None,
    )
    persisted = built["action_artifacts"]["raw_action"]["artifact"]["observation_source"]
    require(reconstructed == persisted, "ACTION_OBSERVATION_SOURCE_READ_BACK_MISMATCH")


def _validate_source_header(
    source: Mapping[str, Any],
    *,
    contract_key: str,
    bundle: Mapping[str, Any],
) -> None:
    require(
        source.get("contract_version") == _CONTRACT_VERSION
        and source.get("source_contract_key") == contract_key
        and source.get("engine_run_id") == bundle["engine_run_id"]
        and source.get("attempt_no") == bundle["attempt_no"],
        "RUN_SOURCE_EVIDENCE_BINDING_MISMATCH",
    )


def _seal_source(body: Mapping[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(dict(body))
    return {**normalized, "content_hash": digest(normalized)}


def _canonical_document(value: Mapping[str, Any]) -> str:
    from dsio_inventory_engine.inventory_contracts.values import canonical_json

    return canonical_json(dict(value))


def _run_references(bundle: Mapping[str, Any]) -> dict[str, str]:
    base = f"artifact:inventory:{bundle['engine_run_id']}:{bundle['attempt_no']}"
    return {
        "bundle": f"{base}:bundle",
        "canonical": f"{base}:canonical-input",
        "prepared": f"{base}:prepared-input",
    }


def _execution_source_reference(bundle: Mapping[str, Any], child_result_id: str) -> str:
    return (
        f"artifact:inventory:{bundle['engine_run_id']}:{bundle['attempt_no']}:"
        f"{child_result_id}:execution-source"
    )


def _outbox_id(bundle: Mapping[str, Any]) -> str:
    return f"IOO-{digest({'engine_run_id': bundle['engine_run_id'], 'attempt_no': bundle['attempt_no']})}"


def _result_ready_outbox(
    bundle: Mapping[str, Any],
    artifacts: list[ArtifactObject],
    execution_sources: list[dict[str, str]],
    *,
    run_cost_evidence: RunCostEvidence | None = None,
) -> OutboxMessage:
    refs = _run_references(bundle)
    bindings = [
        {
            "artifact_reference": artifact.reference,
            "contract_key": artifact.contract_key,
            "content_hash": artifact.content_hash,
            "byte_content_hash": artifact.byte_content_hash,
        }
        for artifact in sorted(artifacts, key=lambda item: item.reference)
    ]
    body = {
        "contract_id": "inventory-result-ready-outbox-v1",
        "contract_version": _CONTRACT_VERSION,
        "source_contract_key": RESULT_READY_OUTBOX_CONTRACT_KEY,
        "outbox_id": _outbox_id(bundle),
        "event_type": "inventory.result.ready",
        "engine_run_id": bundle["engine_run_id"],
        "attempt_no": bundle["attempt_no"],
        "result_bundle_id": bundle["result_bundle_id"],
        "bundle_reference": refs["bundle"],
        "bundle_content_hash": bundle["content_hash"],
        "canonical_input_reference": refs["canonical"],
        "prepared_input_reference": refs["prepared"],
        "effective_child_result_id": bundle["effective_child_result_id"],
        "automatic_publish_allowed": bundle["automatic_publish_allowed"],
        "automatic_order_allowed": bundle["automatic_order_allowed"],
        "artifact_bindings": bindings,
        "execution_source_bindings": sorted(
            execution_sources,
            key=lambda item: item["child_result_id"],
        ),
    }
    if run_cost_evidence is not None:
        body["run_result_manifest_reference"] = run_cost_evidence.manifest["artifact_reference"]
        body["run_result_manifest_content_hash"] = run_cost_evidence.manifest["content_hash"]
    return OutboxMessage.from_body(body)


__all__ = [
    "CANONICAL_INPUT_EVIDENCE_CONTRACT_KEY",
    "EXECUTION_SOURCE_EVIDENCE_CONTRACT_KEY",
    "PREPARED_INPUT_EVIDENCE_CONTRACT_KEY",
    "PersistInventoryResultBundleUseCase",
    "PersistedInventoryResultBundle",
    "ReadAndVerifyInventoryResultBundleUseCase",
    "VerifiedInventoryResultBundle",
]

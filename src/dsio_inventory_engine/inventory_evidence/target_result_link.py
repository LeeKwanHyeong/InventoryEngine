"""Explicit local companion link; the operational Bundle/Outbox remain byte-identical.

Read and recovery require the original sealed inputs and pinned numerical port.
This is not a registered Runtime strategy, a publication approval or an ERP order.
"""

from dataclasses import dataclass
import json

from dsio_inventory_engine.classify_inventory.application import config_hash, validate_config
from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.mathematical import policy_input_binding
from dsio_inventory_engine.inventory_contracts.runtime import (
    InventoryRuntimeExecutionRequest,
    validate_canonical_runtime_binding,
    validate_effective_policy_runtime_binding,
    validate_result_bundle_runtime_binding,
)
from dsio_inventory_engine.inventory_contracts.values import canonical_json, digest, require
from .application import ReadAndVerifyInventoryResultBundleUseCase
from .classification_source import validate_classification_source
from .contracts import ArtifactObject
from .projection import binding_identity

KEY = "inventory.target_result_link"


def _pointer(obj):
    return {
        "artifact_reference": obj.reference,
        "contract_key": obj.contract_key,
        "content_hash": obj.content_hash,
        "byte_content_hash": obj.byte_content_hash,
    }


@dataclass(frozen=True)
class VerifiedTargetResultLink:
    artifact: ArtifactObject
    source_artifacts: tuple[ArtifactObject, ...]
    result_json: str

    @property
    def result(self):
        return json.loads(self.result_json)


class TargetResultLinkUseCase:
    """Storage-neutral, development-only link to an already persisted Result Bundle."""

    def __init__(self, *, deployment, target_storage, repository):
        self.deployment, self.target, self.repository = deployment, target_storage, repository
        self.bundle_reader = ReadAndVerifyInventoryResultBundleUseCase(deployment, repository)

    @staticmethod
    def reference(request):
        return f"artifact:inventory:{request.engine_run_id}:{request.value['attempt_no']}:target-policy-result"

    def _base_inputs(self, *, request, bundle_reference, input_reference):
        request = InventoryRuntimeExecutionRequest.from_dict(request.to_dict())
        require(
            self.deployment.environment == "DEVELOPMENT"
            and self.target.deployment == self.deployment,
            "TARGET_LINK_DEVELOPMENT_ONLY",
        )
        base = self.bundle_reader.execute(
            bundle_reference,
            strategy_execution_plan=request.strategy_execution_plan,
            runtime_request=request,
        )
        validate_canonical_runtime_binding(request, canonical_input=base.canonical_input)
        validate_result_bundle_runtime_binding(
            request,
            base.result_bundle,
            expected_canonical_input_hash=base.canonical_input.input_hash,
        )
        require(
            input_reference is not None and input_reference["kind"] == "INPUT",
            "TARGET_LINK_INPUT_REQUIRED",
        )
        inputs = self.target.repository.read(input_reference)
        canonical = CanonicalInputRequest.from_dict(
            inputs["request"]["recommendation"]["canonical_input"]
        )
        require(
            canonical_json(canonical.to_dict()) == canonical_json(base.canonical_input.to_dict()),
            "TARGET_LINK_CANONICAL_OR_PREPARED_MISMATCH",
        )
        execution = inputs["request"]["recommendation"]["execution"]
        validate_effective_policy_runtime_binding(request, execution["effective_policy_binding"])
        snapshot = validate_classification_source(
            request=request, snapshot=inputs["classification_snapshot"], canonical=canonical
        )
        require(
            snapshot["as_of_yyyyww"] == canonical.to_dict()["context"]["plan_yyyyww"]
            and config_hash(validate_config(inputs["config_values"])) == snapshot["config_hash"],
            "TARGET_LINK_CONFIG_OR_WEEK_MISMATCH",
        )
        require(
            request.strategy_execution_plan["operational_strategy"]["strategy_input_binding"]
            == policy_input_binding(inputs["request"]["policy_input"]),
            "TARGET_LINK_MATHEMATICAL_INPUT_MISMATCH",
        )
        if base.classification_source_artifact is not None:
            require(
                base.classification_source_artifact.document()["classification_snapshot"]
                == snapshot,
                "TARGET_LINK_CLASSIFICATION_HEADER_MISMATCH",
            )
        elif base.policy_analysis_evidence is not None:
            actual = next(
                a.document()["classification_snapshot"]
                for a in base.policy_analysis_evidence.artifacts
                if a.document().get("contract_id") == "inventory-policy-analysis-inputs"
            )
            require(actual == snapshot, "TARGET_LINK_CLASSIFICATION_HEADER_MISMATCH")
        else:
            require(False, "TARGET_LINK_CLASSIFICATION_SOURCE_REQUIRED")
        return request, base, inputs

    def _assemble(self, *, request, bundle_reference, input_reference, manifest_reference):
        request, base, inputs = self._base_inputs(
            request=request, bundle_reference=bundle_reference, input_reference=input_reference
        )
        require(
            manifest_reference is not None
            and manifest_reference["kind"] == "MANIFEST"
            and self.target.repository.lookup(request.engine_run_id, "COMPLETE")
            == manifest_reference,
            "TARGET_LINK_MANIFEST_MISMATCH",
        )
        view = self.target.read_verified(
            request.engine_run_id, expected_input_reference=input_reference
        )
        require(
            view.get("verification_status") == "RECOMPUTED_VERIFIED",
            "TARGET_LINK_FULL_REPLAY_REQUIRED",
        )
        result = view["result"]
        require(
            result["order_plan"]["prepared_input"] == base.prepared_input.to_dict(),
            "TARGET_LINK_CANONICAL_OR_PREPARED_MISMATCH",
        )
        canonical = base.canonical_input
        selection = result["selection_result"]
        search_ref = selection.get(
            "source_result_reference", selection.get("search_result_reference")
        )
        search = ArtifactObject.from_document(
            reference=search_ref,
            contract_key="inventory.policy_search_result"
            if inputs["catalog"] is None
            else "inventory.offline_catalog_psi_result",
            document=result["search_result"],
        )
        selected = ArtifactObject.from_document(
            reference=result["order_plan"]["order_plan_binding"]["selection_result_reference"],
            contract_key="inventory.policy_selection_result"
            if inputs["catalog"] is None
            else "inventory.offline_catalog_policy_selection_result",
            document=selection,
        )
        body = {
            "contract_id": "inventory-target-result-link-v1",
            "contract_version": "1.0.0",
            "source_contract_key": KEY,
            "binding": binding_identity(request),
            "runtime_request_content_hash": request.canonical_hash,
            "bundle_reference": bundle_reference,
            "bundle_content_hash": base.result_bundle["content_hash"],
            "canonical_input_hash": canonical.input_hash,
            "target_input_reference": input_reference,
            "target_manifest_reference": manifest_reference,
            "calculation_binding": result["calculation_binding"],
            "source_artifacts": [_pointer(search), _pointer(selected)],
            "order_plan_binding": result["order_plan"]["order_plan_binding"],
            "counts": view["counts"],
            "automatic_publish_allowed": False,
            "automatic_order_allowed": False,
            "operational_policy_approved": False,
        }
        obj = ArtifactObject.from_document(
            reference=self.reference(request),
            contract_key=KEY,
            document={**body, "content_hash": digest(body)},
        )
        return VerifiedTargetResultLink(obj, (search, selected), canonical_json(result))

    def execute(self, *, request, bundle_reference, expected_input_reference):
        manifest_ref = self.target.repository.lookup(request.engine_run_id, "COMPLETE")
        linked = self._assemble(
            request=request,
            bundle_reference=bundle_reference,
            input_reference=expected_input_reference,
            manifest_reference=manifest_ref,
        )
        replayed = self.repository.commit_artifacts((*linked.source_artifacts, linked.artifact))
        self.read(
            request=request,
            bundle_reference=bundle_reference,
            expected_link_hash=linked.artifact.content_hash,
        )
        return {
            **_pointer(linked.artifact),
            "replayed": replayed,
            "verification_status": "RECOMPUTED_VERIFIED",
            "automatic_publish_allowed": False,
            "automatic_order_allowed": False,
        }

    def read(self, *, request, bundle_reference, expected_link_hash=None):
        obj = self.repository.read_artifact(self.reference(request))
        require(
            obj.contract_key == KEY
            and (expected_link_hash is None or obj.content_hash == expected_link_hash),
            "TARGET_LINK_REFERENCE_MISMATCH",
        )
        doc = obj.document()
        expected = self._assemble(
            request=request,
            bundle_reference=bundle_reference,
            input_reference=doc["target_input_reference"],
            manifest_reference=doc["target_manifest_reference"],
        )
        require(obj == expected.artifact, "TARGET_LINK_REPLAY_MISMATCH")
        for source in expected.source_artifacts:
            require(
                self.repository.read_artifact(source.reference) == source,
                "TARGET_LINK_SOURCE_ARTIFACT_MISMATCH",
            )
        return expected

    def recover(self, *, request, bundle_reference, expected_input_reference):
        # Restore only the pinned original input; never search for a new input or update the outbox.
        self._base_inputs(
            request=request,
            bundle_reference=bundle_reference,
            input_reference=expected_input_reference,
        )
        self.target.recover(
            request.engine_run_id, expected_input_reference=expected_input_reference
        )
        return self.execute(
            request=request,
            bundle_reference=bundle_reference,
            expected_input_reference=expected_input_reference,
        )

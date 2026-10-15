"""Explicit installed-Build → approved Source → PSI → Projection Artifact assembly.

No PostgreSQL writer, lifecycle publication, source approval, or default activation.
The returned execution Artifact fixes exact pointers for read/verify/recovery.
"""

import asyncio
import copy

from dsio_inventory_engine.infrastructure.runtime_build import registered_installed_implementations
from dsio_inventory_engine.inventory_contracts.runtime import InventoryRuntimeExecutionRequest
from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.runtime_inputs import (
    instantiate_mathematical_template,
)
from dsio_inventory_engine.inventory_contracts.values import digest, require, InventoryInputError
from dsio_inventory_engine.inventory_evidence.application import (
    ReadAndVerifyInventoryResultBundleUseCase,
)
from dsio_inventory_engine.inventory_evidence.classification_source import (
    classification_pointer,
    validate_classification_source,
)
from dsio_inventory_engine.inventory_evidence.contracts import ArtifactObject
from dsio_inventory_engine.inventory_evidence.projection import (
    PersistDsimProjectionUseCase,
    VerifyDsimProjectionUseCase,
    binding_identity,
)
from dsio_inventory_engine.inventory_evidence.projection_source import DsimSourceProjector
from .runtime_composition import build_approved_inventory_runtime_pipeline
from .approved_inputs import validate_manifest_request

KEY = "inventory.dsim_source_execution"


class InventorySourceProjectionExecution:
    def __init__(
        self,
        *,
        deployment,
        input_provider,
        evidence_repository,
        catalog,
        expected_build,
        cost_projection=None,
        simulation_registry=None,
    ):
        self.expected_build = copy.deepcopy(expected_build)
        self.implementations = registered_installed_implementations(self.expected_build)
        self.store = evidence_repository

        class ClassifiedSource:
            async def resolve(self, request):
                manifest = validate_manifest_request(request, await input_provider.resolve(request))
                rows = {
                    r["kind"]: r
                    for r in manifest["resources"]
                    if r["kind"] in {"MATHEMATICAL_TEMPLATE", "CLASSIFICATION"}
                }
                raw = instantiate_mathematical_template(
                    rows["MATHEMATICAL_TEMPLATE"]["document"], request.engine_run_id
                )
                canonical = CanonicalInputRequest.from_dict(
                    raw["recommendation"]["canonical_input"]
                )
                snapshot = validate_classification_source(
                    request=request,
                    snapshot=rows["CLASSIFICATION"]["document"],
                    canonical=canonical,
                )
                require(
                    snapshot["as_of_yyyyww"] == canonical.to_dict()["context"]["plan_yyyyww"],
                    "SOURCE_EXECUTION_CLASSIFICATION_WEEK_MISMATCH",
                )
                return manifest

        self.pipeline = build_approved_inventory_runtime_pipeline(
            deployment=deployment,
            input_provider=ClassifiedSource(),
            implementations=self.implementations,
            evidence_repository=evidence_repository,
            cost_projection=cost_projection,
            simulation_registry=simulation_registry,
        )
        self.bundle_verifier = ReadAndVerifyInventoryResultBundleUseCase(deployment, self.store)
        projector = DsimSourceProjector(catalog=catalog, bundle_verifier=self.bundle_verifier)
        self.producer = PersistDsimProjectionUseCase(projector=projector, store=self.store)
        self.verifier = VerifyDsimProjectionUseCase(projector=projector, store=self.store)

    def _request(self, request):
        request = InventoryRuntimeExecutionRequest.from_dict(request.to_dict())
        registered_installed_implementations(self.expected_build)
        plan = request.strategy_execution_plan
        require(
            plan is not None and plan.get("runtime_input_binding") is not None,
            "SOURCE_EXECUTION_MANIFEST_REQUIRED",
        )
        require(
            plan["operational_strategy"]["implementation_content_hash"]
            == self.implementations.mathematical_content_hash
            and plan["psi_simulator"]["implementation_content_hash"]
            == self.implementations.psi_simulator_content_hash,
            "RUNTIME_IMPLEMENTATION_NOT_REGISTERED",
        )
        return request

    async def execute(self, *, request, report, chunk_size=500):
        request = self._request(request)
        result = await self.pipeline.execute(request, report)
        return await asyncio.to_thread(self._seal, request, result, chunk_size)

    def _sources(self, request):
        outbox = self.store.find_outbox(request.engine_run_id, request.value["attempt_no"])
        require(outbox is not None, "SOURCE_EXECUTION_RESULT_REQUIRED")
        payload = outbox.message.payload()
        verified = self.bundle_verifier.execute(
            payload["bundle_reference"],
            strategy_execution_plan=request.strategy_execution_plan,
            runtime_request=request,
        )
        require(
            verified.classification_source_artifact is not None,
            "SOURCE_EXECUTION_CLASSIFICATION_REQUIRED",
        )
        return outbox, verified

    def _seal(self, request, result, chunk_size):
        outbox, verified = self._sources(request)
        require(
            result.inventory_result_content_hash == verified.result_bundle["content_hash"]
            and result.publication_outbox_content_hash == outbox.message.payload_content_hash,
            "SOURCE_EXECUTION_RESULT_MISMATCH",
        )
        reference = self._reference(request)
        try:
            existing = self.store.read_artifact(reference)
        except InventoryInputError as exc:
            if str(exc) != "ARTIFACT_NOT_FOUND":
                raise
        else:
            return self.verify(
                request=request, reference=reference, expected_hash=existing.content_hash
            )
        projection = self.producer.execute(
            request=request,
            bundle_reference=verified.bundle_reference,
            chunk_size=chunk_size,
        )
        projection = {k: v for k, v in projection.items() if k != "replayed"}
        obj = self._artifact(request, outbox, verified, projection)
        self.store.commit_artifacts((obj,))
        return self.verify(request=request, reference=obj.reference, expected_hash=obj.content_hash)

    @staticmethod
    def _reference(request):
        return f"artifact:inventory:{request.engine_run_id}:{request.value['attempt_no']}:source-execution"

    def _artifact(self, request, outbox, verified, projection):
        body = {
            "contract_id": "inventory-dsim-source-execution-v1",
            "contract_version": "1.0.0",
            "source_contract_key": KEY,
            "binding": binding_identity(request),
            "runtime_request_content_hash": request.canonical_hash,
            "installed_build": self.expected_build,
            "runtime_input_binding": request.strategy_execution_plan["runtime_input_binding"],
            "result_bundle_reference": verified.bundle_reference,
            "result_bundle_content_hash": verified.result_bundle["content_hash"],
            "classification_source": classification_pointer(
                verified.classification_source_artifact
            ),
            "result_outbox_id": outbox.message.outbox_id,
            "result_outbox_content_hash": outbox.message.payload_content_hash,
            "projection": projection,
        }
        hashed = digest(body)
        return ArtifactObject.from_document(
            reference=self._reference(request),
            contract_key=KEY,
            document={**body, "content_hash": hashed},
        )

    def verify(self, *, request, reference, expected_hash):
        request = self._request(request)
        obj = self.store.read_artifact(reference)
        require(
            obj.contract_key == KEY and obj.content_hash == expected_hash,
            "SOURCE_EXECUTION_ARTIFACT_MISMATCH",
        )
        document = obj.document()
        outbox, verified = self._sources(request)
        p = document["projection"]
        require(
            type(p) is dict
            and set(p) == {"artifact_reference", "content_hash", "projection_content_hash"},
            "SOURCE_EXECUTION_PROJECTION_POINTER_INVALID",
        )
        projected = self.verifier.execute(
            request=request,
            reference=p["artifact_reference"],
            expected_hash=p["content_hash"],
        )
        require(
            projected.manifest["projection_content_hash"] == p["projection_content_hash"]
            and projected.manifest["bundle_reference"] == verified.bundle_reference,
            "SOURCE_EXECUTION_PROJECTION_MISMATCH",
        )
        require(
            obj == self._artifact(request, outbox, verified, p), "SOURCE_EXECUTION_REPLAY_MISMATCH"
        )
        return {
            "artifact_reference": obj.reference,
            "content_hash": obj.content_hash,
            "document": document,
        }

"""Sealed inputs -> unchanged Python Bundle -> target calculation -> saved result.

Only explicit development assembly. No DB/lifecycle callback or strategy switch.
The input checkpoint precedes numerical work and contains the original Manifest;
recovery never consults a Source provider, even before a Bundle exists.
"""

import asyncio
import copy

from dsio_inventory_engine.infrastructure.runtime_build import registered_installed_implementations
from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.runtime_inputs import (
    instantiate_mathematical_template,
)
from dsio_inventory_engine.inventory_contracts.runtime import (
    InventoryRuntimeExecutionRequest,
    validate_canonical_runtime_binding,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest, require
from dsio_inventory_engine.inventory_evidence.classification_source import (
    validate_classification_source,
)
from dsio_inventory_engine.inventory_evidence.contracts import ArtifactObject
from dsio_inventory_engine.inventory_evidence.policy_analysis import run_shadow_request
from dsio_inventory_engine.prepare_inventory.application.inventory_input import (
    PrepareInventoryInputUseCase,
)
from dsio_inventory_engine.recommend_replenishment.application.run import (
    apply_effective_policy_controls,
)
from .approved_inputs import validate_manifest_request
from .runtime_composition import build_approved_inventory_runtime_pipeline
from .target_result_execution import InventoryTargetResultExecution

KEY = "inventory.target_initial_input"


async def _local_report(**event):
    # Local observation only. Never acknowledge a Platform event or publish a Run.
    return None


class InventoryInitialTargetExecution:
    def __init__(
        self,
        *,
        deployment,
        input_provider,
        target_storage,
        evidence_repository,
        catalog,
        expected_build,
        cost_projection=None,
        simulation_registry=None,
    ):
        self.expected_build = copy.deepcopy(expected_build)
        self.deployment, self.provider = deployment, input_provider
        self.target, self.store = target_storage, evidence_repository
        self.cost, self.registry = cost_projection, simulation_registry
        self.reader = InventoryTargetResultExecution(
            deployment=deployment,
            target_storage=target_storage,
            evidence_repository=evidence_repository,
            catalog=catalog,
            expected_build=expected_build,
        )

    @staticmethod
    def input_reference(request):
        # A Run denotes one Attempt. A different Claim cannot claim its saved target INPUT.
        return f"artifact:inventory:{request.engine_run_id}:target-initial-input"

    def _request(self, request, input_ref):
        request = InventoryRuntimeExecutionRequest.from_dict(copy.deepcopy(request.to_dict()))
        implementations = registered_installed_implementations(self.expected_build)
        plan = request.strategy_execution_plan
        require(
            plan is not None and plan.get("runtime_input_binding") is not None,
            "INITIAL_EXECUTION_MANIFEST_REQUIRED",
        )
        require(
            plan["operational_strategy"]["implementation_content_hash"]
            == implementations.mathematical_content_hash
            and plan["psi_simulator"]["implementation_content_hash"]
            == implementations.psi_simulator_content_hash,
            "INITIAL_EXECUTION_BUILD_MISMATCH",
        )
        inputs = self.target.read_input(input_ref)
        require(
            inputs["binding"]["engine_run_id"] == request.engine_run_id
            and inputs["binding"]["semantics_profile"] == "TARGET_GUARDED"
            and inputs["binding"]["psi_scenario_type"] == "RECOMMENDED",
            "INITIAL_EXECUTION_TARGET_PROFILE_MISMATCH",
        )
        canonical = CanonicalInputRequest.from_dict(
            inputs["request"]["recommendation"]["canonical_input"]
        )
        validate_canonical_runtime_binding(request, canonical_input=canonical)
        snapshot = validate_classification_source(
            request=request,
            snapshot=inputs["classification_snapshot"],
            canonical=canonical,
        )
        require(
            snapshot["as_of_yyyyww"] == canonical.to_dict()["context"]["plan_yyyyww"],
            "INITIAL_EXECUTION_CLASSIFICATION_WEEK_MISMATCH",
        )
        return request, inputs, implementations

    def _checkpoint(self, request, manifest, input_ref):
        body = {
            "contract_id": "inventory-target-initial-input-v1",
            "contract_version": "1.0.0",
            "source_contract_key": KEY,
            "request": request.to_dict(),
            "installed_build": self.expected_build,
            "manifest": manifest,
            "target_input_reference": input_ref,
        }
        return ArtifactObject.from_document(
            reference=self.input_reference(request),
            contract_key=KEY,
            document={**body, "content_hash": digest(body)},
        )

    def _saved(self, request):
        try:
            return self.store.read_artifact(self.input_reference(request))
        except InventoryInputError as exc:
            if str(exc) != "ARTIFACT_NOT_FOUND":
                raise
            return None

    def _validate(self, request, inputs, manifest):
        # Do not invoke automatic analysis during preflight: it can calculate.
        rows = {
            r["kind"]: r["document"]
            for r in manifest["resources"]
            if r["kind"] in {"CONFIG", "CLASSIFICATION", "MATHEMATICAL_TEMPLATE"}
        }
        mathematical = MathematicalPolicyRequest.from_dict(
            instantiate_mathematical_template(rows["MATHEMATICAL_TEMPLATE"], request.engine_run_id)
        )
        require(
            inputs["request"] == run_shadow_request(mathematical).to_dict()
            and inputs["config_values"] == rows["CONFIG"]
            and inputs["classification_snapshot"] == rows["CLASSIFICATION"],
            "INITIAL_EXECUTION_SOURCE_MISMATCH",
        )
        original = mathematical.to_dict()["recommendation"]
        canonical = CanonicalInputRequest.from_dict(original["canonical_input"])
        prepared = PrepareInventoryInputUseCase(self.deployment).execute(canonical).to_dict()
        # Admit the unchanged operational Gate before the resolver can calculate
        # automatic shadow analysis. Controls mutate only this fresh local copy.
        apply_effective_policy_controls(
            prepared,
            original["execution"],
            runtime_request=request,
            canonical_input=canonical,
        )

    async def _execute(self, *, request, target_input_reference, report, recovery):
        input_ref = copy.deepcopy(target_input_reference)
        request, inputs, implementations = await asyncio.to_thread(
            self._request, request, input_ref
        )
        saved = await asyncio.to_thread(self._saved, request)
        require(saved is not None or not recovery, "INITIAL_EXECUTION_CHECKPOINT_REQUIRED")
        if saved is None:
            require(self.provider is not None, "INITIAL_EXECUTION_SOURCE_REQUIRED")
            manifest = copy.deepcopy(await self.provider.resolve(request))
        else:
            manifest = saved.document()["manifest"]
        manifest = validate_manifest_request(request, manifest)
        checkpoint = self._checkpoint(request, manifest, input_ref)
        require(saved is None or saved == checkpoint, "INITIAL_EXECUTION_CHECKPOINT_MISMATCH")
        await asyncio.to_thread(self._validate, request, inputs, manifest)
        if saved is None:
            await asyncio.to_thread(self.store.commit_artifacts, (checkpoint,))

        class SavedManifest:
            async def resolve(self, bound):
                require(
                    bound.canonical_hash == request.canonical_hash,
                    "INITIAL_EXECUTION_REQUEST_MISMATCH",
                )
                return copy.deepcopy(manifest)

        pipeline = build_approved_inventory_runtime_pipeline(
            deployment=self.deployment,
            input_provider=SavedManifest(),
            implementations=implementations,
            evidence_repository=self.store,
            cost_projection=self.cost,
            simulation_registry=self.registry,
        )
        await pipeline.execute(request, report or _local_report)
        await asyncio.to_thread(
            self.target.recover, request.engine_run_id, expected_input_reference=input_ref
        )
        outbox = self.store.find_outbox(request.engine_run_id, request.value["attempt_no"])
        require(outbox is not None, "INITIAL_EXECUTION_BUNDLE_REQUIRED")
        payload = outbox.message.payload()
        bundle = self.store.read_artifact(payload["bundle_reference"])
        sources = {
            "bundle": {"artifact_reference": bundle.reference, "content_hash": bundle.content_hash},
            "target_input": input_ref,
            "target_manifest": self.target.repository.lookup(request.engine_run_id, "COMPLETE"),
        }
        result = await asyncio.to_thread(self.reader.execute, request=request, sources=sources)
        return self._with_checkpoint(result, checkpoint)

    @staticmethod
    def _with_checkpoint(result, checkpoint):
        return {
            **result,
            "initial_input": {
                "artifact_reference": checkpoint.reference,
                "content_hash": checkpoint.content_hash,
            },
        }

    async def execute(self, *, request, target_input_reference, report=None):
        return await self._execute(
            request=request,
            target_input_reference=target_input_reference,
            report=report,
            recovery=False,
        )

    async def recover(self, *, request, target_input_reference, report=None):
        return await self._execute(
            request=request,
            target_input_reference=target_input_reference,
            report=report,
            recovery=True,
        )

    def read(self, *, request, result_pointer):
        input_ref = result_pointer["sources"]["target_input"]
        request, inputs, _ = self._request(request, input_ref)
        saved = self._saved(request)
        require(saved is not None, "INITIAL_EXECUTION_CHECKPOINT_REQUIRED")
        manifest = validate_manifest_request(request, saved.document()["manifest"])
        require(
            saved == self._checkpoint(request, manifest, input_ref),
            "INITIAL_EXECUTION_CHECKPOINT_MISMATCH",
        )
        self._validate(request, inputs, manifest)
        return self._with_checkpoint(
            self.reader.read(request=request, result_pointer=result_pointer), saved
        )

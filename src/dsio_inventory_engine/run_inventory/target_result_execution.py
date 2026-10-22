"""Opt-in local result assembly, not a strategy registration or publication path.

The caller pins existing Bundle/INPUT/MANIFEST pointers. The response groups
existing link and Projection pointers; it creates no new result ID or hash.
Reads replay the saved graph and never write or consult a Source provider.
"""

import copy

from dsio_inventory_engine.infrastructure.runtime_build import registered_installed_implementations
from dsio_inventory_engine.inventory_contracts.runtime import InventoryRuntimeExecutionRequest
from dsio_inventory_engine.inventory_contracts.values import hash_value, require, shape, text
from dsio_inventory_engine.inventory_evidence.application import (
    ReadAndVerifyInventoryResultBundleUseCase,
)
from dsio_inventory_engine.inventory_evidence.projection import (
    PersistDsimProjectionUseCase,
    VerifyDsimProjectionUseCase,
)
from dsio_inventory_engine.inventory_evidence.projection_source import DsimSourceProjector
from dsio_inventory_engine.inventory_evidence.target_result_link import TargetResultLinkUseCase
from dsio_inventory_engine.recommend_replenishment.application.target_verification import (
    target_verification_operation,
)


def _artifact_pointer(value):
    return shape(value, {"artifact_reference": text, "content_hash": hash_value})


def _file_pointer(value, kind):
    def byte_count(v):
        require(type(v) is int and 0 < v <= 128_000_000, "TARGET_RESULT_FILE_SIZE_INVALID")
        return v

    value = shape(value, {"kind": text, "content_hash": hash_value, "byte_count": byte_count})
    require(value["kind"] == kind, "TARGET_RESULT_FILE_KIND_MISMATCH")
    return value


def source_pointers(value):
    return shape(
        copy.deepcopy(value),
        {
            "bundle": _artifact_pointer,
            "target_input": lambda v: _file_pointer(v, "INPUT"),
            "target_manifest": lambda v: _file_pointer(v, "MANIFEST"),
        },
    )


def result_pointers(value):
    return shape(
        copy.deepcopy(value),
        {
            "sources": source_pointers,
            "target_result_link": _artifact_pointer,
            "projection": lambda v: shape(
                v,
                {
                    "artifact_reference": text,
                    "content_hash": hash_value,
                    "projection_content_hash": hash_value,
                },
            ),
        },
    )


class InventoryTargetResultExecution:
    def __init__(self, *, deployment, target_storage, evidence_repository, catalog, expected_build):
        require(deployment.environment == "DEVELOPMENT", "TARGET_RESULT_DEVELOPMENT_ONLY")
        self.expected_build = copy.deepcopy(expected_build)
        self.store, self.target = evidence_repository, target_storage
        self.implementations = registered_installed_implementations(self.expected_build)
        self.bundle_reader = ReadAndVerifyInventoryResultBundleUseCase(deployment, self.store)
        self.link = TargetResultLinkUseCase(
            deployment=deployment, target_storage=target_storage, repository=self.store
        )
        projector = DsimSourceProjector(
            catalog=copy.deepcopy(catalog),
            bundle_verifier=self.bundle_reader,
            target_result_reader=self.link,
        )
        self.producer = PersistDsimProjectionUseCase(projector=projector, store=self.store)
        self.verifier = VerifyDsimProjectionUseCase(projector=projector, store=self.store)

    def _inputs(self, request, sources):
        request = InventoryRuntimeExecutionRequest.from_dict(copy.deepcopy(request.to_dict()))
        registered_installed_implementations(self.expected_build)
        plan = request.strategy_execution_plan
        require(
            plan is not None
            and plan["operational_strategy"]["implementation_content_hash"]
            == self.implementations.mathematical_content_hash
            and plan["psi_simulator"]["implementation_content_hash"]
            == self.implementations.psi_simulator_content_hash,
            "TARGET_RESULT_RUNTIME_BUILD_MISMATCH",
        )
        sources = source_pointers(sources)
        base = self.bundle_reader.execute(
            sources["bundle"]["artifact_reference"],
            strategy_execution_plan=plan,
            runtime_request=request,
        )
        require(
            base.result_bundle["content_hash"] == sources["bundle"]["content_hash"],
            "TARGET_RESULT_BUNDLE_POINTER_MISMATCH",
        )
        for phase, field in (("INPUT", "target_input"), ("COMPLETE", "target_manifest")):
            require(
                self.target.repository.lookup(request.engine_run_id, phase) == sources[field],
                "TARGET_RESULT_STORED_POINTER_MISMATCH",
            )
        return request, sources, base

    @target_verification_operation
    def execute(self, *, request, sources):
        request, sources, _ = self._inputs(request, sources)
        link = self.link.execute(
            request=request,
            bundle_reference=sources["bundle"]["artifact_reference"],
            expected_input_reference=sources["target_input"],
        )
        projection = self.producer.execute(
            request=request, bundle_reference=sources["bundle"]["artifact_reference"]
        )
        # Fixed existing 500-row layout permits deterministic response-loss recovery.
        pointer = {
            "sources": sources,
            "target_result_link": {k: link[k] for k in ("artifact_reference", "content_hash")},
            "projection": {k: v for k, v in projection.items() if k != "replayed"},
        }
        return self.read(request=request, result_pointer=pointer)

    def recover(self, *, request, sources):
        # Recover link/Projection publication after interruption, not missing target calculations.
        # A missing COMPLETE slot is a failure, never a request to find/recalculate latest input.
        return self.execute(request=request, sources=sources)

    @target_verification_operation
    def read(self, *, request, result_pointer):
        pointer = result_pointers(result_pointer)
        request, sources, base = self._inputs(request, pointer["sources"])
        require(
            pointer["target_result_link"]["artifact_reference"] == self.link.reference(request),
            "TARGET_RESULT_LINK_POINTER_MISMATCH",
        )
        linked = self.link.read(
            request=request,
            bundle_reference=sources["bundle"]["artifact_reference"],
            expected_link_hash=pointer["target_result_link"]["content_hash"],
        )
        doc = linked.artifact.document()
        require(
            doc["target_input_reference"] == sources["target_input"]
            and doc["target_manifest_reference"] == sources["target_manifest"],
            "TARGET_RESULT_LINK_SOURCE_MISMATCH",
        )
        projection = pointer["projection"]
        projected = self.verifier.execute(
            request=request,
            reference=projection["artifact_reference"],
            expected_hash=projection["content_hash"],
        )
        manifest = projected.manifest
        require(
            manifest["bundle_reference"] == sources["bundle"]["artifact_reference"]
            and manifest["bundle_content_hash"] == sources["bundle"]["content_hash"]
            and manifest["projection_content_hash"] == projection["projection_content_hash"],
            "TARGET_RESULT_PROJECTION_POINTER_MISMATCH",
        )
        return {
            "result_pointer": pointer,
            "installed_build": copy.deepcopy(self.expected_build),
            "verification_status": "RECOMPUTED_VERIFIED",
            "bundle": dict(base.result_bundle),
            "target_result_link": doc,
            "result": linked.result,
            "projection_row_counts": manifest["table_row_counts"],
            "automatic_publish_allowed": False,
            "automatic_order_allowed": False,
            "database_writes": False,
        }

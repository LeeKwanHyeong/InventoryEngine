"""Append-only Run-gate correction from sealed PSI, never from a simulator.

The original Source execution/Projection remain immutable. A repair envelope
binds both pointers and distinguishes the original calculation Build from the
currently installed Projection Build. No DB connection, Writer or Source port.
"""

import copy

from dsio_inventory_engine.infrastructure.runtime_build import installed_runtime_build
from dsio_inventory_engine.inventory_contracts.runtime import InventoryRuntimeExecutionRequest
from dsio_inventory_engine.inventory_contracts.values import digest, require
from .contracts import ArtifactObject
from .projection import PersistDsimProjectionUseCase, VerifyDsimProjectionUseCase, binding_identity
from .projection_source import apply_claim_action_gates

KEY = "inventory.dsim_projection_gate_repair"
PROFILE = "RUN_AND_ITEM_GATES_V1"


class RepairDsimProjectionGatesUseCase:
    def __init__(self, *, projector, store, expected_calculation_build, expected_projection_build):
        self.projector, self.store = projector, store
        self.calculation_build = copy.deepcopy(expected_calculation_build)
        self.projection_build = copy.deepcopy(expected_projection_build)
        self.verifier = VerifyDsimProjectionUseCase(projector=projector, store=store)
        self.producer = PersistDsimProjectionUseCase(projector=projector, store=store)

    def _source(self, request, reference, expected_hash):
        require(
            self.projection_build == installed_runtime_build(),
            "PROJECTION_REPAIR_INSTALLED_BUILD_MISMATCH",
        )
        request = InventoryRuntimeExecutionRequest.from_dict(request.to_dict())
        obj = self.store.read_artifact(reference)
        doc = obj.document()
        require(
            obj.contract_key == "inventory.dsim_source_execution"
            and obj.content_hash == expected_hash
            and reference
            == f"artifact:inventory:{request.engine_run_id}:{request.value['attempt_no']}:source-execution",
            "PROJECTION_REPAIR_SOURCE_POINTER_MISMATCH",
        )
        require(
            set(doc)
            == {
                "contract_id",
                "contract_version",
                "source_contract_key",
                "binding",
                "runtime_request_content_hash",
                "installed_build",
                "runtime_input_binding",
                "result_bundle_reference",
                "result_bundle_content_hash",
                "classification_source",
                "result_outbox_id",
                "result_outbox_content_hash",
                "projection",
                "content_hash",
            }
            and doc["source_contract_key"] == "inventory.dsim_source_execution"
            and doc["contract_id"] == "inventory-dsim-source-execution-v1"
            and doc["contract_version"] == "1.0.0"
            and doc["binding"] == binding_identity(request)
            and doc["runtime_request_content_hash"] == request.canonical_hash
            and doc["runtime_input_binding"]
            == request.strategy_execution_plan["runtime_input_binding"]
            and doc["installed_build"] == self.calculation_build,
            "PROJECTION_REPAIR_SOURCE_BINDING_MISMATCH",
        )
        require(
            self.calculation_build["content_hash"]
            == digest({k: v for k, v in self.calculation_build.items() if k != "content_hash"}),
            "PROJECTION_REPAIR_CALCULATION_BUILD_HASH_MISMATCH",
        )
        outbox = self.store.find_outbox(request.engine_run_id, request.value["attempt_no"])
        require(outbox is not None, "PROJECTION_REPAIR_OUTBOX_REQUIRED")
        payload = outbox.message.payload()
        require(
            doc["result_outbox_id"] == outbox.message.outbox_id
            and doc["result_outbox_content_hash"] == outbox.message.payload_content_hash
            and doc["result_bundle_reference"] == payload["bundle_reference"]
            and doc["result_bundle_content_hash"] == payload["bundle_content_hash"]
            and doc["classification_source"] == payload["classification_source"],
            "PROJECTION_REPAIR_RESULT_BINDING_MISMATCH",
        )
        for name, field in (
            ("mathematical", "operational_strategy"),
            ("psi_simulator", "psi_simulator"),
        ):
            require(
                doc["installed_build"]["implementations"][name]["implementation_content_hash"]
                == request.strategy_execution_plan[field]["implementation_content_hash"],
                "PROJECTION_REPAIR_CALCULATION_IMPLEMENTATION_MISMATCH",
            )
        pointer = doc["projection"]
        require(
            type(pointer) is dict
            and set(pointer) == {"artifact_reference", "content_hash", "projection_content_hash"},
            "PROJECTION_REPAIR_ORIGINAL_POINTER_INVALID",
        )
        manifest, tables = self.verifier._read_integrity(
            request=request,
            reference=pointer["artifact_reference"],
            expected_hash=pointer["content_hash"],
        )
        require(
            manifest["projection_content_hash"] == pointer["projection_content_hash"]
            and manifest["bundle_reference"] == doc["result_bundle_reference"]
            and manifest["bundle_content_hash"] == doc["result_bundle_content_hash"],
            "PROJECTION_REPAIR_ORIGINAL_MANIFEST_MISMATCH",
        )
        return request, obj, manifest, tables

    @staticmethod
    def _restricted(request, tables):
        rows, changed = copy.deepcopy(tables), 0
        claim = request.to_dict()["claim"]
        for row in rows["dsim.tb_io_replenishment_proposal"]:
            flags = apply_claim_action_gates(
                claim,
                publish_allowed=row["automatic_publish_allowed"],
                order_allowed=row["automatic_order_allowed"],
            )
            if any(row[k] != v for k, v in flags.items()):
                row.update(flags)
                row["row_content_hash"] = digest(
                    {k: v for k, v in row.items() if k != "row_content_hash"}
                )
                changed += 1
        return rows, changed

    def _body(self, request, source, original, tables, pointer, verified):
        expected, changed = self._restricted(request, tables)
        require(verified.tables == expected, "PROJECTION_REPAIR_NON_GATE_CHANGE")
        for key in (
            "bundle_reference",
            "bundle_content_hash",
            "canonical_input_hash",
            "prepared_input_hash",
            "source_artifacts",
            "table_row_counts",
            "catalog_hash",
        ):
            require(
                verified.manifest[key] == original[key],
                "PROJECTION_REPAIR_SOURCE_CHANGED",
            )
        classification = source.document()["classification_source"]
        require(
            any(
                row["artifact_reference"] == classification["artifact_reference"]
                and row["content_hash"] == classification["content_hash"]
                for row in verified.manifest["source_artifacts"]
            ),
            "PROJECTION_REPAIR_CLASSIFICATION_CHANGED",
        )
        return {
            "contract_id": "inventory-dsim-projection-gate-repair-v1",
            "contract_version": "1.0.0",
            "profile": PROFILE,
            "binding": binding_identity(request),
            "runtime_request_content_hash": request.canonical_hash,
            "source_execution": {
                "artifact_reference": source.reference,
                "content_hash": source.content_hash,
            },
            "original_projection": source.document()["projection"],
            "projection": pointer,
            "calculation_build": self.calculation_build,
            "projection_build": self.projection_build,
            "changed_proposal_rows": changed,
        }

    @staticmethod
    def _artifact(request, body):
        hashed = digest(body)
        return ArtifactObject.from_document(
            reference=(
                f"artifact:inventory:{request.engine_run_id}:{request.value['attempt_no']}"
                f":dsim:gate-repair:{hashed}"
            ),
            contract_key=KEY,
            document={**body, "content_hash": hashed},
        )

    def execute(self, *, request, source_reference, source_hash, chunk_size=500):
        require(
            type(chunk_size) is int and 1 <= chunk_size <= 1000,
            "PROJECTION_CHUNK_SIZE_INVALID",
        )
        request, source, original, tables = self._source(request, source_reference, source_hash)
        # Authoritative Bundle replay is a verifier, not a PSI calculation.
        rebuilt = self.projector.build(
            request=request, bundle_reference=original["bundle_reference"]
        )
        expected, _ = self._restricted(request, tables)
        require(rebuilt["tables"] == expected, "PROJECTION_REPAIR_NON_GATE_CHANGE")
        pointer = self.producer.execute(
            request=request, bundle_reference=original["bundle_reference"], chunk_size=chunk_size
        )
        pointer = {k: v for k, v in pointer.items() if k != "replayed"}
        verified = self.verifier.execute(
            request=request,
            reference=pointer["artifact_reference"],
            expected_hash=pointer["content_hash"],
        )
        body = self._body(request, source, original, tables, pointer, verified)
        artifact = self._artifact(request, body)
        replayed = self.store.commit_artifacts((artifact,))
        return {
            "artifact_reference": artifact.reference,
            "content_hash": artifact.content_hash,
            "document": artifact.document(),
            "replayed": replayed,
        }

    def verify(self, *, request, reference, expected_hash):
        artifact = self.store.read_artifact(reference)
        require(
            artifact.contract_key == KEY and artifact.content_hash == expected_hash,
            "PROJECTION_REPAIR_ARTIFACT_MISMATCH",
        )
        document = artifact.document()
        request, source, original, tables = self._source(
            request,
            document["source_execution"]["artifact_reference"],
            document["source_execution"]["content_hash"],
        )
        pointer = document["projection"]
        verified = self.verifier.execute(
            request=request,
            reference=pointer["artifact_reference"],
            expected_hash=pointer["content_hash"],
        )
        require(
            artifact
            == self._artifact(
                request, self._body(request, source, original, tables, pointer, verified)
            ),
            "PROJECTION_REPAIR_REPLAY_MISMATCH",
        )
        return {
            "artifact_reference": reference,
            "content_hash": expected_hash,
            "document": document,
        }

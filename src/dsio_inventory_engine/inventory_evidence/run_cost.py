"""Source-based verification of the Run parent and its development cost children."""

from __future__ import annotations

from dsio_inventory_engine.calculate_landed_cost.artifacts import (
    ReadAndVerifyLandedCostChildArtifactUseCase,
    PROJECTION_KEY,
)
from dsio_inventory_engine.inventory_contracts.run_cost import INPUT_KEY, MANIFEST_KEY, VERSION
from dsio_inventory_engine.inventory_contracts.trade_cost_projection import (
    TradeCostRevisionSetProjection,
)
from dsio_inventory_engine.inventory_contracts.values import canonical_json, require
from dsio_inventory_engine.run_inventory.landed_cost_pipeline import (
    RunCostEvidence,
    ResolvedRunLandedCost,
    build_run_cost_evidence,
)
from dsio_inventory_engine.inventory_contracts.run_result_manifest import (
    run_manifest_reference,
    validate_run_result_manifest,
)
from dsio_inventory_engine.run_inventory.psi_orchestrator import ResolvedCostProfile


class _ArtifactMap:
    def __init__(self, objects):
        self.objects = {row.reference: row for row in objects}
        require(len(self.objects) == len(objects), "ARTIFACT_REFERENCE_DUPLICATE")

    def read_artifact(self, reference):
        require(reference in self.objects, "RUN_COST_ARTIFACT_MISSING")
        return self.objects[reference]


def revalidate_run_cost_evidence(*, evidence: RunCostEvidence, request, run) -> RunCostEvidence:
    verified = verify_run_cost_evidence(
        request=request, run=run, repository=_ArtifactMap(evidence.artifacts)
    )
    require(
        evidence.manifest == verified.manifest and evidence.artifacts == verified.artifacts,
        "RUN_COST_ARTIFACT_SET_MISMATCH",
    )
    return verified


def verify_run_cost_evidence(*, request, run, repository) -> RunCostEvidence:
    root = run_manifest_reference(request)
    parent = repository.read_artifact(root)
    require(parent.contract_key == MANIFEST_KEY, "RUN_RESULT_MANIFEST_CONTRACT_INVALID")
    manifest = validate_run_result_manifest(
        request,
        parent.document(),
        canonical_input_hash=run.canonical_input.input_hash,
        psi_snapshot_id=run.result_bundle["result_bundle_id"],
        psi_content_hash=run.result_bundle["content_hash"],
    )
    inputs = repository.read_artifact(root + ":input")
    projected = repository.read_artifact(root + ":projection")
    profile_object = repository.read_artifact(root + ":profile")
    require(
        inputs.contract_key == INPUT_KEY
        and projected.contract_key == PROJECTION_KEY
        and profile_object.contract_key == "inventory.run_cost_profile_evidence",
        "RUN_COST_SOURCE_CONTRACT_MISMATCH",
    )
    wrapper = projected.document()
    require(
        set(wrapper) == {"contract_id", "contract_version", "projection", "content_hash"}
        and wrapper["contract_id"] == "io-landed-cost-projection-source-v1"
        and wrapper["contract_version"] == VERSION,
        "RUN_COST_SOURCE_CONTRACT_MISMATCH",
    )
    projection = TradeCostRevisionSetProjection.from_dict(wrapper["projection"])
    profile = profile_object.document()
    require(
        set(profile) == {"contract_id", "contract_version", "binding", "payload", "content_hash"}
        and profile["contract_id"] == "inventory-run-cost-profile-evidence-v1"
        and profile["contract_version"] == VERSION
        and profile["binding"] == request.strategy_execution_plan["cost_profile_binding"],
        "RUN_COST_PROFILE_BINDING_MISMATCH",
    )
    resolved_profile = ResolvedCostProfile(
        **profile["binding"], profile_payload_json=canonical_json(profile["payload"])
    )
    expected = build_run_cost_evidence(
        request=request,
        run=run,
        resolved=ResolvedRunLandedCost(inputs.document(), projection),
        cost_profile=resolved_profile,
    )
    require(expected.manifest == manifest, "RUN_RESULT_MANIFEST_REPLAY_MISMATCH")
    for item in expected.artifacts:
        require(
            repository.read_artifact(item.reference) == item, "RUN_COST_ARTIFACT_REPLAY_MISMATCH"
        )
    verifier = ReadAndVerifyLandedCostChildArtifactUseCase(repository)
    for child in manifest["cost_children"]:
        verifier.execute(
            reference=child["artifact_reference"],
            expected_content_hash=child["content_hash"],
            expected_binding=child["binding"],
            expected_shipment_hash=child["shipment_content_hash"],
        )
    return expected

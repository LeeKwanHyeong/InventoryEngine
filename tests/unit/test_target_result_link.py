import copy
from unittest.mock import patch

import pytest

from dsio_inventory_engine.infrastructure.sqlite import SqliteInventoryEvidenceUnitOfWork
from dsio_inventory_engine.infrastructure.target_pipeline_artifacts import (
    LocalTargetPipelineArtifactStore,
)
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.runtime import InventoryRuntimeExecutionRequest
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from dsio_inventory_engine.inventory_evidence.contracts import ArtifactObject
from dsio_inventory_engine.inventory_evidence.target_result_link import TargetResultLinkUseCase
from dsio_inventory_engine.recommend_replenishment.application.target_durable import (
    RunDurableTargetPolicyPipelineUseCase,
)
from tests.support.target_result_link_fixtures import DEPLOYMENT, link_world
from tests.support.runtime_fixtures import reseal_runtime_site_binding
from tests.unit.test_target_execution import ReferenceKernel


def persist(w):
    return w.link.execute(
        request=w.request,
        bundle_reference=w.persisted.bundle_reference,
        expected_input_reference=w.target_repo.lookup(w.request.engine_run_id, "INPUT"),
    )


@pytest.mark.parametrize(
    "field",
    [
        "tenant_id",
        "project_id",
        "plan_id",
        "planning_cycle_id",
        "cycle_site_execution_id",
        "site_cd",
        "attempt_no",
        "site_row_version",
    ],
)
def test_wrong_scope_attempt_and_cycle_fail_before_any_recovery_write(tmp_path, field):
    w = link_world(tmp_path, ReferenceKernel())
    raw = copy.deepcopy(w.request.to_dict())
    if field in ("attempt_no", "site_row_version"):
        raw[field] += 1
    elif field == "site_cd":
        raw["claim"]["scope"][field] = "V999"
    else:
        raw["claim"][field] = (
            "00000000-0000-4000-8000-000000000099" if field.endswith("_id") else "OTHER"
        )
    before = {p.name: p.read_bytes() for p in w.target_root.iterdir()}
    calls = len(w.target.kernel.inputs)
    with pytest.raises(InventoryInputError):
        request = InventoryRuntimeExecutionRequest.from_dict(reseal_runtime_site_binding(raw))
        w.link.recover(
            request=request,
            bundle_reference=w.persisted.bundle_reference,
            expected_input_reference=w.target_repo.lookup(w.request.engine_run_id, "INPUT"),
        )
    assert len(w.target.kernel.inputs) == calls
    assert {p.name: p.read_bytes() for p in w.target_root.iterdir()} == before


@pytest.mark.parametrize(
    "field",
    [
        "counts",
        "calculation_binding",
        "order_plan_binding",
        "target_manifest_reference",
        "bundle_reference",
        "automatic_order_allowed",
    ],
)
def test_resealed_link_cannot_change_provenance_count_or_permissions(tmp_path, field):
    w = link_world(tmp_path, ReferenceKernel())
    ptr = persist(w)
    obj = w.store.read_artifact(ptr["artifact_reference"])
    doc = obj.document()
    if field == "counts":
        doc[field]["candidate_count"] += 1
    elif field == "calculation_binding":
        doc[field]["psi_simulator"]["implementation_content_hash"] = "b" * 64
    elif field == "order_plan_binding":
        doc[field]["selection_result_content_hash"] = "b" * 64
    elif field == "target_manifest_reference":
        doc[field]["content_hash"] = "b" * 64
    elif field == "bundle_reference":
        doc[field] += ":wrong"
    else:
        doc[field] = True
    doc["content_hash"] = digest({k: v for k, v in doc.items() if k != "content_hash"})
    wrong = ArtifactObject.from_document(
        reference=obj.reference, contract_key=obj.contract_key, document=doc
    )
    original = w.store.read_artifact
    with patch.object(
        w.store,
        "read_artifact",
        side_effect=lambda ref: wrong if ref == obj.reference else original(ref),
    ):
        with pytest.raises(InventoryInputError):
            w.link.read(request=w.request, bundle_reference=w.persisted.bundle_reference)


def test_same_reference_different_hash_conflicts_and_does_not_touch_parent(tmp_path):
    w = link_world(tmp_path, ReferenceKernel())
    ptr = persist(w)
    obj = w.store.read_artifact(ptr["artifact_reference"])
    doc = obj.document()
    doc["counts"]["candidate_count"] += 1
    doc["content_hash"] = digest({k: v for k, v in doc.items() if k != "content_hash"})
    wrong = ArtifactObject.from_document(
        reference=obj.reference, contract_key=obj.contract_key, document=doc
    )
    with pytest.raises(InventoryInputError, match="CONFLICT"):
        w.store.commit_artifacts((wrong,))
    assert w.store.read_artifact(ptr["artifact_reference"]) == obj


def test_different_dll_or_environment_cannot_read_or_recover(tmp_path):
    w = link_world(tmp_path, ReferenceKernel())
    persist(w)
    wrong = TargetResultLinkUseCase(
        deployment=DeploymentScope("DSE", "PRODUCTION"), target_storage=w.target, repository=w.store
    )
    with pytest.raises(InventoryInputError, match="DEVELOPMENT_ONLY"):
        wrong.recover(
            request=w.request,
            bundle_reference=w.persisted.bundle_reference,
            expected_input_reference=w.target_repo.lookup(w.request.engine_run_id, "INPUT"),
        )
    w.target.kernel.dll_sha256 = "b" * 64
    with pytest.raises(InventoryInputError):
        w.link.read(request=w.request, bundle_reference=w.persisted.bundle_reference)


def test_missing_source_and_wrong_expected_hash_are_not_accepted(tmp_path):
    w = link_world(tmp_path, ReferenceKernel())
    ptr = persist(w)
    with pytest.raises(InventoryInputError, match="REFERENCE_MISMATCH"):
        w.link.read(
            request=w.request,
            bundle_reference=w.persisted.bundle_reference,
            expected_link_hash="b" * 64,
        )
    linked = w.link.read(request=w.request, bundle_reference=w.persisted.bundle_reference)
    missing = linked.source_artifacts[0].reference
    original = w.store.read_artifact

    def absent(ref):
        if ref == missing:
            raise InventoryInputError("ARTIFACT_NOT_FOUND")
        return original(ref)

    with patch.object(w.store, "read_artifact", side_effect=absent):
        with pytest.raises(InventoryInputError, match="NOT_FOUND"):
            w.link.read(request=w.request, bundle_reference=w.persisted.bundle_reference)
    assert ptr["automatic_order_allowed"] is False


def test_fresh_instances_restore_from_saved_graph_only(tmp_path):
    w = link_world(tmp_path, ReferenceKernel(), mode="LEGACY_STYLE")
    ptr = persist(w)
    repo = SqliteInventoryEvidenceUnitOfWork(tmp_path / "artifacts.sqlite")
    target = RunDurableTargetPolicyPipelineUseCase(
        DEPLOYMENT, ReferenceKernel(), LocalTargetPipelineArtifactStore(w.target_root)
    )
    fresh = TargetResultLinkUseCase(deployment=DEPLOYMENT, target_storage=target, repository=repo)
    del w.sources, w.math
    actual = fresh.read(
        request=w.request,
        bundle_reference=w.persisted.bundle_reference,
        expected_link_hash=ptr["content_hash"],
    )
    assert actual.artifact.content_hash == ptr["content_hash"]
    assert fresh.recover(
        request=w.request,
        bundle_reference=w.persisted.bundle_reference,
        expected_input_reference=w.target_repo.lookup(w.request.engine_run_id, "INPUT"),
    )["replayed"]


def test_integrity_only_view_cannot_be_published_as_a_replayed_link(tmp_path):
    w = link_world(tmp_path, ReferenceKernel())
    view = w.target.read_integrity(w.request.engine_run_id)
    with patch.object(w.target, "read_verified", return_value=view):
        with pytest.raises(InventoryInputError, match="FULL_REPLAY_REQUIRED"):
            persist(w)
    with pytest.raises(InventoryInputError, match="NOT_FOUND"):
        w.store.read_artifact(w.link.reference(w.request))

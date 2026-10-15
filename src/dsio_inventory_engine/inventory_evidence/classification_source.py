"""Analysis-independent sealing of the original claimed Classification Snapshot.

The full Header and item evidence are preserved, never reconstructed from SQL
projections. Header Content Hash and Effective Policy Hash remain distinct.
"""

from dsio_inventory_engine.infrastructure.policy_analysis_inputs import normalized_payload
from dsio_inventory_engine.inventory_contracts.effective_policy_v2 import (
    effective_policy_content_hash,
)
from dsio_inventory_engine.inventory_contracts.values import digest, require
from .contracts import ArtifactObject

KEY = "inventory.run_classification_source_evidence"


def validate_classification_source(*, request, snapshot, canonical):
    bound = request.classification_binding
    require(bound is not None, "CLASSIFICATION_SOURCE_BINDING_REQUIRED")
    snapshot, _ = normalized_payload("CLASSIFICATION", bound["source_snapshot_id"], snapshot)
    claim = request.to_dict()["claim"]
    require(
        all(
            snapshot[k] == claim[k]
            for k in ("tenant_id", "project_id", "config_id", "config_revision_id", "config_hash")
        )
        and snapshot["scope"] == claim["scope"]
        and snapshot["effective_policy_content_hash"] == bound["source_content_hash"],
        "CLASSIFICATION_SOURCE_BINDING_MISMATCH",
    )
    spec = request.strategy_execution_plan.get("policy_analysis_spec")
    if spec is not None:
        require(
            snapshot["content_hash"] == spec["classification_snapshot_content_hash"],
            "CLASSIFICATION_SOURCE_HEADER_HASH_MISMATCH",
        )
    require(
        snapshot["effective_policy_contract_version"] == "2.0.0"
        and effective_policy_content_hash(snapshot["items"])
        == snapshot["effective_policy_content_hash"],
        "CLASSIFICATION_SOURCE_POLICY_HASH_MISMATCH",
    )
    items = {row["item_id"] for row in canonical.to_dict()["snapshots"]["master"]["rows"]}
    require(
        items <= {row["item_id"] for row in snapshot["items"]},
        "CLASSIFICATION_SOURCE_ITEM_COVERAGE_MISMATCH",
    )
    return snapshot


def build_classification_source(*, request, snapshot, canonical):
    snapshot = validate_classification_source(
        request=request, snapshot=snapshot, canonical=canonical
    )
    body = {
        "contract_id": "inventory-run-classification-source-evidence-v1",
        "contract_version": "1.0.0",
        "source_contract_key": KEY,
        "engine_run_id": request.engine_run_id,
        "attempt_no": request.value["attempt_no"],
        "runtime_request_content_hash": request.canonical_hash,
        "canonical_input_hash": canonical.input_hash,
        "classification_snapshot": snapshot,
    }
    return ArtifactObject.from_document(
        reference=f"artifact:inventory:{request.engine_run_id}:{request.value['attempt_no']}:classification-source",
        contract_key=KEY,
        document={**body, "content_hash": digest(body)},
    )


def verify_classification_source(*, request, canonical, repository, pointer):
    require(
        type(pointer) is dict
        and set(pointer)
        == {"artifact_reference", "content_hash", "byte_content_hash", "contract_key"},
        "CLASSIFICATION_SOURCE_POINTER_INVALID",
    )
    obj = repository.read_artifact(pointer["artifact_reference"])
    require(
        obj.contract_key == pointer["contract_key"] == KEY
        and obj.content_hash == pointer["content_hash"]
        and obj.byte_content_hash == pointer["byte_content_hash"],
        "CLASSIFICATION_SOURCE_ARTIFACT_MISMATCH",
    )
    expected = build_classification_source(
        request=request, snapshot=obj.document()["classification_snapshot"], canonical=canonical
    )
    require(obj == expected, "CLASSIFICATION_SOURCE_REPLAY_MISMATCH")
    return obj


def classification_pointer(obj):
    return {
        "artifact_reference": obj.reference,
        "contract_key": obj.contract_key,
        "content_hash": obj.content_hash,
        "byte_content_hash": obj.byte_content_hash,
    }

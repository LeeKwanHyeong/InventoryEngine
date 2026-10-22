"""Projection keeps the original Claim and the exact calculation snapshot."""

import copy

import pytest

from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from dsio_inventory_engine.inventory_evidence.projection_source import snapshot_projection_source


@pytest.mark.parametrize("kind", ["calendar", "master", "forecast"])
def test_upstream_identity_and_canonical_identity_are_both_preserved(kind):
    snapshot = {
        "snapshot_id": "normalized-snapshot",
        "content_hash": "a" * 64,
        "row_count": 26,
        "status": "SEALED",
        "metadata": {"company_cd": "DSE"},
    }
    authority = {"snapshot_id": "original-snapshot", "content_hash": "b" * 64}
    canonical = {"snapshots": {kind: snapshot}, "upstream_bindings": {kind: authority}}
    before = copy.deepcopy(canonical)
    binding = {
        "source_snapshot_id": authority["snapshot_id"],
        "source_content_hash": authority["content_hash"],
    }
    result = snapshot_projection_source(canonical, kind, binding)
    assert result["canonical_snapshot"] == {
        k: snapshot[k] for k in ("snapshot_id", "content_hash", "row_count")
    }
    assert result["source_row_count_basis"] == "CANONICAL_SNAPSHOT"
    assert canonical == before
    for key in ("source_snapshot_id", "source_content_hash"):
        with pytest.raises(InventoryInputError, match="PROJECTION_INPUT_BINDING_MISMATCH"):
            snapshot_projection_source(canonical, kind, {**binding, key: "other"})
    # A derived Snapshot must not masquerade as the original authority.
    with pytest.raises(InventoryInputError, match="PROJECTION_INPUT_BINDING_MISMATCH"):
        snapshot_projection_source(
            canonical,
            kind,
            {
                "source_snapshot_id": snapshot["snapshot_id"],
                "source_content_hash": snapshot["content_hash"],
            },
        )


def test_direct_source_retains_existing_projection_shape():
    snapshot = {
        "snapshot_id": "source",
        "content_hash": "a" * 64,
        "row_count": 0,
        "status": "SEALED",
        "metadata": {"source_events": [], "adjustments": [], "site_cd": "V100"},
    }
    binding = {"source_snapshot_id": "source", "source_content_hash": "a" * 64}
    result = snapshot_projection_source(
        {"snapshots": {"inventory": snapshot}}, "inventory", binding
    )
    assert set(result) == {"snapshot_type", "status", "metadata", "array_evidence"}
    assert result["metadata"] == {"site_cd": "V100"}
    assert all(v["row_count"] == 0 for v in result["array_evidence"].values())

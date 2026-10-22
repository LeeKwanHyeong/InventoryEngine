from dsio_inventory_engine.infrastructure.robustness_artifacts import (
    LocalRobustnessArtifactStore,
    MAX_BYTES,
)
from dsio_inventory_engine.infrastructure.robustness_compression import (
    CompressedRobustnessArtifactStore,
)
from dsio_inventory_engine.inventory_contracts.robustness_shared_inputs import (
    LAYOUT,
    STORAGE_VERSION,
    SHARED_FIELDS,
    MIN_SHARED_BYTES,
    MAX_REFERENCES,
    MAX_DEPTH,
)
from dsio_inventory_engine.inventory_contracts.robustness_storage import (
    MAX_RAW_BYTES,
    MAX_STORED_BYTES,
    MAX_EXPERIMENT_RAW_BYTES,
    MAX_INVOCATION_SECONDS,
)
from tests.unit.test_robustness_shared_inputs import sample


def test_layout_is_physical_only_with_closed_input_roles_and_limits():
    assert STORAGE_VERSION == "3.0.0" and LAYOUT == "SHARED_INPUTS_V1"
    assert SHARED_FIELDS == {"parent_classification", "calendar", "forecast", "future_demand"}
    assert MIN_SHARED_BYTES == 512 and MAX_REFERENCES == 4096 and MAX_DEPTH == 64
    assert MAX_BYTES == MAX_RAW_BYTES == MAX_STORED_BYTES == 128_000_000
    assert MAX_EXPERIMENT_RAW_BYTES == 20_000_000_000 and MAX_INVOCATION_SECONDS == 3600


def test_existing_v1_writer_default_and_reference_contract_are_unchanged(tmp_path):
    value = sample()
    ref = LocalRobustnessArtifactStore(tmp_path).put(value)
    assert set(ref) == {"contract_id", "content_hash", "byte_count"}
    assert ref["content_hash"] == value["content_hash"] and not list(tmp_path.glob("shared-*"))


def test_existing_v2_writer_default_and_reference_contract_are_unchanged(tmp_path):
    value = sample()
    ref = CompressedRobustnessArtifactStore(tmp_path).put(value)
    assert ref["storage_version"] == "2.0.0" and ref["content_hash"] == value["content_hash"]
    assert not list(tmp_path.glob("shared-*"))

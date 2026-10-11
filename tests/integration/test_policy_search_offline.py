"""No DB: calculation -> append-only local evidence -> verifier and CLI."""

import io
import json
from contextlib import redirect_stdout

import pytest
from tests.unit.test_policy_search import execute
from tests.support.policy_search_fixtures import bundle
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from dsio_inventory_engine.infrastructure.policy_search_artifacts import (
    LocalPolicySearchArtifactStore,
)
from dsio_inventory_engine.entrypoints.policy_search import main


def test_append_only_same_hash_is_idempotent_and_tamper_detected(tmp_path):
    store = LocalPolicySearchArtifactStore(tmp_path)
    value = execute()
    assert store.write(value) == store.write(value)
    assert store.read(value["content_hash"]) == value
    path = tmp_path / (value["content_hash"] + ".json")
    # Simulate disk corruption; this test write is confined to pytest's temporary root.
    path.write_text("{}")
    with pytest.raises(InventoryInputError):
        store.read(value["content_hash"])
    with pytest.raises(InventoryInputError):
        store.write(value)


def test_successful_empty_child_cannot_be_resealed(tmp_path):
    value = execute()
    child = value["candidate_evaluations"][0]
    child.update(row_count=0, psi_rows=[])
    child["content_hash"] = digest(
        {key: item for key, item in child.items() if key != "content_hash"}
    )
    value["content_hash"] = digest(
        {key: item for key, item in value.items() if key != "content_hash"}
    )
    with pytest.raises(InventoryInputError, match="EMPTY_SUCCESS"):
        LocalPolicySearchArtifactStore(tmp_path).write(value)
    from jsonschema import Draft202012Validator, ValidationError
    from pathlib import Path

    schema = json.loads(
        (
            Path(__file__).resolve().parents[2] / "schemas/policy_search_result.schema.json"
        ).read_text()
    )
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(value)


def test_result_json_schema_matches_real_evaluator():
    from jsonschema import Draft202012Validator
    from pathlib import Path

    schema = json.loads(
        (
            Path(__file__).resolve().parents[2] / "schemas/policy_search_result.schema.json"
        ).read_text()
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(execute())


def test_offline_cli_writes_retrievable_evidence_without_db(tmp_path):
    raw, config, classification = bundle()
    source = tmp_path / "request.json"
    source.write_text(
        json.dumps(
            {
                "mathematical_request": raw,
                "config_values": config,
                "classification_snapshot": classification,
            }
        )
    )
    output = io.StringIO()
    with redirect_stdout(output):
        status = main(["--request", str(source), "--artifact-root", str(tmp_path)])
    assert status == 0, output.getvalue()
    receipt = json.loads(output.getvalue())
    assert receipt["candidate_count"] == 2
    value = LocalPolicySearchArtifactStore(tmp_path).read(receipt["artifact"]["content_hash"])
    assert value["selected_policies"] is None and not value["database_writes"]

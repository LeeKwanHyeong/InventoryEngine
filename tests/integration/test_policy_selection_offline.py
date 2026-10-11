"""Real candidate PSI -> independent selection -> append-only replay; no database."""

import copy
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from tests.unit.test_policy_selection import configured, select
from tests.unit.test_policy_search import DEPLOYMENT, execute
from dsio_inventory_engine.entrypoints.policy_selection import main
from dsio_inventory_engine.infrastructure.policy_search_artifacts import (
    LocalPolicySearchArtifactStore,
)
from dsio_inventory_engine.infrastructure.policy_selection_artifacts import (
    LocalPolicySelectionArtifactStore,
)
from dsio_inventory_engine.inventory_contracts.policy_selection import (
    verify_policy_selection_result,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest


def reseal(value):
    value["content_hash"] = digest(
        {key: item for key, item in value.items() if key != "content_hash"}
    )
    return value


def validator():
    schema = json.loads(
        (
            Path(__file__).resolve().parents[2] / "schemas/policy_selection_result.schema.json"
        ).read_text()
    )
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@pytest.mark.parametrize("cost", [True, False])
def test_selection_schema_accepts_actual_selected_and_not_selected_results(cost):
    value = select(configured(cost=cost))
    validator().validate(value)
    assert verify_policy_selection_result(value) == value


@pytest.mark.parametrize(
    "change,code",
    [
        (lambda v: v.update(automatic_publish_allowed=True), "PUBLICATION_FORBIDDEN"),
        (lambda v: v.update(operational_policy_approved=True), "PUBLICATION_FORBIDDEN"),
        (lambda v: v.update(extra=True), "CONTRACT_INVALID"),
        (lambda v: v.update(item_selections=[]), "ITEM_FIELDS"),
        (lambda v: v["item_selections"][0].update(source_review_required=None), "REVIEW_INVALID"),
        (lambda v: v["item_selections"][0].update(selected_score=None), "EMPTY_SELECTION"),
        (
            lambda v: next(
                row
                for row in v["item_selections"][0]["candidate_decisions"]
                if row["status"] == "INELIGIBLE"
            ).update(rank=1),
            "RANK_INVALID",
        ),
    ],
)
def test_selection_contract_and_schema_reject_closed_shape_and_state_mismatches(change, code):
    value = select()
    change(value)
    reseal(value)
    with pytest.raises(InventoryInputError, match=code):
        verify_policy_selection_result(value)
    with pytest.raises(ValidationError):
        validator().validate(value)


def test_failed_and_excluded_schema_and_count_type():
    from tests.support.policy_search_fixtures import bundle
    from math_fixtures import reseal_math

    raw, _, _ = bundle()
    raw["recommendation"]["execution"]["item_controls"][0]["max_target_qty"] = "1"
    failed = select(execute(bundle(raw)))
    validator().validate(failed)
    raw, _, _ = bundle()
    raw["policy_input"]["history"].pop()
    validator().validate(select(execute(bundle(reseal_math(raw)))))
    value = select()
    value["selected_item_count"] = True  # Python's bool == 1 must not make it an integer contract.
    with pytest.raises(InventoryInputError, match="COUNT_MISMATCH"):
        verify_policy_selection_result(reseal(value))
    with pytest.raises(ValidationError):
        validator().validate(value)


def test_selection_store_requires_parent_and_replays_idempotently(tmp_path):
    search = execute()
    selected = select(search)
    store = LocalPolicySelectionArtifactStore(tmp_path, DEPLOYMENT)
    with pytest.raises(InventoryInputError, match="NOT_FOUND"):
        store.write(selected)
    LocalPolicySearchArtifactStore(tmp_path).write(search)
    receipt = store.write(selected)
    assert receipt == store.write(selected)
    assert receipt["artifact_id"] == "PSEL-" + selected["content_hash"]
    assert receipt["database_writes"] is False
    assert store.read(selected["content_hash"]) == selected
    assert (
        LocalPolicySearchArtifactStore(tmp_path).read(search["content_hash"])["selected_policies"]
        is None
    )


def test_resealed_wrong_selection_is_rejected_on_write_and_read(tmp_path):
    search = execute()
    LocalPolicySearchArtifactStore(tmp_path).write(search)
    value = select(search)
    forged = copy.deepcopy(value)
    # The attacker preserves a structurally valid pointer but changes the deterministic explanation.
    forged["item_selections"][0]["reason_codes"] = ["RANKED_FIRST", "LOWEST_TOTAL_COST"]
    reseal(forged)
    validator().validate(forged)
    store = LocalPolicySelectionArtifactStore(tmp_path, DEPLOYMENT)
    with pytest.raises(InventoryInputError, match="REPLAY_MISMATCH"):
        store.write(forged)
    path = tmp_path / (forged["content_hash"] + ".json")
    path.write_text(json.dumps(forged))
    with pytest.raises(InventoryInputError, match="REPLAY_MISMATCH"):
        store.read(forged["content_hash"])


def test_selection_read_fails_when_source_is_corrupted(tmp_path):
    search = execute()
    LocalPolicySearchArtifactStore(tmp_path).write(search)
    selected = select(search)
    store = LocalPolicySelectionArtifactStore(tmp_path, DEPLOYMENT)
    store.write(selected)
    (tmp_path / (search["content_hash"] + ".json")).write_text("{}")
    with pytest.raises(InventoryInputError):
        store.read(selected["content_hash"])


def test_resealed_coherent_pointer_to_more_expensive_candidate_is_rejected(tmp_path):
    source = configured(minimum="0.7")  # Both candidates are service feasible.
    LocalPolicySearchArtifactStore(tmp_path).write(source)
    value = select(source)
    item = value["item_selections"][0]
    previous = next(row for row in item["candidate_decisions"] if row["status"] == "SELECTED")
    forged = next(row for row in item["candidate_decisions"] if row["status"] == "NOT_SELECTED")
    previous.update(status="NOT_SELECTED", rank=2, reason_codes=["HIGHER_TOTAL_COST"])
    forged.update(status="SELECTED", rank=1, reason_codes=["RANKED_FIRST"])
    item.update(
        selected_candidate_id=forged["candidate_id"],
        selected_candidate_result_hash=forged["candidate_result_hash"],
        selected_score=copy.deepcopy(forged["score"]),
        selected_policy=copy.deepcopy(
            next(
                child["candidate"]
                for child in source["candidate_evaluations"]
                if child["candidate"]["candidate_id"] == forged["candidate_id"]
            )
        ),
    )
    reseal(value)
    validator().validate(value)
    verify_policy_selection_result(value)  # Shape and pointer alone cannot establish optimality.
    store = LocalPolicySelectionArtifactStore(tmp_path, DEPLOYMENT)
    with pytest.raises(InventoryInputError, match="REPLAY_MISMATCH"):
        store.write(value)
    (tmp_path / (value["content_hash"] + ".json")).write_text(json.dumps(value))
    with pytest.raises(InventoryInputError, match="REPLAY_MISMATCH"):
        store.read(value["content_hash"])


def test_selection_cli_new_file_and_persisted_reference_return_identical_artifact(tmp_path):
    search = execute()
    source = tmp_path / "source.json"
    source.write_text(json.dumps(search))
    receipts = []
    for arguments in (
        ["--search-result", str(source)],
        ["--search-content-hash", search["content_hash"]],
    ):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main([*arguments, "--artifact-root", str(tmp_path)])
        assert code == 0, output.getvalue()
        receipts.append(json.loads(output.getvalue()))
    assert receipts[0] == receipts[1]
    selected = LocalPolicySelectionArtifactStore(tmp_path, DEPLOYMENT).read(
        receipts[0]["artifact"]["content_hash"]
    )
    assert selected == select(search)
    output = io.StringIO()
    with redirect_stdout(output):
        code = main(["--search-result", str(source)])
    assert code == 0 and json.loads(output.getvalue()) == selected


def test_selection_cli_requires_root_for_reference_and_fails_closed_on_scope(tmp_path):
    output = io.StringIO()
    with redirect_stdout(output):
        assert main(["--search-content-hash", "0" * 64]) == 2
    assert json.loads(output.getvalue())["error_code"] == "POLICY_SELECTION_ARTIFACT_ROOT_REQUIRED"
    source = tmp_path / "source.json"
    source.write_text(json.dumps(execute()))
    with redirect_stdout(io.StringIO()):
        assert main(["--search-result", str(source), "--company-cd", "OTHER"]) == 2

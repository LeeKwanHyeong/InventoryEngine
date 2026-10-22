"""Catalog source replay -> verified selection -> append-only restart recovery, without DB."""

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from dsio_inventory_engine.infrastructure.catalog_selection_artifacts import (
    LocalCatalogPolicySelectionArtifactStore,
    LocalCatalogPsiArtifactStore,
)
from dsio_inventory_engine.inventory_contracts.catalog_selection import (
    verify_catalog_selection_result,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from tests.support.catalog_selection_fixtures import feasible_parts, select, selection_case


def reseal(value):
    value["content_hash"] = digest({k: v for k, v in value.items() if k != "content_hash"})
    return value


def validator():
    schema = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "schemas/catalog_policy_selection_result.schema.json"
        ).read_text()
    )
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def stores(root, case):
    source, selector, request, bindings = case
    source_store = LocalCatalogPsiArtifactStore(
        root,
        selector,
        request,
        **{k: v for k, v in bindings.items() if k != "expected_source_hash"},
    )
    return source_store, LocalCatalogPolicySelectionArtifactStore(root, source_store)


@pytest.mark.parametrize("mode", ["LEGACY_STYLE", "ABC_XYZ_SHARED_BUDGET", "WIDE_REFERENCE"])
def test_actual_selected_result_matches_schema_and_is_reproducible(mode):
    case = selection_case(mode=mode)
    value = select(case)
    validator().validate(value)
    assert verify_catalog_selection_result(value) == value
    assert select(case) == value
    source, selector, request, bindings = case
    selector.verify_result(
        value, source, request, expected_selection_hash=value["content_hash"], **bindings
    )


@pytest.mark.parametrize("boh", ["0", "10"])
def test_local_parent_first_storage_is_idempotent_and_restart_replays_exact_inputs(tmp_path, boh):
    case = selection_case(parts=feasible_parts(boh=boh))
    source, _, _, _ = case
    value = select(case)
    source_before, value_before = copy.deepcopy(source), copy.deepcopy(value)
    source_store, store = stores(tmp_path, case)
    with pytest.raises(InventoryInputError, match="NOT_FOUND"):
        store.write(value)
    parent_receipt = source_store.write(source)
    receipt = store.write(value)
    assert receipt == store.write(value)
    assert parent_receipt["artifact_id"] == "OCPSI-" + source["content_hash"]
    assert receipt["artifact_id"] == "OCPSEL-" + value["content_hash"]
    assert receipt["durable_local_artifact"] is True and receipt["database_writes"] is False
    assert source == source_before and value == value_before
    assert (
        stores(tmp_path, selection_case(parts=feasible_parts(boh=boh)))[1].read(
            value["content_hash"]
        )
        == value
    )
    assert source_store.read(source["content_hash"])["selected_policies"] is None
    validator().validate(value)


def test_storage_keeps_a_defensive_copy_of_the_explicit_config_and_classification(tmp_path):
    case = selection_case()
    source, _, _, bindings = case
    value = select(case)
    parent, store = stores(tmp_path, case)
    bindings["config_values"]["policy_search"]["cost_assumptions"]["fixed_order_cost"] = "999"
    bindings["classification_snapshot"]["items"][0]["effective_order_action"] = "ALLOW"
    parent.write(source)
    store.write(value)
    assert store.read(value["content_hash"]) == value


@pytest.mark.parametrize(
    "change,code",
    [
        (lambda v: v.update(automatic_publish_allowed=True), "PUBLICATION_FORBIDDEN"),
        (lambda v: v.update(automatic_order_allowed=True), "PUBLICATION_FORBIDDEN"),
        (lambda v: v.update(operational_policy_approved=True), "PUBLICATION_FORBIDDEN"),
        (lambda v: v.update(database_writes=True), "PUBLICATION_FORBIDDEN"),
        (lambda v: v.update(contract_id="inventory-policy-selection-result"), "CONTRACT_INVALID"),
        (lambda v: v.update(extra=True), "CONTRACT_INVALID"),
        (lambda v: v.update(item_selections=[]), "ITEM_FIELDS"),
        (lambda v: v["item_selections"][0].update(selected_score=None), "POINTER_INVALID"),
        (lambda v: v["item_selections"][0].update(source_review_required=None), "POINTER_INVALID"),
        (
            lambda v: v["item_selections"][0]["candidate_decisions"][0].update(
                verified_metrics=None
            ),
            "METRIC_FIELDS",
        ),
        (
            lambda v: v["item_selections"][0]["candidate_decisions"][0].update(
                baseline_comparison={}
            ),
            "COMPARISON_INVALID",
        ),
        (lambda v: v.update(selected_item_count=True), "COUNT_MISMATCH"),
    ],
)
def test_python_and_json_schema_fail_closed_on_publication_and_incoherent_states(change, code):
    value = select()
    change(value)
    reseal(value)
    with pytest.raises(InventoryInputError, match=code):
        verify_catalog_selection_result(value)
    with pytest.raises(ValidationError):
        validator().validate(value)


def test_duplicate_and_missing_candidate_decisions_fail_full_result_replay():
    source, selector, request, bindings = selection_case()
    value = selector.execute(source, request, **bindings)
    value["item_selections"][0]["candidate_decisions"].pop()
    reseal(value)
    with pytest.raises(InventoryInputError):
        selector.verify_result(
            value, source, request, expected_selection_hash=value["content_hash"], **bindings
        )


def test_rehashed_explanation_is_not_authenticated_by_its_hash_on_write_or_read(tmp_path):
    case = selection_case()
    source, _, _, _ = case
    value = select(case)
    parent, store = stores(tmp_path, case)
    parent.write(source)
    value["item_selections"][0]["reason_codes"] = ["RANKED_FIRST", "LOWEST_TOTAL_COST"]
    reseal(value)
    validator().validate(value)
    verify_catalog_selection_result(value)
    with pytest.raises(InventoryInputError, match="REPLAY_MISMATCH"):
        store.write(value)
    (tmp_path / (value["content_hash"] + ".json")).write_text(json.dumps(value))
    with pytest.raises(InventoryInputError, match="REPLAY_MISMATCH"):
        store.read(value["content_hash"])


def test_coherent_pointer_to_a_more_expensive_candidate_is_rejected_by_source_replay(tmp_path):
    case = selection_case()
    source, _, _, _ = case
    value = select(case)
    parent, store = stores(tmp_path, case)
    parent.write(source)
    item = value["item_selections"][0]
    winner = next(row for row in item["candidate_decisions"] if row["status"] == "SELECTED")
    expensive = next(
        row
        for row in item["candidate_decisions"]
        if row["status"] == "NOT_SELECTED" and row["score"]["total_cost"] == "102"
    )
    rank = expensive["rank"]
    winner.update(status="NOT_SELECTED", rank=rank, reason_codes=["HIGHER_TOTAL_COST"])
    expensive.update(status="SELECTED", rank=1, reason_codes=["RANKED_FIRST"])
    item.update(
        selected_candidate_id=expensive["candidate_id"],
        selected_candidate_result_hash=expensive["candidate_result_hash"],
        selected_score=copy.deepcopy(expensive["score"]),
        selected_policy=copy.deepcopy(
            next(
                child["candidate"]
                for child in source["candidate_evaluations"]
                if child["candidate"]["candidate_id"] == expensive["candidate_id"]
            )
        ),
    )
    reseal(value)
    validator().validate(value)
    verify_catalog_selection_result(value)  # Internal pointers alone are not proof of optimality.
    with pytest.raises(InventoryInputError, match="REPLAY_MISMATCH"):
        store.write(value)


def test_missing_corrupt_or_wrong_context_parent_cannot_be_recovered_as_current(tmp_path):
    case = selection_case()
    source, _, _, _ = case
    value = select(case)
    parent, store = stores(tmp_path, case)
    parent.write(source)
    store.write(value)
    wrong = selection_case(parts=feasible_parts(boh="100"))
    with pytest.raises(InventoryInputError, match="REPLAY_MISMATCH"):
        stores(tmp_path, wrong)[1].read(value["content_hash"])
    (tmp_path / (source["content_hash"] + ".json")).write_text("{}")
    with pytest.raises(InventoryInputError):
        store.read(value["content_hash"])


def test_recovery_requires_sources_but_does_not_query_or_start_a_runtime(tmp_path, monkeypatch):
    # The verifier owns explicit in-memory bindings, not a "latest" Source discovery API.
    import socket

    monkeypatch.setattr(socket, "create_connection", lambda *a, **kw: pytest.fail("No network"))
    case = selection_case()
    source, _, _, _ = case
    value = select(case)
    parent, store = stores(tmp_path, case)
    parent.write(source)
    store.write(value)
    assert stores(tmp_path, selection_case())[1].read(value["content_hash"]) == value


def test_source_replay_uses_real_mathematical_strategy_not_stored_metrics(monkeypatch):
    from dsio_inventory_engine.recommend_replenishment.application.policy_search import (
        CandidateStrategy,
    )

    source, selector, request, bindings = selection_case()
    original = CandidateStrategy.decide
    calls = []

    def traced(self, observation):
        calls.append(observation.content_hash)
        return original(self, observation)

    monkeypatch.setattr(CandidateStrategy, "decide", traced)
    selector.execute(source, request, **bindings)
    assert len(calls) == 32 * 3


def test_json_and_python_contract_accept_failed_and_excluded_results():
    parts = feasible_parts()
    parts[0]["recommendation"]["execution"]["item_controls"][0]["max_target_qty"] = "1"
    failed = select(selection_case(parts=parts))
    excluded = select(
        selection_case(feature_changes=lambda v: v["sources"]["unit_cost"].update(rows=[]))
    )
    for value in (failed, excluded):
        validator().validate(value)
        verify_catalog_selection_result(value)


def test_new_result_is_not_accepted_by_abc_selector_or_old_contract():
    from dsio_inventory_engine.inventory_contracts.policy_selection import (
        verify_policy_selection_result,
    )
    from dsio_inventory_engine.recommend_replenishment.application.policy_selection import (
        RunPolicySelectionUseCase,
    )
    from tests.support.catalog_selection_fixtures import DEPLOYMENT

    value = select()
    with pytest.raises(InventoryInputError, match="CONTRACT_INVALID"):
        verify_policy_selection_result(value)
    source, _, _, _ = selection_case()
    with pytest.raises(InventoryInputError, match="CONTRACT_INVALID"):
        RunPolicySelectionUseCase(DEPLOYMENT).execute(source)

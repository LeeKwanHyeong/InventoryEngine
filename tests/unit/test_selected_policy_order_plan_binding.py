"""Existing identity reuse and exact-source linkage, without a new PSI runner."""

import copy
from unittest.mock import patch

import pytest

from dsio_inventory_engine.inventory_contracts.mathematical import (
    MathematicalPolicyRequest,
    policy_input_binding,
)
from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.order_planning import SelectedPolicyOrderPlanBinding
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from math_fixtures import reseal_math
from tests.support.order_plan_fixtures import order_plan_case
from tests.support.catalog_selection_fixtures import feasible_parts, select, selection_case
from tests.support.catalog_search_fixtures import run_bundle


@pytest.mark.parametrize(
    "mode", ["ABC_V1", "ABC_XYZ_SHARED_BUDGET", "LEGACY_STYLE", "WIDE_REFERENCE"]
)
def test_binding_reuses_existing_identities_without_copying_policies(mode):
    source, selection, request, binding = order_plan_case(mode)
    before = copy.deepcopy((source, selection, request.to_dict()))
    prefix = "PSEL-" if mode == "ABC_V1" else "OCPSEL-"
    value = binding.verify_sources(
        selection, request, expected_selection_hash=selection["content_hash"]
    )
    assert value["selection_result_reference"] == prefix + selection["content_hash"]
    assert value["canonical_input_hash"] == selection["canonical_input_hash"]
    assert value["mathematical_input_binding"] == policy_input_binding(
        request.to_dict()["policy_input"]
    )
    assert not {"order_plan_id", "binding_id", "content_hash", "items", "selected_policies"} & set(
        value
    )
    assert before == (source, selection, request.to_dict())
    assert source["selected_policies"] is None
    assert all(
        selection[key] is False
        for key in (
            "automatic_publish_allowed",
            "automatic_order_allowed",
            "operational_policy_approved",
            "database_writes",
        )
    )


def test_binding_is_defensive_reproducible_and_does_not_run_a_simulation():
    _, selection, request, binding = order_plan_case("ABC_V1")
    with patch(
        "dsio_inventory_engine.recommend_replenishment.application.run.simulate",
        side_effect=AssertionError("Binding must not run PSI"),
    ):
        assert binding == SelectedPolicyOrderPlanBinding.from_sources(
            selection, request, expected_selection_hash=selection["content_hash"]
        )
        output = binding.to_dict()
        output["mathematical_input_binding"]["snapshot_id"] = "changed"
        assert binding.to_dict()["mathematical_input_binding"]["snapshot_id"] != "changed"


def test_wrong_selection_hash_is_rejected():
    _, selection, request, _ = order_plan_case()
    with pytest.raises(InventoryInputError, match="ORDER_PLAN_SELECTION_HASH_MISMATCH"):
        SelectedPolicyOrderPlanBinding.from_sources(
            selection, request, expected_selection_hash="f" * 64
        )


@pytest.mark.parametrize("field", ["engine_run_id", "site_cd", "plan_id", "plan_yyyyww"])
def test_other_run_site_plan_or_w0_cannot_be_substituted(field):
    _, selection, request, _ = order_plan_case()
    selection["context"][field] = "202601" if field == "plan_yyyyww" else "OTHER"
    selection["content_hash"] = digest({k: v for k, v in selection.items() if k != "content_hash"})
    with pytest.raises(InventoryInputError, match="ORDER_PLAN_CONTEXT_MISMATCH"):
        SelectedPolicyOrderPlanBinding.from_sources(
            selection, request, expected_selection_hash=selection["content_hash"]
        )


@pytest.mark.parametrize("field", ["canonical_input_hash", "mathematical_input_hash"])
def test_other_frozen_source_is_rejected_even_with_a_resealed_selection(field):
    _, selection, request, _ = order_plan_case()
    selection[field] = "f" * 64
    selection["content_hash"] = digest({k: v for k, v in selection.items() if k != "content_hash"})
    with pytest.raises(InventoryInputError, match="ORDER_PLAN_.*INPUT_MISMATCH"):
        SelectedPolicyOrderPlanBinding.from_sources(
            selection, request, expected_selection_hash=selection["content_hash"]
        )


@pytest.mark.parametrize(
    "kind",
    [
        "calendar",
        "master",
        "forecast",
        "inventory",
        "receipts",
        "customer_orders",
        "prior_inventory",
        "policies",
    ],
)
def test_unsealed_canonical_input_is_rejected(kind):
    _, selection, request, _ = order_plan_case()
    raw = request.to_dict()
    raw["recommendation"]["canonical_input"]["snapshots"][kind]["status"] = "COLLECTING"
    canonical_hash = CanonicalInputRequest.from_dict(
        raw["recommendation"]["canonical_input"]
    ).input_hash
    raw["recommendation"]["execution"]["canonical_input_hash"] = canonical_hash
    raw["policy_input"]["context"]["canonical_input_hash"] = canonical_hash
    request = MathematicalPolicyRequest.from_dict(reseal_math(raw))
    with pytest.raises(InventoryInputError, match="ORDER_PLAN_SEALED_INPUT_REQUIRED"):
        SelectedPolicyOrderPlanBinding.from_sources(
            selection, request, expected_selection_hash=selection["content_hash"]
        )


def test_unsealed_mathematical_input_is_rejected():
    _, selection, request, _ = order_plan_case()
    raw = request.to_dict()
    raw["policy_input"]["status"] = "COLLECTING"
    request = MathematicalPolicyRequest.from_dict(raw)
    with pytest.raises(InventoryInputError, match="ORDER_PLAN_SEALED_INPUT_REQUIRED"):
        SelectedPolicyOrderPlanBinding.from_sources(
            selection, request, expected_selection_hash=selection["content_hash"]
        )


def test_incomplete_item_selection_does_not_silently_drop_a_master_item():
    _, selection, request, _ = order_plan_case("ABC_V1")
    selection["item_selections"][0]["item_id"] = "OTHER"
    chosen = selection["item_selections"][0]
    chosen["selected_policy"]["item_id"] = "OTHER"
    selection["content_hash"] = digest({k: v for k, v in selection.items() if k != "content_hash"})
    with pytest.raises(InventoryInputError, match="ORDER_PLAN_ITEM_COVERAGE_MISMATCH"):
        SelectedPolicyOrderPlanBinding.from_sources(
            selection, request, expected_selection_hash=selection["content_hash"]
        )


def test_other_mathematical_snapshot_reference_is_rejected_without_a_new_hash_layer():
    _, selection, request, binding = order_plan_case()
    value = binding.to_dict()
    value["mathematical_input_binding"]["snapshot_id"] = "OTHER"
    changed = SelectedPolicyOrderPlanBinding.from_dict(value)
    with pytest.raises(InventoryInputError, match="ORDER_PLAN_SOURCE_BINDING_MISMATCH"):
        changed.verify_sources(
            selection, request, expected_selection_hash=selection["content_hash"]
        )


def test_unknown_selection_type_cannot_be_promoted_to_a_plan():
    _, selection, request, _ = order_plan_case()
    selection["contract_id"] = "inventory-untrusted-selection"
    with pytest.raises(InventoryInputError, match="ORDER_PLAN_SELECTION_CONTRACT_INVALID"):
        SelectedPolicyOrderPlanBinding.from_sources(
            selection, request, expected_selection_hash=selection["content_hash"]
        )


def test_unknown_policy_kind_cannot_execute_through_the_binding_contract():
    _, selection, request, _ = order_plan_case("ABC_V1")
    selection["item_selections"][0]["selected_policy"]["policy_kind"] = "ARBITRARY_STRATEGY"
    selection["content_hash"] = digest({k: v for k, v in selection.items() if k != "content_hash"})
    with pytest.raises(InventoryInputError, match="ORDER_PLAN_POLICY_KIND_UNSUPPORTED"):
        SelectedPolicyOrderPlanBinding.from_sources(
            selection, request, expected_selection_hash=selection["content_hash"]
        )


@pytest.mark.parametrize("action", ["BLOCK", "REVIEW"])
def test_actual_fsn_selection_and_gate_evidence_are_preserved(action):
    _, values, _ = run_bundle()
    values["policy_overlays"]["fsn_order_action"]["N"] = action
    case = selection_case(parts=feasible_parts(boh="100", values=values))
    selection, request = select(case), case[2]
    before = copy.deepcopy((selection, request.to_dict()))
    bound = SelectedPolicyOrderPlanBinding.from_sources(
        selection, request, expected_selection_hash=selection["content_hash"]
    )
    assert (
        bound.verify_sources(selection, request, expected_selection_hash=selection["content_hash"])
        == bound.to_dict()
    )
    item = selection["item_selections"][0]
    if action == "BLOCK":
        assert item["status"] == "NOT_SELECTED"
        assert "ORDER_GATE_BLOCKED" in item["reason_codes"]
    else:
        assert item["status"] == "SELECTED" and item["source_review_required"] is True
    assert before == (selection, request.to_dict())

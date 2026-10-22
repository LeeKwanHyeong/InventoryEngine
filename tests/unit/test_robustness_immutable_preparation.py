"""Exact-input reuse never certifies changed sources or shares mutable policy rows."""

from dataclasses import FrozenInstanceError
import json
from unittest.mock import patch

import pytest

from dsio_inventory_engine.evaluate_replenishment.robustness import immutable_preparation as module
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.replenishment import RecommendationRequest
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, canonical_json
from tests.integration.test_robustness_durable_offline import prepared
from tests.support.robustness_item_fixtures import DEV


@pytest.fixture(scope="module")
def inputs():
    _, assembly, _ = prepared()
    return assembly


def get(slot, value, *, request=None, source=None, source_request=None, deployment=DEV, hash_=None):
    return slot.prepare(
        deployment,
        request or RecommendationRequest.from_dict(value["candidate_recommendation"]),
        source if source is not None else value["development_source"],
        source_request if source_request is not None else value["source_request"],
        hash_ if hash_ is not None else value["development_source"]["content_hash"],
    )


def test_identical_values_in_distinct_objects_reuse_validation_and_serialization(inputs):
    slot = module.ImmutableCausalPreparation()
    with patch.object(
        module.PrepareInventoryInputUseCase,
        "execute",
        autospec=True,
        side_effect=module.PrepareInventoryInputUseCase.execute,
    ) as preparation:
        first = get(slot, inputs)
        second = get(slot, json.loads(canonical_json(inputs)))
    assert first is second
    assert preparation.call_count == 1
    assert (slot.preparations, slot.hits) == (1, 1)
    assert (
        first.input_hash
        == RecommendationRequest.from_dict(inputs["candidate_recommendation"]).input_hash
    )


def test_checkout_detaches_every_policy_calendar_forecast_receipt_and_position(inputs):
    slot = module.ImmutableCausalPreparation()
    value = get(slot, inputs)
    original = value.checkout()
    dirty = value.checkout()
    dirty[0]["execution"]["item_controls"][0]["max_order_qty"] = "0"
    for name in ("policies", "calendar", "demands", "positions", "receipt_decisions"):
        dirty[2][name][0]["contamination"] = name
        dirty[2][name].clear()
    assert value.checkout() == original
    assert value.checkout()[2]["policies"] is not original[2]["policies"]
    with pytest.raises(FrozenInstanceError):
        value.prepared_json = "{}"


@pytest.mark.parametrize("part", ["source", "source_request", "claimed_hash", "request", "company"])
def test_changed_or_forged_inputs_cannot_hit_existing_slot(inputs, part):
    slot = module.ImmutableCausalPreparation()
    get(slot, inputs)
    args = {}
    if part == "source":
        source = json.loads(canonical_json(inputs["development_source"]))
        source["w0_position"]["available_qty"] = "999"
        args["source"] = source  # Same claimed source hash is deliberately retained.
    elif part == "source_request":
        source_request = json.loads(canonical_json(inputs["source_request"]))
        source_request["scope"]["site_cd"] = "OTHER"
        args["source_request"] = source_request
    elif part == "claimed_hash":
        args["hash_"] = "a" * 64
    elif part == "request":
        raw = json.loads(canonical_json(inputs["candidate_recommendation"]))
        raw["execution"]["contract_version"] = "CORRUPTED"
        args["request"] = RecommendationRequest(canonical_json(raw))
    else:
        args["deployment"] = DeploymentScope("OTHER", "DEVELOPMENT")
    with pytest.raises(InventoryInputError):
        get(slot, inputs, **args)
    assert slot.hits == 0
    assert slot._slot is None
    assert (
        get(slot, inputs).checkout() == get(module.ImmutableCausalPreparation(), inputs).checkout()
    )
    assert slot.preparations == 3


def test_policy_control_change_is_new_preparation_not_cached_old_policy(inputs):
    slot = module.ImmutableCausalPreparation()
    original = get(slot, inputs)
    raw = RecommendationRequest.from_dict(inputs["candidate_recommendation"]).to_dict()
    raw["execution"]["item_controls"][0]["max_target_qty"] = "999"
    changed = get(slot, inputs, request=RecommendationRequest.from_dict(raw))
    assert changed.input_hash != original.input_hash
    assert changed.checkout()[0]["execution"]["item_controls"][0]["max_target_qty"] == "999"
    assert slot.preparations == 2 and slot.hits == 0
    # The slot is single-entry: revisiting the old input prepares again.
    assert get(slot, inputs) == original
    assert slot.preparations == 3


def test_disabled_and_over_budget_slots_compute_without_retention(inputs):
    disabled = module.ImmutableCausalPreparation(enabled=False)
    assert get(disabled, inputs) == get(disabled, inputs)
    assert (disabled.preparations, disabled.hits) == (2, 0)
    size = module.MAX_RETAINED_BYTES
    first = get(module.ImmutableCausalPreparation(), inputs)
    # Each object fits, but the combined retained strings do not.
    limit = max(len(v.encode()) for v in vars(first).values()) + 1
    assert limit < size
    slot = module.ImmutableCausalPreparation()
    with patch.object(module, "MAX_RETAINED_BYTES", limit):
        assert get(slot, inputs) == get(slot, inputs)
    assert slot._slot is None and slot.preparations == 2 and slot.hits == 0


def test_production_and_non_boolean_switch_are_rejected(inputs):
    with pytest.raises(InventoryInputError, match="CAUSAL_DEVELOPMENT_ONLY"):
        get(
            module.ImmutableCausalPreparation(),
            inputs,
            deployment=DeploymentScope("DSE", "PRODUCTION"),
        )
    for flag in (0, 1, None, "true"):
        with pytest.raises(InventoryInputError, match="CAUSAL_REUSE_FLAG_INVALID"):
            module.ImmutableCausalPreparation(enabled=flag)

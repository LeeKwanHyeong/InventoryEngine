"""New grouping remains outside the unchanged Runtime, FSN gates and approved inputs."""

import copy

import pytest

from dsio_inventory_engine.classify_inventory.legacy_adapters import LegacyStyleSegmentationAdapter
from dsio_inventory_engine.classify_inventory.legacy_features import feature_input_binding
from tests.support.legacy_style_fixtures import GOLDEN, components, feature_request
from tests.support.policy_search_fixtures import bundle
from tests.unit.test_policy_search import execute


@pytest.mark.parametrize("case", GOLDEN["gate_cases"][:2], ids=lambda c: c["id"])
def test_new_grouping_does_not_change_real_fsn_policy_execution_or_original_hashes(case):
    raw, config, _ = bundle()
    if case["existing_order_action"] == "BLOCK":
        config["policy_overlays"]["fsn_order_action"]["N"] = "BLOCK"
    parts = bundle(raw, config_values=config)
    original = copy.deepcopy(parts)
    before = execute(parts)
    _, producer, classifier = components()
    request = feature_request()
    detailed = classifier.classify(
        producer.produce(request, expected_binding=feature_input_binding(request))
    )
    neutral = LegacyStyleSegmentationAdapter(classifier).segment(
        detailed, configuration_hash=detailed.to_dict()["configuration_hash"]
    )
    assert neutral.assignments[0].status == case["group_assignment"]
    after = execute(parts)
    assert before["content_hash"] == after["content_hash"]
    assert parts == original
    assert parts[2]["items"][0]["effective_order_action"] == case["expected_order_action"]
    assert after["baseline_items"][0]["psi_rows"]
    if case["expected_order_action"] == "BLOCK":
        assert all(child["orders"] == [] for child in after["candidate_evaluations"])
        assert all(
            row["proposal"]["action_type"] == "HOLD"
            for child in after["candidate_evaluations"]
            for row in child["decision_evidence"]
        )
    else:
        assert any(child["orders"] for child in after["candidate_evaluations"])
        assert all(
            not order["automatic_order_allowed"]
            for child in after["candidate_evaluations"]
            for order in child["orders"]
        )

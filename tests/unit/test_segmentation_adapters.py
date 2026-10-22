"""Pure contracts/adapters; never weaken classification or operational policy gates."""

import copy
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal

import pytest

from dsio_inventory_engine.classify_inventory.abc_xyz import AbcXyzSegmentation
from dsio_inventory_engine.classify_inventory.adapters import (
    AbcXyzCandidateRangeAdapter,
    AbcXyzSegmentationAdapter,
)
from dsio_inventory_engine.classify_inventory.application import classify_items, config_hash
from dsio_inventory_engine.inventory_contracts.policy_search import (
    candidate_policies,
    normalize_policy_search,
)
from dsio_inventory_engine.inventory_contracts.segmentation import (
    CandidateRange,
    CandidateRangeBinding,
    CandidateRangeProvider,
    SearchGroupAssignment,
    SegmentationProvider,
    SegmentationResult,
)
from tests.support.policy_search_fixtures import GOLDEN
from tests.unit.test_classification_snapshot import inputs, metric


def classified(rows=None):
    return AbcXyzSegmentation().classify(
        rows if rows is not None else inputs().metrics,
        abc_a_cumulative_share=Decimal("0.8"),
        abc_b_cumulative_share=Decimal("0.95"),
        xyz_x_max_cv2=Decimal("0.49"),
        xyz_y_max_cv2=Decimal("1"),
        lookback_weeks=52,
    )


def profile():
    return {
        key: value
        for key, value in GOLDEN["search_config"]["group_profiles"][0].items()
        if key != "segment_key"
    }


def assignment(group="AX"):
    return SearchGroupAssignment("ITEM-A", group, "ASSIGNED", None, "AX")


def test_pure_class_is_compatible_and_adapter_preserves_all_item_assignments():
    rows = (*inputs().metrics, metric("ZERO", "0", "0", "0"))
    original = copy.deepcopy(rows)
    result = classified(rows)
    assert result == classify_items(
        rows,
        abc_a_cumulative_share=Decimal("0.8"),
        abc_b_cumulative_share=Decimal("0.95"),
        xyz_x_max_cv2=Decimal("0.49"),
        xyz_y_max_cv2=Decimal("1"),
        lookback_weeks=52,
    )
    provider: SegmentationProvider = AbcXyzSegmentationAdapter()
    output = provider.segment(result, configuration_hash=config_hash(inputs().config_values))
    assert output.method_id == "ABC_XYZ" and output.implementation_version == "1.0.0"
    assert output.source_content_hash == result.source_content_hash
    assert len(output.assignments) == len(rows)
    zero = next(row for row in output.assignments if row.item_id == "ZERO")
    assert zero.to_dict() == {
        "item_id": "ZERO",
        "search_group_id": None,
        "status": "UNCLASSIFIED",
        "reason_code": "MISSING_REVENUE",
        "abc_xyz_class": None,
    }
    assert replace(output, assignments=tuple(reversed(output.assignments))) == output
    assert replace(output, configuration_hash="b" * 64).content_hash != output.content_hash
    assert rows == original


def test_neutral_groups_are_not_limited_to_abc_xyz_but_do_not_enable_legacy_runtime():
    row = assignment("LEGACY_STYLE:LT_HIGH:PRICE_LOW")
    result = SegmentationResult("LEGACY_STYLE", "0.1.0", "a" * 64, "b" * 64, (row,))
    assert result.assignments[0].abc_xyz_class == "AX"
    assert result.assignments[0].search_group_id != result.assignments[0].abc_xyz_class
    with pytest.raises(ValueError, match="POLICY_SEARCH_ITEM_UNCLASSIFIED"):
        AbcXyzCandidateRangeAdapter().resolve(GOLDEN["search_config"], assignment=row)


def test_range_normalizes_decimal_spelling_order_and_is_defensively_immutable():
    raw = profile()
    raw["rmos_values"] = ["1.0", "0.0"]
    first = CandidateRange.from_dict(raw)
    raw["rmos_values"][:] = ["0", "1"]
    second = CandidateRange.from_dict(raw)
    assert first == second and first.content_hash == second.content_hash
    raw["rmos_values"].append("2")
    assert first.rmos_values == ("0", "1")
    serialized = first.to_dict()
    serialized["rmos_values"].append("3")
    assert first == second
    with pytest.raises(FrozenInstanceError):
        first.emos_values = ("2",)


@pytest.mark.parametrize("with_override", [False, True])
def test_candidate_adapter_preserves_v1_rows_ids_and_override_precedence(with_override):
    config = copy.deepcopy(GOLDEN["search_config"])
    if with_override:
        config["item_overrides"] = [
            {**profile(), "item_id": "ITEM-A", "reason": "  Development exception  "}
        ]
    original = copy.deepcopy(config)
    provider: CandidateRangeProvider = AbcXyzCandidateRangeAdapter()
    binding = provider.resolve(config, assignment=assignment())
    assert binding.range_source == ("ITEM_OVERRIDE" if with_override else "GROUP_PROFILE")
    assert binding.override_reason == ("Development exception" if with_override else None)
    assert binding.candidate_range == CandidateRange.from_dict(profile())
    assert AbcXyzCandidateRangeAdapter().candidates(
        config, item_id="ITEM-A", segment_key="AX"
    ) == candidate_policies(config, item_id="ITEM-A", segment_key="AX")
    normalized = provider.resolve(normalize_policy_search(config), assignment=assignment())
    assert normalized.content_hash == binding.content_hash
    assert config == original


@pytest.mark.parametrize(
    "change,segment,code",
    [
        (lambda c: c.update(enabled=False), "AX", "POLICY_SEARCH_DISABLED"),
        (lambda c: None, None, "POLICY_SEARCH_ITEM_UNCLASSIFIED"),
        (lambda c: None, "CZ", "POLICY_SEARCH_GROUP_NOT_CONFIGURED"),
        (lambda c: c.update(unexpected=True), None, "POLICY_SEARCH_CONFIG_INVALID"),
        (lambda c: c.update(max_candidates_per_item=1), "AX", "POLICY_SEARCH_CANDIDATE_LIMIT"),
    ],
)
def test_candidate_errors_match_compatibility_function(change, segment, code):
    config = copy.deepcopy(GOLDEN["search_config"])
    change(config)
    for provider in (candidate_policies, AbcXyzCandidateRangeAdapter().candidates):
        with pytest.raises(ValueError, match=f"^{code}$"):
            provider(config, item_id="ITEM-A", segment_key=segment)


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"status": "APPROVED"}, "STATUS_INVALID"),
        ({"search_group_id": None}, "INVALID_ITEM_IDENTIFIER"),
        ({"abc_xyz_class": "FAKE"}, "ABC_XYZ_INVALID"),
        ({"reason_code": "unexpected"}, "ASSIGNED_REASON_UNEXPECTED"),
        (
            {"status": "UNCLASSIFIED", "search_group_id": None, "reason_code": None},
            "UNCLASSIFIED_REASON_REQUIRED",
        ),
    ],
)
def test_assignment_closed_states(kwargs, code):
    with pytest.raises(ValueError, match=code):
        replace(assignment(), **kwargs)


def test_result_rejects_duplicate_coverage_invalid_hash_and_version():
    value = SegmentationResult("ABC_XYZ", "1.0.0", "a" * 64, "b" * 64, (assignment(),))
    for changes, code in [
        ({"assignments": ()}, "ASSIGNMENTS_REQUIRED"),
        ({"assignments": (assignment(), assignment())}, "DUPLICATE_ITEM"),
        ({"source_content_hash": "unknown"}, "HASH"),
        ({"implementation_version": "unversioned"}, "IMPLEMENTATION_VERSION_INVALID"),
    ]:
        with pytest.raises(ValueError, match=code):
            replace(value, **changes)


def test_range_rejects_duplicate_values_unknown_fields_and_excess_candidates():
    for changes, limit, code in [
        ({"emos_values": ["1", "1.0"]}, 256, "DUPLICATE_VALUE"),
        ({"unexpected": True}, 256, "PROFILE_INVALID"),
        ({}, 1, "CANDIDATE_LIMIT"),
        ({"emos_values": ["0"]}, 256, "DECIMAL_RANGE"),
    ]:
        with pytest.raises(ValueError, match=code):
            CandidateRange.from_dict({**profile(), **changes}, max_candidates=limit)


def test_range_binding_cannot_grant_an_unclassified_item_or_invent_override():
    bound = AbcXyzCandidateRangeAdapter().resolve(GOLDEN["search_config"], assignment=assignment())
    bad = SearchGroupAssignment("ITEM-A", None, "UNCLASSIFIED", "ZERO_MEAN_DEMAND")
    with pytest.raises(ValueError, match="CANDIDATE_ASSIGNMENT_REQUIRED"):
        replace(bound, assignment=bad)
    with pytest.raises(ValueError, match="OVERRIDE_REASON_INVALID"):
        replace(bound, range_source="ITEM_OVERRIDE")
    assert isinstance(bound, CandidateRangeBinding)


def test_classification_adapter_rejects_missing_duplicate_or_contradictory_items():
    result = classified()
    for changed in (
        replace(result, items=result.items[:-1]),
        replace(result, items=(result.items[0], result.items[0], result.items[2])),
        replace(result, items=({**result.items[0], "segment_key": "CZ"}, *result.items[1:])),
    ):
        with pytest.raises(ValueError, match="SEGMENTATION_"):
            AbcXyzSegmentationAdapter().segment(changed, configuration_hash="a" * 64)

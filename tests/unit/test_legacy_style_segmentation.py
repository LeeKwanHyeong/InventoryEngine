"""Literal Golden and fail-closed integrity tests; no DB or operational activation."""

import copy
from dataclasses import FrozenInstanceError, replace
from decimal import ROUND_DOWN, localcontext
from fractions import Fraction
from itertools import product

import pytest

from dsio_inventory_engine.classify_inventory.adapters import AbcXyzCandidateRangeAdapter
from dsio_inventory_engine.classify_inventory.legacy_adapters import (
    LegacyStyleCandidateRangeAdapter,
    LegacyStyleSegmentationAdapter,
)
from dsio_inventory_engine.classify_inventory.legacy_features import (
    LegacyFeatureSnapshot,
    feature_input_binding,
)
from dsio_inventory_engine.classify_inventory.legacy_rules import (
    LegacyStyleRules,
    RULES_PROFILE_HASH,
)
from dsio_inventory_engine.classify_inventory.legacy_style import LegacySegmentationResult
from dsio_inventory_engine.inventory_contracts.values import canonical_json, digest
from tests.support.legacy_style_fixtures import (
    GOLDEN,
    RULES_DOCUMENT,
    components,
    feature_request,
    item_exception,
)


def produce(request):
    _, producer, _ = components()
    return producer.produce(request, expected_binding=feature_input_binding(request))


def reseal(value):
    body = {key: val for key, val in value.items() if key != "content_hash"}
    return {**body, "content_hash": digest(body)}


@pytest.mark.parametrize(
    "case", GOLDEN["routing_cases"] + GOLDEN["boundary_cases"], ids=lambda c: c["id"]
)
def test_literal_group_and_boundary_golden(case):
    _, _, classifier = components()
    result = classifier.route_factors(**case["features"])
    assert result["factor_group_id"] == case["expected_group_id"]
    assert result["profile_id"] == case["expected_profile"]
    if "expected_score" in case:
        assert result["score"] == case["expected_score"]


@pytest.mark.parametrize("case", GOLDEN["reference_feature_cases"], ids=lambda c: c["id"])
def test_sealed_reference_features_and_adapter_match_hand_expectations(case):
    rules, _, classifier = components()
    request = feature_request(reference_case=case)
    original = copy.deepcopy(request)
    snapshot = produce(request)
    feature = snapshot.to_dict()["features"][0]
    assert feature["status"] == "READY"
    assert feature["reference_policy"]["safety_stock_qty"] == case["expected_safety_stock_qty"]
    assert feature["reference_policy"]["rop_qty"] == case["expected_reference_rop_qty"]
    assert Fraction(feature["rop_cover_numerator"]) / Fraction(
        feature["rop_cover_denominator"]
    ) == Fraction(case["expected_coverage_fraction"])
    detailed = classifier.classify(snapshot)
    body = detailed.to_dict()
    assignment = (
        LegacyStyleSegmentationAdapter(classifier)
        .segment(detailed, configuration_hash=body["configuration_hash"])
        .assignments[0]
    )
    assert (
        assignment.search_group_id == case["expected_group_id"] and assignment.abc_xyz_class == "AX"
    )
    assert body["items"][0]["profile_id"] == case["expected_profile"]
    binding = LegacyStyleCandidateRangeAdapter(classifier, rules).resolve(
        body, assignment=assignment
    )
    assert len(list(product(*binding.candidate_range.to_dict().values()))) == 32
    assert request == original


def missing_request(case_id):
    request = feature_request()
    sources = request["sources"]
    if case_id == "MISSING_PRICE":
        sources["unit_cost"]["rows"] = []
    elif case_id == "MISSING_LEAD_TIME":
        sources["lead_time"]["rows"] = []
    elif case_id == "ONE_MISSING_HISTORY_WEEK":
        sources["history"]["rows"].pop()
    elif case_id == "UNKNOWN_SOURCE":
        sources["history"]["evidence_kind"] = "UNVERIFIED"
    elif case_id == "UNSUPPORTED_UOM":
        request["universe"][0]["uom"] = "BOX"
    elif case_id == "JPY_NOT_AUTOMATIC_USD":
        sources["unit_cost"]["rows"][0]["currency"] = "JPY"
    elif case_id == "PRICE_AGE_92_DAYS":
        sources["unit_cost"]["rows"][0]["price_as_of"] = "2024-12-15"
    elif case_id == "PRICE_IN_FUTURE":
        sources["unit_cost"]["rows"][0]["price_as_of"] = "2025-03-18"
    elif case_id == "ZERO_PRICE_IS_NOT_FREE_DEFAULT":
        sources["unit_cost"]["rows"][0]["unit_cost"] = "0"
    elif case_id == "ZERO_MEAN_NO_FORECAST_RESCUE":
        for row in sources["history"]["rows"]:
            row["demand_qty"] = "0"
    elif case_id == "NON_FINITE_OR_NEGATIVE_FEATURE":
        sources["history"]["rows"][0]["demand_qty"] = "NaN"
    elif case_id == "REFERENCE_ROP_NOT_AVAILABLE":
        for row in sources["history"]["rows"]:
            row["demand_qty"] = "1000000000000"
        sources["lead_time"]["rows"][0]["lead_time_days"] = 3660
    else:
        raise AssertionError(case_id)
    return request


@pytest.mark.parametrize("case", GOLDEN["negative_cases"], ids=lambda c: c["id"])
def test_missing_data_golden_is_not_imputed_or_rescued_by_exception(case):
    request = missing_request(case["id"])
    request["item_exceptions"] = [item_exception()]
    _, _, classifier = components()
    snapshot = produce(request)
    feature = snapshot.to_dict()["features"][0]
    result = classifier.classify(snapshot)
    body = result.to_dict()
    assert feature["primary_reason"] == case["expected_primary_reason"]
    assert body["items"][0]["status"] == case["expected_status"]
    assert body["items"][0]["search_group_id"] is None
    assert body["items"][0]["profile_id"] is None
    assert snapshot.to_dict()["input"]["sources"] == request["sources"]
    neutral = LegacyStyleSegmentationAdapter(classifier).segment(
        result, configuration_hash=body["configuration_hash"]
    )
    assert neutral.assignments[0].reason_code == case["expected_primary_reason"]
    with pytest.raises(ValueError, match="LS_SOURCE_BINDING_MISMATCH"):
        LegacyStyleCandidateRangeAdapter(classifier, classifier.rules).resolve(
            body, assignment=neutral.assignments[0]
        )


def test_multiple_missing_reasons_have_fixed_priority_and_keep_full_evidence():
    request = missing_request("ONE_MISSING_HISTORY_WEEK")
    request["sources"]["unit_cost"]["rows"] = []
    request["sources"]["history"]["evidence_kind"] = "UNVERIFIED"
    feature = produce(request).to_dict()["features"][0]
    assert feature["reason_codes"] == [
        "LS_MISSING_REQUIRED_FACTOR",
        "LS_HISTORY_INCOMPLETE",
        "LS_SOURCE_UNVERIFIED",
    ]
    assert feature["primary_reason"] == "LS_MISSING_REQUIRED_FACTOR"


@pytest.mark.parametrize("lead", [True, -1, 1.5, 3661, "7"])
def test_invalid_physical_lead_time_is_not_truncated_or_replaced(lead):
    request = feature_request()
    request["sources"]["lead_time"]["rows"][0]["lead_time_days"] = lead
    assert produce(request).to_dict()["features"][0]["primary_reason"] == "LS_INVALID_FEATURE_VALUE"


@pytest.mark.parametrize("price", ["-1", "0"])
def test_nonpositive_price_is_not_free_inventory(price):
    request = feature_request()
    request["sources"]["unit_cost"]["rows"][0]["unit_cost"] = price
    assert (
        produce(request).to_dict()["features"][0]["primary_reason"] == "LS_NON_POSITIVE_UNIT_COST"
    )


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", True, "1e-999999"])
def test_invalid_demand_is_preserved_but_not_calculated(value):
    request = feature_request()
    request["sources"]["history"]["rows"][0]["demand_qty"] = value
    assert produce(request).to_dict()["features"][0]["primary_reason"] == "LS_INVALID_FEATURE_VALUE"


@pytest.mark.parametrize(
    "mutation",
    ["missing_close", "future_source", "no_generator", "sales_not_demand", "wrong_time_uom"],
)
def test_temporal_semantics_and_source_evidence_are_not_silently_promoted(mutation):
    request = feature_request()
    expected = "LS_SOURCE_UNVERIFIED"
    if mutation == "missing_close":
        request["sources"]["history"]["closed_yyyyww"].pop()
        expected = "LS_HISTORY_INCOMPLETE"
    elif mutation == "future_source":
        request["sources"]["history"]["available_at"] = "2025-03-17T00:00:01Z"
        expected = "LS_FUTURE_OR_STALE_FEATURE"
    elif mutation == "no_generator":
        request["sources"]["history"]["generator"] = None
    elif mutation == "sales_not_demand":
        request["sources"]["history"]["quantity_semantics"] = "FULFILLED_SALES"
    elif mutation == "wrong_time_uom":
        request["sources"]["lead_time"]["time_uom"] = "WEEK"
        expected = "LS_UNSUPPORTED_UOM"
    assert produce(request).to_dict()["features"][0]["primary_reason"] == expected


@pytest.mark.parametrize(
    "start,end,active",
    [
        ("2025-03-17", "2025-04-01", True),
        ("2025-03-01", "2025-03-17", False),
        ("2025-03-18", "2025-04-01", False),
    ],
)
def test_item_exception_interval_scope_group_and_original_factor_preservation(start, end, active):
    request = feature_request()
    request["item_exceptions"] = [item_exception(start=start, end=end)]
    rules, _, classifier = components()
    result = classifier.classify(produce(request))
    body = result.to_dict()
    row = body["items"][0]
    assert row["factor_group_id"] == "LS1:R1-L1-P1"
    assert row["profile_id"] == ("LARGE" if active else "SMALL")
    assert row["range_source"] == ("ITEM_OVERRIDE" if active else "GROUP_PROFILE")
    if active:
        assert row["search_group_id"] == "LS1:ITEM:" + digest(["DSE", "C100", "V100", "ITEM-A"])
        assert row["item_exception"]["reason"] == "개발 비교 전용"
    neutral = LegacyStyleSegmentationAdapter(classifier).segment(
        result, configuration_hash=body["configuration_hash"]
    )
    binding = LegacyStyleCandidateRangeAdapter(classifier, rules).resolve(
        body, assignment=neutral.assignments[0]
    )
    assert binding.range_source == row["range_source"]
    assert binding.override_reason == ("개발 비교 전용" if active else None)


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("duplicate_item", "LS_DUPLICATE_ITEM"),
        ("duplicate_history", "LS_DUPLICATE_ITEM"),
        ("orphan", "LS_ORPHAN_ITEM"),
        ("wrong_site", "LS_SCOPE_OR_W0_MISMATCH"),
        ("wrong_week", "LS_SCOPE_OR_W0_MISMATCH"),
        ("bad_calendar", "LS_SCOPE_OR_W0_MISMATCH"),
        ("bad_version", "LS_SOURCE_BINDING_MISMATCH"),
        ("extra_field", "LS_SOURCE_BINDING_MISMATCH"),
        ("duplicate_exception", "LS_OVERRIDE_DUPLICATE"),
        ("invalid_exception", "LS_OVERRIDE_INVALID"),
        ("wrong_exception_site", "LS_OVERRIDE_INVALID"),
        ("orphan_exception", "LS_OVERRIDE_INVALID"),
    ],
)
def test_invalid_input_is_a_run_error_not_a_fallback(mutation, reason):
    request = feature_request()
    if mutation == "duplicate_item":
        request["universe"].append(copy.deepcopy(request["universe"][0]))
    elif mutation == "duplicate_history":
        request["sources"]["history"]["rows"].append(
            copy.deepcopy(request["sources"]["history"]["rows"][0])
        )
    elif mutation == "orphan":
        request["sources"]["history"]["rows"][0]["item_id"] = "ORPHAN"
    elif mutation == "wrong_site":
        request["sources"]["history"]["scope"] = {
            "company_cd": "DSE",
            "subs_cd": "C100",
            "site_cd": "V101",
        }
    elif mutation == "wrong_week":
        request["sources"]["history"]["rows"][0]["yyyyww"] = "202512"
    elif mutation == "bad_calendar":
        request["calendar"][0] = copy.deepcopy(request["calendar"][1])
    elif mutation == "bad_version":
        request["contract_version"] = "9.0.0"
    elif mutation == "extra_field":
        request["automatic_order_allowed"] = True
    elif mutation == "duplicate_exception":
        request["item_exceptions"] = [item_exception(), item_exception()]
    elif mutation == "invalid_exception":
        request["item_exceptions"] = [{**item_exception(), "reason": ""}]
    elif mutation == "wrong_exception_site":
        request["item_exceptions"] = [{**item_exception(), "site_cd": "V101"}]
    elif mutation == "orphan_exception":
        request["item_exceptions"] = [{**item_exception(), "item_id": "ORPHAN"}]
    with pytest.raises(ValueError, match=reason):
        produce(request)


def test_rules_cannot_be_tampered_or_rehashed_as_the_pinned_version():
    assert RULES_PROFILE_HASH == GOLDEN["rules_profile_hash"]
    changed = copy.deepcopy(RULES_DOCUMENT)
    changed["profile"]["factors"]["unit_cost"]["cut_values"][0] = "11"
    for rehashed in (False, True):
        if rehashed:
            changed["profile_content_hash"] = digest(changed["profile"])
        with pytest.raises(ValueError, match="LS_RULES_HASH_MISMATCH"):
            LegacyStyleRules.from_document(changed)


def test_external_binding_rejects_source_and_context_substitution():
    _, producer, _ = components()
    request = feature_request()
    pinned = feature_input_binding(request)
    for mutate in (
        lambda r: r["sources"]["unit_cost"]["rows"][0].update(unit_cost="200"),
        lambda r: r["sources"]["unit_cost"].update(revision="OTHER-REV"),
        lambda r: r["context"].update(master_snapshot_revision="OTHER-MASTER"),
        lambda r: r["context"].update(classification_content_hash="d" * 64),
    ):
        changed = copy.deepcopy(request)
        mutate(changed)
        with pytest.raises(ValueError, match="LS_SOURCE_BINDING_MISMATCH"):
            producer.produce(changed, expected_binding=pinned)


def test_feature_and_assignment_rehash_do_not_bypass_recalculation():
    _, _, classifier = components()
    snapshot = produce(feature_request())
    changed = snapshot.to_dict()
    changed["features"][0]["rop_cover_numerator"] = "999999"
    with pytest.raises(ValueError, match="LS_SOURCE_BINDING_MISMATCH"):
        classifier.classify(LegacyFeatureSnapshot.from_dict(reseal(changed)))
    result = classifier.classify(snapshot)
    changed = result.to_dict()
    changed["items"][0]["profile_id"] = "LARGE"
    with pytest.raises(ValueError, match="LS_SOURCE_BINDING_MISMATCH"):
        classifier.verify(LegacySegmentationResult.from_dict(reseal(changed)))


def test_hashes_are_stable_across_input_order_decimal_context_and_defensive_copies():
    request = feature_request(item_ids=("ITEM-A", "ITEM-B"))
    original = copy.deepcopy(request)
    first = produce(request)
    request["universe"].reverse()
    request["calendar"].reverse()
    request["abc_result"]["assignments"].reverse()
    for source in request["sources"].values():
        source["rows"].reverse()
    with localcontext() as context:
        context.prec, context.rounding = 6, ROUND_DOWN
        second = produce(request)
    assert first.content_hash == second.content_hash
    second.to_dict()["features"][0]["reason_codes"].append("FORGED")
    assert first.to_dict() == second.to_dict()
    with pytest.raises(FrozenInstanceError):
        first._json = "tampered"
    assert original == first.to_dict()["input"]


def test_base_abc_snapshot_is_unchanged_and_legacy_groups_cannot_enter_abc_runtime():
    rules, _, classifier = components()
    request = feature_request()
    abc_before = canonical_json(request["abc_result"])
    result = classifier.classify(produce(request))
    body = result.to_dict()
    neutral = LegacyStyleSegmentationAdapter(classifier).segment(
        result, configuration_hash=body["configuration_hash"]
    )
    assert canonical_json(request["abc_result"]) == abc_before
    assert canonical_json(body["feature_snapshot"]["input"]["abc_result"]) == abc_before
    assert neutral.configuration_hash != request["context"]["base_configuration_hash"]
    with pytest.raises(ValueError, match="LS_SOURCE_BINDING_MISMATCH"):
        LegacyStyleSegmentationAdapter(classifier).segment(result, configuration_hash="e" * 64)
    assignment = replace(neutral.assignments[0], search_group_id="LS1:R3-L3-P1")
    with pytest.raises(ValueError, match="LS_SOURCE_BINDING_MISMATCH"):
        LegacyStyleCandidateRangeAdapter(classifier, rules).resolve(body, assignment=assignment)
    from tests.support.policy_search_fixtures import GOLDEN as SEARCH_GOLDEN

    with pytest.raises(ValueError, match="POLICY_SEARCH_ITEM_UNCLASSIFIED"):
        AbcXyzCandidateRangeAdapter().resolve(
            SEARCH_GOLDEN["search_config"], assignment=neutral.assignments[0]
        )


def test_original_three_zero_history_items_are_not_rescued_or_relabelled():
    request = feature_request(item_ids=("DS_A017518", "DS_A082612", "DS_A082843"))
    for row in request["sources"]["history"]["rows"]:
        row["demand_qty"] = "0"
    for row in request["abc_result"]["assignments"]:
        row.update(
            search_group_id=None,
            status="UNCLASSIFIED",
            reason_code="ZERO_MEAN_DEMAND",
            abc_xyz_class=None,
        )
    _, _, classifier = components()
    result = classifier.classify(produce(request)).to_dict()
    assert len(result["items"]) == 3
    assert all(
        row["status"] == "UNCLASSIFIED"
        and row["search_group_id"] is None
        and row["abc_xyz_class"] is None
        and row["primary_reason"] == "LS_ZERO_MEAN_DEMAND"
        for row in result["items"]
    )


def test_cross_multiplication_does_not_round_an_almost_equal_boundary():
    _, _, classifier = components()
    result = classifier.route_factors(
        rop_cover_numerator="1",
        rop_cover_denominator="2.00000000000000000000000000000000000000000001",
        physical_lead_time_days=7,
        unit_cost_usd="5",
    )
    assert result["factor_group_id"] == "LS1:R1-L1-P1"


@pytest.mark.parametrize(
    "age_date,status", [("2024-12-16", "READY"), ("2024-12-15", "UNCLASSIFIED")]
)
def test_price_freshness_includes_day_91_but_not_92(age_date, status):
    request = feature_request()
    request["sources"]["unit_cost"]["rows"][0]["price_as_of"] = age_date
    assert produce(request).to_dict()["features"][0]["status"] == status


@pytest.mark.parametrize("domain", ["history", "lead_time", "unit_cost"])
def test_absent_dataset_is_preserved_and_does_not_become_a_default_source(domain):
    request = feature_request()
    request["sources"][domain] = None
    value = produce(request).to_dict()
    assert value["features"][0]["primary_reason"] == "LS_MISSING_REQUIRED_FACTOR"
    assert value["input"]["sources"][domain] is None


def test_observed_source_and_equal_cutoff_are_valid_but_do_not_approve_anything():
    request = feature_request()
    for source in request["sources"].values():
        source.update(
            evidence_kind="OBSERVED_PINNED",
            generator=None,
            available_at=request["context"]["input_cutoff"],
        )
    snapshot = produce(request).to_dict()
    assert snapshot["features"][0]["status"] == "READY"
    assert snapshot["feature_snapshot_id"].startswith("LSF-")
    assert "operational_io_eligible" not in snapshot


def test_adjacent_item_exception_intervals_are_not_duplicates():
    request = feature_request()
    request["item_exceptions"] = [
        item_exception(start="2025-03-01", end="2025-03-17", profile="MEDIUM"),
        item_exception(),
    ]
    _, _, classifier = components()
    assert classifier.classify(produce(request)).to_dict()["items"][0]["profile_id"] == "LARGE"


def test_build_binding_and_snapshot_hash_are_verified_not_just_carried():
    _, producer, _ = components()
    request = feature_request()
    request["context"]["reference_calculator_build_hash"] = "e" * 64
    with pytest.raises(ValueError, match="LS_SOURCE_BINDING_MISMATCH"):
        producer.produce(request, expected_binding=feature_input_binding(request))
    value = produce(feature_request()).to_dict()
    value["feature_snapshot_id"] = "LSF-FORGED"
    with pytest.raises(ValueError, match="LS_SOURCE_BINDING_MISMATCH"):
        LegacyFeatureSnapshot.from_dict(value)
    with pytest.raises(ValueError, match="LS_SOURCE_BINDING_MISMATCH"):
        producer.verify(LegacyFeatureSnapshot.from_dict(reseal(value)))


@pytest.mark.parametrize(
    "field,value",
    [("contract_version", "9.0.0"), ("implementation_version", "9.0.0"), ("method_id", "ABC_XYZ")],
)
def test_rehashed_result_contract_identity_changes_are_rejected(field, value):
    _, _, classifier = components()
    result = classifier.classify(produce(feature_request())).to_dict()
    result[field] = value
    with pytest.raises(ValueError, match="LS_SOURCE_BINDING_MISMATCH"):
        classifier.verify(LegacySegmentationResult.from_dict(reseal(result)))


def test_new_feature_group_and_range_hash_fingerprints_after_literal_golden_validation():
    rules, producer, classifier = components()
    request = feature_request()
    snapshot = producer.produce(request, expected_binding=feature_input_binding(request))
    detailed = classifier.classify(snapshot)
    neutral = LegacyStyleSegmentationAdapter(classifier).segment(
        detailed, configuration_hash=detailed.to_dict()["configuration_hash"]
    )
    binding = LegacyStyleCandidateRangeAdapter(classifier, rules).resolve(
        detailed.to_dict(), assignment=neutral.assignments[0]
    )
    assert (
        snapshot.to_dict()["feature_snapshot_id"]
        == "LSF-ebbb0a4563f8a3f1978a552240c113ce1b8a40f2fcb43577ebde4ac8c8b4627b"
    )
    assert (
        snapshot.content_hash == "ded5e36f30c30251daa457b01c35dca940980ab62a79bf199e9b8d92995062c9"
    )
    assert (
        detailed.content_hash == "ac6212397ad399a4f28aff4ab2e0789a730880db7d9c3375c890cbbc837fbdd4"
    )
    assert (
        neutral.content_hash == "c6f0358d4be5d8a08d4738264a20a29d8ea5ebf1b449a4a2a046b7fdfa0f3cab"
    )
    assert (
        binding.content_hash == "3bc064a368f7aa92861f6ec2dcb9a39acfbd26ae940b4cb86d6b30dac8cb763c"
    )


@pytest.mark.parametrize(
    "mutation",
    ["invalid_timestamp", "invalid_id", "invalid_rows", "invalid_hash", "non_json_number"],
)
def test_malformed_source_metadata_uses_closed_run_error_contract(mutation):
    request = feature_request()
    if mutation == "invalid_timestamp":
        request["sources"]["history"]["available_at"] = None
    elif mutation == "invalid_id":
        request["sources"]["history"]["source_id"] = None
    elif mutation == "invalid_rows":
        request["sources"]["history"]["rows"] = {}
    elif mutation == "invalid_hash":
        request["context"]["base_configuration_hash"] = "not-a-hash"
    elif mutation == "non_json_number":
        request["sources"]["unit_cost"]["rows"][0]["unit_cost"] = float("inf")
    with pytest.raises(ValueError, match="^LS_SOURCE_BINDING_MISMATCH$"):
        produce(request)

"""Exact requested candidate sets, deterministic IDs, provenance and replay verification."""

import copy
from itertools import product

import pytest

from dsio_inventory_engine.classify_inventory.candidate_catalog import (
    MODES,
    PARAMETERS,
    OfflineCandidateCatalog,
)
from dsio_inventory_engine.inventory_contracts.values import digest
from tests.support.catalog_search_fixtures import catalog_bundle
from tests.support.legacy_style_fixtures import GOLDEN


@pytest.mark.parametrize(
    "mode,count", [("LEGACY_STYLE", 32), ("ABC_XYZ_SHARED_BUDGET", 32), ("WIDE_REFERENCE", 88)]
)
def test_actual_candidate_parameters_counts_and_semantic_duplicates(mode, count):
    _, producer, catalog = catalog_bundle(mode=mode)
    body = catalog.to_dict()
    candidates = body["items"][0]["candidates"]
    assert len(candidates) == count
    assert len({row["candidate_id"] for row in candidates}) == count
    assert len({row["semantic_policy_hash"] for row in candidates}) == count
    assert all("segment_key" not in row and row["search_group_id"] != "AX" for row in candidates)
    assert all(row["policy_kind"] in {"ROP_FIXED_Q", "PERIODIC_ORDER_UP_TO"} for row in candidates)
    assert body["max_candidates_per_item"] == count
    assert (
        producer.verify(catalog, expected_catalog_hash=catalog.content_hash).content_hash
        == catalog.content_hash
    )


def test_wide_catalog_is_the_exact_union_not_a_256_candidate_superset():
    _, producer, _ = catalog_bundle()
    rules = producer.classifier.rules.to_dict()
    expected = set()
    order = (
        "policy_kinds",
        "rmos_values",
        "emos_values",
        "service_level_values",
        "review_cycle_weeks",
    )
    for profile in rules["candidate_profiles"].values():
        expected |= set(product(*(profile[k] for k in order)))
    _, _, wide = catalog_bundle(mode="WIDE_REFERENCE")
    actual = {
        tuple(row[key] for key in PARAMETERS) for row in wide.to_dict()["items"][0]["candidates"]
    }
    assert actual == expected
    assert len(actual) == GOLDEN["expected_unique_reference_candidates"] == 88
    assert ("ROP_FIXED_Q", "3", "0.25", "0.95", 4) not in actual


@pytest.mark.parametrize(
    "mutation", ["drop", "duplicate", "parameter", "group", "mode", "identity"]
)
def test_rehashed_fake_catalog_is_rejected(mutation):
    _, producer, catalog = catalog_bundle()
    body = catalog.to_dict()
    if mutation == "drop":
        body["items"][0]["candidates"].pop()
    elif mutation == "duplicate":
        body["items"][0]["candidates"].append(copy.deepcopy(body["items"][0]["candidates"][0]))
    elif mutation == "parameter":
        body["items"][0]["candidates"][0]["rmos"] = "12"
    elif mutation == "group":
        body["items"][0]["candidates"][0]["search_group_id"] = "AX"
    elif mutation == "mode":
        body["mode"] = "WIDE_REFERENCE"
    elif mutation == "identity":
        body["contract_version"] = "9.0.0"
    body["content_hash"] = digest(
        {key: value for key, value in body.items() if key != "content_hash"}
    )
    forged = OfflineCandidateCatalog.from_dict(body)
    with pytest.raises(ValueError, match="CATALOG_(REPLAY|CONTRACT)_MISMATCH"):
        producer.verify(forged, expected_catalog_hash=forged.content_hash)


def test_catalog_hash_pin_and_source_pin_cannot_be_replaced():
    _, producer, catalog = catalog_bundle()
    with pytest.raises(ValueError, match="CATALOG_HASH_MISMATCH"):
        producer.verify(catalog, expected_catalog_hash="e" * 64)
    from dsio_inventory_engine.classify_inventory.legacy_style import LegacySegmentationResult

    result = LegacySegmentationResult.from_dict(catalog.to_dict()["legacy_result"])
    with pytest.raises(ValueError, match="CATALOG_SOURCE_HASH_MISMATCH"):
        producer.produce(result, mode="LEGACY_STYLE", expected_result_hash="e" * 64)
    with pytest.raises(ValueError, match="CATALOG_MODE_INVALID"):
        producer.produce(result, mode="RANDOM_POLICY", expected_result_hash=result.content_hash)


@pytest.mark.parametrize("mode", MODES)
def test_missing_feature_keeps_the_item_and_has_no_fabricated_default_candidates(mode):
    def missing(feature):
        feature["sources"]["unit_cost"]["rows"] = []

    _, _, catalog = catalog_bundle(mode=mode, feature_changes=missing)
    item = catalog.to_dict()["items"][0]
    assert item["status"] == "EXCLUDED"
    assert item["candidates"] == []
    assert item["reason_code"] == "LS_MISSING_REQUIRED_FACTOR"

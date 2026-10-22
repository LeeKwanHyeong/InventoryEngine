"""Independent development input assembly; never modifies an existing approved Snapshot."""

import copy

from dsio_inventory_engine.classify_inventory.candidate_catalog import CandidateCatalogProducer
from dsio_inventory_engine.classify_inventory.legacy_features import feature_input_binding
from dsio_inventory_engine.inventory_contracts.segmentation import (
    SearchGroupAssignment,
    SegmentationResult,
)
from tests.support.legacy_style_fixtures import components, feature_request
from tests.support.policy_search_fixtures import GOLDEN, bundle, multi_item_bundle
from math_fixtures import build_math, reseal_math


def run_bundle(*, policy=None, forecast=None, config_values=None, metric_changes=None):
    raw = build_math(
        {
            "id": "offline-catalog-constant",
            "history": "constant",
            "boh": "0",
            "forecast": ["10", "10", "10"] if forecast is None else forecast,
            "profile": {"lookback_weeks": 26, "stddev_ddof": 0},
            "policy": policy or {},
        }
    )
    search = copy.deepcopy(GOLDEN["search_config"])
    search["lookback_weeks"] = 26
    return bundle(raw, search, config_values=config_values, metric_changes=metric_changes)


def feature_from_bundle(parts):
    raw, _, classification = parts
    canonical = raw["recommendation"]["canonical_input"]
    context = canonical["context"]
    math = raw["policy_input"]
    ids = tuple(row["item_id"] for row in canonical["snapshots"]["master"]["rows"])
    request = feature_request(item_ids=ids)
    request["context"].update(
        **{
            key: context[key]
            for key in (
                "company_cd",
                "subs_cd",
                "site_cd",
                "master_as_of_date",
                "master_snapshot_revision",
            )
        },
        decision_yyyyww=context["plan_yyyyww"],
        input_cutoff=context["inventory_cutoff_at"],
        calendar_snapshot_id=canonical["snapshots"]["calendar"]["snapshot_id"],
        base_configuration_hash=classification["config_hash"],
        classification_snapshot_id=classification["classification_snapshot_id"],
        classification_content_hash=classification["content_hash"],
    )
    request["calendar"] = [
        {"yyyyww": row["yyyyww"], "start_date": row["start_date"]}
        for row in sorted(math["calendar"], key=lambda r: r["start_date"])
    ]
    request["universe"] = [
        {"item_id": row["item_id"], "uom": row["uom"]}
        for row in canonical["snapshots"]["master"]["rows"]
    ]
    originals = {row["item_id"]: row for row in classification["items"]}
    request["abc_result"] = SegmentationResult(
        "ABC_XYZ",
        "1.0.0",
        classification["source_content_hash"],
        classification["config_hash"],
        tuple(
            SearchGroupAssignment(
                item,
                originals[item]["segment_key"],
                "ASSIGNED"
                if originals[item]["classification_status"] == "CLASSIFIED"
                else "UNCLASSIFIED",
                originals[item]["unclassified_reason_code"],
                originals[item]["segment_key"],
            )
            for item in ids
        ),
    ).to_dict()
    for source in request["sources"].values():
        source.update(
            scope={key: context[key] for key in ("company_cd", "subs_cd", "site_cd")},
            decision_yyyyww=context["plan_yyyyww"],
            available_at=context["inventory_cutoff_at"],
        )
    request["sources"]["history"].update(
        closed_yyyyww=[row["yyyyww"] for row in request["calendar"]],
        rows=[
            {key: row[key] for key in ("item_id", "yyyyww", "demand_qty")}
            for row in math["history"]
        ],
    )
    request["sources"]["lead_time"]["rows"] = [
        {"item_id": row["item_id"], "lead_time_days": row["lead_time_days"]}
        for row in canonical["snapshots"]["policies"]["rows"]
        if row["effective_from"] <= context["plan_start_date"] < row["effective_to"]
    ]
    for row in request["sources"]["unit_cost"]["rows"]:
        row["price_as_of"] = context["master_as_of_date"]
    return request


def catalog_bundle(*, mode="LEGACY_STYLE", parts=None, feature_changes=None):
    parts = parts or run_bundle()
    feature = feature_from_bundle(parts)
    if feature_changes:
        feature_changes(feature)
    _, producer, classifier = components()
    detailed = classifier.classify(
        producer.produce(feature, expected_binding=feature_input_binding(feature))
    )
    catalogs = CandidateCatalogProducer(classifier)
    catalog = catalogs.produce(detailed, mode=mode, expected_result_hash=detailed.content_hash)
    return parts, catalogs, catalog


def two_item_bundle():
    one, values, _ = run_bundle()
    raw, _, _ = multi_item_bundle()
    raw["policy_input"] = copy.deepcopy(one["policy_input"])
    raw["policy_input"]["context"]["canonical_input_hash"] = raw["recommendation"]["execution"][
        "canonical_input_hash"
    ]
    raw["policy_input"]["history"] += [
        {**row, "item_id": "ITEM-B"} for row in list(raw["policy_input"]["history"])
    ]
    return bundle(reseal_math(raw), values["policy_search"], config_values=values)

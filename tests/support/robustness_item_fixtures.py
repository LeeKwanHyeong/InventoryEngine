"""Literal 2055 hand inputs. No official paths, seeds, study or DB authority.

FSN uses a separate manual movement record; uncensored gross demand is not
fulfilled movement. These synthetic test records are never operational evidence.
"""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import json

from math_fixtures import reseal_math
from fixtures import reseal
from dsio_inventory_engine.classify_inventory.application import (
    CONFIG_SCHEMA_V21_ID,
    CONFIG_SCHEMA_V21_VERSION,
    CONFIG_SCHEMA_V21_HASH,
    InventoryScope,
    ItemMetric,
    build_snapshot,
    config_hash,
)
from dsio_inventory_engine.classify_inventory.candidate_catalog import CandidateCatalogProducer
from dsio_inventory_engine.classify_inventory.legacy_features import feature_input_binding
from dsio_inventory_engine.evaluate_replenishment.robustness.item_projection import (
    PrepareItemCatalogUseCase,
)
from dsio_inventory_engine.inventory_contracts.effective_policy_v2 import (
    EFFECTIVE_POLICY_V2_FIELDS,
    effective_policy_content_hash,
)
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.segmentation import (
    SearchGroupAssignment,
    SegmentationResult,
)
from dsio_inventory_engine.inventory_contracts.values import digest
from tests.support.catalog_search_fixtures import feature_from_bundle
from tests.support.legacy_style_fixtures import components
from tests.support.policy_search_fixtures import GOLDEN
from tests.support.robustness_source_fixtures import W0, source_case, mathematical_case
from tests.unit.test_seven_axis_segmentation import inputs, v2_values, v2_metric

DEV = DeploymentScope("DSE", "DEVELOPMENT")


def item_case(
    *,
    mode="LEGACY_STYLE",
    gate="REVIEW",
    max_target="1000",
    capacity=None,
    unclassified_other=False,
):
    source_parts, math = mathematical_case(source_case())
    source_request, source, _ = source_parts
    raw = math.to_dict()
    if capacity is not None:
        canonical = raw["recommendation"]["canonical_input"]
        canonical["snapshots"]["policies"]["rows"][0]["physical_max_capacity"] = capacity
        canonical = reseal(canonical)
        raw["recommendation"]["canonical_input"] = canonical
        canonical_hash = CanonicalInputRequest.from_dict(canonical).input_hash
        raw["recommendation"]["execution"]["canonical_input_hash"] = canonical_hash
        raw["policy_input"]["context"]["canonical_input_hash"] = canonical_hash
    ctx = raw["recommendation"]["canonical_input"]["context"]
    values = v2_values()
    values["policy_search"] = deepcopy(GOLDEN["search_config"])
    values["policy_search"]["lookback_weeks"] = 26
    demands = {"ITEM-A": 1, "ITEM-B": 10, "ITEM-C": 5}
    metrics = tuple(
        v2_metric(
            ItemMetric(
                item, 52, 0, 52, Decimal(qty * 52), Decimal(qty * qty * 52), Decimal(qty * 52 * 2)
            ),
            fsn_source_status="UNVERIFIED" if gate == "BLOCK" else "VERIFIED",
            positive_week_count=52 if gate == "ALLOW" else 1,
            last_positive_demand_yyyyww=(
                W0 - timedelta(weeks=1 if gate == "ALLOW" else 27)
            ).strftime("%G%V"),
        )
        for item, qty in demands.items()
    )
    if unclassified_other:
        metrics = tuple(
            replace(m, abc_source_row_count=0, abc_observed_week_count=0, abc_revenue=Decimal(0))
            if m.item_id == "ITEM-C"
            else m
            for m in metrics
        )
    parent = build_snapshot(
        replace(
            inputs(),
            scope=InventoryScope("project-a", "DSE", "C100", "V101", "V101"),
            config_revision_id=ctx["configuration_revision"],
            config_values=values,
            config_hash=config_hash(values),
            config_schema_id=CONFIG_SCHEMA_V21_ID,
            config_schema_version=CONFIG_SCHEMA_V21_VERSION,
            config_schema_hash=CONFIG_SCHEMA_V21_HASH,
            actual_yyyyww=(W0 - timedelta(weeks=1)).strftime("%G%V"),
            decision_yyyyww=W0.strftime("%G%V"),
            actual_close_window_start_yyyyww=(W0 - timedelta(weeks=52)).strftime("%G%V"),
            actual_close_history_hash=digest({"manual_movement": gate, "items": demands}),
            metrics=metrics,
        )
    )
    parent = json.loads(json.dumps(parent))
    original = next(r for r in parent["items"] if r["item_id"] == "ITEM-A")
    projection = {"item_id": "ITEM-A", **{k: original[k] for k in EFFECTIVE_POLICY_V2_FIELDS}}
    ex = raw["recommendation"]["execution"]
    ex["effective_item_policies"] = [projection]
    ex["effective_policy_binding"].update(
        classification_snapshot_id=parent["classification_snapshot_id"],
        classification_config_hash=parent["config_hash"],
        effective_policy_content_hash=effective_policy_content_hash([projection]),
    )
    ex["item_controls"][0]["max_target_qty"] = max_target
    raw = reseal_math(raw)
    feature = feature_from_bundle((raw, values, parent))
    feature["universe"] = [{"item_id": item, "uom": "EA"} for item in demands]
    feature["abc_result"] = SegmentationResult(
        "ABC_XYZ",
        "1.0.0",
        parent["source_content_hash"],
        parent["config_hash"],
        tuple(
            SearchGroupAssignment(
                r["item_id"],
                r["segment_key"],
                "ASSIGNED" if r["classification_status"] == "CLASSIFIED" else "UNCLASSIFIED",
                r["unclassified_reason_code"],
                r["segment_key"],
            )
            for r in parent["items"]
        ),
    ).to_dict()
    feature["sources"]["history"]["rows"] = [
        {"item_id": item, "yyyyww": b["yyyyww"], "demand_qty": str(qty)}
        for item, qty in demands.items()
        for b in feature["calendar"]
    ]
    feature["sources"]["lead_time"]["rows"] = [
        {"item_id": item, "lead_time_days": lead}
        for item, lead in (("ITEM-A", 35), ("ITEM-B", 7), ("ITEM-C", 49))
    ]
    feature["sources"]["unit_cost"]["rows"] = [
        {"item_id": item, "unit_cost": "2", "currency": "USD", "price_as_of": W0.isoformat()}
        for item in demands
    ]
    _, producer, classifier = components()
    detailed = classifier.classify(
        producer.produce(feature, expected_binding=feature_input_binding(feature))
    )
    catalogs = CandidateCatalogProducer(classifier)
    catalog = catalogs.produce(detailed, mode=mode, expected_result_hash=detailed.content_hash)
    return {
        "request": MathematicalPolicyRequest.from_dict(raw),
        "parent_classification": parent,
        "expected_parent_hash": parent["content_hash"],
        "catalog": catalog,
        "expected_catalog_hash": catalog.content_hash,
        "config_values": values,
        "source": source,
        "source_request": source_request,
        "expected_source_hash": source["content_hash"],
        "producer": catalogs,
    }


def prepare_case(case):
    return PrepareItemCatalogUseCase(DEV, case["producer"]).execute(
        **{k: v for k, v in case.items() if k != "producer"}
    )

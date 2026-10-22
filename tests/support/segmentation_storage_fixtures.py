"""New explicitly pinned, synthetic fixture. Never upgrades an existing Snapshot."""

from dataclasses import replace

from dsio_inventory_engine.classify_inventory.application import (
    build_snapshot,
    config_hash,
    CONFIG_SCHEMA_V2_ID,
    CONFIG_SCHEMA_V2_VERSION,
    CONFIG_SCHEMA_V2_HASH,
)
from dsio_inventory_engine.classify_inventory.legacy_features import feature_input_binding
from dsio_inventory_engine.inventory_contracts.segmentation import (
    SearchGroupAssignment,
    SegmentationResult,
)
from dsio_inventory_engine.inventory_evidence.segmentation_analysis import analysis_binding
from tests.support.legacy_style_fixtures import components, feature_request
from tests.unit.test_seven_axis_segmentation import inputs, v2_values, v2_metric


def storage_input(*, feature_change=None, item_count=3, gate="BLOCK"):
    values = v2_values()
    values["policy_overlays"]["fsn_order_action"]["N"] = gate
    base = inputs()
    metrics = tuple(
        v2_metric(replace(base.metrics[0], item_id=f"ITEM-{i:05d}")) for i in range(item_count)
    )
    snapshot = build_snapshot(
        replace(
            base,
            config_values=values,
            config_hash=config_hash(values),
            config_schema_id=CONFIG_SCHEMA_V2_ID,
            config_schema_version=CONFIG_SCHEMA_V2_VERSION,
            config_schema_hash=CONFIG_SCHEMA_V2_HASH,
            metrics=metrics,
            actual_yyyyww="202511",
            decision_yyyyww="202512",
            actual_close_window_start_yyyyww="202412",
        )
    )
    feature = feature_request(item_ids=tuple(r.item_id for r in metrics))
    feature["context"].update(
        classification_snapshot_id=snapshot["classification_snapshot_id"],
        classification_content_hash=snapshot["content_hash"],
        base_configuration_hash=snapshot["config_hash"],
    )
    feature["abc_result"] = SegmentationResult(
        "ABC_XYZ",
        "1.0.0",
        snapshot["source_content_hash"],
        snapshot["config_hash"],
        tuple(
            SearchGroupAssignment(
                r["item_id"], r["segment_key"], "ASSIGNED", None, r["segment_key"]
            )
            for r in snapshot["items"]
        ),
    ).to_dict()
    if feature_change:
        feature_change(feature)
    _, producer, classifier = components()
    result = classifier.classify(
        producer.produce(feature, expected_binding=feature_input_binding(feature))
    )
    return classifier, result, snapshot, analysis_binding(result, snapshot)

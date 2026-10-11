"""Development-only literal inputs; expectations are in golden_policy_search.json."""

import copy
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from math_fixtures import build_math, GOLDEN_MATH
from tests.unit.test_seven_axis_segmentation import inputs, v2_values, v2_metric
from dsio_inventory_engine.classify_inventory.application import (
    CONFIG_SCHEMA_V21_ID,
    CONFIG_SCHEMA_V21_VERSION,
    CONFIG_SCHEMA_V21_HASH,
    InventoryScope,
    ItemMetric,
    build_snapshot,
    config_hash,
)

GOLDEN = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures/golden_policy_search.json").read_text()
)


def bundle(raw=None, search=None, config_values=None, metric_changes=None):
    raw = raw or build_math(GOLDEN_MATH["cases"][0])
    ctx = raw["recommendation"]["canonical_input"]["context"]
    config = copy.deepcopy(config_values) if config_values is not None else v2_values()
    config["policy_search"] = copy.deepcopy(search or GOLDEN["search_config"])
    metrics = tuple(
        v2_metric(
            ItemMetric(row["item_id"], 52, 0, 52, Decimal("520"), Decimal("5200"), Decimal("800")),
            **(metric_changes or {}),
        )
        for row in raw["recommendation"]["canonical_input"]["snapshots"]["master"]["rows"]
    )
    snapshot = build_snapshot(
        replace(
            inputs(),
            scope=InventoryScope(
                "project-a", ctx["company_cd"], ctx["subs_cd"], ctx["site_cd"], ctx["site_cd"]
            ),
            config_revision_id=ctx["configuration_revision"],
            config_values=config,
            config_hash=config_hash(config),
            config_schema_id=CONFIG_SCHEMA_V21_ID,
            config_schema_version=CONFIG_SCHEMA_V21_VERSION,
            config_schema_hash=CONFIG_SCHEMA_V21_HASH,
            metrics=metrics,
        )
    )
    return raw, config, snapshot


def multi_item_bundle(*, second_forecast=None):
    """Two independent inventory positions sharing one sealed Source and search configuration."""
    from fixtures import build_request
    from strategy_fixtures import request
    from math_fixtures import reseal_math

    raw = build_math(GOLDEN_MATH["cases"][0])
    source = build_request(
        {
            "id": "two-item-policy-search",
            "items": [
                {"id": "ITEM-A", "boh": "10", "forecast": ["10", "10", "10"]},
                {"id": "ITEM-B", "boh": "100", "forecast": second_forecast or ["10", "10", "10"]},
            ],
        }
    )
    recommendation = request(source).to_dict()
    recommendation["execution"].update(
        contract_version="1.1.0",
        strategy=raw["recommendation"]["execution"]["strategy"],
    )
    raw["recommendation"] = recommendation
    raw["policy_input"]["context"]["canonical_input_hash"] = recommendation["execution"][
        "canonical_input_hash"
    ]
    raw["policy_input"]["history"] += [
        {**row, "item_id": "ITEM-B"} for row in raw["policy_input"]["history"]
    ]
    search = copy.deepcopy(GOLDEN["search_config"])
    profile = search["group_profiles"][0]
    search["group_profiles"] = [
        {**profile, "segment_key": segment}
        for segment in ("AX", "AY", "AZ", "BX", "BY", "BZ", "CX", "CY", "CZ")
    ]
    return bundle(reseal_math(raw), search)

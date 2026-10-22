"""Pre-extraction fingerprints: never regenerate expectations from the new classes."""

from dataclasses import replace

import pytest

from dsio_inventory_engine.classify_inventory.application import (
    CONFIG_SCHEMA_V2_HASH,
    CONFIG_SCHEMA_V2_ID,
    CONFIG_SCHEMA_V2_VERSION,
    build_snapshot,
    config_hash,
)
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.recommend_replenishment.application.policy_search import (
    RunPolicySearchUseCase,
)
from dsio_inventory_engine.recommend_replenishment.application.policy_selection import (
    RunPolicySelectionUseCase,
)
from tests.support.policy_search_fixtures import bundle, multi_item_bundle
from tests.unit.test_classification_snapshot import inputs as v1_inputs
from tests.unit.test_seven_axis_segmentation import inputs, v2_metric, v2_values


@pytest.mark.parametrize(
    "version,expected",
    [
        (
            "1.1.0",
            (
                "b2314be2d1662c4abf2bc6f5e6dfe4033d41048d27decbe2fe8b3566691ce253",
                "fa68b90d53aaa4b3b94423c8fc57843bb1ebad53efa5f831d18f924258aeb1f3",
                "6e1d122e4b414e97cd1375e9404090deff44edadc03b2c6df0d64f91a4627986",
                "a660fdcd-d474-51c1-b7b6-2b6cc7c7eae8",
                "70a37a4f9b859050f7ac091981371527a19ceb317557f96b45e1dc61892a71cc",
            ),
        ),
        (
            "2.0.0",
            (
                "efef9510a72db49e77fce1c23eb05c14a46bebfcaa3fa6654d5cccf7fba285f4",
                "144016d43d6c6d57b15691ade4f3b97d44fffcf4d70c08f085dab18617b021f7",
                "1e9a14eb46a87c4205782080f20fd1d3badbc832c7e1b742adb55b5d6cb084d0",
                "f1b003db-64be-5cf4-8962-a6af505369c9",
                "951b09ce6e9ad93f70c6f6689b29260aaa4adca7564d349663f519995cd5d2e0",
            ),
        ),
    ],
)
def test_published_snapshot_and_effective_policy_fingerprints(version, expected):
    value = v1_inputs()
    if version == "2.0.0":
        base = inputs()
        values = v2_values()
        value = replace(
            base,
            config_values=values,
            config_hash=config_hash(values),
            config_schema_id=CONFIG_SCHEMA_V2_ID,
            config_schema_version=CONFIG_SCHEMA_V2_VERSION,
            config_schema_hash=CONFIG_SCHEMA_V2_HASH,
            metrics=tuple(v2_metric(row) for row in base.metrics),
        )
    snapshot = build_snapshot(value)
    keys = (
        "content_hash",
        "source_content_hash",
        "config_hash",
        "classification_snapshot_id",
        "effective_policy_content_hash",
    )
    assert tuple(snapshot[key] for key in keys) == expected


@pytest.mark.parametrize(
    "factory,expected",
    [
        (
            bundle,
            (
                "a42c083bd1d9268535b8271babd678c9d36a5f93d2582163c12ee4f91814b47d",
                "07a3c6989a7f89999142e20b1248b8929f29fa0cd4dd38d2e968e0dd1b2a6133",
                "6d2425d38ac012e416d1bdfaf378f9f66d823c904f5b55adb2a49cfdb31b7547",
                "5e1808cb4bde77ee48feb065679065614f3513bcb72326a3882d3d59cd45fc75",
                "1dc99a92df87938682b845042f4d48f9a23ae731bf90f7ba97583736b4a9ae17",
            ),
        ),
        (
            multi_item_bundle,
            (
                "c7bb79105852b0c143f4126765db8de4b963d398156000cdc352df0b7b6975ef",
                "3104f80fb925645adcf44bcaf80219a1a58d92dbdca5f4dbdedc57bc83faa5ab",
                "73aab2b400ad93de50434118bfd2fb386c565465ad20445d448ec1eb908c81bd",
                "9b09664fc2046c6e456dd0f2fe725779f76c9ca804e9615f086c4b03f2b5ee8c",
                "0dbc093fb1dc07cf3d5185de791d7e8b7746b06dbb08576f7922804f4317c445",
            ),
        ),
    ],
)
def test_config_21_psi_search_and_selection_fingerprints(factory, expected):
    raw, config, snapshot = factory()
    deployment = DeploymentScope("DSE", "DEVELOPMENT")
    result = RunPolicySearchUseCase(deployment).execute(
        MathematicalPolicyRequest.from_dict(raw),
        config_values=config,
        classification_snapshot=snapshot,
    )
    selection = RunPolicySelectionUseCase(deployment).execute(result)
    assert (
        snapshot["content_hash"],
        snapshot["effective_policy_content_hash"],
        result["search_config_hash"],
        result["content_hash"],
        selection["content_hash"],
    ) == expected

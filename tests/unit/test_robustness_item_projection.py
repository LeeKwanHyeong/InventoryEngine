"""Parent identity and complete Catalog checks; no PSI or official study execution."""

from copy import deepcopy
import hashlib
import json
import uuid

import pytest

from math_fixtures import reseal_math
from dsio_inventory_engine.classify_inventory.candidate_catalog import OfflineCandidateCatalog
from dsio_inventory_engine.evaluate_replenishment.robustness.item_projection import (
    PrepareItemCatalogUseCase,
)
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from tests.support.robustness_item_fixtures import DEV, item_case, prepare_case
from tests.support.legacy_style_fixtures import components
from dsio_inventory_engine.classify_inventory.legacy_features import feature_input_binding
from dsio_inventory_engine.classify_inventory.application import SNAPSHOT_NAMESPACE
from dsio_inventory_engine.inventory_contracts.effective_policy_v2 import (
    effective_policy_content_hash,
)


@pytest.mark.parametrize(
    "mode,count", [("ABC_XYZ_SHARED_BUDGET", 32), ("LEGACY_STYLE", 32), ("WIDE_REFERENCE", 88)]
)
def test_full_parent_and_complete_catalog_are_preserved_without_reclassification(mode, count):
    case = item_case(mode=mode)
    before = deepcopy(
        (case["parent_classification"], case["catalog"].to_dict(), case["request"].to_dict())
    )
    assembly = prepare_case(case)
    assert assembly["parent_classification"] == before[0]
    assert assembly["candidate_catalog"] == before[1]
    assert len(assembly["parent_classification"]["items"]) == 3
    assert len(assembly["complete_candidates"]) == count
    assert assembly["observation_work_bound"] == 2145 * count <= 200_000
    projection = assembly["item_projection"]
    assert projection["parent_item_ids"] == ["ITEM-A", "ITEM-B", "ITEM-C"]
    assert projection["parent_classification_hash"] == before[0]["content_hash"]
    # ITEM-A alone would be AX. Its parent-universe grade remains BX.
    assert projection["original_item"]["segment_key"] == "BX"
    assert projection["effective_item_policy"]["effective_order_action"] == "REVIEW"
    assert assembly["mathematical_request"] == before[2]
    assert (
        case["parent_classification"],
        case["catalog"].to_dict(),
        case["request"].to_dict(),
    ) == before
    PrepareItemCatalogUseCase(DEV, case["producer"]).verify(
        assembly, expected_assembly_hash=assembly["content_hash"]
    )


def test_immutable_input_and_original_policy_do_not_depend_on_catalog_mode():
    assemblies = [
        prepare_case(item_case(mode=mode))
        for mode in ("ABC_XYZ_SHARED_BUDGET", "LEGACY_STYLE", "WIDE_REFERENCE")
    ]
    assert len({a["evaluation_binding"]["immutable_input_hash"] for a in assemblies}) == 1
    assert len({a["item_projection"]["content_hash"] for a in assemblies}) == 1
    assert len({a["parent_classification"]["content_hash"] for a in assemblies}) == 1
    assert len({a["evaluation_binding"]["catalog_hash"] for a in assemblies}) == 3


def test_physical_attempt_id_does_not_change_the_semantic_input_binding():
    case = item_case()
    first = prepare_case(case)
    raw = case["request"].to_dict()
    raw["recommendation"]["canonical_input"]["context"]["engine_run_id"] = "HAND-RETRY-ATTEMPT"
    case["request"] = MathematicalPolicyRequest.from_dict(raw)
    second = prepare_case(case)
    assert first["content_hash"] != second["content_hash"]
    assert first["evaluation_binding"] == second["evaluation_binding"]
    assert first["item_projection"] == second["item_projection"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda c: c["parent_classification"]["items"].pop(),
        lambda c: c["parent_classification"].update(classification_snapshot_id="OTHER-PARENT"),
        lambda c: c.update(expected_parent_hash="f" * 64),
        lambda c: c.update(expected_catalog_hash="f" * 64),
        lambda c: c["config_values"]["segmentation"]["fsn"].update(enabled=False),
        lambda c: c["source"]["w0_position"].update(available_qty="999"),
    ],
)
def test_bad_parent_config_catalog_or_source_is_not_treated_as_a_valid_projection(mutation):
    case = item_case()
    mutation(case)
    with pytest.raises(InventoryInputError):
        prepare_case(case)


@pytest.mark.parametrize(
    "key,value", [("lookback_weeks", 13), ("stddev_ddof", 1), ("allow_legacy_fallback", True)]
)
def test_other_history_profiles_are_not_silently_used_in_the_frozen_comparison(key, value):
    case = item_case()
    raw = case["request"].to_dict()
    raw["policy_input"]["profile"][key] = value
    case["request"] = MathematicalPolicyRequest.from_dict(reseal_math(raw))
    with pytest.raises(InventoryInputError):
        prepare_case(case)


def test_same_parent_id_cannot_be_attached_to_trimmed_or_resealed_items():
    case = item_case()
    parent = case["parent_classification"]
    parent["items"] = parent["items"][:1]
    parent["content_hash"] = digest(
        {k: v for k, v in parent.items() if k not in {"classification_snapshot_id", "content_hash"}}
    )
    case["expected_parent_hash"] = parent["content_hash"]
    with pytest.raises(InventoryInputError, match="ITEM_PARENT_ID_MISMATCH"):
        prepare_case(case)


def test_missing_selected_item_is_a_closed_input_error_not_an_uncaught_iteration_error():
    case = item_case()
    parent = case["parent_classification"]
    parent["items"] = [r for r in parent["items"] if r["item_id"] != "ITEM-A"]
    parent["effective_policy_content_hash"] = effective_policy_content_hash(parent["items"])
    unsigned = {
        k: v for k, v in parent.items() if k not in {"classification_snapshot_id", "content_hash"}
    }
    parent["content_hash"] = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    parent["classification_snapshot_id"] = str(
        uuid.uuid5(SNAPSHOT_NAMESPACE, parent["content_hash"])
    )
    case["expected_parent_hash"] = parent["content_hash"]
    with pytest.raises(InventoryInputError, match="ITEM_PARENT_ITEM_MISSING"):
        prepare_case(case)


def test_json_type_changes_cannot_reuse_the_original_assembly_seal():
    case = item_case()
    assembly = prepare_case(case)
    assembly["automatic_publish_allowed"] = 0
    with pytest.raises(InventoryInputError, match="ITEM_ASSEMBLY_HASH_MISMATCH"):
        PrepareItemCatalogUseCase(DEV, case["producer"]).verify(
            assembly, expected_assembly_hash=assembly["content_hash"]
        )


def test_resealed_sixteen_candidate_catalog_cannot_replace_the_complete_thirty_two():
    case = item_case()
    raw = case["catalog"].to_dict()
    next(r for r in raw["items"] if r["item_id"] == "ITEM-A")["candidates"] = next(
        r for r in raw["items"] if r["item_id"] == "ITEM-A"
    )["candidates"][:16]
    raw["content_hash"] = digest({k: v for k, v in raw.items() if k != "content_hash"})
    case.update(
        catalog=OfflineCandidateCatalog.from_dict(raw), expected_catalog_hash=raw["content_hash"]
    )
    with pytest.raises(InventoryInputError, match="CATALOG_REPLAY_MISMATCH"):
        prepare_case(case)


@pytest.mark.parametrize(
    "kind,error",
    [
        ("history", "ITEM_HISTORY_SOURCE_MISMATCH"),
        ("parent", "ITEM_CATALOG_PARENT_MISMATCH"),
        ("lead_time", "ITEM_CATALOG_PHYSICAL_LEAD_TIME_MISMATCH"),
        ("abc", "ITEM_CATALOG_ABC_BINDING_MISMATCH"),
    ],
)
def test_genuinely_rebuilt_catalog_still_must_match_exact_parent_and_source(kind, error):
    case = item_case()
    feature = deepcopy(case["catalog"].to_dict()["legacy_result"]["feature_snapshot"]["input"])
    if kind == "history":
        for row in feature["sources"]["history"]["rows"]:
            if row["item_id"] == "ITEM-A":
                row["demand_qty"] = "2"
    elif kind == "parent":
        feature["context"]["classification_snapshot_id"] = "OTHER-PARENT"
    elif kind == "lead_time":
        next(r for r in feature["sources"]["lead_time"]["rows"] if r["item_id"] == "ITEM-A")[
            "lead_time_days"
        ] = 7
    else:
        feature["abc_result"]["source_content_hash"] = "e" * 64
    _, producer, classifier = components()
    detailed = classifier.classify(
        producer.produce(feature, expected_binding=feature_input_binding(feature))
    )
    catalog = case["producer"].produce(
        detailed, mode="LEGACY_STYLE", expected_result_hash=detailed.content_hash
    )
    case.update(catalog=catalog, expected_catalog_hash=catalog.content_hash)
    with pytest.raises(InventoryInputError, match=error):
        prepare_case(case)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda a: a["complete_candidates"].pop(),
        lambda a: a["history_statistics"].update(mean_weekly_demand="999"),
        lambda a: a["item_projection"]["original_item"].update(segment_key="AX"),
        lambda a: a["candidate_recommendation"]["execution"]["item_controls"][0].update(
            max_order_qty="9999"
        ),
        lambda a: a["evaluation_binding"]["unit_price"].update(unit_price="999"),
        lambda a: a.update(automatic_publish_allowed=True),
    ],
)
def test_resealed_assembly_cannot_forge_candidates_stats_policy_control_or_price(mutation):
    case = item_case()
    assembly = prepare_case(case)
    mutation(assembly)
    assembly["content_hash"] = digest({k: v for k, v in assembly.items() if k != "content_hash"})
    with pytest.raises(InventoryInputError, match="ITEM_ASSEMBLY_REPLAY_MISMATCH"):
        PrepareItemCatalogUseCase(DEV, case["producer"]).verify(
            assembly, expected_assembly_hash=assembly["content_hash"]
        )

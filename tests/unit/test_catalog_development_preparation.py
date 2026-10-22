import copy
from unittest.mock import patch

import pytest

from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.development_universe import (
    seal_development_universe,
    scoped_effective_policies,
)
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.prepare_inventory.application.catalog_development import (
    prepare_development_catalog_inputs,
)
from dsio_inventory_engine.prepare_inventory.application.inventory_input import (
    PrepareInventoryInputUseCase,
)
from tests.support.catalog_development_fixtures import preparation_case


def test_real_catalog_preparation_preserves_all_parent_grades_and_gates_without_psi():
    args = preparation_case()
    before = copy.deepcopy(args)
    with (
        patch(
            "dsio_inventory_engine.simulate_inventory.application.baseline.RunPsiSimulationUseCase.execute",
            side_effect=AssertionError("PSI forbidden"),
        ),
        patch("socket.socket.connect", side_effect=AssertionError("network forbidden")),
    ):
        result = prepare_development_catalog_inputs(**args)
    assert args == before
    parent = args["classification_snapshot"]
    derived = result["classification_snapshot"]
    assert len(derived["items"]) == len(parent["items"]) == 2
    assert derived["effective_policy_content_hash"] == parent["effective_policy_content_hash"]
    for old, new in zip(parent["items"], derived["items"], strict=True):
        for key in (
            "axis_results",
            "segment_key",
            "effective_policy_hash",
            "effective_order_action",
            "recommendation_calculation_allowed",
            "automatic_order_allowed",
            "automatic_publish_allowed",
        ):
            assert old[key] == new[key]
    raw = result["mathematical_request"]
    canonical = CanonicalInputRequest.from_dict(raw["recommendation"]["canonical_input"])
    assert (
        canonical.to_dict()["snapshots"]["master"]
        == args["mathematical_request"]["recommendation"]["canonical_input"]["snapshots"]["master"]
    )
    assert canonical.to_dict()["development_universe"]["exclusions"] == [
        {"item_id": "ITEM-B", "reason": "EXCLUDED_DEVELOPMENT_SAMPLE"}
    ]
    prepared = PrepareInventoryInputUseCase(args["deployment"]).execute(canonical).to_dict()
    assert [r["item_id"] for r in prepared["master"]] == ["ITEM-A"]
    assert raw["policy_input"]["history_row_count"] == 26
    assert result["config_values"]["segmentation"] == args["config_values"]["segmentation"]
    assert result["config_values"]["policy_matrix"] == args["config_values"]["policy_matrix"]
    assert result["config_values"]["policy_overlays"] == args["config_values"]["policy_overlays"]
    assert {m: len(c["items"][0]["candidates"]) for m, c in result["catalogs"].items()} == {
        "ABC_XYZ_SHARED_BUDGET": 32,
        "LEGACY_STYLE": 32,
        "WIDE_REFERENCE": 88,
    }
    assert not result["database_write"] and not result["runtime_claimed"]
    assert prepare_development_catalog_inputs(**args) == result


@pytest.mark.parametrize(
    "change,code",
    [
        ("missing", "HISTORY_COVERAGE"),
        ("duplicate", "HISTORY_COVERAGE"),
        ("future", "HISTORY_COVERAGE"),
        ("wrong_scope", "HISTORY_SCOPE"),
        ("future_available", "FUTURE_POLICY_INFORMATION"),
        ("missing_cost", "COST_REQUIRED"),
        ("bad_parent", "PARENT_HASH"),
        ("duplicate_item", "SAMPLE_INVALID"),
        ("orphan_item", "SAMPLE_INVALID"),
        ("production", "DEVELOPMENT_ONLY"),
    ],
)
def test_preparation_rejects_missing_future_tampered_or_operational_inputs(change, code):
    args = preparation_case()
    rows = args["history_source"]["rows"]
    if change == "missing":
        rows.pop(0)
    elif change == "duplicate":
        rows[1] = copy.deepcopy(rows[0])
    elif change == "future":
        rows[0]["yyyyww"] = args["mathematical_request"]["recommendation"]["canonical_input"][
            "context"
        ]["plan_yyyyww"]
    elif change == "wrong_scope":
        args["history_source"]["scope"]["site_cd"] = "OTHER"
    elif change == "future_available":
        args["history_source"]["available_at"] = "2099-01-01T00:00:00Z"
    elif change == "missing_cost":
        args["cost_assumptions"] = None
    elif change == "bad_parent":
        args["classification_snapshot"]["items"][0]["segment_key"] = "CZ"
    elif change == "duplicate_item":
        args["item_ids"] *= 2
    elif change == "orphan_item":
        args["item_ids"] = ["ABSENT"]
    elif change == "production":
        args["deployment"] = DeploymentScope("DSE", "PRODUCTION")
    with pytest.raises(ValueError, match=code):
        prepare_development_catalog_inputs(**args)


def test_sample_cannot_be_silently_read_as_an_older_universe_contract():
    case = prepare_development_catalog_inputs(**preparation_case())
    scope = case["mathematical_request"]["recommendation"]["canonical_input"][
        "development_universe"
    ]
    for version in ("v1", "v2"):
        old = {k: v for k, v in scope.items() if k != "content_hash"}
        old["contract_id"] = "io-development-calculation-universe-" + version
        with pytest.raises(ValueError):
            seal_development_universe(old)


def test_bounded_execution_context_and_revision_preserve_parent_gates():
    args = preparation_case()
    parent = copy.deepcopy(args["classification_snapshot"])
    context = args["mathematical_request"]["recommendation"]["canonical_input"]["context"]
    result = prepare_development_catalog_inputs(
        **args,
        configuration_revision_id="aafc4f40-b03a-4c75-a154-17bd5520738f",
        execution_context={
            k: context[k]
            for k in (
                "engine_run_id",
                "planning_cycle_id",
                "planning_cycle_revision_id",
                "cycle_site_execution_id",
            )
        },
        catalog_modes=("LEGACY_STYLE",),
    )
    assert set(result["catalogs"]) == {"LEGACY_STYLE"}
    assert result["config_values"]["policy_search"]["max_candidates_per_item"] == 32
    assert (
        result["classification_snapshot"]["config_revision_id"]
        == "aafc4f40-b03a-4c75-a154-17bd5520738f"
    )
    assert (
        result["classification_snapshot"]["effective_policy_content_hash"]
        == parent["effective_policy_content_hash"]
    )
    for old, new in zip(parent["items"], result["classification_snapshot"]["items"], strict=True):
        for key in (
            "axis_results",
            "segment_key",
            "effective_policy_hash",
            "effective_order_action",
            "automatic_publish_allowed",
            "automatic_order_allowed",
        ):
            assert old[key] == new[key]
    with pytest.raises(ValueError, match="DEV_CATALOG_MODES"):
        prepare_development_catalog_inputs(**args, catalog_modes=("UNKNOWN",))
    with pytest.raises(ValueError, match="DEV_CATALOG_EXECUTION_CONTEXT"):
        prepare_development_catalog_inputs(**args, execution_context={"engine_run_id": "bad"})


def test_sample_action_evidence_requires_the_sealed_universe():
    from dsio_inventory_engine.recommend_replenishment.application.run import (
        apply_effective_policy_controls,
    )
    from dsio_inventory_engine.run_inventory.result_artifacts import seal_action_observation_source

    case = preparation_case()
    result = prepare_development_catalog_inputs(**case, catalog_modes=("LEGACY_STYLE",))
    rec = result["mathematical_request"]["recommendation"]
    canonical = CanonicalInputRequest.from_dict(rec["canonical_input"])
    prepared = PrepareInventoryInputUseCase(case["deployment"]).execute(canonical).to_dict()
    admissions = apply_effective_policy_controls(
        prepared, rec["execution"], canonical_input=canonical
    )
    args = dict(
        prepared_input=prepared,
        execution=rec["execution"],
        observation_input_hash=canonical.input_hash,
        admissions_by_item=admissions,
    )
    with pytest.raises(ValueError, match="ACTION_EFFECTIVE_POLICY_ADMISSION_UNIVERSE_MISMATCH"):
        seal_action_observation_source(**args)
    source = seal_action_observation_source(**args, canonical_input=rec["canonical_input"])
    assert [r["item_id"] for r in source["effective_item_policies"]] == ["ITEM-A"]
    bad = copy.deepcopy(rec["canonical_input"])
    bad["development_universe"]["included_item_ids"] = ["ITEM-B"]
    with pytest.raises(ValueError):
        seal_action_observation_source(**args, canonical_input=bad)


def test_selected_order_binding_uses_the_same_explicit_development_universe():
    from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
    from dsio_inventory_engine.inventory_contracts.order_planning import (
        SelectedPolicyOrderPlanBinding,
    )
    from dsio_inventory_engine.recommend_replenishment.application.policy_search import (
        RunPolicySearchUseCase,
    )
    from dsio_inventory_engine.recommend_replenishment.application.policy_selection import (
        RunPolicySelectionUseCase,
    )

    case = preparation_case()
    prepared = prepare_development_catalog_inputs(**case, catalog_modes=("LEGACY_STYLE",))
    request = MathematicalPolicyRequest.from_dict(prepared["mathematical_request"])
    source = RunPolicySearchUseCase(case["deployment"]).execute(
        request,
        config_values=prepared["config_values"],
        classification_snapshot=prepared["classification_snapshot"],
    )
    selected = RunPolicySelectionUseCase(case["deployment"]).execute(source)
    binding = SelectedPolicyOrderPlanBinding.from_sources(
        selected, request, expected_selection_hash=selected["content_hash"]
    )
    assert binding.to_dict()["canonical_input_hash"] == source["canonical_input_hash"]
    assert [r["item_id"] for r in selected["item_selections"]] == ["ITEM-A"]


def test_sample_target_pipeline_keeps_full_parent_but_orders_only_the_sample():
    from dsio_inventory_engine.classify_inventory.candidate_catalog import (
        CandidateCatalogProducer,
        OfflineCandidateCatalog,
    )
    from dsio_inventory_engine.classify_inventory.legacy_style import LegacyFactorSegmentation
    from dsio_inventory_engine.classify_inventory.legacy_rules import LegacyStyleRules
    from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
    from dsio_inventory_engine.recommend_replenishment.application.target_execution import (
        RunTargetPolicyPipelineUseCase,
    )
    from tests.support.target_execution_fixtures import boundary
    from tests.unit.test_target_execution import ReferenceKernel

    case = preparation_case()
    prepared = prepare_development_catalog_inputs(**case, catalog_modes=("LEGACY_STYLE",))
    request = MathematicalPolicyRequest.from_dict(prepared["mathematical_request"])
    producer = CandidateCatalogProducer(
        LegacyFactorSegmentation(
            LegacyStyleRules.from_document(case["rules_document"]),
            reference_calculator_build_hash=case["reference_calculator_build_hash"],
        )
    )
    catalog = prepared["catalogs"]["LEGACY_STYLE"]
    result = RunTargetPolicyPipelineUseCase(
        case["deployment"], ReferenceKernel(), producer
    ).execute(
        request,
        boundary(request.to_dict()["recommendation"]),
        config_values=prepared["config_values"],
        classification_snapshot=prepared["classification_snapshot"],
        catalog=OfflineCandidateCatalog.from_dict(catalog),
        expected_catalog_hash=catalog["content_hash"],
    )
    assert len(result["search_result"]["candidate_evaluations"]) == 32
    assert {r["item_id"] for r in result["order_plan"]["psi_rows"]} == {"ITEM-A"}


def test_sample_exclusion_cannot_hide_a_blocked_policy():
    result = prepare_development_catalog_inputs(**preparation_case())
    rec = result["mathematical_request"]["recommendation"]
    policies = copy.deepcopy(rec["execution"]["effective_item_policies"])
    policies[1]["recommendation_calculation_allowed"] = False
    with pytest.raises(ValueError, match="SAMPLE_CANNOT_HIDE_BLOCKED"):
        scoped_effective_policies(
            rec["canonical_input"], policies, rec["execution"]["effective_policy_binding"]
        )


def test_sample_json_schema_is_compatible_but_cannot_masquerade_as_v2():
    import json
    from pathlib import Path
    import jsonschema
    from referencing import Registry, Resource

    result = prepare_development_catalog_inputs(**preparation_case())
    data = result["mathematical_request"]["recommendation"]["canonical_input"]
    root = Path(__file__).resolve().parents[2] / "schemas"
    registry = Registry().with_resources(
        (p.name, Resource.from_contents(json.loads(p.read_text())))
        for p in root.glob("canonical_input*.schema.json")
    )
    validator = jsonschema.Draft202012Validator(
        json.loads((root / "canonical_input_v21.schema.json").read_text()), registry=registry
    )
    validator.validate(data)
    wrong = copy.deepcopy(data)
    wrong["development_universe"]["contract_id"] = "io-development-calculation-universe-v2"
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(wrong)
    wrong = copy.deepcopy(data)
    del wrong["development_universe"]["classification_content_hash"]
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(wrong)
    with pytest.raises(ValueError, match="PRODUCTION_FORBIDDEN"):
        PrepareInventoryInputUseCase(DeploymentScope("DSE", "PRODUCTION")).execute(
            CanonicalInputRequest.from_dict(data)
        )

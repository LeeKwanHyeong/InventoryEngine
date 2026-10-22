"""Every explicit candidate executes the existing CandidateStrategy, Guard and PSI."""

import copy
from datetime import date
from decimal import Decimal, Inexact, ROUND_DOWN, localcontext
import json
from pathlib import Path

import pytest

from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.values import digest
from dsio_inventory_engine.recommend_replenishment.application.catalog_search import (
    RunCatalogPolicySearchUseCase,
)
from dsio_inventory_engine.recommend_replenishment.application import policy_search, run
from tests.support.catalog_search_fixtures import catalog_bundle, run_bundle, two_item_bundle
from tests.support.policy_search_fixtures import bundle
from math_fixtures import adjustment, reseal_math

GOLDEN = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures/golden_catalog_psi.json").read_text()
)


def execute(data=None):
    parts, producer, catalog = data or catalog_bundle()
    raw, config, classification = parts
    return RunCatalogPolicySearchUseCase(DeploymentScope("DSE", "DEVELOPMENT"), producer).execute(
        MathematicalPolicyRequest.from_dict(raw),
        catalog=catalog,
        expected_catalog_hash=catalog.content_hash,
        config_values=config,
        classification_snapshot=classification,
    )


@pytest.mark.parametrize(
    "mode,count", [("LEGACY_STYLE", 32), ("ABC_XYZ_SHARED_BUDGET", 32), ("WIDE_REFERENCE", 88)]
)
def test_every_candidate_uses_actual_common_guard_and_psi_without_mutating_inputs(
    monkeypatch, mode, count
):
    data = catalog_bundle(mode=mode)
    original = copy.deepcopy(data[0])
    seen, guard_calls = [], []
    real_simulate, real_guard = policy_search.simulate, run.validate_action

    def simulate(*args, **kwargs):
        assert isinstance(args[3], policy_search.CandidateStrategy)
        seen.append(args[3].candidate["candidate_id"])
        return real_simulate(*args, **kwargs)

    def guard(observation, proposal):
        guard_calls.append(observation["decision_id"])
        return real_guard(observation, proposal)

    monkeypatch.setattr(policy_search, "simulate", simulate)
    monkeypatch.setattr(run, "validate_action", guard)
    result = execute(data)
    assert result["contract_id"] == "inventory-offline-catalog-psi-result"
    assert len(result["candidate_evaluations"]) == len(set(seen)) == count
    assert len(guard_calls) == count * 3
    for child in result["candidate_evaluations"]:
        assert child["status"] == "SUCCEEDED"
        assert child["row_count"] == len(child["psi_rows"]) == 3
        assert child["canonical_input_hash"] == result["canonical_input_hash"]
        assert child["prepared_input_hash"] == result["prepared_input_hash"]
        assert not child["automatic_order_allowed"] and not child["automatic_publish_allowed"]
        assert all(
            later["on_hand_boh_qty"] == earlier["on_hand_eoh_qty"]
            for earlier, later in zip(child["psi_rows"], child["psi_rows"][1:])
        )
    assert data[0] == original
    assert result["observation_work_bound"] == count * 6
    assert result["selected_policies"] is None and not result["database_writes"]


@pytest.mark.parametrize("mode", ["LEGACY_STYLE", "WIDE_REFERENCE"])
def test_hand_calculated_replenishment_pending_supply_service_and_cost(mode):
    result = execute(catalog_bundle(mode=mode))
    baseline = result["baseline_items"][0]
    assert [r["on_hand_eoh_qty"] for r in baseline["psi_rows"]] == GOLDEN["baseline"]["eoh"]
    assert (
        baseline["metrics"]["forecast_shortage_qty"] == GOLDEN["baseline"]["forecast_shortage_qty"]
    )
    for kind in ("ROP_FIXED_Q", "PERIODIC_ORDER_UP_TO"):
        expected = GOLDEN[kind]
        child = next(
            r
            for r in result["candidate_evaluations"]
            if r["candidate"]["policy_kind"] == kind
            and all(r["candidate"][k] == v for k, v in expected["parameters"].items())
        )
        assert [r["on_hand_eoh_qty"] for r in child["psi_rows"]] == expected["eoh"]
        assert [r["recommended_receipt_qty"] for r in child["psi_rows"]] == expected[
            "recommended_receipts"
        ]
        assert [r["forecast_shortage_qty"] for r in child["psi_rows"]] == expected[
            "forecast_shortages"
        ]
        assert [r["quantity"] for r in child["orders"]] == expected["order_quantities"]
        metrics = child["metrics"]
        assert metrics["current_demand_fulfilled_qty"] == expected["fulfilled_qty"]
        for key in (
            "ending_on_hand_qty",
            "order_count",
            "cost_components",
            "total_cost",
            "guard_rejection_count",
        ):
            assert metrics[key] == expected[key]
        assert all(order["status"] == "SIMULATED_RECEIVED" for order in child["orders"])


@pytest.mark.parametrize("action", ["BLOCK", "REVIEW"])
def test_original_fsn_gate_is_preserved_on_all_88_candidates(action):
    raw, values, _ = run_bundle()
    values["policy_overlays"]["fsn_order_action"]["N"] = action
    parts = run_bundle(config_values=values)
    original = copy.deepcopy(parts)
    result = execute(catalog_bundle(mode="WIDE_REFERENCE", parts=parts))
    assert len(result["candidate_evaluations"]) == 88
    assert not result["automatic_order_allowed"] and not result["automatic_publish_allowed"]
    if action == "BLOCK":
        assert all(child["orders"] == [] for child in result["candidate_evaluations"])
        assert all(
            row["proposal"]["action_type"] == "HOLD"
            for child in result["candidate_evaluations"]
            for row in child["decision_evidence"]
        )
    else:
        assert any(child["orders"] for child in result["candidate_evaluations"])
        assert all(
            not order["automatic_order_allowed"]
            for child in result["candidate_evaluations"]
            for order in child["orders"]
        )
    assert parts == original


def test_moq_and_order_multiple_remain_hard_constraints_not_a_capacity_clamp():
    parts = run_bundle(policy={"moq": "20", "order_multiple": "10", "physical_max_capacity": "15"})
    result = execute(catalog_bundle(parts=parts))
    assert all(child["orders"] == [] for child in result["candidate_evaluations"])
    assert any(
        row["validation"]["status"] == "REJECTED"
        for child in result["candidate_evaluations"]
        for row in child["decision_evidence"]
    )
    assert all(child["metrics"]["ordered_qty"] == "0" for child in result["candidate_evaluations"])


def test_sde_changes_protection_but_not_any_physical_due_date():
    _, values, _ = run_bundle()
    values["segmentation"]["sde"]["application_mode"] = "OPERATIONAL"
    parts = run_bundle(
        config_values=values,
        metric_changes={
            "supplier_count": 1,
            "planning_lead_time_days": Decimal("7"),
            "p50_lead_time_days": Decimal("7"),
            "p90_lead_time_days": Decimal("21"),
            "on_time_delivery_rate": Decimal("0.99"),
            "receipt_sample_count": 10,
            "sde_source_status": "VERIFIED",
        },
    )
    result = execute(catalog_bundle(parts=parts))
    for child in result["candidate_evaluations"]:
        assert (
            child["decision_evidence"][0]["candidate_policy_evidence"]["protection_lead_time_days"]
            == "21"
        )
        assert all(
            (date.fromisoformat(r["due_date"]) - date.fromisoformat(r["order_date"])).days == 7
            for r in child["orders"]
        )


def test_one_candidate_failure_does_not_erase_baseline_or_other_candidates():
    parts = run_bundle()
    parts[0]["recommendation"]["execution"]["item_controls"][0]["max_target_qty"] = "25"
    result = execute(catalog_bundle(parts=parts))
    assert {r["status"] for r in result["candidate_evaluations"]} == {"SUCCEEDED", "FAILED"}
    assert all(
        r["row_count"] is None and r["psi_rows"] is None and r["metrics"] is None
        for r in result["candidate_evaluations"]
        if r["status"] == "FAILED"
    )
    assert result["baseline_items"][0]["psi_rows"]


def test_missing_features_exclude_candidates_but_retain_baseline_and_original_evidence():
    def missing(feature):
        feature["sources"]["unit_cost"]["rows"] = []

    result = execute(catalog_bundle(feature_changes=missing))
    assert result["candidate_evaluations"] == []
    assert result["excluded_items"] == [
        {"item_id": "ITEM-A", "reason_code": "LS_MISSING_REQUIRED_FACTOR"}
    ]
    assert len(result["baseline_items"][0]["psi_rows"]) == 3
    assert (
        result["candidate_catalog"]["legacy_result"]["feature_snapshot"]["input"]["sources"][
            "unit_cost"
        ]["rows"]
        == []
    )


def test_approved_business_override_is_not_replaced_by_the_candidate_catalog():
    raw, values, _ = run_bundle()
    raw["policy_input"]["adjustments"] = [adjustment()]
    parts = bundle(reseal_math(raw), values["policy_search"], config_values=values)
    result = execute(catalog_bundle(parts=parts))
    assert result["candidate_evaluations"] == []
    assert result["excluded_items"] == [
        {"item_id": "ITEM-A", "reason_code": "APPROVED_OVERRIDE_PINNED"}
    ]
    assert result["baseline_items"][0]["psi_rows"]


def test_no_demand_and_cost_missing_are_explicit_not_zero_cost_success():
    raw, values, _ = run_bundle(forecast=["0", "0", "0"])
    search = copy.deepcopy(values["policy_search"])
    search["cost_assumptions"] = None
    parts = bundle(raw, search, config_values=values)
    result = execute(catalog_bundle(parts=parts))
    assert len(result["candidate_evaluations"]) == 32
    for child in result["candidate_evaluations"]:
        assert child["metrics"]["service_status"] == "NO_DEMAND_IN_HORIZON"
        assert child["metrics"]["current_demand_fill_rate"] is None
        assert child["metrics"]["cost_status"] == "COST_PROFILE_NOT_BOUND"
        assert child["metrics"]["total_cost"] is None


def test_numeric_context_does_not_change_catalog_or_psi_hash():
    data = catalog_bundle()
    expected = execute(data)
    with localcontext() as context:
        context.prec, context.rounding, context.traps[Inexact] = 7, ROUND_DOWN, True
        assert execute(data) == expected


def test_result_verifier_replays_guard_psi_and_metrics_and_rejects_rehashed_rows():
    data = catalog_bundle()
    result = execute(data)
    parts, producer, catalog = data
    usecase = RunCatalogPolicySearchUseCase(DeploymentScope("DSE", "DEVELOPMENT"), producer)
    arguments = dict(
        catalog=catalog,
        expected_catalog_hash=catalog.content_hash,
        config_values=parts[1],
        classification_snapshot=parts[2],
    )
    request = MathematicalPolicyRequest.from_dict(parts[0])
    usecase.verify_result(result, request, expected_result_hash=result["content_hash"], **arguments)
    forged = copy.deepcopy(result)
    forged["candidate_evaluations"][0]["psi_rows"][0]["on_hand_eoh_qty"] = "999"
    forged["content_hash"] = digest({k: v for k, v in forged.items() if k != "content_hash"})
    with pytest.raises(ValueError, match="CATALOG_PSI_REPLAY_MISMATCH"):
        usecase.verify_result(
            forged, request, expected_result_hash=forged["content_hash"], **arguments
        )


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ("site", "CATALOG_CONTEXT_BINDING_MISMATCH"),
        ("master", "CATALOG_CONTEXT_BINDING_MISMATCH"),
        ("cutoff", "CATALOG_CONTEXT_BINDING_MISMATCH"),
        ("calendar", "CATALOG_CALENDAR_BINDING_MISMATCH"),
        ("classification", "CATALOG_CLASSIFICATION_BINDING_MISMATCH"),
        ("history", "CATALOG_HISTORY_BINDING_MISMATCH"),
        ("lead_time", "CATALOG_LEAD_TIME_BINDING_MISMATCH"),
        ("universe_uom", "CATALOG_UNIVERSE_MISMATCH"),
    ],
)
def test_feature_catalog_cannot_substitute_sources_under_a_valid_rehashed_pin(mutation, reason):
    def change(feature):
        if mutation == "site":
            feature["context"]["site_cd"] = "V102"
            for source in feature["sources"].values():
                source["scope"] = {**source["scope"], "site_cd": "V102"}
        elif mutation == "master":
            feature["context"]["master_snapshot_revision"] = "OTHER-MASTER"
        elif mutation == "cutoff":
            feature["context"]["input_cutoff"] = "2026-09-27T15:00:00Z"
        elif mutation == "calendar":
            feature["context"]["calendar_snapshot_id"] = "OTHER-CALENDAR"
        elif mutation == "classification":
            feature["context"]["classification_content_hash"] = "e" * 64
        elif mutation == "history":
            feature["sources"]["history"]["rows"][0]["demand_qty"] = "11"
        elif mutation == "lead_time":
            feature["sources"]["lead_time"]["rows"][0]["lead_time_days"] = 14
        elif mutation == "universe_uom":
            feature["universe"][0]["uom"] = "KG"

    with pytest.raises(ValueError, match=reason):
        execute(catalog_bundle(feature_changes=change))


def test_abc_unclassified_item_is_not_admitted_even_with_a_legacy_feature_group():
    parts = run_bundle(
        metric_changes={"xyz_total_demand": Decimal("0"), "xyz_demand_square_sum": Decimal("0")}
    )
    assert parts[2]["items"][0]["classification_status"] == "UNCLASSIFIED"
    result = execute(catalog_bundle(parts=parts))
    assert result["candidate_evaluations"] == []
    assert result["excluded_items"] == [
        {"item_id": "ITEM-A", "reason_code": "POLICY_SEARCH_ITEM_UNCLASSIFIED"}
    ]
    assert result["baseline_items"][0]["psi_rows"]


def test_forged_abc_metadata_cannot_override_the_original_classification_gate():
    parts = run_bundle(
        metric_changes={"xyz_total_demand": Decimal("0"), "xyz_demand_square_sum": Decimal("0")}
    )

    def fake_grade(feature):
        feature["abc_result"]["assignments"][0].update(
            status="ASSIGNED", search_group_id="AX", abc_xyz_class="AX", reason_code=None
        )

    with pytest.raises(ValueError, match="CATALOG_ABC_RESULT_MISMATCH"):
        execute(catalog_bundle(parts=parts, feature_changes=fake_grade))


def test_ved_service_floor_is_used_by_every_candidate_not_the_reference_rop_level():
    _, values, _ = run_bundle()
    values["segmentation"]["ved"].update(
        enabled=True,
        default_class="V",
        assignment_snapshot_id="00000000-0000-0000-0000-000000000009",
        assignment_content_hash="9" * 64,
    )
    result = execute(catalog_bundle(parts=run_bundle(config_values=values)))
    assert all(
        child["decision_evidence"][0]["candidate_policy_evidence"]["effective_csl"] == "0.99"
        for child in result["candidate_evaluations"]
    )


def test_catalog_verification_happens_once_not_per_item_or_per_candidate(monkeypatch):
    data = catalog_bundle(mode="WIDE_REFERENCE")
    producer = data[1]
    seen = []
    real_verify = producer.classifier.verify

    def verify(*args, **kwargs):
        seen.append(True)
        return real_verify(*args, **kwargs)

    monkeypatch.setattr(producer.classifier, "verify", verify)
    assert len(execute(data)["candidate_evaluations"]) == 88
    assert len(seen) == 1


def test_catalog_workload_limit_is_checked_before_any_candidate_runs(monkeypatch):
    data = catalog_bundle(mode="WIDE_REFERENCE")
    real_prepare = policy_search.prepare_mathematical_strategy

    def large_view(*args, **kwargs):
        recommendation, prepared, report, strategy = real_prepare(*args, **kwargs)
        prepared["calendar"] = prepared["calendar"] * 30
        return recommendation, prepared, report, strategy

    monkeypatch.setattr(policy_search, "prepare_mathematical_strategy", large_view)

    def forbidden(*args, **kwargs):
        raise AssertionError("No candidate should be executed over the work bound")

    monkeypatch.setattr(policy_search, "simulate", forbidden)
    with pytest.raises(ValueError, match="POLICY_SEARCH_WORKLOAD_LIMIT"):
        execute(data)


def test_operational_deployment_and_foreign_catalog_hash_are_rejected():
    parts, producer, catalog = catalog_bundle()
    kwargs = dict(
        catalog=catalog,
        expected_catalog_hash=catalog.content_hash,
        config_values=parts[1],
        classification_snapshot=parts[2],
    )
    request = MathematicalPolicyRequest.from_dict(parts[0])
    with pytest.raises(ValueError, match="CATALOG_DEVELOPMENT_ONLY"):
        RunCatalogPolicySearchUseCase(DeploymentScope("DSE", "PRODUCTION"), producer).execute(
            request, **kwargs
        )
    kwargs["expected_catalog_hash"] = "d" * 64
    with pytest.raises(ValueError, match="CATALOG_HASH_MISMATCH"):
        RunCatalogPolicySearchUseCase(DeploymentScope("DSE", "DEVELOPMENT"), producer).execute(
            request, **kwargs
        )


def test_two_items_keep_separate_inventory_ledgers_under_the_same_88_policies():
    result = execute(catalog_bundle(mode="WIDE_REFERENCE", parts=two_item_bundle()))
    assert len(result["candidate_evaluations"]) == 176
    grouped = {
        item: [c for c in result["candidate_evaluations"] if c["candidate"]["item_id"] == item]
        for item in ("ITEM-A", "ITEM-B")
    }
    assert all(len(children) == 88 for children in grouped.values())
    baselines = {row["item_id"]: row for row in result["baseline_items"]}
    assert [r["on_hand_eoh_qty"] for r in baselines["ITEM-A"]["psi_rows"]] == ["0", "0", "0"]
    assert [r["on_hand_eoh_qty"] for r in baselines["ITEM-B"]["psi_rows"]] == ["90", "80", "70"]
    for item, children in grouped.items():
        assert all(r["item_id"] == item for child in children for r in child["psi_rows"])
        assert all(r["item_id"] == item for child in children for r in child["orders"])
    ids = [
        order["order_id"] for child in result["candidate_evaluations"] for order in child["orders"]
    ]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    "change,reason",
    [
        ("limit", "POLICY_SEARCH_CANDIDATE_LIMIT"),
        ("range_override", "CATALOG_CONFIG_RANGE_OVERRIDE_UNSUPPORTED"),
        ("service", "CATALOG_SERVICE_RULE_MISMATCH"),
    ],
)
def test_config_limits_are_not_silently_ignored_by_the_explicit_catalog(change, reason):
    raw, values, _ = run_bundle()
    search = copy.deepcopy(values["policy_search"])
    if change == "limit":
        search["max_candidates_per_item"] = 32
    elif change == "range_override":
        search["item_overrides"] = [
            {
                **{k: v for k, v in search["group_profiles"][0].items() if k != "segment_key"},
                "item_id": "ITEM-A",
                "reason": "Existing range must not be silently ignored",
            }
        ]
    elif change == "service":
        search["minimum_fill_rate"] = "0.90"
    parts = bundle(raw, search, config_values=values)
    with pytest.raises(ValueError, match=reason):
        execute(catalog_bundle(parts=parts, mode="WIDE_REFERENCE"))


def test_catalog_result_cannot_masquerade_as_the_old_abc_selection_contract():
    from dsio_inventory_engine.recommend_replenishment.application.policy_selection import (
        RunPolicySelectionUseCase,
    )

    result = execute()
    with pytest.raises(ValueError, match="POLICY_SEARCH_RESULT_CONTRACT_INVALID"):
        RunPolicySelectionUseCase(DeploymentScope("DSE", "DEVELOPMENT")).execute(result)

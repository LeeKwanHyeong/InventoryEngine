"""Technical smoke uses 2040 only: official 2033/2034 outcomes are not test fixtures."""

import copy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path

import pytest

from dsio_inventory_engine.classify_inventory.legacy_features import LegacyFeatureProducer
from dsio_inventory_engine.classify_inventory.legacy_rules import LegacyStyleRules
from dsio_inventory_engine.classify_inventory.legacy_style import LegacyFactorSegmentation
from dsio_inventory_engine.evaluate_replenishment.catalog_comparison import (
    MODES,
    OFFICIAL_PROTOCOL_HASH,
    good_policies,
    run_catalog_comparison,
    summarize_comparison,
    validate_protocol,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.recommend_replenishment.application.catalog_selection import (
    RunCatalogPolicySelectionUseCase,
)
from math_fixtures import reseal_math
from fixtures import reseal
from scripts.verify_catalog_comparison import execute, write_new
from tests.support.catalog_comparison_fixtures import comparison_case

ROOT = Path(__file__).resolve().parents[2]


def smoke():
    protocol = json.loads(
        (ROOT / "docs/architecture/data/catalog-holdout-comparison-v1.json").read_text()
    )
    assert digest(protocol) == OFFICIAL_PROTOCOL_HASH
    protocol.update(
        experiment_id="TECHNICAL-SMOKE-NOT-HOLDOUT",
        periods=[{"case_id": "SMOKE-ONLY", "start_date": "2040-01-02", "seed": 440001}],
        items=[protocol["items"][0], protocol["items"][2]],
        horizon_weeks=3,
        timing_repetitions=1,
        confirmed_receipt_rule={"relative_week": 1, "historical_mean_multiplier": "2"},
    )
    build = sha256(
        (
            ROOT / "src/dsio_inventory_engine/recommend_replenishment/application/math_policy.py"
        ).read_bytes()
    ).hexdigest()
    rules = LegacyStyleRules.from_document(
        json.loads(
            (ROOT / "docs/architecture/data/legacy-style-development-rules-v1.json").read_text()
        )
    )
    components = {
        "feature_producer": LegacyFeatureProducer(rules, reference_calculator_build_hash=build),
        "classifier": LegacyFactorSegmentation(rules, reference_calculator_build_hash=build),
    }
    return (
        protocol,
        [comparison_case(protocol, protocol["periods"][0], reference_build_hash=build)],
        components,
    )


@pytest.fixture(scope="module")
def compared():
    protocol, cases, components = smoke()
    captured = {}

    def sink(case_id, mode, source, selection, selector, request, bindings):
        captured[mode] = (source, selection)

    value = run_catalog_comparison(
        cases, protocol, expected_protocol_hash=digest(protocol), artifact_sink=sink, **components
    )
    return value, captured


def test_three_actual_catalogs_share_inputs_and_preserve_all_rows(compared):
    result, captured = compared
    assert len(result["records"]) == 6
    assert set(captured) == {*MODES, "REALIZED_WIDE_NO_RESELECTION"}
    for mode, expected in zip(MODES, (32, 32, 88), strict=True):
        source, selection = captured[mode]
        assert len(source["candidate_evaluations"]) == expected * 2
        assert all(
            row["planning_candidate_count"] == expected
            for row in result["records"]
            if row["mode"] == mode
        )
        assert selection["database_writes"] is False
        assert selection["automatic_publish_allowed"] is False
        assert selection["automatic_order_allowed"] is False
        assert source["baseline_items"] == captured["WIDE_REFERENCE"][0]["baseline_items"]
    assert captured["REALIZED_WIDE_NO_RESELECTION"][1] is None
    assert result["holdout_reselection_performed"] is False
    assert result["parameter_tuning_performed"] is False


def test_new_actual_demand_cannot_change_planning_or_invoke_a_new_selection(compared, monkeypatch):
    protocol, cases, components = smoke()
    case, feature = cases[0]
    changed = copy.deepcopy(case.replay_request)
    for row in changed["recommendation"]["canonical_input"]["snapshots"]["forecast"]["rows"]:
        row["forecast_qty"] = str(int(row["forecast_qty"]) * 4 + 30)
    changed["recommendation"]["canonical_input"] = reseal(
        changed["recommendation"]["canonical_input"]
    )
    canonical_hash = CanonicalInputRequest.from_dict(
        changed["recommendation"]["canonical_input"]
    ).input_hash
    changed["recommendation"]["execution"]["canonical_input_hash"] = canonical_hash
    changed["policy_input"]["context"]["canonical_input_hash"] = canonical_hash
    changed = reseal_math(changed)
    original = RunCatalogPolicySelectionUseCase.execute
    called = []

    def guarded(self, source, request, **bindings):
        assert request.to_dict() == case.planning_request
        called.append(source["candidate_mode"])
        return original(self, source, request, **bindings)

    monkeypatch.setattr(RunCatalogPolicySelectionUseCase, "execute", guarded)
    result = run_catalog_comparison(
        [(replace(case, replay_request=changed), feature)],
        protocol,
        expected_protocol_hash=digest(protocol),
        **components,
    )
    before = compared[0]
    assert called == list(MODES)
    assert (
        result["case_bindings"][0]["catalog_source_selection_hashes"]
        == before["case_bindings"][0]["catalog_source_selection_hashes"]
    )
    assert result["case_bindings"][0]["gate_hash"] == before["case_bindings"][0]["gate_hash"]
    assert (
        result["case_bindings"][0]["realized_wide_hash"]
        != before["case_bindings"][0]["realized_wide_hash"]
    )
    assert result["summary"] != before["summary"]


def test_repetition_is_deterministic_but_timing_is_not_a_semantic_input(compared):
    protocol, cases, components = smoke()
    protocol["timing_repetitions"] = 2
    events = []
    result = run_catalog_comparison(
        cases,
        protocol,
        expected_protocol_hash=digest(protocol),
        progress=events.append,
        **components,
    )
    assert result["records"] == compared[0]["records"]
    assert result["case_bindings"] == compared[0]["case_bindings"]
    assert all(len(result["timing"][0]["samples"][mode]) == 2 for mode in MODES)
    timed = [row["mode"] for row in events if row["phase"] == "TIMED_PLANNING"]
    assert timed == [*MODES, *MODES[1:], MODES[0]]
    assert events[-1]["phase"] == "CHOICES_LOCKED_REALIZED_REPLAY"
    body = {
        key: value
        for key, value in result.items()
        if key not in {"timing", "semantic_content_hash"}
    }
    assert digest(body) == result["semantic_content_hash"]


@pytest.mark.parametrize(
    "change,code",
    [
        (lambda p: p.update(database_writes=True), "BOUNDARY"),
        (lambda p: p.update(automatic_publish_allowed=True), "BOUNDARY"),
        (lambda p: p.update(automatic_order_allowed=True), "BOUNDARY"),
        (lambda p: p.update(holdout_reselection_allowed=True), "BOUNDARY"),
        (lambda p: p.update(parameter_tuning_allowed=True), "BOUNDARY"),
        (lambda p: p["periods"][0].update(start_date="2024-01-01"), "REUSED_HOLDOUT"),
        (
            lambda p: p["periods"].append({**p["periods"][0], "case_id": "DUPLICATE"}),
            "OVERLAPPING_PERIODS",
        ),
        (lambda p: p["candidate_counts"].update(LEGACY_STYLE=31), "PROTOCOL_INVALID"),
    ],
)
def test_protocol_fails_closed_before_any_calculation(change, code):
    protocol, _, _ = smoke()
    change(protocol)
    with pytest.raises(InventoryInputError, match=code):
        validate_protocol(protocol, expected_protocol_hash=digest(protocol))


def test_official_protocol_cannot_be_silently_tuned():
    protocol, _, _ = smoke()
    with pytest.raises(InventoryInputError, match="PROTOCOL_HASH"):
        validate_protocol(protocol, expected_protocol_hash=OFFICIAL_PROTOCOL_HASH)


def test_changed_boh_is_not_treated_as_a_demand_only_replay():
    protocol, cases, components = smoke()
    case, feature = cases[0]
    changed = copy.deepcopy(case.replay_request)
    changed["recommendation"]["canonical_input"]["snapshots"]["inventory"]["rows"][0][
        "on_hand_qty"
    ] = "999"
    changed = reseal_math(changed)
    with pytest.raises(InventoryInputError):
        run_catalog_comparison(
            [(replace(case, replay_request=changed), feature)],
            protocol,
            expected_protocol_hash=digest(protocol),
            **components,
        )


def test_good_candidate_ties_ignore_ids_and_do_not_reward_ineligible_low_cost(compared):
    rows = copy.deepcopy(compared[1]["WIDE_REFERENCE"][0]["candidate_evaluations"])
    good, best = good_policies(rows)
    for child in rows:
        child["candidate"]["candidate_id"] = "OCCP-" + "f" * 64
    assert good_policies(rows) == (good, best)
    for child in rows:
        child["metrics"].update(policy_selection_eligible=False, total_cost="0")
    assert good_policies(rows) == (set(), None)


def test_paired_cost_claim_excludes_service_failures_and_no_candidate(compared):
    records = copy.deepcopy(compared[0]["records"])
    for row in records:
        if row["mode"] == "LEGACY_STYLE":
            row["realized_service_feasible"] = False
            row["realized_metrics"]["total_cost"] = "0"
    paired = summarize_comparison(records)["paired_service_feasible_comparisons"][
        "LEGACY_STYLE_VS_ABC"
    ]
    assert paired["jointly_feasible_pair_count"] == 0
    assert paired["excluded_pair_count"] == 2
    assert paired["cost_change_ratio"] is None
    assert paired["challenger_total_cost"] is None


def test_append_only_harness_stores_inputs_before_psi_and_verifies_restart(tmp_path):
    protocol, _, _ = smoke()
    protocol["items"] = protocol["items"][:1]
    report = execute(protocol, tmp_path, expected_protocol_hash=digest(protocol))
    assert len(report["artifact_receipts"]) == 4
    assert all(row["source"]["durable_local_artifact"] for row in report["artifact_receipts"])
    assert report["artifact_receipts"][-1]["selection"] is None
    pinned = json.loads((tmp_path / "pinned-inputs.json").read_text())
    assert pinned["protocol_hash"] == digest(protocol)
    assert pinned["cases"][0]["case_id"] == "SMOKE-ONLY"
    assert json.loads((tmp_path / "comparison-report.json").read_text()) == report
    with pytest.raises(InventoryInputError, match="OUTPUT_NOT_EMPTY"):
        execute(protocol, tmp_path, expected_protocol_hash=digest(protocol))
    with pytest.raises(FileExistsError):
        write_new(tmp_path / "comparison-report.json", {})

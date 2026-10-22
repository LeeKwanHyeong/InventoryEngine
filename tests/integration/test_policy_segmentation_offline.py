"""Actual PSI/guard/selector experiment; no DB, network, trained operating model or gate change."""

import copy
from dataclasses import replace
from unittest.mock import patch

import pytest

from fixtures import reseal
from math_fixtures import reseal_math
from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.values import digest
from dsio_inventory_engine.evaluate_replenishment import policy_segmentation as experiment
from tests.support.policy_segmentation_fixtures import ABC_PROFILES, PROFILES, experiment_cases


@pytest.fixture(scope="module")
def measured():
    cases = experiment_cases()
    before = digest([c.planning_request for c in cases] + [c.replay_request for c in cases])
    saved, events = {}, []
    actual_run = experiment._run

    def record(case, values, *, replay=False):
        events.append((case.split, case.case_id, replay))
        return actual_run(case, values, replay=replay)

    def save(search, selection):
        saved[search["content_hash"]] = search
        saved[selection["content_hash"]] = selection

    with (
        patch("socket.socket.connect", side_effect=AssertionError("NETWORK_FORBIDDEN")),
        patch.object(experiment, "_run", side_effect=record),
    ):
        result = experiment.run_experiment(
            cases, PROFILES, ABC_PROFILES, artifact_sink=save, timing_repeats=1
        )
    assert before == digest([c.planning_request for c in cases] + [c.replay_request for c in cases])
    return result, saved, events


def test_three_real_arms_have_same_inputs_gates_and_diagnostic_reference(measured):
    report, saved, _ = measured
    assert len(report["rows"]) == 36
    for receipt in report["receipts"]:
        searches = [saved[h] for h in receipt["arm_search_hashes"].values()]
        assert len({r["canonical_input_hash"] for r in searches}) == 1
        assert len({r["prepared_input_hash"] for r in searches}) == 1
        assert digest(receipt["gate_projection"]) == receipt["gate_projection_hash"]
        for search in searches:
            assert not search["database_writes"] and not search["automatic_order_allowed"]
        replay = saved[receipt["replay_hash"]]
        assert replay["canonical_input_hash"] != receipt["planning_canonical_hash"]
        assert all(
            "forecast" not in name.lower()
            for features in receipt["feature_snapshot"]["items"].values()
            for name in features
        )
    assert report["training_row_count"] > 0 and report["validation_row_count"] > 0
    assert report["learning_exclusions"]  # Irreducibly infeasible cases are disclosed, not hidden.
    assert not report["holdout_reselection_allowed"]
    assert not report["automatic_publish_allowed"] and not report["operational_policy_approved"]


def test_holdout_choices_are_fixed_before_any_realized_replay(measured):
    report, _, events = measured
    for receipt in report["receipts"]:
        sequence = [replay for split, case_id, replay in events if case_id == receipt["case_id"]]
        assert sequence == [False, False, False, True]
    for row in report["rows"]:
        if row["realized_cost_regret"] is not None:
            assert experiment.number(row["realized_cost_regret"]) >= 0


def test_mean_zero_no_demand_and_review_are_not_silently_promoted(measured):
    report, _, _ = measured
    zero_history = [r for r in report["rows"] if r["item_id"] == "SKU-ZERO-HISTORY"]
    no_demand = [r for r in report["rows"] if r["item_id"] == "SKU-NO-FUTURE-DEMAND"]
    assert all(not r["selected"] and r["planning_candidate_count"] == 0 for r in zero_history)
    assert all(
        not r["selected"] and r["realized_metrics"]["current_demand_fill_rate"] is None
        for r in no_demand
    )
    for receipt in report["receipts"]:
        gate = receipt["gate_projection"]["SKU-ZERO-HISTORY"]
        assert gate["effective_order_action"] == "BLOCK"
        fsn = next(r for r in gate["effective_axis_projection"] if r["axis"] == "FSN")
        assert fsn["policy_effect"]["order_action"] == "REVIEW"


def test_good_candidate_recall_and_costs_come_from_actual_psi(measured):
    report, saved, _ = measured
    summary = experiment.summarize_report(report)
    assert summary["WIDE_REFERENCE"]["best_cost_candidate_misses"] == 0
    assert (
        summary["WIDE_REFERENCE"]["evaluated_candidates_per_pass"]
        > summary["ML_PROFILE"]["evaluated_candidates_per_pass"]
    )
    for row in report["rows"]:
        if not row["selected"]:
            continue
        receipt = next(r for r in report["receipts"] if r["case_id"] == row["case_id"])
        children = saved[receipt["replay_hash"]]["candidate_evaluations"]
        child = next(r for r in children if r["content_hash"] == row["replay_candidate_hash"])
        assert row["realized_metrics"] == child["metrics"]
        assert sum(
            experiment.number(r["net_forecast_qty"]) for r in child["psi_rows"]
        ) == experiment.number(row["realized_metrics"]["current_demand_qty"])
    assert summary["paired_ABC_ML"]["excluded_pairs"] > 0


def test_tampered_summary_report_fails_hash_verification(measured):
    report = copy.deepcopy(measured[0])
    report["rows"][0]["realized_service_feasible"] = not report["rows"][0][
        "realized_service_feasible"
    ]
    with pytest.raises(ValueError, match="REPORT_HASH"):
        experiment.summarize_report(report)


def test_unverified_operational_fsn_still_blocks_every_candidate_arm(measured):
    case = experiment_cases()[-1]
    metric = case.classification_inputs.metrics[0]
    metrics = (
        replace(metric, fsn_source_status="UNVERIFIED"),
        *case.classification_inputs.metrics[1:],
    )
    case = replace(case, classification_inputs=replace(case.classification_inputs, metrics=metrics))
    snapshot = experiment._snapshot(case, experiment._broad_values(case, PROFILES))
    for arm in experiment.ARMS:
        values, _ = experiment._arm_values(
            case, PROFILES, ABC_PROFILES, arm, measured[0]["model"], snapshot
        )
        _, search, selection = experiment._run(case, values)
        item = next(r for r in selection["item_selections"] if r["item_id"] == metric.item_id)
        assert item["status"] == "NOT_SELECTED"
        children = [
            r
            for r in search["candidate_evaluations"]
            if r["candidate"]["item_id"] == metric.item_id
        ]
        assert all(not r["orders"] for r in children)
        assert all(not r["metrics"]["policy_selection_eligible"] for r in children)


def test_changing_holdout_outcomes_cannot_retrain_or_reselect_planning_policies(measured):
    cases = experiment_cases()
    for index, case in enumerate(cases):
        if case.split != "HOLDOUT":
            continue
        replay = copy.deepcopy(case.replay_request)
        canonical = replay["recommendation"]["canonical_input"]
        for row in canonical["snapshots"]["forecast"]["rows"]:
            row["forecast_qty"] = str(experiment.number(row["forecast_qty"]) * 2)
        reseal(canonical)
        input_hash = CanonicalInputRequest.from_dict(canonical).input_hash
        replay["recommendation"]["execution"]["canonical_input_hash"] = input_hash
        replay["policy_input"]["context"]["canonical_input_hash"] = input_hash
        cases[index] = replace(case, replay_request=reseal_math(replay))
    changed = experiment.run_experiment(cases, PROFILES, ABC_PROFILES, timing_repeats=1)
    original = measured[0]
    assert changed["model"] == original["model"]
    assert [r["locked_policy"] for r in changed["rows"]] == [
        r["locked_policy"] for r in original["rows"]
    ]
    assert [r["realized_metrics"] for r in changed["rows"]] != [
        r["realized_metrics"] for r in original["rows"]
    ]

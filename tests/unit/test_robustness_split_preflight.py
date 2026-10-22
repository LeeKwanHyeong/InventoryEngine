from copy import deepcopy

import pytest

from dsio_inventory_engine.evaluate_replenishment.robustness.split_preflight import (
    MODES,
    build_split_work_plan,
    project_split_resources,
    verify_split_resource_projection,
    verify_split_work_plan,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from tests.support.split_preflight_fixtures import protocol, reseal, samples


def test_whole_population_has_all_paths_without_generating_source_or_cross_cost_duplicate_psi():
    source = protocol()
    before = deepcopy(source)
    plan = build_split_work_plan(source)
    assert plan["counts"] == {
        "PRIMARY": 198,
        "STRESS": 16,
        "TERMINAL_UPPER_BOUND": 180,
        "TIMING": 27,
    }
    assert len(plan["tasks"]) == plan["total_task_upper_bound"] == 421
    assert len({t["task_id"] for t in plan["tasks"]}) == 421
    assert plan["primary_candidate_week_work"] == 1_132_560
    assert plan["stress_candidate_week_work"] == 91_520
    assert plan["timing_candidate_week_work"] == 88_920
    assert plan["terminal_candidate_week_work_upper_bound"] == 9_360
    assert source == before
    assert plan["metadata_only"] is True and plan["official_execution_allowed"] is False
    assert verify_split_work_plan(plan, source) == plan


def test_holdout_and_stress_inventory_and_terminal_locks_are_not_omitted():
    plan = build_split_work_plan(protocol())
    tasks = plan["tasks"]
    assert len([t for t in tasks if t["kind"] == "PRIMARY" and t["split"] == "HOLDOUT"]) == 96
    assert {t["item_id"] for t in tasks if t["kind"] == "STRESS"} == {
        "RB-INTERMITTENT-L7",
        "RB-INTERMITTENT-L49",
    }
    terminal = [t for t in tasks if t["kind"] == "TERMINAL_UPPER_BOUND"]
    assert all(
        t["candidate_count"] == 1
        and t["weeks"] == 52
        and t["requires_selection_lock"] is True
        and t["deduplicate_after_lock"] is True
        for t in terminal
    )
    assert all(t["candidate_count"] == 88 for t in tasks if t["kind"] in {"PRIMARY", "STRESS"})


@pytest.mark.parametrize(
    "fault", ["missing", "duplicate", "seed", "flag_type", "window", "candidate_count"]
)
def test_rehashed_plan_cannot_hide_missing_tasks_or_changed_contract(fault):
    source = protocol()
    plan = build_split_work_plan(source)
    if fault == "missing":
        plan["tasks"].pop()
    elif fault == "duplicate":
        plan["tasks"][1] = deepcopy(plan["tasks"][0])
    elif fault == "flag_type":
        plan["official_execution_allowed"] = 0
    else:
        plan["tasks"][0][
            {"seed": "seed", "window": "window_id", "candidate_count": "candidate_count"}[fault]
        ] = "CHANGED"
    with pytest.raises(InventoryInputError, match="WORK_PLAN_CHANGED"):
        verify_split_work_plan(reseal(plan), source)


def test_changed_protocol_fails_before_work_population_is_built():
    source = protocol()
    source["windows"][0]["selection_seeds"].pop()
    with pytest.raises(InventoryInputError, match="PROTOCOL_HASH_MISMATCH"):
        build_split_work_plan(source)


def test_resource_proxy_matches_hand_arithmetic_and_never_zero_fills_unknown_full_cost():
    source = protocol()
    plan = build_split_work_plan(source)
    values = samples()
    before = deepcopy(values)
    report = project_split_resources(source, plan, values)
    assert report["known_catalog_proxy"] == {
        "raw_bytes": 20_200_000,
        "physical_bytes": 202_000,
        "files": 20_200,
        "wall_ns": 86_760,
    }
    assert report["known_catalog_proxy_with_margin"] == {
        "raw_bytes": 25_250_000,
        "physical_bytes": 252_500,
        "files": 25_250,
        "wall_ns": 108_450,
    }
    assert all(
        report[k] is None
        for k in ("full_raw_bytes", "full_files", "full_wall_ns", "full_physical_bytes")
    )
    assert report["status"] == "BLOCKED_INCOMPLETE_MEASUREMENTS"
    assert len(report["unmeasured_components"]) == 6
    assert report["official_execution_allowed"] is False
    assert values == before
    assert verify_split_resource_projection(report, source, plan, values) == report


def test_compression_cannot_turn_raw_budget_failure_into_success():
    source = protocol()
    values = samples()
    for value in values:
        value["raw_bytes_added"] = 128_000_000
        value["physical_bytes_added"] = 1
        reseal(value)
    report = project_split_resources(source, build_split_work_plan(source), values)
    assert report["known_catalog_proxy"]["physical_bytes"] == 241
    assert "KNOWN_CATALOG_PROXY_EXCEEDS_RAW_BUDGET" in report["blocking_reasons"]


@pytest.mark.parametrize("dimension", ["files", "wall_time"])
def test_file_and_whole_bank_call_headroom_are_checked_independently(dimension):
    source = protocol()
    values = samples()
    for value in values:
        if dimension == "files":
            value["files_added"] = 1000
        else:
            value["total_wall_ns"] = 3_000_000_000_000
        reseal(value)
    report = project_split_resources(source, build_split_work_plan(source), values)
    reason = (
        "KNOWN_CATALOG_PROXY_EXCEEDS_FILE_LIMIT"
        if dimension == "files"
        else "MEASURED_BANK_CALL_WITH_MARGIN_EXCEEDS_DEADLINE"
    )
    assert reason in report["blocking_reasons"]


@pytest.mark.parametrize(
    "fault",
    [
        "missing_mode",
        "duplicate",
        "pool",
        "zero",
        "bool_size",
        "phase",
        "sum_time",
        "w0",
        "not_replayed",
        "unsafe",
        "extra",
        "version",
        "rows",
        "hash",
    ],
)
def test_invalid_or_incomparable_samples_fail_closed(fault):
    source = protocol()
    values = samples()
    sample = values[0]
    if fault == "missing_mode":
        values.pop()
    elif fault == "duplicate":
        values.append(deepcopy(sample))
    elif fault == "pool":
        sample["pool_id"] = "e" * 64
    elif fault == "phase":
        sample["phase_wall_ns"]["run"] = 0
    elif fault == "sum_time":
        sample["phase_wall_ns"] = dict.fromkeys(sample["phase_wall_ns"], 3_000_000_000_000)
    else:
        key, value = {
            "zero": ("raw_bytes_added", 0),
            "bool_size": ("raw_bytes_added", True),
            "w0": ("w0", "2037-01-05"),
            "not_replayed": ("psi_recalculation_status", "NOT_RUN"),
            "unsafe": ("automatic_publish_allowed", True),
            "extra": ("latest", True),
            "version": ("contract_version", "9.0.0"),
            "rows": ("psi_row_count", 1),
            "hash": ("result_hash", "not-a-hash"),
        }[fault]
        sample[key] = value
    reseal(sample)
    with pytest.raises(InventoryInputError):
        project_split_resources(source, build_split_work_plan(source), values)


@pytest.mark.parametrize("margin", [0, 24, 101, True, "25", 25.0])
def test_margin_cannot_be_disabled_or_silently_coerced(margin):
    source = protocol()
    with pytest.raises(InventoryInputError, match="MARGIN_INVALID"):
        project_split_resources(
            source, build_split_work_plan(source), samples(), margin_percent=margin
        )


def test_repeated_measurements_use_per_mode_maxima_and_do_not_multiply_study_population():
    source = protocol()
    values = samples()
    extra = deepcopy(values[0])
    extra["raw_bytes_added"] *= 2
    values.append(reseal(extra))
    report = project_split_resources(source, build_split_work_plan(source), values)
    assert (
        report["known_catalog_proxy"]["raw_bytes"] == 20_200_000 + 9 * MODES[extra["mode"]] * 1000
    )


@pytest.mark.parametrize(
    "field,wrong",
    [
        ("full_raw_bytes", 0),
        ("status", "PASSED"),
        ("official_execution_allowed", 0),
        ("margin_percent", 0),
    ],
)
def test_rehashed_projection_cannot_override_unknowns_status_or_exact_boolean_types(field, wrong):
    source = protocol()
    plan, values = build_split_work_plan(source), samples()
    report = project_split_resources(source, plan, values)
    report[field] = wrong
    with pytest.raises(InventoryInputError, match="PROJECTION_CHANGED"):
        verify_split_resource_projection(reseal(report), source, plan, values)

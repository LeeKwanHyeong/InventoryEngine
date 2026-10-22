"""Full workload accounting without generating a single official demand path.

Hand-case extrapolation is a capacity warning, never experiment authorization.
Unmeasured work remains unknown, not a zero-cost omission from the budget.
"""

from collections import Counter
from copy import deepcopy
from decimal import Decimal, ROUND_CEILING

from dsio_inventory_engine.inventory_contracts.values import canonical_json, digest, require
from .contracts import PROTOCOL_HASH, SAFETY, input_errors, sealed, shape

MODES = {"ABC_XYZ_SHARED_BUDGET": 32, "LEGACY_STYLE": 32, "WIDE_REFERENCE": 88}
UNKNOWN_COMPONENTS = (
    "LOCKED_SINGLE_POLICY_52_WEEK_TERMINAL_REPLAY",
    "RANGE_PROJECTION_COST_REPRICING_SELECTION_AND_SPLIT_MANIFEST",
    "POPULATED_ROOT_RESCAN_AND_COLD_PROCESS_GROWTH",
    "OFFICIAL_ITEM_AND_PATH_SIZE_VARIATION",
    "INTERRUPTION_QUARANTINE_AND_RETRY_HEADROOM",
    "OFFICIAL_SOURCE_GENERATION_AND_FULL_POPULATION_CLASSIFICATION",
)


@input_errors
def build_split_work_plan(protocol: dict) -> dict:
    require(digest(protocol) == PROTOCOL_HASH, "PREFLIGHT_PROTOCOL_HASH_MISMATCH")
    tasks = []

    def add(kind, window, item, split, seed, mode, weeks=65, **extra):
        body = {
            "kind": kind,
            "window_id": window["window_id"],
            "item_id": item["item_id"],
            "split": split,
            "seed": seed,
            "mode": mode,
            "candidate_count": 1 if kind == "TERMINAL_UPPER_BOUND" else MODES[mode],
            "weeks": weeks,
            **extra,
        }
        tasks.append({"task_id": digest(body), **body})

    for window in protocol["windows"]:
        for item in protocol["items"]:
            for split, seeds in (
                ("NOMINAL", [None]),
                ("SELECTION", window["selection_seeds"]),
                ("VALIDATION", window["validation_seeds"]),
                ("HOLDOUT", window["holdout_seeds"]),
            ):
                for seed in seeds:
                    add("PRIMARY", window, item, split, seed, "WIDE_REFERENCE")
            if item["item_id"] in protocol["diagnostics"]["stress_applies_to_items"]:
                for scenario in protocol["diagnostics"]["stress_scenarios"]:
                    add(
                        "STRESS",
                        window,
                        item,
                        "STRESS",
                        window["diagnostic_seeds"][scenario["seed_index"]],
                        "WIDE_REFERENCE",
                        scenario_id=scenario["id"],
                    )
            for mode in MODES:
                for profile in protocol["cost"]["profiles"]:
                    for arm in protocol["selection"]["arms"]:
                        add(
                            "TERMINAL_UPPER_BOUND",
                            window,
                            item,
                            "VALIDATION",
                            window["validation_seeds"][0],
                            mode,
                            52,
                            cost_profile=profile["profile_id"],
                            arm=arm,
                            requires_selection_lock=True,
                            deduplicate_after_lock=True,
                        )
    timing = protocol["execution"]["timing_subset"]
    window = next(w for w in protocol["windows"] if w["window_id"] == timing["window_id"])
    for repetition in range(timing["repetitions"]):
        modes = list(MODES)
        modes = modes[repetition:] + modes[:repetition]
        for item in protocol["items"]:
            for mode in modes:
                add(
                    "TIMING",
                    window,
                    item,
                    timing["split"],
                    window["selection_seeds"][timing["seed_index"]],
                    mode,
                    repetition=repetition + 1,
                    cross_mode_bank_reuse=False,
                )
    counts = dict(Counter(t["kind"] for t in tasks))
    require(
        counts == {"PRIMARY": 198, "STRESS": 16, "TERMINAL_UPPER_BOUND": 180, "TIMING": 27}
        and len({t["task_id"] for t in tasks}) == len(tasks),
        "PREFLIGHT_WORK_POPULATION_MISMATCH",
    )
    return sealed(
        "inventory-robustness-split-work-plan",
        {
            "tasks": tasks,
            "counts": counts,
            "total_task_upper_bound": len(tasks),
            "primary_candidate_week_work": 198 * 88 * 65,
            "stress_candidate_week_work": 16 * 88 * 65,
            "timing_candidate_week_work": 9 * (32 + 32 + 88) * 65,
            "terminal_candidate_week_work_upper_bound": 180 * 52,
            "cost_profiles": [p["profile_id"] for p in protocol["cost"]["profiles"]],
            "primary_banks_reused_for_ranges_and_cost_profiles": True,
            "holdout_requires_selection_lock": True,
            "metadata_only": True,
            "official_paths_generated": False,
            "official_execution_allowed": False,
        },
    )


def _verify_seal(value, contract):
    require(type(value) is dict, "PREFLIGHT_DOCUMENT_INVALID")
    body = {k: v for k, v in value.items() if k != "content_hash"}
    require(
        value["content_hash"] == digest(body)
        and value["contract_id"] == contract
        and value["contract_version"] == "1.0.0"
        and value["protocol_hash"] == PROTOCOL_HASH
        and all(value[k] is False for k in SAFETY),
        "PREFLIGHT_SEAL_INVALID",
    )


@input_errors
def verify_split_work_plan(value: dict, protocol: dict) -> dict:
    require(
        canonical_json(value) == canonical_json(build_split_work_plan(protocol)),
        "PREFLIGHT_WORK_PLAN_CHANGED",
    )
    return deepcopy(value)


def _sample(value):
    _verify_seal(value, "inventory-robustness-hand-resource-sample")
    shape(
        value,
        (
            "contract_id",
            "contract_version",
            "protocol_hash",
            *SAFETY,
            "content_hash",
            "mode",
            "w0",
            "candidate_count",
            "psi_row_count",
            "result_hash",
            "input_hash",
            "assembly_hash",
            "realization_hash",
            "catalog_hash",
            "candidate_ids_hash",
            "pool_id",
            "storage_version",
            "raw_bytes_added",
            "physical_bytes_added",
            "files_added",
            "phase_wall_ns",
            "total_wall_ns",
            "sample_kind",
            "integrity_status",
            "psi_recalculation_status",
            "official_paths_generated",
        ),
    )
    require(
        value["mode"] in MODES
        and value["candidate_count"] == MODES[value["mode"]]
        and value["psi_row_count"] == value["candidate_count"] * 65
        and value["w0"] == "2055-01-04"
        and value["sample_kind"] == "HAND_CASE_MEASURED_NOT_OFFICIAL_BOUND"
        and value["storage_version"] == "3.0.0"
        and value["integrity_status"] == "VERIFIED"
        and value["psi_recalculation_status"] == "VERIFIED"
        and value["official_paths_generated"] is False,
        "PREFLIGHT_SAMPLE_CONTEXT_INVALID",
    )
    from dsio_inventory_engine.inventory_contracts.values import hash_value

    for key in (
        "result_hash",
        "input_hash",
        "assembly_hash",
        "realization_hash",
        "catalog_hash",
        "candidate_ids_hash",
        "pool_id",
    ):
        hash_value(value[key])
    for key in ("raw_bytes_added", "physical_bytes_added", "files_added"):
        require(type(value[key]) is int and value[key] > 0, "PREFLIGHT_SAMPLE_SIZE_INVALID")
    shape(value["phase_wall_ns"], ("run", "inspect", "verify_recalculation"))
    require(
        all(type(n) is int and 0 < n <= 3_600_000_000_000 for n in value["phase_wall_ns"].values())
        and type(value["total_wall_ns"]) is int
        and sum(value["phase_wall_ns"].values()) <= value["total_wall_ns"] <= 3_600_000_000_000,
        "PREFLIGHT_SAMPLE_TIME_INVALID",
    )
    return deepcopy(value)


@input_errors
def project_split_resources(
    protocol: dict, plan: dict, samples: list, *, margin_percent=25
) -> dict:
    verified_plan = verify_split_work_plan(plan, protocol)
    require(type(samples) is list and len(samples) >= 3, "PREFLIGHT_SAMPLES_INCOMPLETE")
    require(type(margin_percent) is int and 25 <= margin_percent <= 100, "PREFLIGHT_MARGIN_INVALID")
    verified = [_sample(s) for s in samples]
    require(
        len({s["content_hash"] for s in verified}) == len(verified), "PREFLIGHT_DUPLICATE_SAMPLE"
    )
    require({s["mode"] for s in verified} == set(MODES), "PREFLIGHT_SAMPLES_INCOMPLETE")
    require(len({s["pool_id"] for s in verified}) == 1, "PREFLIGHT_DIFFERENT_POOLS")
    maxima = {}
    for mode in MODES:
        members = [s for s in verified if s["mode"] == mode]
        maxima[mode] = {
            **{
                key: max(s[key] for s in members)
                for key in ("raw_bytes_added", "physical_bytes_added", "files_added")
            },
            "phase_wall_ns": {
                key: max(s["phase_wall_ns"][key] for s in members)
                for key in ("run", "inspect", "verify_recalculation")
            },
            "total_wall_ns": max(s["total_wall_ns"] for s in members),
        }
    proxy = {key: 0 for key in ("raw_bytes", "physical_bytes", "files", "wall_ns")}
    for task in verified_plan["tasks"]:
        if task["kind"] == "TERMINAL_UPPER_BOUND":
            continue  # Unknown remains null in the full total, never silently zero.
        measure = maxima[task["mode"]]
        proxy["raw_bytes"] += measure["raw_bytes_added"]
        proxy["physical_bytes"] += measure["physical_bytes_added"]
        proxy["files"] += measure["files_added"]
        proxy["wall_ns"] += measure["total_wall_ns"]
    factor = Decimal(100 + margin_percent) / 100
    padded = {
        key: int((Decimal(n) * factor).to_integral_value(rounding=ROUND_CEILING))
        for key, n in proxy.items()
    }
    reasons = list(UNKNOWN_COMPONENTS)
    if padded["raw_bytes"] > protocol["execution"]["max_bytes_per_local_experiment"]:
        reasons.append("KNOWN_CATALOG_PROXY_EXCEEDS_RAW_BUDGET")
    if padded["files"] > 100_000:
        reasons.append("KNOWN_CATALOG_PROXY_EXCEEDS_FILE_LIMIT")
    if max(m["total_wall_ns"] for m in maxima.values()) * factor > 3_600_000_000_000:
        reasons.append("MEASURED_BANK_CALL_WITH_MARGIN_EXCEEDS_DEADLINE")
    return sealed(
        "inventory-robustness-split-resource-preflight",
        {
            "work_plan_hash": verified_plan["content_hash"],
            "sample_hashes": [s["content_hash"] for s in verified],
            "pool_id": verified[0]["pool_id"],
            "status": "BLOCKED_INCOMPLETE_MEASUREMENTS",
            "blocking_reasons": reasons,
            "work_population_counts": verified_plan["counts"],
            "known_catalog_proxy": proxy,
            "known_catalog_proxy_with_margin": padded,
            "margin_percent": margin_percent,
            "full_raw_bytes": None,
            "full_physical_bytes": None,
            "full_wall_ns": None,
            "full_files": None,
            "unmeasured_components": list(UNKNOWN_COMPONENTS),
            "estimate_is_hard_upper_bound": False,
            "cost_profiles_multiply_psi_runs": False,
            "global_control_overhead_is_constant": False,
            "invocation_limit_is_entire_study_limit": False,
            "official_paths_generated": False,
            "official_execution_allowed": False,
        },
    )


@input_errors
def verify_split_resource_projection(value, protocol, plan, samples, *, margin_percent=25):
    require(
        canonical_json(value)
        == canonical_json(
            project_split_resources(protocol, plan, samples, margin_percent=margin_percent)
        ),
        "PREFLIGHT_RESOURCE_PROJECTION_CHANGED",
    )
    return deepcopy(value)

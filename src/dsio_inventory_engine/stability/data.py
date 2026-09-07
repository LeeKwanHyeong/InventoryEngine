"""Independent draws by item/week, never a replay of the old periodic fixture."""

from datetime import date, timedelta

from dsio_inventory_engine.fit_replenishment.data import repackage
from dsio_inventory_engine.inventory_contracts.stability import StabilityRequest
from dsio_inventory_engine.inventory_contracts.training import TrainingRequest
from dsio_inventory_engine.inventory_contracts.values import digest, require
from dsio_inventory_engine.training.demo import build_demo_request

W0, TRAIN, VALIDATION, HOLDOUT = 78, 52, 52, 104
HOLDOUT_START = W0 + TRAIN + VALIDATION
NAMES = ("NORMAL", "INTERMITTENT", "DECLINING", "SURGE", "ZERO")


def draw(seed: int, item: str, index: int, channel: str, modulus: int) -> int:
    return (
        int(digest(["nonrepeating-hash-demand-v1", seed, item, index, channel])[:16], 16) % modulus
    )


def quantity(seed: int, item: str, index: int) -> int:
    # Seasonal means may recur; draws/regimes are NOT copied from earlier weeks.
    noise = draw(seed, item, index, "noise", 11) - 5
    regime = draw(seed, item, index // 17, "regime", 9) - 4
    season = 3 if index % 52 < 13 else -2 if index % 52 >= 39 else 0
    if item == "ZERO":
        return 0
    if item == "INTERMITTENT":
        return (
            10 + draw(seed, item, index, "size", 31)
            if draw(seed, item, index, "arrival", 5) == 0
            else 0
        )
    if item == "DECLINING":
        return max(0, 28 - index // 14 + noise + regime)
    if item == "SURGE":
        return max(
            0,
            12 + noise + season + regime + (35 if draw(seed, item, index, "shock", 17) == 0 else 0),
        )
    return max(0, 18 + noise + season + regime)


def build_dataset(request: StabilityRequest, *, holdout_seed: int | None = None) -> TrainingRequest:
    plan = request.to_dict()
    if holdout_seed is not None:
        require(holdout_seed in plan["holdout_data_seeds"], "UNDECLARED_HOLDOUT_SEED")
    data = build_demo_request().to_dict()
    token = digest([plan["study_id"], plan["development_data_seed"]])[:20]
    data.update(
        dataset_id="STABILITY-" + token,
        simulation_run_id="SYNTH-" + token,
        calendar_snapshot_id="CAL-" + token,
        demand_snapshot_id="DMD-" + token,
        w0_index=W0,
        train_weeks=TRAIN,
        validation_weeks=VALIDATION,
        test_weeks=HOLDOUT,
    )
    data["context"].update(
        planning_cycle_id="PC-" + token,
        cycle_site_execution_id="SITE-" + token,
        configuration_revision="STABILITY-v1",
    )
    first = date(2026, 1, 5)
    data["calendar"] = [
        {
            "yyyyww": (first + timedelta(weeks=i)).strftime("%G%V"),
            "start_date": (first + timedelta(weeks=i)).isoformat(),
            "end_date": (first + timedelta(weeks=i, days=6)).isoformat(),
        }
        for i in range(HOLDOUT_START + HOLDOUT)
    ]
    data["demand"] = []
    for i, bucket in enumerate(data["calendar"]):
        seed = plan["development_data_seed"] if i < HOLDOUT_START else holdout_seed
        for name in NAMES:
            # Required DTO rows are explicitly unobserved placeholders until the
            # selection is pinned. No holdout generator calls happen in selection.
            qty = 0 if seed is None else quantity(seed, name, i)
            data["demand"].append(
                {"item_id": name, "uom": "EA", "yyyyww": bucket["yyyyww"], "demand_qty": str(qty)}
            )
    data["scenarios"] = [
        s
        for s in data["scenarios"]
        if s["scenario_id"] in ("BASE", "HIGH_SHORTAGE_COST", "SERVICE_99", "DELAY_2W")
    ]
    return repackage(data)


def partition_hashes(training: TrainingRequest) -> dict:
    data = training.to_dict()
    result = {}
    for name, lo, hi in (
        ("HISTORY", 0, W0),
        ("TRAIN", W0, W0 + TRAIN),
        ("VALIDATION", W0 + TRAIN, HOLDOUT_START),
        ("TEST", HOLDOUT_START, len(data["calendar"])),
    ):
        calendar = data["calendar"][lo:hi]
        weeks = {r["yyyyww"] for r in calendar}
        result[name] = {
            "start_date": calendar[0]["start_date"],
            "end_date": calendar[-1]["end_date"],
            "weeks": len(calendar),
            "content_hash": digest(
                {
                    "calendar": calendar,
                    "demand": [r for r in data["demand"] if r["yyyyww"] in weeks],
                }
            ),
        }
    return result


def sensitivity_dataset(training: TrainingRequest, horizon: int, terminal: str) -> TrainingRequest:
    require(horizon in (52, 104) and terminal in ("SOURCE", "ZERO"), "STABILITY_SENSITIVITY")
    data = training.to_dict()
    data["test_weeks"] = horizon
    data["calendar"] = data["calendar"][: HOLDOUT_START + horizon]
    weeks = {r["yyyyww"] for r in data["calendar"]}
    data["demand"] = [r for r in data["demand"] if r["yyyyww"] in weeks]
    if terminal == "ZERO":
        for s in data["scenarios"]:
            s["cost"]["terminal_backlog_per_unit"] = "0"
    return repackage(data)

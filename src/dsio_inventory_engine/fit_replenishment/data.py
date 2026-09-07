"""Causal TRAIN-only positive protection-period forecast-error labels."""

import math
from decimal import Decimal
from typing import cast

from dsio_inventory_engine.inventory_contracts.training import TrainingRequest, content, normalize
from dsio_inventory_engine.inventory_contracts.values import digest, require
from dsio_inventory_engine.learned.features import FEATURE_NAMES
from dsio_inventory_engine.evaluate_replenishment.admission import (
    SERVICE_LEVEL,
    episode_range,
    frozen_mean,
)
from dsio_inventory_engine.training.reference import demand_series


def training_fingerprint(data: dict) -> dict:
    start, end = episode_range(data, "TRAIN")
    weeks = {r["yyyyww"] for r in data["calendar"][:end]}
    # No validation/test actual values enter model lineage or training.
    return {
        "context": data["context"],
        "items": data["items"],
        "calendar": data["calendar"][:end],
        "demand": [r for r in data["demand"] if r["yyyyww"] in weeks],
        "scenarios": data["scenarios"],
        "w0_index": start,
        "warmup_weeks": data["warmup_weeks"],
        "lookback_weeks": data["lookback_weeks"],
        "replenishment_cycle_weeks": data["replenishment_cycle_weeks"],
        "missing_history": data["missing_history"],
        "demand_snapshot_id": data["demand_snapshot_id"],
        "policy_snapshot_id": data["policy_snapshot_id"],
    }


def supervised(data: dict) -> list[dict]:
    start, end = episode_range(data, "TRAIN")
    lookback = data["lookback_weeks"]
    result = []
    # Source service levels (scenario variation is for policy evaluation, not extra labels).
    for item in data["items"]:
        series = demand_series(data, item)
        horizon = item["lead_time_weeks"] + data["replenishment_cycle_weeks"]
        for origin in range(start, end - horizon + 1):
            history = series[origin - lookback : origin]
            labels = series[origin : origin + horizon]
            require(
                len(history) == lookback and None not in history and None not in labels,
                "ML_INCOMPLETE_HISTORY",
            )
            valid_history = cast(list[Decimal], history)
            valid_labels = cast(list[Decimal], labels)
            mean = math.fsum(float(x) for x in valid_history) / lookback
            std = math.sqrt(
                math.fsum((float(x) - mean) ** 2 for x in valid_history) / (lookback - 1)
            )
            scale = max(mean, float(item["quantity_step"]))
            forecast = Decimal(frozen_mean(data, item, origin, lookback))
            positive_error = (
                max(0.0, math.fsum(float(x) for x in valid_labels) - float(forecast) * horizon)
                / scale
            )
            values = [0.0] * len(FEATURE_NAMES)
            values[6:10] = [
                std / scale,
                item["lead_time_weeks"] / 52,
                float(SERVICE_LEVEL[item["service_level_code"]]),
                sum(x == 0 for x in history) / lookback,
            ]
            result.append(
                {
                    "item_id": item["item_id"],
                    "origin_index": origin,
                    "history_end_exclusive": origin,
                    "label_end_exclusive": origin + horizon,
                    "available_at": data["calendar"][origin + horizon - 1]["end_date"],
                    "features": values,
                    "target": positive_error,
                    "quantile": float(SERVICE_LEVEL[item["service_level_code"]]),
                }
            )
    require(bool(result), "NO_ML_TRAINING_LABELS")
    return result


def repackage(data: dict) -> TrainingRequest:
    data = normalize(data)
    data["content_hash"] = digest(content(data))
    return TrainingRequest.from_dict(data)

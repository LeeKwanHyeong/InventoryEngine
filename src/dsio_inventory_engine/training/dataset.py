"""One-week supervised pairs: past-only features and separately hashed future labels."""

from decimal import Decimal, localcontext

from dsio_inventory_engine.inventory_contracts.values import digest, quantity_text as q
from .reference import NUMBERS, demand_series, due_date, observed_demand


def splits(data: dict) -> list[tuple[str, int, int]]:
    start = data["w0_index"]
    result = []
    for name in ("train", "validation", "test"):
        end = start + data[f"{name}_weeks"]
        result.append((name.upper(), start, end))
        start = end
    return result


def feature(data: dict, item: dict, index: int, history: list | None = None) -> dict:
    with localcontext(NUMBERS):
        history = demand_series(data, item) if history is None else history
        lookback = min(data["lookback_weeks"], index - item["active_from_index"])
        lookback = 26 if lookback >= 26 else 13
        values = [observed_demand(history, i) for i in range(index - lookback, index)]
        mean = sum(values, Decimal(0)) / lookback
        row = {
            "item_id": item["item_id"],
            "uom": item["uom"],
            "origin_index": index,
            "issued_on": due_date(data, index),
            "target_index": index,
            "target_yyyyww": data["calendar"][index]["yyyyww"],
            "history_start_index": index - lookback,
            "history_end_index_exclusive": index,
            "demand_history_qty": [q(x) for x in values],
            "forecast_qty": q(mean),
            "forecast_source_type": "SYNTHETIC_CAUSAL_MEAN",
            "forecast_generator_version": "trailing-mean-v1",
            "information_timing": "PRIOR_WEEK_ACTUAL_KNOWN_AT_NEXT_WEEK_START",
        }
        row["feature_hash"] = digest(row)
        return row


def build_supervised(data: dict) -> dict:
    features, labels, partitions = [], [], []
    for split, start, end in splits(data):
        partitions.append(
            {
                "split": split,
                "from_index": start,
                "to_index_exclusive": end,
                "label_horizon_weeks": 1,
            }
        )
        for item in data["items"]:
            series = demand_series(data, item)
            for index in range(start, end):
                row = feature(data, item, index, series)
                row_id = f"{data['dataset_id']}:{item['item_id']}:{index}"
                features.append({"row_id": row_id, "split": split, **row})
                labels.append(
                    {
                        "row_id": row_id,
                        "split": split,
                        "target_index": index,
                        "actual_demand_qty": q(observed_demand(series, index)),
                        "available_on": due_date(data, index + 1),
                    }
                )
    return {
        "features": features,
        "labels": labels,
        "partitions": partitions,
        "features_hash": digest(features),
        "labels_hash": digest(labels),
        "split_hash": digest(partitions),
        "source_type": "SYNTHETIC_DEMAND",
        "observed_forecast_vintages_verified": False,
        "scaler_fitted": False,
        "model_trained": False,
    }

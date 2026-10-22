"""Small, dependency-free research model for policy-profile routing, not replenishment.

This model has no operational registry, approval, database, or publication capability.
Its training loss is lexicographic: infeasible cases first, then normalized cost regret.
"""

from __future__ import annotations

import copy
from decimal import Context, Decimal, InvalidOperation, localcontext

from dsio_inventory_engine.inventory_contracts.values import digest, require

DECIMAL_CONTEXT = Context(prec=50)


def number(value: str) -> Decimal:
    require(type(value) is str and 0 < len(value) <= 96, "SEGMENTATION_NUMBER")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("SEGMENTATION_NUMBER") from exc
    require(result.is_finite() and result >= 0, "SEGMENTATION_NUMBER")
    return result


def text_number(value: Decimal) -> str:
    return format(value, "f")


def _validate_rows(rows: list[dict]) -> tuple[list[str], list[str]]:
    require(2 <= len(rows) <= 1024, "SEGMENTATION_TRAINING_SIZE")
    require(len({r["row_id"] for r in rows}) == len(rows), "SEGMENTATION_DUPLICATE_ROW")
    features = sorted(rows[0]["features"])
    profiles = sorted(rows[0]["profile_outcomes"])
    require(bool(features) and len(profiles) >= 2, "SEGMENTATION_EMPTY_FEATURES_OR_PROFILES")
    for row in rows:
        require(sorted(row["features"]) == features, "SEGMENTATION_FEATURE_CONTRACT")
        require(sorted(row["profile_outcomes"]) == profiles, "SEGMENTATION_PROFILE_CONTRACT")
        for value in row["features"].values():
            number(value)
        require(
            any(outcome["feasible"] for outcome in row["profile_outcomes"].values()),
            "SEGMENTATION_NO_FEASIBLE_TRAINING_PROFILE",
        )
        for outcome in row["profile_outcomes"].values():
            require(type(outcome["feasible"]) is bool, "SEGMENTATION_FEASIBILITY")
            if outcome["feasible"]:
                number(outcome["cost"])
    return features, profiles


def routing_loss(rows: list[dict], profile_ids: list[str]) -> tuple[int, Decimal]:
    """A cheap but infeasible route never beats a feasible route."""
    require(len(rows) == len(profile_ids), "SEGMENTATION_ROUTE_COUNT")
    failed, regret = 0, Decimal(0)
    with localcontext(DECIMAL_CONTEXT):
        for row, profile in zip(rows, profile_ids, strict=True):
            outcomes = row["profile_outcomes"]
            require(profile in outcomes, "SEGMENTATION_UNKNOWN_PROFILE")
            feasible = [number(v["cost"]) for v in outcomes.values() if v["feasible"]]
            require(bool(feasible), "SEGMENTATION_NO_FEASIBLE_TRAINING_PROFILE")
            outcome = outcomes[profile]
            if not outcome["feasible"]:
                failed += 1
            else:
                best = min(feasible)
                regret += (number(outcome["cost"]) - best) / max(Decimal(1), best)
    return failed, regret


def fit_profile_tree(rows: list[dict], *, max_depth: int = 2, min_leaf: int = 2) -> dict:
    """Fit only caller-supplied TRAIN rows; no labels or item IDs become features."""
    rows = sorted(copy.deepcopy(rows), key=lambda row: row["row_id"])
    features, profiles = _validate_rows(rows)
    require(type(max_depth) is int and 0 <= max_depth <= 3, "SEGMENTATION_DEPTH")
    require(type(min_leaf) is int and min_leaf >= 2, "SEGMENTATION_LEAF_SIZE")

    def leaf(data):
        return min((routing_loss(data, [profile] * len(data)), profile) for profile in profiles)

    def grow(data, depth):
        loss, profile = leaf(data)
        node = {"profile_id": profile, "row_count": len(data)}
        if depth >= max_depth or len(data) < min_leaf * 2:
            return node
        best = None
        for feature in features:
            values = sorted({number(row["features"][feature]) for row in data})
            for lower, upper in zip(values, values[1:]):
                threshold = (lower + upper) / 2
                left = [r for r in data if number(r["features"][feature]) <= threshold]
                right = [r for r in data if number(r["features"][feature]) > threshold]
                if min(len(left), len(right)) < min_leaf:
                    continue
                left_loss, _ = leaf(left)
                right_loss, _ = leaf(right)
                score = (left_loss[0] + right_loss[0], left_loss[1] + right_loss[1])
                proposal = (score, feature, threshold)
                if score < loss and (best is None or proposal < best[:3]):
                    best = (*proposal, left, right)
        if best is None:
            return node
        _, feature, threshold, left, right = best
        return {
            "feature": feature,
            "threshold": text_number(threshold),
            "left": grow(left, depth + 1),
            "right": grow(right, depth + 1),
            "row_count": len(data),
        }

    with localcontext(DECIMAL_CONTEXT):
        tree = grow(rows, 0)
    body = {
        "contract_id": "inventory-policy-segmentation-experiment-model",
        "contract_version": "1.0.0",
        "algorithm": "cost-sensitive-greedy-tree-v1",
        "purpose": "POLICY_PROFILE_ROUTING_NOT_REPLENISHMENT",
        "feature_names": features,
        "profile_ids": profiles,
        "max_depth": max_depth,
        "min_leaf": min_leaf,
        "training_row_count": len(rows),
        "training_data_hash": digest(rows),
        "tree": tree,
        "development_only": True,
        "operational_approved": False,
    }
    return {**body, "content_hash": digest(body)}


def predict_profile(model: dict, features: dict[str, str]) -> str:
    require(
        model.get("contract_id") == "inventory-policy-segmentation-experiment-model"
        and model.get("contract_version") == "1.0.0"
        and model.get("algorithm") == "cost-sensitive-greedy-tree-v1"
        and model.get("purpose") == "POLICY_PROFILE_ROUTING_NOT_REPLENISHMENT"
        and model.get("development_only") is True
        and model.get("operational_approved") is False,
        "SEGMENTATION_MODEL_CONTRACT",
    )
    require(
        model["content_hash"] == digest({k: v for k, v in model.items() if k != "content_hash"}),
        "SEGMENTATION_MODEL_HASH",
    )
    require(sorted(features) == model["feature_names"], "SEGMENTATION_FEATURE_CONTRACT")
    for value in features.values():
        number(value)
    node = model["tree"]
    for _ in range(5):
        if "profile_id" in node:
            require(node["profile_id"] in model["profile_ids"], "SEGMENTATION_UNKNOWN_PROFILE")
            return node["profile_id"]
        require(node.get("feature") in features, "SEGMENTATION_TREE_FEATURE")
        node = node[
            "left" if number(features[node["feature"]]) <= number(node["threshold"]) else "right"
        ]
    raise ValueError("SEGMENTATION_TREE_DEPTH")

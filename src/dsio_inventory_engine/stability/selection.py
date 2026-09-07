"""Deterministic paired cost/service gates, never selecting a lucky model seed."""

from decimal import Decimal, localcontext

from dsio_inventory_engine.inventory_contracts.values import digest, require
from dsio_inventory_engine.training.reference import NUMBERS

RULE = {
    "version": "cost-service-guardrails-v1",
    "mean_cost_ratio_max": "1.00",
    "worst_cost_ratio_max": "1.10",
    "fill_rate_delta_min": "-0.02",
    "item_fill_rate_delta_min": "-0.05",
    "physical_capacity_excess_max": "0",
    "scope": "DEVELOPMENT_RESEARCH_ONLY_NOT_P0_19",
    "seed_selection": "KEEP_ALL_SEEDS",
}


def spread(values: list[Decimal]) -> dict:
    require(bool(values), "EMPTY_STABILITY_METRIC")
    with localcontext(NUMBERS):
        mean = sum(values, Decimal(0)) / len(values)
        std = (
            (sum(((v - mean) ** 2 for v in values), Decimal(0)) / (len(values) - 1)).sqrt()
            if len(values) > 1
            else Decimal(0)
        )
        return {
            "n": len(values),
            "mean": str(mean),
            "sample_std": str(std),
            "min": str(min(values)),
            "max": str(max(values)),
        }


def assess(rows: list[dict]) -> dict:
    require(bool(rows), "EMPTY_STABILITY_COMPARISON")
    ratios, deltas, item_deltas, violations = [], [], [], []
    with localcontext(NUMBERS):
        for row in rows:
            baseline, candidate = row["mathematical"], row["learned"]
            require(
                baseline["initial_state_hash"] == candidate["initial_state_hash"]
                and baseline["currency"] == candidate["currency"]
                and baseline["item_weeks"] == candidate["item_weeks"]
                and baseline["cost_convention"] == candidate["cost_convention"],
                "UNPAIRED_STABILITY_INPUT",
            )
            cost = Decimal(baseline["total_cost"])
            require(cost > 0, "ZERO_COMPARISON_COST")
            ratio = Decimal(candidate["total_cost"]) / cost
            delta = Decimal(candidate["on_time_fill_rate"]) - Decimal(baseline["on_time_fill_rate"])
            ratios.append(ratio)
            deltas.append(delta)
            base_items = {r["item_id"]: r for r in baseline["metrics"]}
            require(
                set(base_items) == {r["item_id"] for r in candidate["metrics"]},
                "UNPAIRED_ITEM_METRICS",
            )
            cell_items = []
            for metric in candidate["metrics"]:
                other = base_items[metric["item_id"]]
                require(metric["demand_qty"] == other["demand_qty"], "UNPAIRED_DEMAND")
                if Decimal(metric["demand_qty"]) > 0:
                    cell_items.append(
                        Decimal(metric["on_time_fill_rate"]) - Decimal(other["on_time_fill_rate"])
                    )
            item_deltas.extend(cell_items)
            reasons = []
            if ratio > Decimal(RULE["worst_cost_ratio_max"]):
                reasons.append("COST_REGRESSION")
            if delta < Decimal(RULE["fill_rate_delta_min"]):
                reasons.append("SERVICE_REGRESSION")
            if min(cell_items, default=Decimal(0)) < Decimal(RULE["item_fill_rate_delta_min"]):
                reasons.append("ITEM_SERVICE_REGRESSION")
            if Decimal(candidate["physical_capacity_excess_qty"]) > 0:
                reasons.append("CAPACITY_EXCESS")
            if reasons:
                violations.append(
                    {"seed": row["seed"], "scenario_id": row["scenario_id"], "reasons": reasons}
                )
        cost_stats = spread(ratios)
        mean_cost_passed = Decimal(cost_stats["mean"]) <= Decimal(RULE["mean_cost_ratio_max"])
        # Scenarios share demand, and seeds share a validation window. These are
        # descriptive cells, not IID samples; no false confidence interval.
        by_seed = []
        for seed in sorted({r["seed"] for r in rows}):
            indices = [i for i, r in enumerate(rows) if r["seed"] == seed]
            by_seed.append(
                {
                    "seed": seed,
                    "cost_ratio": str(sum(ratios[i] for i in indices) / len(indices)),
                    "fill_delta": str(sum(deltas[i] for i in indices) / len(indices)),
                }
            )
        return {
            "passed": not violations and mean_cost_passed,
            "mean_cost_passed": mean_cost_passed,
            "cost_ratio": cost_stats,
            "fill_delta": spread(deltas),
            "worst_item_fill_delta": str(min(item_deltas, default=Decimal(0))),
            "violation_cells": violations,
            "by_seed": by_seed,
            "seed_cost_ratio": spread([Decimal(r["cost_ratio"]) for r in by_seed]),
            "seed_fill_delta": spread([Decimal(r["fill_delta"]) for r in by_seed]),
            "statistical_superiority_claimed": False,
        }


def select_candidates(request: dict, validation_rows: list[dict]) -> dict:
    require(
        len(validation_rows) == len(request["candidates"]) * len(request["training_seeds"]) * 8,
        "INCOMPLETE_VALIDATION_MATRIX",
    )
    selections = {}
    for family in ("PREDICTIVE_ML", "DEEP_RL"):
        scores = []
        for candidate in request["candidates"]:
            rows = [
                r
                for r in validation_rows
                if r["family"] == family and r["candidate_id"] == candidate["candidate_id"]
            ]
            expected = {
                (seed, s)
                for seed in request["training_seeds"]
                for s in ("BASE", "HIGH_SHORTAGE_COST", "SERVICE_99", "DELAY_2W")
            }
            require(
                len(rows) == len(expected)
                and {(r["seed"], r["scenario_id"]) for r in rows} == expected,
                "INCOMPLETE_VALIDATION_MATRIX",
            )
            scores.append({"candidate_id": candidate["candidate_id"], **assess(rows)})
        ranked = sorted(
            scores,
            key=lambda r: (
                not r["passed"],
                len(r["violation_cells"]),
                Decimal(r["cost_ratio"]["mean"]),
                -Decimal(r["fill_delta"]["mean"]),
                r["candidate_id"],
            ),
        )
        winner = ranked[0]
        selections[family] = {
            "candidate_id": winner["candidate_id"],
            "validation_gate_passed": winner["passed"],
            "purpose": "RESEARCH_COMPARISON_ONLY",
            "all_seed_models_retained": True,
            "effective_strategy": family if winner["passed"] else "MATHEMATICAL",
            "candidates": sorted(scores, key=lambda r: r["candidate_id"]),
        }
    result = {
        "rule": RULE,
        "families": selections,
        "selection_split": "VALIDATION",
        "holdout_opened": False,
        "operational_approval": False,
    }
    result["content_hash"] = digest(result)
    return result

"""Fresh, explicitly synthetic comparison inputs. Never a live Source or Runtime loader."""

import copy
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING, localcontext

from fixtures import build_request
from math_fixtures import GOLDEN_MATH, build_math, reseal_math
from strategy_fixtures import request as recommendation_request
from tests.support.catalog_search_fixtures import feature_from_bundle
from tests.support.policy_search_fixtures import GOLDEN
from tests.unit.test_seven_axis_segmentation import inputs, v2_metric, v2_values

from dsio_inventory_engine.classify_inventory.application import (
    CONFIG_SCHEMA_V21_HASH,
    CONFIG_SCHEMA_V21_ID,
    CONFIG_SCHEMA_V21_VERSION,
    InventoryScope,
    ItemMetric,
    build_snapshot,
    config_hash,
)
from dsio_inventory_engine.evaluate_replenishment.policy_segmentation import ExperimentCase
from dsio_inventory_engine.inventory_contracts.values import digest
from dsio_inventory_engine.recommend_replenishment.application.policy_search import NUMBERS


def _draw(seed, item, week, channel, modulus):
    return (
        int(digest(["HASH_DRAW_CATALOG_HOLDOUT_V1", seed, item, week, channel])[:16], 16) % modulus
    )


def _quantity(seed, item, week, channel):
    pattern = item["demand_pattern"]
    if pattern == "STABLE":
        return 6 + _draw(seed, item["item_id"], week, channel, 5)
    if pattern == "INTERMITTENT":
        return (
            20 + _draw(seed, item["item_id"], week, channel + "-size", 21)
            if _draw(seed, item["item_id"], week, channel, 100) < 35
            else 0
        )
    return (0, 5, 15, 40)[_draw(seed, item["item_id"], week, channel, 4)]


def comparison_case(protocol, period, *, reference_build_hash):
    """Only history sets Forecast/BOH/features; independent FUTURE draws are replay-only."""
    with localcontext(NUMBERS):
        return _comparison_case(protocol, period, reference_build_hash=reference_build_hash)


def _comparison_case(protocol, period, *, reference_build_hash):
    start = date.fromisoformat(period["start_date"])
    horizon, lookback = protocol["horizon_weeks"], protocol["mathematical_lookback_weeks"]
    codes = [(start + timedelta(weeks=i)).strftime("%G%V") for i in range(horizon)]
    histories, actual, items = {}, {}, []
    by_item = {item["item_id"]: item for item in protocol["items"]}
    for item in protocol["items"]:
        item_id = item["item_id"]
        history = [_quantity(period["seed"], item, i, "HISTORY") for i in range(52)]
        mean = sum(map(Decimal, history[-lookback:])) / lookback
        future = [_quantity(period["seed"], item, i, "UNTOUCHED_FUTURE") for i in range(horizon)]
        known_order = int(item["known_order_qty"])
        actual[item_id] = [str(max(known_order, q)) for q in future]
        histories[item_id] = history
        boh = (mean * ((item["lead_time_days"] + 6) // 7 + 2)).to_integral_value(
            rounding=ROUND_CEILING
        )
        items.append(
            {
                "id": item_id,
                "boh": str(boh),
                "forecast": [str(mean.to_integral_value(rounding=ROUND_CEILING))] * horizon,
                "orders": [item["known_order_qty"]] * horizon,
                "receipts": [
                    {
                        "week": protocol["confirmed_receipt_rule"]["relative_week"],
                        "qty": str(
                            int(
                                mean
                                * Decimal(
                                    protocol["confirmed_receipt_rule"]["historical_mean_multiplier"]
                                )
                            )
                        ),
                        "status": "CONFIRMED",
                    }
                ],
            }
        )
    canonical = build_request(
        {
            "id": period["case_id"],
            "start_date": start.isoformat(),
            "weeks": codes,
            "source_type": "SYNTHETIC_BOH",
            "items": items,
        }
    )
    canonical["context"]["plan_end_date"] = (start + timedelta(weeks=horizon, days=-1)).isoformat()
    for kind in ("inventory", "prior_inventory", "receipts"):
        canonical["snapshots"][kind]["metadata"].update(
            generator_version=protocol["generator_version"],
            simulation_run_id=protocol["experiment_id"] + ":" + period["case_id"],
        )
    for row in canonical["snapshots"]["policies"]["rows"]:
        row.update(
            effective_to=(start + timedelta(weeks=horizon)).isoformat(),
            lead_time_days=by_item[row["item_id"]]["lead_time_days"],
            moq=protocol["moq"],
            order_multiple=protocol["order_multiple"],
        )

    def package(source):
        raw = build_math(GOLDEN_MATH["cases"][0])
        rec = recommendation_request(source).to_dict()
        rec["execution"].update(
            contract_version="1.1.0", strategy=raw["recommendation"]["execution"]["strategy"]
        )
        raw["recommendation"] = rec
        context = source["context"]
        raw["policy_input"]["context"].update(
            **{
                key: context[key]
                for key in ("company_cd", "subs_cd", "site_cd", "configuration_revision")
            },
            canonical_input_hash=rec["execution"]["canonical_input_hash"],
            as_of_date=(start - timedelta(days=1)).isoformat(),
            available_at=context["inventory_cutoff_at"],
        )
        raw["policy_input"]["snapshot_id"] = "MATH-" + period["case_id"]
        raw["policy_input"]["profile"].update(lookback_weeks=lookback, stddev_ddof=0)
        raw["policy_input"]["calendar"] = [
            {
                "yyyyww": (start - timedelta(weeks=lookback - i)).strftime("%G%V"),
                "start_date": (start - timedelta(weeks=lookback - i)).isoformat(),
                "end_date": (start - timedelta(weeks=lookback - i, days=-6)).isoformat(),
            }
            for i in range(lookback)
        ]
        raw["policy_input"]["history"] = [
            {"item_id": item, "uom": "EA", "yyyyww": bucket["yyyyww"], "demand_qty": str(q)}
            for item, history in histories.items()
            for bucket, q in zip(raw["policy_input"]["calendar"], history[-lookback:], strict=True)
        ]
        return reseal_math(raw)

    planning = package(copy.deepcopy(canonical))
    replay_source = copy.deepcopy(canonical)
    for row in replay_source["snapshots"]["forecast"]["rows"]:
        row["forecast_qty"] = actual[row["item_id"]][codes.index(row["yyyyww"])]
    replay = package(replay_source)
    values = v2_values()
    search = copy.deepcopy(GOLDEN["search_config"])
    search.update(
        lookback_weeks=26,
        max_candidates_per_item=128,
        minimum_fill_rate=protocol["minimum_fill_rate"],
        cost_assumptions=copy.deepcopy(protocol["cost_assumptions"]),
    )
    # Config is common to all three modes; neutral candidate lists live in separate Catalogs.
    values["policy_search"] = search
    metrics = []
    for item in protocol["items"]:
        quantities = list(map(Decimal, histories[item["item_id"]]))
        positive = [i for i, q in enumerate(quantities) if q > 0]
        last = (start - timedelta(weeks=52 - positive[-1])).strftime("%G%V") if positive else None
        total = sum(quantities)
        metrics.append(
            v2_metric(
                ItemMetric(
                    item["item_id"],
                    52,
                    0,
                    52,
                    total,
                    sum(q * q for q in quantities),
                    total * Decimal(item["unit_cost_usd"]),
                ),
                positive_week_count=len(positive),
                last_positive_demand_yyyyww=last,
            )
        )
    ctx = planning["recommendation"]["canonical_input"]["context"]
    classification_inputs = replace(
        inputs(),
        scope=InventoryScope(
            "offline-catalog-holdout",
            **{
                "company_cd": ctx["company_cd"],
                "subs_cd": ctx["subs_cd"],
                "plant_cd": ctx["site_cd"],
                "site_cd": ctx["site_cd"],
            },
        ),
        config_revision_id=ctx["configuration_revision"],
        config_values=values,
        config_hash=config_hash(values),
        config_schema_id=CONFIG_SCHEMA_V21_ID,
        config_schema_version=CONFIG_SCHEMA_V21_VERSION,
        config_schema_hash=CONFIG_SCHEMA_V21_HASH,
        actual_yyyyww=(start - timedelta(weeks=1)).strftime("%G%V"),
        actual_close_window_start_yyyyww=(start - timedelta(weeks=52)).strftime("%G%V"),
        actual_close_history_hash=digest(histories),
        source_manifest_sha256=digest(histories),
        source_relation="SYNTHETIC_FIXTURE.CLOSED_DEMAND_NOT_DATABASE",
        metrics=tuple(metrics),
    )
    snapshot = build_snapshot(classification_inputs)
    feature = feature_from_bundle((planning, values, snapshot))
    feature["context"]["reference_calculator_build_hash"] = reference_build_hash
    for kind, source in feature["sources"].items():
        source.update(
            source_id=period["case_id"] + ":" + kind,
            revision=protocol["experiment_id"],
            generator={
                "version": protocol["generator_version"],
                "seed": period["seed"],
                "input_revision": digest(protocol),
            },
        )
    for row in feature["sources"]["unit_cost"]["rows"]:
        row.update(
            unit_cost=by_item[row["item_id"]]["unit_cost_usd"],
            price_as_of=(start - timedelta(days=1)).isoformat(),
        )
    return ExperimentCase(
        period["case_id"], "HOLDOUT", planning, replay, classification_inputs
    ), feature

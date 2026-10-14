"""Explicit development calculation scope; never change the shared Master."""

from .values import (
    canonical_json,
    choice,
    digest,
    hash_value,
    identifier,
    records,
    require,
    shape,
    yyyyww,
)


def seal_development_universe(value):
    unsigned = {k: v for k, v in value.items() if k != "content_hash"}
    result = shape(
        unsigned,
        {
            "contract_id": choice("io-development-calculation-universe-v1"),
            "environment_scope": choice("DEVELOPMENT"),
            "demand_run_id": identifier,
            "plan_yyyyww": yyyyww,
            "original_master_content_hash": hash_value,
            "original_forecast_content_hash": hash_value,
            "source_demand_window_file_sha256": hash_value,
            "approval_reference": identifier,
            "included_item_ids": lambda v: (
                [identifier(x) for x in v]
                if type(v) is list
                else require(False, "DEVELOPMENT_UNIVERSE_ITEMS")
            ),
            "exclusions": lambda v: records(
                v,
                {
                    "item_id": identifier,
                    "reason": choice("EXCLUDED_ENDED_DEMAND_WINDOW"),
                    "demand_end_yyyyww": yyyyww,
                },
                limit=20_000,
            ),
        },
    )
    result["included_item_ids"].sort()
    result["exclusions"].sort(key=canonical_json)
    included = result["included_item_ids"]
    excluded = [r["item_id"] for r in result["exclusions"]]
    require(
        bool(included) and len(included) <= 20_000 and len(set(included)) == len(included),
        "DEVELOPMENT_UNIVERSE_ITEMS",
    )
    require(
        len(set(excluded)) == len(excluded) and not set(included) & set(excluded),
        "DEVELOPMENT_UNIVERSE_OVERLAP",
    )
    require(
        # demand_end_dt is the first EXCLUDED week in the Demand producer.
        all(r["demand_end_yyyyww"] <= result["plan_yyyyww"] for r in result["exclusions"]),
        "DEVELOPMENT_UNIVERSE_NOT_ENDED",
    )
    result["content_hash"] = digest(result)
    if "content_hash" in value:
        require(result["content_hash"] == value["content_hash"], "DEVELOPMENT_UNIVERSE_HASH")
    return result


def admitted_development_master(data, master, deployment):
    scope = data.get("development_universe")
    if scope is None:
        return master
    require(deployment.environment == "DEVELOPMENT", "DEVELOPMENT_UNIVERSE_PRODUCTION_FORBIDDEN")
    scope = seal_development_universe(scope)
    context = data["context"]
    upstream = data["upstream_bindings"]
    require(
        all(
            upstream[k]["snapshot_id"] + (".canonical" if k == "forecast" else "")
            == data["snapshots"][k]["snapshot_id"]
            for k in ("calendar", "master", "forecast")
        ),
        "DEVELOPMENT_UPSTREAM_ID_MISMATCH",
    )
    require(
        scope["demand_run_id"] == context["demand_run_id"]
        and scope["plan_yyyyww"] == context["plan_yyyyww"]
        and scope["original_master_content_hash"] == upstream["master"]["content_hash"]
        and scope["original_forecast_content_hash"] == upstream["forecast"]["content_hash"],
        "DEVELOPMENT_UNIVERSE_BINDING",
    )
    included = set(scope["included_item_ids"])
    excluded = {r["item_id"] for r in scope["exclusions"]}
    require(included | excluded == set(master), "DEVELOPMENT_UNIVERSE_COVERAGE")
    require(
        {r["item_id"] for r in data["snapshots"]["forecast"]["rows"]} == included,
        "DEVELOPMENT_UNIVERSE_FORECAST_COVERAGE",
    )
    return {k: master[k] for k in sorted(included)}

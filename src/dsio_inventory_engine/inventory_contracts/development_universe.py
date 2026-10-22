"""Explicit development calculation scope; never change the shared Master."""

from .values import (
    canonical_json,
    choice,
    digest,
    hash_value,
    identifier,
    require,
    shape,
    yyyyww,
)


def seal_development_universe(value):
    sample_scope = value.get("contract_id") == "io-development-calculation-universe-v3"
    policy_scope = (
        sample_scope or value.get("contract_id") == "io-development-calculation-universe-v2"
    )

    def gate_codes(codes):
        require(type(codes) is list and 0 < len(codes) <= 32, "DEVELOPMENT_EXCLUSION_GATE_CODES")
        normalized = [identifier(c) for c in codes]
        require(len(set(normalized)) == len(normalized), "DEVELOPMENT_EXCLUSION_GATE_CODES")
        return sorted(normalized)

    def exclusion(row):
        if sample_scope and row.get("reason") == "EXCLUDED_DEVELOPMENT_SAMPLE":
            return shape(
                row, {"item_id": identifier, "reason": choice("EXCLUDED_DEVELOPMENT_SAMPLE")}
            )
        if policy_scope and row.get("reason") == "EXCLUDED_POLICY_CALCULATION_BLOCKED":
            return shape(
                row,
                {
                    "item_id": identifier,
                    "reason": choice("EXCLUDED_POLICY_CALCULATION_BLOCKED"),
                    "policy_gate_reason_codes": gate_codes,
                },
            )
        return shape(
            row,
            {
                "item_id": identifier,
                "reason": choice("EXCLUDED_ENDED_DEMAND_WINDOW"),
                "demand_end_yyyyww": yyyyww,
            },
        )

    unsigned = {k: v for k, v in value.items() if k != "content_hash"}
    require(
        type(unsigned.get("exclusions")) is list and len(unsigned["exclusions"]) <= 20_000,
        "DEVELOPMENT_UNIVERSE_EXCLUSIONS",
    )
    result = shape(
        unsigned,
        {
            "contract_id": choice(
                "io-development-calculation-universe-v3"
                if sample_scope
                else "io-development-calculation-universe-v2"
                if policy_scope
                else "io-development-calculation-universe-v1"
            ),
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
            "exclusions": lambda v: sorted([exclusion(r) for r in v], key=canonical_json),
            **(
                {
                    "classification_snapshot_id": identifier,
                    "classification_content_hash": hash_value,
                }
                if policy_scope
                else {}
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
        all(
            r["demand_end_yyyyww"] <= result["plan_yyyyww"]
            for r in result["exclusions"]
            if r["reason"] == "EXCLUDED_ENDED_DEMAND_WINDOW"
        ),
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
            upstream[k]["snapshot_id"]
            + (
                ".canonical"
                + (
                    "." + scope["content_hash"][:16]
                    if scope["contract_id"]
                    in {
                        "io-development-calculation-universe-v2",
                        "io-development-calculation-universe-v3",
                    }
                    else ""
                )
                if k == "forecast"
                else ""
            )
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


def scoped_effective_policies(canonical, policies, binding):
    """Keep the full policy hash, admit only explicitly included development items."""
    universe = canonical.get("development_universe")
    if not universe or universe["contract_id"] not in {
        "io-development-calculation-universe-v2",
        "io-development-calculation-universe-v3",
    }:
        return policies
    require(
        universe["classification_snapshot_id"] == binding["classification_snapshot_id"],
        "CLASSIFICATION_EXCLUSION_BINDING_MISMATCH",
    )
    by_item = {r["item_id"]: r for r in policies}
    excluded = {
        r["item_id"]: r
        for r in universe["exclusions"]
        if r["reason"] == "EXCLUDED_POLICY_CALCULATION_BLOCKED"
    }
    included = set(universe["included_item_ids"])
    sampled_out = {
        r["item_id"] for r in universe["exclusions"] if r["reason"] == "EXCLUDED_DEVELOPMENT_SAMPLE"
    }
    require(
        set(by_item) == included | set(excluded) | sampled_out,
        "EFFECTIVE_POLICY_UNIVERSE_MISMATCH",
    )
    require(
        all(by_item[item]["recommendation_calculation_allowed"] is True for item in sampled_out),
        "DEVELOPMENT_SAMPLE_CANNOT_HIDE_BLOCKED_ITEM",
    )
    for item, exclusion in excluded.items():
        policy = by_item[item]
        require(
            policy["recommendation_calculation_allowed"] is False
            and sorted(policy["policy_gate_reason_codes"]) == exclusion["policy_gate_reason_codes"],
            "CLASSIFICATION_EXCLUSION_NOT_BLOCKED",
        )
    return [r for r in policies if r["item_id"] in included]

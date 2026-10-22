"""Lossless observation references to the already sealed shared action Source.

The original observation hash remains authoritative. Only immutable Source
fields are omitted; mutable inventory state and pending orders remain per row.
"""

from collections import defaultdict

from .values import require

CONTRACT = "inventory-observation-source-reference-v1"
FIELDS = {
    "contract_id",
    "decision_id",
    "input_content_hash",
    "item_id",
    "uom",
    "decision_date",
    "bucket",
    "state",
    "pending_supply",
}


def compact_observation(observation):
    return {"contract_id": CONTRACT, **{k: observation[k] for k in FIELDS - {"contract_id"}}}


def source_item_index(source):
    indexes = {}
    for name in ("policies", "item_controls", "demands", "positions", "receipt_decisions"):
        groups = defaultdict(list)
        for row in source[name]:
            groups[row["item_id"]].append(row)
        indexes[name] = groups
    return indexes


def item_source(source, indexes, item):
    return {**source, **{name: groups.get(item, []) for name, groups in indexes.items()}}


def expand_observation(observation, source):
    if observation.get("contract_id") != CONTRACT:
        return observation
    require(set(observation) == FIELDS, "ACTION_OBSERVATION_REFERENCE_FIELDS")
    item, uom, decision_date = (observation[k] for k in ("item_id", "uom", "decision_date"))
    buckets = [i for i, row in enumerate(source["calendar"]) if row["start_date"] == decision_date]
    require(len(buckets) == 1, "ACTION_OBSERVATION_AUTHORITY_MISMATCH")
    calendar = source["calendar"][buckets[0] :]
    require(observation["bucket"] == calendar[0], "ACTION_OBSERVATION_AUTHORITY_MISMATCH")
    policies = sorted(
        (r for r in source["policies"] if r["item_id"] == item),
        key=lambda r: (r["effective_from"], r["effective_to"], r["policy_id"]),
    )
    applicable = [r for r in policies if r["effective_from"] <= decision_date < r["effective_to"]]
    controls = [r for r in source["item_controls"] if r["item_id"] == item]
    rules = [r for r in source["quantity_rules"] if r["uom"] == uom]
    require(
        len(applicable) == len(controls) == len(rules) == 1, "ACTION_OBSERVATION_AUTHORITY_MISMATCH"
    )
    demands = {
        r["yyyyww"]: r for r in source["demands"] if r["item_id"] == item and r["uom"] == uom
    }
    result = {
        **observation,
        "contract_id": "io-replenishment-observation-v1",
        "context": source["context"],
        "bucket": calendar[0],
        "calendar": calendar,
        "future_demand": [demands[b["yyyyww"]] for b in calendar],
        "policy": applicable[0],
        "policy_schedule": policies,
        "control": controls[0],
        "quantity_rule": rules[0],
        "strategy": source["strategy"],
        "approval_reference": source["execution_approval_reference"],
        "allowed_action_types": source["allowed_action_types"],
        "capacity_mode": source["capacity_mode"],
    }
    if source["strategy_input_binding"] is not None:
        result["strategy_input_binding"] = source["strategy_input_binding"]
    return result


def expanded_action_rows(artifact):
    source = artifact["observation_source"]
    indexes = source_item_index(source)
    for row in artifact["rows"]:
        observation = row["observation"]
        yield {
            **row,
            "observation": expand_observation(
                observation, item_source(source, indexes, observation["item_id"])
            ),
        }

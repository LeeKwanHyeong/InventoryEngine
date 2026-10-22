"""Bounded item work units; no policy, order, or inventory state crosses a part."""

from collections import defaultdict

from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, require

ITEM_TABLES = ("master", "positions", "demands", "policies", "receipt_decisions")
RESOURCE_ERRORS = {"RECOMMENDATION_OBSERVATION_LIMIT", "RECOMMENDATION_EVIDENCE_LIMIT"}


def control_partitions(prepared, execution):
    from .run import validate_controls

    ids = [r["item_id"] for r in prepared["master"]]
    require(len(set(ids)) == len(ids), "CONTROL_UNIVERSE_MISMATCH")
    require(
        {r["item_id"] for r in execution["item_controls"]} == set(ids), "CONTROL_UNIVERSE_MISMATCH"
    )
    indexes = {}
    for name in ITEM_TABLES:
        groups = defaultdict(list)
        for row in prepared[name]:
            groups[row["item_id"]].append(row)
        indexes[name] = groups
    controls = {r["item_id"]: r for r in execution["item_controls"]}

    def part(selected):
        data = {
            **prepared,
            **{name: [r for item in selected for r in indexes[name][item]] for name in ITEM_TABLES},
        }
        config = {**execution, "item_controls": [controls[item] for item in selected]}
        try:
            validate_controls(data, config)
        except InventoryInputError as exc:
            if str(exc) not in RESOURCE_ERRORS or len(selected) == 1:
                raise
            middle = len(selected) // 2
            yield from part(selected[:middle])
            yield from part(selected[middle:])
        else:
            yield data, config

    # Retain original order, so existing row ordering and decision IDs survive.
    for offset in range(0, len(ids), 128):
        yield from part(ids[offset : offset + 128])


def validate_partitioned_controls(prepared, execution):
    for _data, _config in control_partitions(prepared, execution):
        pass


def simulate_partitioned(prepared, execution, input_hash, strategy, *, admissions=None):
    from .run import simulate

    rows, decisions, orders = [], [], []
    for data, config in control_partitions(prepared, execution):
        part_rows, part_decisions, part_orders = simulate(
            data,
            config,
            input_hash,
            strategy,
            admissions=admissions,
            compact_observations=True,
        )
        rows.extend(part_rows)
        decisions.extend(part_decisions)
        orders.extend(part_orders)
    return rows, decisions, orders

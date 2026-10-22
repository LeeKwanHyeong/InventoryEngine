"""Input/output mapping only; expected values come from captured C# output."""

from datetime import date, timedelta

from dsio_inventory_engine.simulate_inventory.application.legacy_reference import (
    LegacyBucket,
    LegacyDatedQuantity,
    LegacyLot,
    LegacyPolicy,
    LegacyPsiRequest,
)


def replay_request(given):
    buckets = tuple(
        LegacyBucket(
            row["bucket_id"],
            date.fromisoformat(row["start"]),
            date.fromisoformat(row["end"]),
            date(int(row["base_month"][:4]), int(row["base_month"][4:]), 1)
            if row["base_month"]
            else None,
        )
        for row in given["buckets"]
    )

    def quantities(name):
        return tuple(
            LegacyDatedQuantity(date.fromisoformat(row["when"]), float(row["quantity"]))
            for row in given[name]
        )

    policies = tuple(
        LegacyPolicy(
            date.fromisoformat(row["start"]),
            date.fromisoformat(row["end"]),
            *(float(row[k]) for k in ("rop", "target", "mos", "ma_demand")),
        )
        for row in given["policies"]
    )
    return LegacyPsiRequest(
        buckets=buckets,
        demands=quantities("demands"),
        policies=policies,
        receipts=quantities("receipts"),
        boh=float(given["boh"]),
        backorder=float(given["backorder"]),
        pending_po=float(given["pending_po"]),
        internal_lead_time=float(given["internal_lead_time"]),
        stock_policy=given["stock_policy"],
        lot=LegacyLot(*(float(given["lot"][k]) for k in ("minimum", "maximum", "step"))),
    )


def columns(rows, days):
    numbers = (
        "demand",
        "boh",
        "due_in",
        "eoh",
        "backorder",
        "backorder_close",
        "pending_po_close",
        "purchase_order",
        "demand_allocation",
        "stock_allocation",
        "weekly_allocation_rate",
        "exp_mos",
        "rop",
        "target",
    )
    result = {name: [getattr(row, name) for row in rows] for name in numbers}
    result["bucket_ids"] = [row.bucket.bucket_id for row in rows]
    ordered = [row for row in rows if row.purchase_order > 0]
    result["order_dates"] = [row.bucket.start.isoformat() for row in ordered]
    result["due_dates"] = [(row.bucket.start + timedelta(days=days)).isoformat() for row in ordered]
    result["terminal_pending_po"] = rows[-1].pending_po_close
    return result


def assert_native_numbers(actual, expected):
    """No tolerance: round-trip C# strings must decode to identical binary64."""
    if isinstance(expected, list):
        assert len(actual) == len(expected)
        for left, right in zip(actual, expected, strict=True):
            assert_native_numbers(left, right)
    elif type(actual) is float:
        assert actual.hex() == float(expected).hex(), (actual, expected)
    else:
        assert actual == expected

"""Explicit, DB-free reference of frozen C# PSI; never selected by Runtime.

This preserves Legacy quirks for comparison, including first-match rows,
end-of-bucket ordering, Forecast backlog, current-bucket DueIn writes and
out-of-horizon PO. It is NOT an operational strategy or a Guard bypass.
"""

from dataclasses import dataclass, fields, replace
from datetime import date, timedelta
import math

from .legacy_numbers import decimal_ceiling_of_double, dotnet_round


def _number(value: float) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("LEGACY_REFERENCE_FINITE_NONNEGATIVE_NUMBER")
    return float(value)


def _date(value: date) -> None:
    if type(value) is not date:
        raise ValueError("LEGACY_REFERENCE_DATE_ONLY")


@dataclass(frozen=True)
class LegacyBucket:
    bucket_id: str
    start: date
    end: date
    base_month: date | None = None

    def __post_init__(self):
        _date(self.start)
        _date(self.end)
        if self.start > self.end or not isinstance(self.bucket_id, str) or not self.bucket_id:
            raise ValueError("LEGACY_REFERENCE_BUCKET")
        if self.base_month is not None:
            _date(self.base_month)
            if self.base_month.day != 1:
                raise ValueError("LEGACY_REFERENCE_BASE_MONTH")


@dataclass(frozen=True)
class LegacyDatedQuantity:
    when: date
    quantity: float

    def __post_init__(self):
        _date(self.when)
        _number(self.quantity)


@dataclass(frozen=True)
class LegacyPolicy:
    start: date
    end: date
    rop: float
    target: float
    mos: float = 0.0
    ma_demand: float = 0.0

    def __post_init__(self):
        _date(self.start)
        _date(self.end)
        if self.start > self.end:
            raise ValueError("LEGACY_REFERENCE_POLICY_INTERVAL")
        for name in ("rop", "target", "mos", "ma_demand"):
            _number(getattr(self, name))


@dataclass(frozen=True)
class LegacyLot:
    minimum: float = 1.0
    maximum: float = 1.0
    step: float = 1.0

    def __post_init__(self):
        for field in fields(self):
            _number(getattr(self, field.name))
        if self.step == 0 or self.minimum > self.maximum:
            raise ValueError("LEGACY_REFERENCE_LOT")

    def size(self, quantity: float) -> float:
        if type(quantity) not in (int, float) or not math.isfinite(quantity):
            raise ValueError("LEGACY_REFERENCE_FINITE_LOT_QUANTITY")
        value = float(quantity)
        low, high, step = float(self.minimum), float(self.maximum), float(self.step)
        if value < low:
            return low
        origin = high if value > high else low
        return decimal_ceiling_of_double((value - origin) / step) * step + origin


@dataclass(frozen=True)
class LegacyPsiRequest:
    buckets: tuple[LegacyBucket, ...]
    demands: tuple[LegacyDatedQuantity, ...] = ()
    policies: tuple[LegacyPolicy, ...] = ()
    receipts: tuple[LegacyDatedQuantity, ...] = ()
    boh: float = 0.0
    backorder: float = 0.0
    pending_po: float = 0.0
    internal_lead_time: float = 0.0
    stock_policy: str = "OUTL"
    lot: LegacyLot = LegacyLot()

    def __post_init__(self):
        for name, item_type in (
            ("buckets", LegacyBucket),
            ("demands", LegacyDatedQuantity),
            ("policies", LegacyPolicy),
            ("receipts", LegacyDatedQuantity),
        ):
            rows = getattr(self, name)
            if type(rows) is not tuple or not all(type(row) is item_type for row in rows):
                raise ValueError("LEGACY_REFERENCE_IMMUTABLE_INPUT")
        if not self.buckets or len({b.bucket_id for b in self.buckets}) != len(self.buckets):
            raise ValueError("LEGACY_REFERENCE_BUCKETS_REQUIRED_UNIQUE")
        for name in ("boh", "backorder", "pending_po", "internal_lead_time"):
            _number(getattr(self, name))
        if type(self.stock_policy) is not str or type(self.lot) is not LegacyLot:
            raise ValueError("LEGACY_REFERENCE_POLICY_OR_LOT")
        legacy_lead_time_days(self.internal_lead_time)


def legacy_lead_time_days(internal_lead_time: float) -> int:
    """Original TimeUOM=28800 and truncation, NOT the upstream PO conversion."""
    days = int(_number(internal_lead_time) / 28800.0)
    if days > 2147483647:
        raise ValueError("LEGACY_REFERENCE_LEAD_TIME_RANGE")
    return days


def legacy_arrival_bucket(buckets: tuple[LegacyBucket, ...], due: date) -> int | None:
    _date(due)
    for index, bucket in enumerate(buckets):
        if bucket.start <= due <= bucket.end:
            return index
    return None


@dataclass(frozen=True)
class LegacyPsiRow:
    bucket: LegacyBucket
    demand: float
    due_in: float
    boh: float
    eoh: float
    backorder: float
    backorder_close: float
    pending_po_close: float
    purchase_order: float
    demand_allocation: float
    stock_allocation: float
    weekly_allocation_rate: float
    exp_mos: float
    rop: float
    target: float


def run_legacy_reference(request: LegacyPsiRequest) -> tuple[LegacyPsiRow, ...]:
    """One immutable input -> fresh mutable local rows -> immutable output.

    No optimization, source lookup, publication, model, FSN or Run binding.
    Missing Source policy/quantity means zero here ONLY because SetBucket did
    so; this is not permission to treat missing operational Source as zero.
    """
    if type(request) is not LegacyPsiRequest:
        raise ValueError("LEGACY_REFERENCE_REQUEST_REQUIRED")
    prepared = []
    for bucket in request.buckets:
        month = bucket.base_month or bucket.start.replace(day=1)
        policy = next((p for p in request.policies if p.start <= month <= p.end), None)
        demand = next(
            (r.quantity for r in request.demands if bucket.start <= r.when <= bucket.end), 0.0
        )
        receipt = next(
            (r.quantity for r in request.receipts if bucket.start <= r.when <= bucket.end), 0.0
        )
        prepared.append(
            {
                "bucket": bucket,
                "demand": float(demand),
                "due_in": float(receipt),
                "rop": float(policy.rop) if policy else 0.0,
                "target": float(policy.target) if policy else 0.0,
                "mos": float(policy.mos) if policy else 0.0,
                "ma": float(policy.ma_demand) if policy else 0.0,
            }
        )
    before_eoh, before_bo, before_po = map(
        float, (request.boh, request.backorder, request.pending_po)
    )
    days = legacy_lead_time_days(request.internal_lead_time)
    result = []
    for row in prepared:
        boh, backlog, pending = before_eoh, before_bo, before_po
        pending -= row["due_in"]
        eoh = boh + row["due_in"]
        allocation = 0.0
        demand_allocation = 0.0
        backorder = 0.0
        if eoh >= backlog:
            eoh -= backlog
            allocation += backlog
            backlog = 0.0
        else:
            backlog -= eoh
            allocation += eoh
            eoh = 0.0
        if eoh >= row["demand"]:
            demand_allocation = row["demand"]
            allocation += row["demand"]
            eoh -= row["demand"]
        else:
            demand_allocation = eoh
            allocation += eoh
            backorder = row["demand"] - eoh
            backlog += backorder
            eoh = 0.0
        order, temporary_order = 0.0, 0.0
        if eoh + pending - backlog < row["rop"]:
            raw_order = row["target"] - eoh - pending + backlog
            order = request.lot.size(raw_order)
            if request.internal_lead_time == 0:
                eoh += order
                temporary_order = order
                order = 0.0
            due = row["bucket"].start + timedelta(days=days)
            arrival = legacy_arrival_bucket(request.buckets, due)
            if arrival is not None:
                prepared[arrival]["due_in"] += order
        pending += order
        if request.internal_lead_time == 0:
            if eoh >= backlog:
                eoh -= backlog
                allocation += backlog
                backlog, backorder = 0.0, 0.0
            else:
                backlog -= eoh
                allocation += eoh
                eoh = 0.0
                demand_allocation = 0.0
            order = temporary_order
        exp_mos = 0.0
        if request.stock_policy == "OUTL" and row["mos"] > 0:
            denominator = row["target"] / row["mos"]
            if denominator != 0:
                exp_mos = dotnet_round(eoh / denominator)
        elif request.stock_policy in ("MINQ", "ROPQ", "SFST") and row["ma"] > 0:
            exp_mos = dotnet_round(eoh / row["ma"])
        if not math.isfinite(exp_mos):
            exp_mos = 0.0
        rate = dotnet_round(demand_allocation / row["demand"]) if row["demand"] > 0 else 0.0
        values = (
            eoh,
            backlog,
            pending,
            order,
            allocation,
            demand_allocation,
            rate,
            exp_mos,
            row["due_in"],
        )
        if not all(math.isfinite(v) for v in values):
            raise ValueError("LEGACY_REFERENCE_NONFINITE_RESULT")
        result.append(
            LegacyPsiRow(
                row["bucket"],
                row["demand"],
                row["due_in"],
                boh,
                eoh,
                backorder,
                backlog,
                pending,
                order,
                demand_allocation,
                allocation,
                rate,
                exp_mos,
                row["rop"],
                row["target"],
            )
        )
        before_eoh, before_bo, before_po = eoh, backlog, pending
    # SetDuein can write into an already calculated bucket; preserve the final
    # stored DueIn too, without recalculating that bucket's old EOH/allocations.
    return tuple(replace(row, due_in=prepared[i]["due_in"]) for i, row in enumerate(result))

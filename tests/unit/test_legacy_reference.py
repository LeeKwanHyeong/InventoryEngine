from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime
from decimal import Decimal, localcontext
import math

import pytest

from dsio_inventory_engine.simulate_inventory.application.legacy_numbers import (
    decimal_ceiling_of_double,
    dotnet_round,
)
from dsio_inventory_engine.simulate_inventory.application.legacy_reference import (
    LegacyBucket,
    LegacyDatedQuantity,
    LegacyLot,
    LegacyPolicy,
    LegacyPsiRequest,
    legacy_arrival_bucket,
    legacy_lead_time_days,
    run_legacy_reference,
)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, True, "1", Decimal("1")])
def test_invalid_or_implicit_numeric_conversion_is_rejected(value):
    with pytest.raises(ValueError):
        LegacyDatedQuantity(date(2026, 1, 1), value)


@pytest.mark.parametrize("minimum,maximum,step", [(0, 1, 0), (2, 1, 1), (0, 1, -1)])
def test_invalid_lot_fails_before_computation(minimum, maximum, step):
    with pytest.raises(ValueError):
        LegacyLot(minimum, maximum, step)


def test_reference_contract_is_immutable_and_not_a_runtime_strategy():
    bucket = LegacyBucket("W", date(2026, 1, 1), date(2026, 1, 7))
    request = LegacyPsiRequest((bucket,), boh=10)
    with pytest.raises(FrozenInstanceError):
        request.boh = 12
    with pytest.raises(ValueError):
        replace(request, buckets=[bucket])
    with pytest.raises(ValueError):
        replace(request, buckets=())
    with pytest.raises(ValueError):
        replace(request, buckets=(bucket, bucket))
    with pytest.raises(ValueError):
        run_legacy_reference({"buckets": [bucket]})
    assert not hasattr(request, "automatic_publish_allowed")
    assert not hasattr(request, "operational_io_eligible")
    assert not hasattr(request, "strategy")


def test_subday_dates_are_explicitly_outside_this_reference_scope():
    with pytest.raises(ValueError):
        LegacyBucket("W", datetime(2026, 1, 1), date(2026, 1, 7))
    with pytest.raises(ValueError):
        LegacyBucket("W", date(2026, 1, 7), date(2026, 1, 1))
    with pytest.raises(ValueError):
        LegacyBucket("W", date(2026, 1, 1), date(2026, 1, 7), date(2026, 1, 2))
    with pytest.raises(ValueError):
        LegacyPolicy(date(2026, 2, 1), date(2026, 1, 1), 1, 2)


def test_numeric_primitives_keep_dotnet_intermediate_rounding():
    assert dotnet_round(2.675) == 2.68
    assert round(2.675, 2) == 2.67
    assert decimal_ceiling_of_double(10.000000000000002) == 10
    assert math.ceil(10.000000000000002) == 11
    assert math.copysign(1, dotnet_round(-0.005)) == -1
    assert LegacyLot(0, 100, 1).size(-4) == 0
    with localcontext() as context:
        context.prec = 2
        assert decimal_ceiling_of_double(123456789012345.6) == 123456789012346


@pytest.mark.parametrize("digits", [-1, 16, 2.0, True])
def test_invalid_rounding_digits(digits):
    with pytest.raises(ValueError):
        dotnet_round(1.25, digits)


def test_nonfinite_results_and_out_of_scope_ranges_fail_explicitly():
    with pytest.raises(ValueError):
        decimal_ceiling_of_double(float("inf"))
    with pytest.raises(ValueError):
        decimal_ceiling_of_double(1e29)
    with pytest.raises(ValueError):
        legacy_lead_time_days(28800 * 2147483648)
    with pytest.raises(ValueError):
        LegacyLot().size(float("nan"))
    bucket = LegacyBucket("W", date(2026, 1, 1), date(2026, 1, 7))
    with pytest.raises(ValueError):
        run_legacy_reference(
            LegacyPsiRequest(
                (bucket,), boh=1.7e308, receipts=(LegacyDatedQuantity(bucket.start, 1.7e308),)
            )
        )


def test_arrival_uses_first_inclusive_bucket_not_next_boundary():
    buckets = (
        LegacyBucket("A", date(2026, 1, 1), date(2026, 1, 7)),
        LegacyBucket("B", date(2026, 1, 7), date(2026, 1, 14)),
    )
    assert legacy_arrival_bucket(buckets, date(2026, 1, 7)) == 0
    assert legacy_arrival_bucket(buckets, date(2026, 1, 15)) is None
    assert legacy_lead_time_days(230399.99) == 7

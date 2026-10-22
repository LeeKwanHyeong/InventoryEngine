"""Fixed hand calculations, not new official 52-week experimental evidence."""

from copy import deepcopy
from decimal import Decimal, localcontext

import pytest

from dsio_inventory_engine.evaluate_replenishment.robustness import score_trace, verify_scorecard
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from tests.support.robustness_fixtures import cost_trace, hand_trace


@pytest.mark.parametrize(
    "profile,total",
    [
        ("FIXED_COMPATIBILITY", "350.00"),
        ("PRICE_BASE", "3550.00"),
        ("PRICE_HOLDING_HIGH", "3800.00"),
        ("PRICE_ORDER_HIGH", "3700.00"),
        ("PRICE_NO_FORECAST_PENALTY", "3300.00"),
    ],
)
def test_five_cost_profiles_match_independent_hand_expectations(profile, total):
    value = score_trace(cost_trace(), profile_id=profile)
    s = value["score"]
    assert (
        s["week_count"],
        s["on_hand_eoh_unit_weeks"],
        s["customer_backorder_unit_weeks"],
        s["order_count"],
        s["ordered_qty"],
        s["forecast_shortage_qty"],
    ) == (52, "260", "2", 2, "10", "1")
    assert s["cost"]["display_total_cost"] == total
    assert s["average_on_hand_eoh_qty"] == "5"
    assert s["average_on_hand_eoh_value"] == "1250"
    if profile == "PRICE_BASE":
        assert {
            k: str(Decimal(v).quantize(Decimal("0.01"))) for k, v in s["cost"]["components"].items()
        } == {
            "holding": "250.00",
            "customer_backorder": "500.00",
            "fixed_order": "50.00",
            "variable_order": "2500.00",
            "forecast_shortage": "250.00",
        }


def test_score_excludes_tail_and_terminal_pipeline_does_not_add_to_cost():
    result = score_trace(cost_trace())
    assert result["score"]["cost"]["display_total_cost"] == "3550.00"
    assert result["score"]["order_count"] == 2
    assert result["tail"]["week_count"] == 13
    assert result["tail"]["ordered_qty"] == "100"
    assert result["tail"]["order_count"] == 1
    assert result["terminal"]["51"] == {
        "week_index": 51,
        "on_hand_eoh_qty": "5",
        "customer_backorder_qty": "0",
        "known_pipeline_qty": "10",
        "recommended_pipeline_qty": "8",
        "total_pipeline_qty": "18",
        "pipeline_value": "4500",
    }
    assert result["terminal"]["64"]["known_pipeline_qty"] == "7"
    assert result["terminal"]["64"]["recommended_pipeline_qty"] == "0"
    assert result["prefixes"]["13"]["on_hand_eoh_unit_weeks"] == "65"
    assert result["prefixes"]["26"]["on_hand_eoh_unit_weeks"] == "130"
    assert result["prefixes"]["52"] == result["score"]
    # Old backlog fulfillment is NOT current-period customer fulfillment.
    assert result["score"]["current_demand_qty"] == "3"
    assert result["score"]["current_fulfilled_qty"] == "0"
    assert result["score"]["fill_rate"] == "0"


def test_no_demand_is_null_but_stock_cost_is_kept():
    result = score_trace(hand_trace(fill=None, reserved=5))
    assert result["score"]["fill_rate"] is None
    assert result["score"]["service_status"] == "NO_DEMAND_IN_HORIZON"
    assert result["score"]["path_service_success"] is False
    assert result["score"]["cost"]["display_total_cost"] == "250.00"


def test_missing_price_is_not_zero_and_fixed_assumption_is_explicit():
    source = cost_trace()
    source["binding"]["unit_price"] = None
    result = score_trace(source)
    assert result["score"]["cost"]["status"] == "NOT_CALCULABLE"
    assert result["score"]["cost"]["total_cost"] is None
    assert result["score"]["average_on_hand_eoh_value"] is None
    assert (
        score_trace(source, profile_id="FIXED_COMPATIBILITY")["score"]["cost"]["display_total_cost"]
        == "350.00"
    )


def test_decimal_context_does_not_change_cost_or_hash_and_input_is_not_mutated():
    value = cost_trace()
    before = deepcopy(value)
    result = score_trace(value)
    with localcontext() as ctx:
        ctx.prec = 7
        assert score_trace(value) == result
    assert value == before
    assert all(
        result[key] is False
        for key in (
            "automatic_publish_allowed",
            "automatic_order_allowed",
            "operational_policy_approved",
            "database_writes",
        )
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda t: t["rows"].pop(),
        lambda t: t["rows"][52].update(boh_qty="1"),
        lambda t: t["rows"][1].update(backorder_open_qty="0"),
        lambda t: t["rows"][0].update(fulfilled_confirmed_customer_order_qty="1"),
        lambda t: t["rows"][2].update(forecast_shortage_qty="0"),
        lambda t: t["rows"][0].update(seq=True),
        lambda t: t["rows"][4].update(start_date="2055-01-01"),
        lambda t: t["orders"].pop(0),
        lambda t: t["orders"].append(deepcopy(t["orders"][0])),
        lambda t: t["orders"][0].update(order_date="2055-01-01"),
        lambda t: t["orders"][0].update(receipt_date="2060-01-01"),
        lambda t: t["gate"].update(effective_order_action="BLOCK"),
        lambda t: t["gate"].update(recommendation_calculation_allowed=False),
        lambda t: t["gate"].update(recommendation_calculation_allowed=1),
        lambda t: t["binding"].update(site_cd="V100"),
        lambda t: t["binding"]["unit_price"].update(currency="KRW"),
        lambda t: t["binding"]["unit_price"].update(price_as_of="2060-01-01"),
        lambda t: t["rows"][0].update(boh_qty="NaN"),
        lambda t: t["rows"][0].update(boh_qty="0.5"),
        lambda t: t["rows"][0].update(total_cost="0"),
        lambda t: t.update(total_cost="0"),
    ],
)
def test_invalid_psi_or_unbacked_supply_is_not_repriced(mutation):
    value = cost_trace()
    mutation(value)
    with pytest.raises(InventoryInputError):
        score_trace(value)


def test_failed_trace_has_no_fabricated_zero_metrics():
    value = hand_trace(failed=True)
    result = score_trace(value)
    assert result["failure_reason"] == "WORKER_FAILED"
    assert all(result[k] is None for k in ("score", "tail", "prefixes", "terminal"))
    value["rows"] = []
    with pytest.raises(InventoryInputError):
        score_trace(value)


def test_resealed_cost_or_tail_change_fails_full_window_recomputation():
    source = cost_trace()
    result = score_trace(source)
    verify_scorecard(result, source, expected_scorecard_hash=result["content_hash"])
    result["tail"]["order_count"] = 0
    result["content_hash"] = digest({k: v for k, v in result.items() if k != "content_hash"})
    with pytest.raises(InventoryInputError, match="SCORECARD_REPLAY_MISMATCH"):
        verify_scorecard(result, source, expected_scorecard_hash=result["content_hash"])


@pytest.mark.parametrize("fill,success", [("0.95", True), ("0.949999", False)])
def test_service_boundary_is_not_display_rounding(fill, success):
    assert score_trace(hand_trace(fill=fill))["score"]["path_service_success"] is success


@pytest.mark.parametrize("bad", [None, [], 1, True, {"binding": []}])
def test_malformed_input_uses_contract_error_not_incidental_exception(bad):
    with pytest.raises(InventoryInputError):
        score_trace(bad)

"""Literal stock-flow and frozen-statistic expectations; no production PSI oracle."""

import ast
from copy import deepcopy
from decimal import localcontext
from pathlib import Path

import pytest

from dsio_inventory_engine.evaluate_replenishment.robustness.development_source import (
    DevelopmentSourceProducer,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from tests.support.robustness_source_fixtures import history_request, W0


@pytest.mark.parametrize(
    "lead,initial,ending,pending,offset",
    [(7, "3", "1", 0, None), (35, "7", "0", 1, 0), (49, "9", "2", 1, 2)],
)
def test_constant_one_ea_hand_warmup_and_w0_pipeline(lead, initial, ending, pending, offset):
    result = DevelopmentSourceProducer().produce(history_request(lead=lead))
    policy = result["reference_policy"]
    assert policy["reference_mean"] == "1"
    assert policy["population_stddev"] == "0"
    assert policy["safety_stock_qty"] == "0"
    assert policy["rop_qty"] == str(lead // 7)
    assert policy["raw_monthly_q_qty"] == "5"
    assert policy["lot_qty"] == "5"
    assert result["warmup_initial_boh_qty"] == initial
    assert [o["receipt_id"] for o in result["warmup_orders"]] == [
        f"WARMUP-{i}" for i in range(2, 52, 5)
    ]
    assert result["w0_position"]["on_hand_qty"] == ending
    assert result["w0_position"]["available_qty"] == ending
    assert result["w0_position"]["reserved_qty"] == result["w0_position"]["backorder_qty"] == "0"
    assert len(result["w0_due_in"]) == pending
    if pending:
        from datetime import timedelta

        assert result["w0_due_in"][0]["quantity"] == "5"
        assert result["w0_due_in"][0]["receipt_date"] == (W0 + timedelta(weeks=offset)).isoformat()
        assert result["w0_due_in"][0]["due_date"] == result["w0_due_in"][0]["receipt_date"]
    rows = result["warmup_ledger"]
    assert len(rows) == 52 and rows[-1]["eoh_qty"] == ending
    assert rows[0]["week_index"] == -52 and rows[-1]["week_index"] == -1
    # 52 total demand. Ten orders of 5; exact final stock+pipeline = initial+50-52.
    assert int(ending) + pending * 5 == int(initial) + 50 - 52


def test_shortage_does_not_censor_history_or_become_customer_backorder():
    value = history_request(demand=0)
    value["history"][-1]["gross_demand_qty"] = "10"
    result = DevelopmentSourceProducer().produce(value)
    assert result["warmup_orders"] == []
    assert result["warmup_ledger"][-1]["forecast_shortage_qty"] == "10"
    assert result["w0_position"]["backorder_qty"] == "0"
    assert result["uncensored_history"][-1]["gross_demand_qty"] == "10"
    assert result["planning_forecast"][0]["gross_forecast_qty"] == "1"


def test_actual_customer_backorder_is_carried_to_w0_not_reset():
    value = history_request(demand=0)
    value["history"][-1].update(gross_demand_qty="10", confirmed_customer_order_qty="10")
    result = DevelopmentSourceProducer().produce(value)
    assert result["w0_position"]["on_hand_qty"] == "0"
    assert result["w0_position"]["backorder_qty"] == "10"
    assert result["warmup_ledger"][-1]["forecast_shortage_qty"] == "0"


def test_reference_variance_and_policy_are_frozen_before_warmup():
    value = history_request(lead=7)
    for i, row in enumerate(value["history"][26:52]):
        row["gross_demand_qty"] = str(2 * (i % 2))
    before = DevelopmentSourceProducer().produce(value)
    assert before["reference_policy"]["population_stddev"] == "1"
    assert before["reference_policy"]["safety_stock_qty"] == "2"
    assert before["reference_policy"]["rop_qty"] == "3"
    for row in value["history"][52:]:
        row["gross_demand_qty"] = "100"
    after = DevelopmentSourceProducer().produce(value)
    assert after["reference_policy"] == before["reference_policy"]
    assert after["planning_forecast"][0]["gross_forecast_qty"] == "100"


def test_zero_reference_and_zero_w0_history_never_invent_orders():
    result = DevelopmentSourceProducer().produce(history_request(demand=0))
    assert result["warmup_orders"] == [] and result["w0_due_in"] == []
    assert result["w0_history_status"] == "ZERO_DEMAND_HISTORY"
    assert all(r["gross_forecast_qty"] == "0" for r in result["planning_forecast"])
    assert result["source_authority_verified"] is False
    assert result["automatic_order_allowed"] is False


def test_source_hash_replay_catches_resealed_ledger_and_pipeline_mutation():
    original = history_request()
    producer = DevelopmentSourceProducer()
    result = producer.produce(original)
    producer.verify(result, original, expected_source_hash=result["content_hash"])
    result["w0_due_in"][0]["quantity"] = "10"
    result["content_hash"] = digest({k: v for k, v in result.items() if k != "content_hash"})
    with pytest.raises(InventoryInputError, match="SOURCE_REPLAY_MISMATCH"):
        producer.verify(result, original, expected_source_hash=result["content_hash"])


def test_source_is_immutable_by_copy_and_independent_of_ambient_decimal():
    value = history_request()
    original = deepcopy(value)
    result = DevelopmentSourceProducer().produce(value)
    with localcontext() as ctx:
        ctx.prec = 8
        assert DevelopmentSourceProducer().produce(value) == result
    assert value == original


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r["history"].pop(),
        lambda r: r["history"][0].update(start_date=r["w0"]),
        lambda r: r["history"][3].update(start_date=r["history"][2]["start_date"]),
        lambda r: r["history"][0].update(gross_demand_qty=None),
        lambda r: r["history"][0].update(gross_demand_qty="0.5"),
        lambda r: r["history"][0].update(gross_demand_qty="-1"),
        lambda r: r["history"][0].update(confirmed_customer_order_qty="2"),
        lambda r: r.update(lead_time_days=True),
        lambda r: r.update(lead_time_days=56),
        lambda r: r.update(lead_time_days=8),
        lambda r: r.update(moq="0"),
        lambda r: r["scope"].update(site_cd="V100"),
        lambda r: r.update(future_truth=["9"]),
    ],
)
def test_incomplete_or_changed_source_is_not_zero_imputed(mutation):
    value = history_request()
    mutation(value)
    with pytest.raises(InventoryInputError):
        DevelopmentSourceProducer().produce(value)


def test_warmup_module_does_not_import_production_strategy_guard_or_psi():
    root = Path(__file__).resolve().parents[2]
    tree = ast.parse(
        (
            root
            / "src/dsio_inventory_engine/evaluate_replenishment/robustness/development_source.py"
        ).read_text()
    )
    imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    assert all(
        not any(
            word in (name or "")
            for word in ("recommend_replenishment", "simulate_inventory", "training")
        )
        for name in imports
    )

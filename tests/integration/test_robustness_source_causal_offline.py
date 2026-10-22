"""Offline hand cases: Source -> Canonical -> unchanged admission/Guard -> causal PSI.

Neither official paths nor a Catalog experiment are generated. Probes explicitly
test the observation boundary; one test also uses the production mathematical strategy.
"""

from copy import deepcopy
import json

import pytest

from fixtures import reseal
from dsio_inventory_engine.evaluate_replenishment.robustness.causal_simulation import (
    RunCausalPsiUseCase,
)
from dsio_inventory_engine.evaluate_replenishment.robustness.source_binding import (
    RealizedDemandPath,
)
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.replenishment import RecommendationRequest
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from dsio_inventory_engine.recommend_replenishment.application.mathematical import (
    prepare_mathematical_strategy,
)
from tests.support.robustness_source_fixtures import (
    source_case,
    history_request,
    path_for,
    probe,
    bindings,
    mathematical_case,
)

DEV = DeploymentScope("DSE", "DEVELOPMENT")


def run(case, path=None, *, strategy=None):
    req = case[2]
    path = path or path_for(req)
    return RunCausalPsiUseCase(DEV).execute(req, strategy or probe(req), **bindings(case, path))


def test_w0_receipt_is_not_also_added_to_boh_and_review_stays_nonautomatic():
    case = source_case()
    original = deepcopy((case[0], case[1], case[2].to_dict()))
    result = run(case)
    first = result["psi_rows"][0]
    assert (
        first["boh_qty"],
        first["confirmed_supplier_receipt_qty"],
        first["available_qty"],
        first["fulfilled_forecast_qty"],
        first["eoh_qty"],
    ) == ("0", "5", "5", "1", "4")
    assert result["effective_policy_admission"]["effective_order_action"] == "REVIEW"
    assert result["automatic_publish_allowed"] is False
    assert result["automatic_order_allowed"] is False
    assert all(o["automatic_order_allowed"] is False for o in result["recommended_orders"])
    assert (case[0], case[1], case[2].to_dict()) == original


def test_warmup_customer_backorder_survives_w0_and_receipts_clear_it_first():
    raw = history_request()
    raw["history"][-1].update(gross_demand_qty="10", confirmed_customer_order_qty="10")
    case = source_case(raw)
    assert case[1]["w0_position"]["backorder_qty"] == "9"
    result = run(
        case, path_for(case[2], values=["0"] * 65), strategy=probe(case[2], action="HOLD", qty="0")
    )
    first = result["psi_rows"][0]
    assert (
        first["backorder_open_qty"],
        first["confirmed_supplier_receipt_qty"],
        first["fulfilled_backorder_qty"],
        first["backorder_close_qty"],
        first["eoh_qty"],
    ) == ("9", "5", "5", "4", "0")


@pytest.mark.parametrize("shock", [0, 1, 12, 51, 64])
def test_future_suffix_and_path_metadata_do_not_change_prefix_observations_actions_or_guard(shock):
    case = source_case()
    left = path_for(case[2], values=["0"] * 65, key="LEFT-HAND-PATH", seed=101)
    values = ["0"] * 65
    values[shock:] = ["987654"] * (65 - shock)
    right = path_for(case[2], values=values, key="RIGHT-HAND-PATH", seed=102)
    a, b = run(case, left), run(case, right)
    # Decision is before consumption, so the decision AT the shock is still identical.
    assert a["decision_evidence"][: shock + 1] == b["decision_evidence"][: shock + 1]
    assert (
        a["psi_rows"][shock]["forecast_shortage_qty"]
        != b["psi_rows"][shock]["forecast_shortage_qty"]
    )
    for result in (a, b):
        text = json.dumps([d["observation"] for d in result["decision_evidence"]])
        assert left.content_hash not in text and right.content_hash not in text
        assert "LEFT-HAND-PATH" not in text and "RIGHT-HAND-PATH" not in text
        assert all(
            r["gross_forecast_qty"] == "1"
            for d in result["decision_evidence"]
            for r in d["observation"]["future_demand"]
        )
    if shock < 64:
        assert (
            a["decision_evidence"][shock + 1]["observation"]["state"]
            != b["decision_evidence"][shock + 1]["observation"]["state"]
        )


def test_production_mathematical_strategy_also_has_exact_future_prefix_invariance():
    case, math_request = mathematical_case(source_case())
    _, _, _, strategy = prepare_mathematical_strategy(math_request, DEV)
    left = path_for(case[2], values=["1"] * 65, key="MATH-LEFT", seed=103)
    right = path_for(case[2], values=["1"] * 13 + ["900000"] * 52, key="MATH-RIGHT", seed=104)
    a, b = run(case, left, strategy=strategy), run(case, right, strategy=strategy)
    assert a["decision_evidence"][:14] == b["decision_evidence"][:14]
    assert a["psi_rows"][13]["forecast_shortage_qty"] != b["psi_rows"][13]["forecast_shortage_qty"]
    assert a["recommended_orders"]  # Real strategy, not an always-HOLD stand-in.


def test_customer_orders_are_fixed_and_consumed_once_not_replaced_by_truth():
    raw = history_request()
    raw["known_future_customer_order_qty"] = "2"
    case = source_case(raw)
    result = run(case, path_for(case[2], values=["3"] * 65))
    first = result["psi_rows"][0]
    assert first["confirmed_customer_order_qty"] == "2"
    assert first["forecast_consumed_qty"] == "2"
    assert first["net_forecast_qty"] == "1"
    assert (
        first["fulfilled_confirmed_customer_order_qty"],
        first["fulfilled_forecast_qty"],
        first["eoh_qty"],
    ) == ("2", "1", "2")
    for d in result["decision_evidence"]:
        assert all(
            r["confirmed_customer_order_qty"] == "2" and r["net_forecast_qty"] == "0"
            for r in d["observation"]["future_demand"]
        )
    with pytest.raises(InventoryInputError, match="BELOW_KNOWN_ORDER"):
        run(case, path_for(case[2], values=["1"] * 65))


def test_fsn_block_is_preserved_and_probe_cannot_bypass_it():
    case = source_case(action="BLOCK")
    result = run(case, strategy=probe(case[2], action="HOLD", qty="0"))
    assert result["effective_policy_admission"]["no_order_gate"] is True
    assert result["recommended_orders"] == []
    with pytest.raises(InventoryInputError, match="EFFECTIVE_GATE_BYPASS"):
        run(case)


def test_original_finite_horizon_guard_is_not_relaxed_for_new_orders():
    case = source_case()
    result = run(case, strategy=probe(case[2], action="ORDER_QTY", qty="5"))
    last = result["decision_evidence"][-1]["validation"]
    assert last["accepted_order_qty"] == "0"
    assert "ARRIVAL_OUTSIDE_PLAN_HORIZON" in last["reason_codes"]
    assert all(
        o["receipt_date"] <= result["context"]["plan_end_date"]
        for o in result["recommended_orders"]
    )


def test_resealed_result_cannot_replace_realized_psi_or_original_guard_evidence():
    case = source_case()
    path = path_for(case[2])
    result = run(case, path)
    runner = RunCausalPsiUseCase(DEV)
    runner.verify(
        result,
        case[2],
        probe(case[2]),
        expected_result_hash=result["content_hash"],
        **bindings(case, path),
    )
    result["decision_evidence"][0]["validation"]["accepted_order_qty"] = "100"
    result["content_hash"] = digest({k: v for k, v in result.items() if k != "content_hash"})
    with pytest.raises(InventoryInputError, match="RESULT_REPLAY_MISMATCH"):
        runner.verify(
            result,
            case[2],
            probe(case[2]),
            expected_result_hash=result["content_hash"],
            **bindings(case, path),
        )


@pytest.mark.parametrize(
    "field", ["POSITION", "RECEIPT", "FORECAST", "PHYSICAL_LT", "LINEAGE", "CALENDAR", "MASTER"]
)
def test_canonical_resealing_does_not_authorize_changed_warmup_projection(field):
    case = source_case()
    value = case[2].to_dict()
    canonical = value["canonical_input"]
    snaps = canonical["snapshots"]
    if field == "POSITION":
        snaps["inventory"]["rows"][0].update(on_hand_qty="5", available_qty="5")
        snaps["prior_inventory"]["rows"][0]["eoh_qty"] = "5"
    elif field == "RECEIPT":
        snaps["receipts"]["rows"][0]["due_qty"] = "10"
    elif field == "FORECAST":
        snaps["forecast"]["rows"][0]["forecast_qty"] = "100"
    elif field == "PHYSICAL_LT":
        snaps["policies"]["rows"][0]["lead_time_days"] = 7
    elif field == "LINEAGE":
        snaps["inventory"]["metadata"]["generator_version"] = "fake-source"
    elif field == "CALENDAR":
        snaps["calendar"]["snapshot_id"] = "OTHER-CALENDAR"
        snaps["forecast"]["metadata"]["calendar_snapshot_id"] = "OTHER-CALENDAR"
    elif field == "MASTER":
        canonical["context"]["master_snapshot_revision"] = "OTHER-MASTER"
    value["canonical_input"] = reseal(canonical)
    from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest

    value["execution"]["canonical_input_hash"] = CanonicalInputRequest.from_dict(
        value["canonical_input"]
    ).input_hash
    changed = (case[0], case[1], RecommendationRequest.from_dict(value))
    with pytest.raises(InventoryInputError):
        run(changed)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda v: v["rows"].pop(),
        lambda v: v["rows"][0].update(seq=1),
        lambda v: v["rows"][1].update(seq=0),
        lambda v: v["rows"][0].update(realized_gross_demand_qty=None),
        lambda v: v["rows"][0].update(realized_gross_demand_qty="0.5"),
        lambda v: v["rows"][0].update(confirmed_customer_order_qty="999"),
        lambda v: v.update(future_label="HIGH_DEMAND"),
        lambda v: v.update(seed=True),
    ],
)
def test_truth_contract_rejects_missing_rows_extra_future_features_or_demand_scope_mutation(
    mutation,
):
    case = source_case()
    value = path_for(case[2]).to_dict()
    mutation(value)
    with pytest.raises(InventoryInputError):
        RealizedDemandPath.from_dict(value)


def test_wrong_hash_scope_and_operational_request_do_not_enter_new_runner():
    case = source_case()
    path = path_for(case[2])
    b = bindings(case, path)
    b["expected_realization_hash"] = "0" * 64
    with pytest.raises(InventoryInputError, match="REALIZATION_HASH_MISMATCH"):
        RunCausalPsiUseCase(DEV).execute(case[2], probe(case[2]), **b)
    wrong = path.to_dict()
    wrong["planning_request_hash"] = "1" * 64
    with pytest.raises(InventoryInputError, match="PLANNING_BINDING_MISMATCH"):
        run(case, RealizedDemandPath.from_dict(wrong))
    request = case[2].to_dict()
    request["execution"]["execution_purpose"] = "OPERATIONAL"
    op = (case[0], case[1], RecommendationRequest.from_dict(request))
    with pytest.raises(InventoryInputError, match="SHADOW_ONLY"):
        run(op)
    with pytest.raises(InventoryInputError, match="DEVELOPMENT_ONLY"):
        RunCausalPsiUseCase(DeploymentScope("DSE", "PRODUCTION")).execute(
            case[2], probe(case[2]), **bindings(case, path)
        )

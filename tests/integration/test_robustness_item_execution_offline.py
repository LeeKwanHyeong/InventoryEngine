"""Actual complete Catalog -> real Guard/causal PSI -> pure 52+13-week scorecards.

Only literal hand paths in 2055: not official generation, ranking or a main study.
"""

from copy import deepcopy
from decimal import Decimal, localcontext
from unittest.mock import patch

import pytest

from dsio_inventory_engine.evaluate_replenishment.robustness.contracts import NUMBERS, ROW_FIELDS
from dsio_inventory_engine.evaluate_replenishment.robustness.item_execution import (
    RunItemCatalogUseCase,
)
from dsio_inventory_engine.evaluate_replenishment.robustness.scoring import score_trace
from dsio_inventory_engine.evaluate_replenishment.robustness.selection import assess_paths
from dsio_inventory_engine.evaluate_replenishment.robustness.source_binding import (
    RealizedDemandPath,
)
from dsio_inventory_engine.inventory_contracts.replenishment import (
    RecommendationRequest,
    ReplenishmentProposal,
)
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    canonical_json,
    digest,
)
from dsio_inventory_engine.recommend_replenishment.application.policy_search import (
    CandidateStrategy,
)
from tests.support.robustness_item_fixtures import DEV, item_case, prepare_case
from tests.support.robustness_source_fixtures import path_for


def execute(case, *, values=None, split="NOMINAL"):
    assembly = prepare_case(case)
    rec = RecommendationRequest.from_dict(assembly["mathematical_request"]["recommendation"])
    path = path_for(rec, values=values, key="HAND-CATALOG-PATH", seed=71)
    path = RealizedDemandPath.from_dict({**path.to_dict(), "split": split})
    runner = RunItemCatalogUseCase(DEV, case["producer"])
    result = runner.execute(
        assembly,
        expected_assembly_hash=assembly["content_hash"],
        realization=path,
        expected_realization_hash=path.content_hash,
    )
    return case, assembly, path, result


@pytest.fixture(scope="module")
def executed():
    return execute(item_case())


def children(result):
    return [r for leaf in result["leaves"] for r in leaf["candidate_executions"]]


def test_every_candidate_retains_raw_guard_and_psi_and_scores_actual_rows(executed):
    case, assembly, _, result = executed
    rows = children(result)
    assert [r["candidate"] for r in rows] == assembly["complete_candidates"]
    assert [len(leaf["candidate_executions"]) for leaf in result["leaves"]] == [16, 16]
    assert all(len(canonical_json(leaf).encode()) <= 128_000_000 for leaf in result["leaves"])
    assert len(result["evaluator_bank_fragment"]["traces"]) == 32
    for child, trace, card in zip(
        rows, result["evaluator_bank_fragment"]["traces"], result["path_scorecards"], strict=True
    ):
        assert child["status"] == "SUCCEEDED"
        raw = child["causal_result"]
        assert len(raw["decision_evidence"]) == len(raw["psi_rows"]) == 65
        assert trace["rows"] == [{k: r[k] for k in ROW_FIELDS} for r in raw["psi_rows"]]
        assert card == score_trace(trace)
        assert card["score"]["current_demand_qty"] == "52"
        assert card["tail"]["current_demand_qty"] == "13"
        assert raw["effective_policy_admission"]["effective_order_action"] == "REVIEW"
        assert raw["automatic_publish_allowed"] is raw["automatic_order_allowed"] is False
        assert all(o["automatic_order_allowed"] is False for o in raw["recommended_orders"])
    assert result["assembly"]["parent_classification"] == case["parent_classification"]
    assert result["assembly"]["candidate_catalog"] == case["catalog"].to_dict()
    assert result["risk_aggregation_status"] == "NOT_RUN_REQUIRES_COMPLETE_SPLIT"
    assert result["selection_status"] == "NOT_RUN"


def test_literal_monthly_cover_candidate_has_hand_checked_guard_service_and_cost(executed):
    _, _, _, result = executed
    index = next(
        i
        for i, child in enumerate(children(result))
        if all(
            child["candidate"][k] == v
            for k, v in {
                "policy_kind": "ROP_FIXED_Q",
                "rmos": "1.5",
                "emos": "1",
                "target_service_level": "0.95",
                "review_cycle_weeks": 1,
            }.items()
        )
    )
    raw = children(result)[index]["causal_result"]
    # Mean=1/week, monthly=52/12. ROP=ceil(1.5*52/12)=7,
    # Q=ceil(52/12)=5; W0 BOH=0 and a separate due-in of 5.
    assert [r["on_hand_eoh_qty"] for r in raw["psi_rows"][:6]] == ["4", "3", "2", "1", "0", "4"]
    accepted = raw["decision_evidence"][0]["validation"]
    assert (accepted["requested_qty"], accepted["accepted_order_qty"], accepted["due_date"]) == (
        "5",
        "5",
        "2055-02-08",
    )
    score = result["path_scorecards"][index]["score"]
    # Independently enumerable order weeks: 0,3,8,13,...,48 (11 orders).
    assert (score["on_hand_eoh_unit_weeks"], score["order_count"], score["ordered_qty"]) == (
        "197",
        11,
        "55",
    )
    assert score["fill_rate"] == "1" and score["ending_customer_backorder_qty"] == "0"
    with localcontext(NUMBERS):
        assert (
            Decimal(score["cost"]["components"]["holding"])
            == Decimal(197) * Decimal(2) * Decimal("0.2") / 52
        )
        assert Decimal(score["cost"]["components"]["fixed_order"]) == 11 * 25
        assert Decimal(score["cost"]["components"]["variable_order"]) == 55 * 2


def test_five_cost_assumptions_reprice_verified_psi_without_inventing_new_paths(executed):
    _, _, _, result = executed
    trace = next(
        t
        for t in result["evaluator_bank_fragment"]["traces"]
        if all(
            t["candidate"][k] == v
            for k, v in {
                "policy_kind": "ROP_FIXED_Q",
                "rmos": "1.5",
                "emos": "1",
                "target_service_level": "0.95",
                "review_cycle_weeks": 1,
            }.items()
        )
    )
    fixed = score_trace(trace, profile_id="FIXED_COMPATIBILITY")["score"]
    assert fixed["cost"]["total_cost"] == "582"
    base = score_trace(trace)["score"]
    holding_high = score_trace(trace, profile_id="PRICE_HOLDING_HIGH")["score"]
    order_high = score_trace(trace, profile_id="PRICE_ORDER_HIGH")["score"]
    no_forecast_penalty = score_trace(trace, profile_id="PRICE_NO_FORECAST_PENALTY")["score"]
    with localcontext(NUMBERS):
        assert (
            Decimal(holding_high["cost"]["components"]["holding"])
            == Decimal(base["cost"]["components"]["holding"]) * 2
        )
        assert (
            Decimal(order_high["cost"]["components"]["fixed_order"])
            - Decimal(base["cost"]["components"]["fixed_order"])
            == 825
        )
    assert no_forecast_penalty["cost"]["total_cost"] == base["cost"]["total_cost"]
    assert all(
        s["current_demand_qty"] == "52" and s["order_count"] == 11
        for s in (fixed, base, holding_high, order_high, no_forecast_penalty)
    )


@pytest.mark.parametrize(
    "mode,count,leaves",
    [("ABC_XYZ_SHARED_BUDGET", 32, [16, 16]), ("WIDE_REFERENCE", 88, [16, 16, 16, 16, 16, 8])],
)
def test_other_modes_execute_the_complete_list_not_a_sixteen_candidate_substitute(
    mode, count, leaves
):
    case, assembly, path, result = execute(item_case(mode=mode))
    assert len(children(result)) == len(result["path_scorecards"]) == count
    assert [len(leaf["candidate_executions"]) for leaf in result["leaves"]] == leaves
    assert (
        result["source_manifest"]["complete_catalog_hash"]
        == assembly["candidate_catalog"]["content_hash"]
    )
    assert assembly["observation_work_bound"] == count * 2145
    if mode == "WIDE_REFERENCE":
        # The aggregate is larger than the old flat Reader limit. Only the new
        # in-memory verifier accepts bounded leaves; no existing limit changes.
        assert len(canonical_json(result).encode()) > 128_000_000
        RunItemCatalogUseCase(DEV, case["producer"]).verify(
            result,
            expected_result_hash=result["content_hash"],
            expected_assembly_hash=assembly["content_hash"],
            expected_realization_hash=path.content_hash,
        )


def test_unclassified_other_item_is_kept_in_parent_and_catalog_not_silently_removed():
    case, assembly, _, result = execute(item_case(unclassified_other=True))
    parent = next(r for r in assembly["parent_classification"]["items"] if r["item_id"] == "ITEM-C")
    excluded = next(r for r in assembly["candidate_catalog"]["items"] if r["item_id"] == "ITEM-C")
    assert parent["classification_status"] == "UNCLASSIFIED"
    assert excluded["status"] == "EXCLUDED" and excluded["reason_code"]
    assert excluded["candidates"] == []
    assert len(children(result)) == 32
    assert assembly["parent_classification"] == case["parent_classification"]


def test_fsn_block_is_not_converted_to_shadow_permission():
    _, _, _, result = execute(item_case(gate="BLOCK"))
    for child, card in zip(children(result), result["path_scorecards"], strict=True):
        raw = child["causal_result"]
        assert raw["recommended_orders"] == []
        assert all(d["proposal"]["action_type"] == "HOLD" for d in raw["decision_evidence"])
        assert raw["effective_policy_admission"]["effective_order_action"] == "BLOCK"
        assert card["score"]["calculation_gate_allowed"] is False
        assert card["score"]["path_service_success"] is False


def test_all_calculation_failures_stay_in_the_complete_candidate_denominator():
    _, _, _, result = execute(item_case(max_target="0"))
    assert len(children(result)) == len(result["path_scorecards"]) == 32
    for child, card in zip(children(result), result["path_scorecards"], strict=True):
        assert child["status"] == card["status"] == "FAILED"
        assert child["failure_reason"] == "STRATEGY_EXECUTION_FAILED"
        assert child["failure_evidence"][0]["decision_id"].startswith("DEC-")
        assert child["causal_result"] is card["score"] is None
    assert all(
        t["rows"] is t["orders"] is None for t in result["evaluator_bank_fragment"]["traces"]
    )
    assessment = assess_paths(result["evaluator_bank_fragment"])
    assert len(assessment["candidate_assessments"]) == 32
    for row in assessment["candidate_assessments"]:
        assert "TECHNICAL_PATH_FAILURE" in row["assessment"]["reason_codes"]
        assert row["assessment"]["mean_total_cost"] is None


def test_partial_candidate_failure_does_not_shrink_the_catalog():
    _, _, _, result = execute(item_case(max_target="10"))
    states = [r["status"] for r in children(result)]
    assert 0 < states.count("FAILED") < 32
    assert 0 < states.count("SUCCEEDED") < 32
    assert len(result["path_scorecards"]) == 32


def test_capacity_rejection_remains_actual_guard_evidence_not_a_rewritten_order():
    _, _, _, result = execute(item_case(capacity="7"))
    validations = [
        d["validation"] for c in children(result) for d in c["causal_result"]["decision_evidence"]
    ]
    assert any(
        Decimal(d["raw_order_qty"]) > 0 and d["accepted_order_qty"] == "0" for d in validations
    )
    assert all(Decimal(d["accepted_order_qty"]) % 5 == 0 for d in validations)
    for c in children(result):
        assert all(Decimal(r["on_hand_eoh_qty"]) <= 7 for r in c["causal_result"]["psi_rows"])


def test_no_demand_is_null_service_and_not_zero_cost():
    _, _, _, result = execute(item_case(), values=["0"] * 65)
    for card in result["path_scorecards"]:
        assert card["score"]["fill_rate"] is None
        assert card["score"]["service_status"] == "NO_DEMAND_IN_HORIZON"
        assert card["score"]["cost"]["status"] == "CALCULABLE"
        assert Decimal(card["score"]["cost"]["total_cost"]) > 0


def test_realization_metadata_and_future_shocks_do_not_enter_candidate_observations(executed):
    case, assembly, path, left = executed
    raw = path.to_dict()
    for row in raw["rows"][13:]:
        row["realized_gross_demand_qty"] = "99"
    raw.update(path_id="OTHER-HAND-PATH", seed=72)
    right_path = RealizedDemandPath.from_dict(raw)
    right = RunItemCatalogUseCase(DEV, case["producer"]).execute(
        assembly,
        expected_assembly_hash=assembly["content_hash"],
        realization=right_path,
        expected_realization_hash=right_path.content_hash,
    )
    for a, b in zip(children(left), children(right), strict=True):
        # W13 action precedes actual W13 consumption, so include that decision.
        assert (
            a["causal_result"]["decision_evidence"][:14]
            == b["causal_result"]["decision_evidence"][:14]
        )
    assert (
        left["path_scorecards"][0]["score"]["forecast_shortage_qty"]
        != right["path_scorecards"][0]["score"]["forecast_shortage_qty"]
    )


def test_full_source_verifier_replays_real_strategy_guard_and_cost(executed):
    case, assembly, path, result = executed
    original = canonical_json(result)
    RunItemCatalogUseCase(DEV, case["producer"]).verify(
        result,
        expected_result_hash=result["content_hash"],
        expected_assembly_hash=assembly["content_hash"],
        expected_realization_hash=path.content_hash,
    )
    assert canonical_json(result) == original


def test_arithmetically_valid_hold_only_strategy_cannot_impersonate_catalog_execution(executed):
    case, assembly, path, _ = executed
    actual_decide = CandidateStrategy.decide

    def hold_only(strategy, observation):
        proposal = actual_decide(strategy, observation).to_dict()
        proposal.update(action_type="HOLD", quantity="0", reason_codes=["HAND_COUNTERFEIT_HOLD"])
        return ReplenishmentProposal.from_dict(proposal)

    runner = RunItemCatalogUseCase(DEV, case["producer"])
    with patch.object(CandidateStrategy, "decide", hold_only):
        forged = runner.execute(
            assembly,
            expected_assembly_hash=assembly["content_hash"],
            realization=path,
            expected_realization_hash=path.content_hash,
        )
    assert all(t["orders"] == [] for t in forged["evaluator_bank_fragment"]["traces"])
    assert all(
        card == score_trace(t)
        for card, t in zip(
            forged["path_scorecards"], forged["evaluator_bank_fragment"]["traces"], strict=True
        )
    )
    with pytest.raises(InventoryInputError, match="ITEM_RESULT_REPLAY_MISMATCH"):
        runner.verify(
            forged,
            expected_result_hash=forged["content_hash"],
            expected_assembly_hash=assembly["content_hash"],
            expected_realization_hash=path.content_hash,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r["leaves"].pop(),
        lambda r: r["leaves"][0]["candidate_executions"].pop(),
        lambda r: r["leaves"][0]["candidate_executions"][0]["causal_result"]["decision_evidence"][
            0
        ]["validation"].update(accepted_order_qty="999"),
        lambda r: r["path_scorecards"][0]["score"]["cost"].update(total_cost="0"),
        lambda r: r["path_scorecards"][0]["score"].update(fill_rate="0.999"),
    ],
)
def test_self_resealed_missing_candidates_guard_or_metric_forgery_is_rejected(executed, mutation):
    case, assembly, path, result = executed
    altered = deepcopy(result)
    mutation(altered)
    altered["content_hash"] = digest({k: v for k, v in altered.items() if k != "content_hash"})
    with pytest.raises(InventoryInputError, match="ITEM_RESULT_REPLAY_MISMATCH"):
        RunItemCatalogUseCase(DEV, case["producer"]).verify(
            altered,
            expected_result_hash=altered["content_hash"],
            expected_assembly_hash=assembly["content_hash"],
            expected_realization_hash=path.content_hash,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(automatic_publish_allowed=0),
        lambda r: r["leaves"][0].update(leaf_index=0.0),
    ],
)
def test_python_equal_but_json_different_values_cannot_keep_the_original_hash(executed, mutation):
    case, assembly, path, result = executed
    altered = deepcopy(result)
    mutation(altered)
    with pytest.raises(InventoryInputError, match="ITEM_RESULT_HASH_MISMATCH"):
        RunItemCatalogUseCase(DEV, case["producer"]).verify(
            altered,
            expected_result_hash=result["content_hash"],
            expected_assembly_hash=assembly["content_hash"],
            expected_realization_hash=path.content_hash,
        )


def test_seventh_leaf_is_rejected_before_unbounded_parent_loading(executed):
    case, assembly, path, result = executed
    altered = {**result, "leaves": result["leaves"] * 4}
    with pytest.raises(InventoryInputError, match="ITEM_RESULT_LEAF_LIMIT"):
        RunItemCatalogUseCase(DEV, case["producer"]).verify(
            altered,
            expected_result_hash=result["content_hash"],
            expected_assembly_hash=assembly["content_hash"],
            expected_realization_hash=path.content_hash,
        )


def test_unknown_input_failure_is_not_downgraded_to_a_failed_candidate(executed):
    case, assembly, path, _ = executed
    with patch(
        "dsio_inventory_engine.evaluate_replenishment.robustness.item_execution.RunCausalPsiUseCase.execute",
        side_effect=InventoryInputError("HAND_INVALID_BINDING"),
    ):
        with pytest.raises(InventoryInputError, match="HAND_INVALID_BINDING"):
            RunItemCatalogUseCase(DEV, case["producer"]).execute(
                assembly,
                expected_assembly_hash=assembly["content_hash"],
                realization=path,
                expected_realization_hash=path.content_hash,
            )

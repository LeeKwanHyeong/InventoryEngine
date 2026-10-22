"""Literal 2055 paths only; actual candidates/Guard/PSI, no official experiment or DB."""

from copy import deepcopy
from unittest.mock import patch

import pytest

from dsio_inventory_engine.evaluate_replenishment.robustness import item_execution_steps as steps
from dsio_inventory_engine.evaluate_replenishment.robustness.contracts import clone
from dsio_inventory_engine.evaluate_replenishment.robustness.source_binding import (
    RealizedDemandPath,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, canonical_json
from tests.integration.test_robustness_durable_offline import prepared
from tests.support.robustness_item_fixtures import DEV
from dsio_inventory_engine.evaluate_replenishment.robustness.item_execution import (
    RunItemCatalogUseCase,
)

HASHES = {
    "ABC_XYZ_SHARED_BUDGET": "a04d8830ff370b3717dff144042abfcc9a4d6d882a4b10d60f415de851277ee7",
    "LEGACY_STYLE": "fbd77610f2019fb44b68dbf12a3667dbe6f31238e2d428909355bc7e7f1c67c6",
    "WIDE_REFERENCE": "0c2a92d7fdd6d835a665d3c2bf1a4865b0fe797e0851e0a9268750fbfa31759b",
}


def context(case, assembly, path, enabled=True):
    runner = RunItemCatalogUseCase(DEV, case["producer"], reuse_preparation=enabled)
    return steps.prepare_context(
        runner.preparer,
        DEV,
        assembly,
        expected_assembly_hash=assembly["content_hash"],
        realization=path,
        expected_realization_hash=path.content_hash,
        reuse_preparation=enabled,
    )


@pytest.mark.parametrize("mode", HASHES)
def test_all_candidates_original_bytes_hash_and_state_match_without_reuse(mode):
    case, assembly, path = prepared(mode=mode)
    ctx = context(case, assembly, path)
    oracle = context(case, assembly, path, enabled=False)
    before = canonical_json(assembly)
    leaves = []
    for index in range(ctx.leaf_count):
        actual = steps.execute_leaf(ctx, index)
        expected = steps.execute_leaf(oracle, index)
        assert canonical_json(actual).encode() == canonical_json(expected).encode()
        leaves.append(actual)
    result = steps.finish_result(ctx, leaves)
    assert result["content_hash"] == HASHES[mode]
    count = len(assembly["complete_candidates"])
    assert (ctx.runner.preparation.preparations, ctx.runner.preparation.hits) == (1, count - 1)
    assert (oracle.runner.preparation.preparations, oracle.runner.preparation.hits) == (count, 0)
    assert canonical_json(assembly) == before
    assert ctx.admission["automatic_order_allowed"] is False
    assert ctx.admission["automatic_publish_allowed"] is False


def test_reversed_leaf_order_and_mutated_returned_results_cannot_contaminate_candidates():
    case, assembly, path = prepared()
    ctx, oracle = context(case, assembly, path), context(case, assembly, path, enabled=False)
    second = steps.execute_leaf(ctx, 1)
    assert second == steps.execute_leaf(oracle, 1)
    original_second = deepcopy(second)
    second["candidate_executions"][0]["causal_result"]["psi_rows"][0]["eoh_qty"] = "99999"
    first = steps.execute_leaf(ctx, 0)
    assert first == steps.execute_leaf(oracle, 0)
    assert steps.execute_leaf(ctx, 1) == original_second
    policies = {
        c["candidate"]["candidate_id"]: c["causal_result"]["decision_evidence"][0]["proposal"]
        for leaf in (first, original_second)
        for c in leaf["candidate_executions"]
    }
    assert len({canonical_json(p["calculated_policy"]) for p in policies.values()}) > 1


@pytest.mark.parametrize("gate,max_target", [("BLOCK", "1000"), ("REVIEW", "0")])
def test_fsn_gate_and_failed_candidates_do_not_inherit_success_state(gate, max_target):
    case, assembly, path = prepared(gate=gate, max_target=max_target)
    ctx = context(case, assembly, path)
    oracle = context(case, assembly, path, enabled=False)
    for index in range(ctx.leaf_count):
        leaf = steps.execute_leaf(ctx, index)
        assert leaf == steps.execute_leaf(oracle, index)
        for child in leaf["candidate_executions"]:
            if gate == "BLOCK":
                assert child["causal_result"]["recommended_orders"] == []
            else:
                assert child["status"] == "FAILED" and child["causal_result"] is None


def test_actual_guard_and_strategy_run_on_replay_and_bad_realization_is_not_cached():
    case, assembly, path = prepared()
    ctx = context(case, assembly, path)
    leaf = steps.execute_leaf(ctx, 0)
    with patch.object(
        steps.CandidateStrategy, "decide", autospec=True, side_effect=steps.CandidateStrategy.decide
    ) as decide:
        assert steps.verify_leaf(ctx, leaf, index=0, expected_hash=leaf["content_hash"]) == leaf
    assert decide.call_count == 16 * 65
    assert ctx.runner.preparation.preparations == 1
    wrong = ctx.internal.to_dict()
    wrong["planning_request_hash"] = "a" * 64
    bad = RealizedDemandPath.from_dict(wrong)
    with pytest.raises(InventoryInputError, match="CAUSAL_PLANNING_BINDING_MISMATCH"):
        ctx.runner.execute(
            ctx.recommendation,
            None,
            source=assembly["development_source"],
            source_request=assembly["source_request"],
            expected_source_hash=assembly["development_source"]["content_hash"],
            realization=bad,
            expected_realization_hash=bad.content_hash,
        )
    # Future realization is never a prepared planning input.
    slot = ctx.runner.preparation._slot[1]
    assert "realized_gross_demand_qty" not in slot.request_json + slot.prepared_json
    assert clone(assembly) == assembly

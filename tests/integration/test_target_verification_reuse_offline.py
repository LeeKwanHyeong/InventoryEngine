"""One numerical replay per operation, never a persistent verification cache."""

from contextlib import nullcontext
from unittest.mock import patch

import pytest

from dsio_inventory_engine.run_inventory.initial_target_execution import (
    InventoryInitialTargetExecution,
)
from tests.support.initial_execution_fixtures import initial_world
from tests.unit.test_target_execution import ReferenceKernel


@pytest.mark.asyncio
async def test_initial_calculation_replay_and_each_new_request_remain_independent(tmp_path):
    kernel = ReferenceKernel()
    w = initial_world(tmp_path, kernel)
    first = await w.runtime.execute(request=w.request, target_input_reference=w.input_ref)
    one_pass = sum(first["result"]["completed_transition_calls"].values())
    assert one_pass > 0
    assert len(kernel.inputs) == 2 * one_pass  # calculation AND independent replay
    files = {p.name: p.read_bytes() for p in w.target_root.iterdir()}

    before = len(kernel.inputs)
    recovered = await w.runtime.recover(request=w.request, target_input_reference=w.input_ref)
    assert recovered == first
    assert len(kernel.inputs) - before == one_pass
    before = len(kernel.inputs)
    assert w.runtime.read(request=w.request, result_pointer=first["result_pointer"]) == first
    assert len(kernel.inputs) - before == one_pass

    restarted = InventoryInitialTargetExecution(**w.runtime_kwargs)
    before = len(kernel.inputs)
    assert await restarted.recover(request=w.request, target_input_reference=w.input_ref) == first
    assert len(kernel.inputs) - before == one_pass
    assert {p.name: p.read_bytes() for p in w.target_root.iterdir()} == files
    assert not first["automatic_publish_allowed"] and not first["automatic_order_allowed"]


@pytest.mark.asyncio
async def test_same_build_result_is_identical_to_uncached_full_replays(tmp_path):
    kernel = ReferenceKernel()
    w = initial_world(tmp_path, kernel, mode="LEGACY_STYLE")
    # Disable only the new internal operation scope: the original numerical,
    # Link and Projection verification paths still run without alteration.
    with patch.object(w.target, "_verification_scope", side_effect=nullcontext):
        before = await w.runtime.execute(request=w.request, target_input_reference=w.input_ref)
    one_pass = sum(before["result"]["completed_transition_calls"].values())
    assert len(kernel.inputs) == 6 * one_pass
    calls = len(kernel.inputs)
    after = await w.runtime.recover(request=w.request, target_input_reference=w.input_ref)
    assert len(kernel.inputs) - calls == one_pass
    assert after == before  # every PSI row, action, reason, Hash and pointer

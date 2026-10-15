"""Additional receipt conflict, TOCTOU and null/long-horizon projection checks."""

import json
import sqlite3
from unittest.mock import patch

import pytest

from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from dsio_inventory_engine.inventory_evidence.projection import binding_identity
from dsio_inventory_engine.inventory_evidence.projection_source import DsimSourceProjector
from tests.integration.test_dsim_projection_offline import projection_world, write


@pytest.mark.asyncio
async def test_conflicting_existing_hash_is_not_overwritten(tmp_path):
    w = projection_world(tmp_path)
    await write(w)
    with sqlite3.connect(w.uow.path) as conn:
        row = json.loads(conn.execute("SELECT body FROM offline_projection_receipt").fetchone()[0])
        row["projection_content_hash"] = "0" * 64
        conn.execute("UPDATE offline_projection_receipt SET body=?", (json.dumps(row),))
    with pytest.raises(InventoryInputError, match="PROJECTION_WRITE_ONCE_CONFLICT"):
        await write(w)


@pytest.mark.asyncio
async def test_claim_changed_during_source_replay_blocks_write(tmp_path):
    w = projection_world(tmp_path)
    real = w.verifier.execute

    def verify_then_change(**kwargs):
        result = real(**kwargs)
        authority = binding_identity(w.request)
        authority["claim_hash"] = "0" * 64
        with sqlite3.connect(w.uow.path) as conn:
            conn.execute("UPDATE offline_projection_authority SET body=?", (json.dumps(authority),))
        return result

    with patch.object(w.verifier, "execute", side_effect=verify_then_change):
        with pytest.raises(InventoryInputError, match="PROJECTION_LEDGER_BINDING_MISMATCH"):
            await write(w)
    async with w.uow.transaction(w.request) as tx:
        assert await tx.read_receipt() is None
        assert not any((await tx.read_tables()).values())


def metric(*, no_demand=False):
    return dict(
        service_status="NO_DEMAND_IN_HORIZON" if no_demand else "AVAILABLE",
        current_demand_qty="0" if no_demand else "10",
        current_demand_fulfilled_qty="0" if no_demand else "10",
        current_demand_fill_rate=None if no_demand else "1",
        average_on_hand_eoh_qty="10",
        ending_on_hand_qty="10",
        ending_backorder_qty="0",
        order_count=0,
        service_feasible=not no_demand,
        cost_status="COST_PROFILE_NOT_BOUND" if no_demand else "AVAILABLE_DEVELOPMENT_ASSUMPTION",
        total_cost=None if no_demand else "99",
        currency=None if no_demand else "USD",
    )


def test_no_demand_and_unbound_cost_are_null():
    rows = []
    search = {
        "context": {"plan_yyyyww": "202640"},
        "baseline_items": [{"psi_rows": [{"yyyyww": "202642"}]}],
    }
    DsimSourceProjector._evaluation(
        None,
        lambda _, **row: rows.append(row),
        "eval",
        "search",
        "buffer",
        "candidate",
        "CTX-" + "a" * 64,
        search,
        metric(no_demand=True),
        None,
        "10000000-0000-4000-8000-000000000001",
    )
    assert rows[0]["current_demand_fill_rate"] is None
    assert rows[0]["total_cost"] is None and rows[0]["cost_profile_hash"] is None


def test_long_evaluation_id_binds_parent_run_and_exact_profile():
    rows = []
    search = {
        "context": {"engine_run_id": "OTHER-EVALUATION-SOURCE", "plan_yyyyww": "202640"},
        "baseline_items": [{"psi_rows": [{"yyyyww": "202740"}]}],
    }
    profile = {"currency": "USD", "fixed_order_cost": "99"}
    parent = "10000000-0000-4000-8000-000000000001"
    eid = DsimSourceProjector._evaluation(
        None,
        lambda _, **row: rows.append(row),
        "eval",
        "search",
        "buffer",
        "candidate",
        "CTX-" + "a" * 64,
        search,
        metric(),
        profile,
        parent,
    )
    assert eid == digest(
        dict(
            engine_run_id=parent,
            child_result_id="eval",
            search_child_result_id="search",
            buffer_id="buffer",
            candidate_id="candidate",
            evaluation_context_id="CTX-" + "a" * 64,
        )
    )
    assert rows[0]["cost_profile_hash"] == digest(profile)
    assert rows[0]["evaluation_end_yyyyww"] == "202740"

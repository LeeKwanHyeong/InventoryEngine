"""Existing CandidateStrategy/Guard/PSI against literal manual expectations only."""

import copy
import json
from pathlib import Path

import pytest

from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.recommend_replenishment.application.policy_search import (
    RunPolicySearchUseCase,
)
from tests.support.selected_order_plan_fixtures import golden_parts

GOLDEN = json.loads(
    (
        Path(__file__).resolve().parents[1] / "fixtures/golden_legacy_python_semantics.json"
    ).read_text()
)


@pytest.mark.parametrize("case", GOLDEN["order_cases"], ids=lambda row: row["case"])
def test_current_candidates_run_real_common_psi_without_changing_legacy_expectations(case):
    given = case["input"]
    raw, config, classification = golden_parts(
        {
            "id": case["case"],
            "kind": given["kind"],
            "minimum_fill_rate": "0.7",
            "boh": given["boh"],
            "forecast": given["forecast"],
            "policy": {"lead_time_days": given["lead_time_days"]},
        }
    )
    before = copy.deepcopy((raw, config, classification, case))
    output = RunPolicySearchUseCase(DeploymentScope("DSE", "DEVELOPMENT")).execute(
        MathematicalPolicyRequest.from_dict(raw),
        config_values=config,
        classification_snapshot=classification,
    )
    assert (raw, config, classification, case) == before
    assert len(output["candidate_evaluations"]) == 1
    child = output["candidate_evaluations"][0]
    assert child["status"] == "SUCCEEDED"
    assert child["candidate"]["policy_kind"] == given["kind"]
    assert [r["eoh_qty"] for r in child["psi_rows"]] == case["python_expected"]["eoh"]
    assert [r["recommended_receipt_qty"] for r in child["psi_rows"]] == case["python_expected"][
        "receipt"
    ]
    assert [
        [o[k] for k in ("order_date", "due_date", "receipt_date", "quantity")]
        for o in child["orders"]
    ] == case["python_expected"]["orders"]
    assert [d["validation"]["reason_codes"] for d in child["decision_evidence"]] == case[
        "python_expected"
    ]["validation_reasons"]
    first = child["decision_evidence"][0]
    assert first["observation"]["policy"]["lead_time_days"] == given["lead_time_days"]
    assert first["validation"]["calculated_policy"]["rop_qty"] == given["rop"]
    assert first["validation"]["calculated_policy"]["target_inventory_qty"] == given["target"]
    if given["lead_time_days"] == 7:
        trigger = GOLDEN["trigger_case"]
        assert first["observation"]["state"]["inventory_position_qty"] == trigger["position"]
        assert first["validation"]["accepted_order_qty"] == trigger["q"]
        assert trigger["legacy_order_triggered"] is False
    assert child["automatic_publish_allowed"] is False
    assert child["automatic_order_allowed"] is False
    assert output["database_writes"] is False

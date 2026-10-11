"""Actual horizon simulations, experiment replay and local CLI; no external writes."""

import copy
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from dsio_inventory_engine.entrypoints.policy_sensitivity import main
from dsio_inventory_engine.infrastructure.policy_search_artifacts import (
    LocalPolicySearchArtifactStore,
)
from dsio_inventory_engine.infrastructure.policy_sensitivity_artifacts import (
    LocalPolicySensitivityArtifactStore,
)
from dsio_inventory_engine.inventory_contracts.policy_sensitivity import (
    verify_policy_sensitivity_result,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from tests.support.policy_sensitivity_fixtures import long_experiment
from tests.unit.test_policy_sensitivity import run, golden_data, DEPLOYMENT


def schema(name):
    value = json.loads(
        (
            Path(__file__).resolve().parents[2] / f"schemas/policy_sensitivity_{name}.schema.json"
        ).read_text()
    )
    Draft202012Validator.check_schema(value)
    return Draft202012Validator(value)


def test_literal_three_week_request_result_schemas():
    data = golden_data()
    schema("request").validate(data)
    schema("result").validate(run(data))


@pytest.mark.parametrize(
    "change,code",
    [
        (lambda r: r.update(automatic_order_allowed=True), "PUBLICATION_FORBIDDEN"),
        (lambda r: r.update(extra=True), "CONTRACT_INVALID"),
        (lambda r: r.update(matrix=[]), "RESULT_FIELDS_INVALID"),
        (lambda r: r.update(terminal_mode="SALVAGE"), "MODE_INVALID"),
    ],
)
def test_closed_result(change, code):
    result = run(golden_data())
    change(result)
    result["content_hash"] = digest({k: v for k, v in result.items() if k != "content_hash"})
    with pytest.raises(InventoryInputError, match=code):
        verify_policy_sensitivity_result(result)
    with pytest.raises(ValidationError):
        schema("result").validate(result)


def test_parent_required_idempotency_and_rehashed_result_tamper_replay(tmp_path):
    data = golden_data()
    result = run(data)
    store = LocalPolicySensitivityArtifactStore(tmp_path, DEPLOYMENT)
    with pytest.raises(InventoryInputError, match="NOT_FOUND"):
        store.write(result)
    LocalPolicySearchArtifactStore(tmp_path).write(data["cases"][0]["search_result"])
    receipt = store.write(result)
    assert receipt == store.write(result)
    assert store.read(result["content_hash"]) == result
    forged = copy.deepcopy(result)
    forged["matrix"][0]["candidate_metrics"][0]["metrics"]["total_cost"] = "0"
    forged["content_hash"] = digest({k: v for k, v in forged.items() if k != "content_hash"})
    with pytest.raises(InventoryInputError, match="REPLAY_MISMATCH"):
        store.write(forged)
    (tmp_path / (forged["content_hash"] + ".json")).write_text(json.dumps(forged))
    with pytest.raises(InventoryInputError, match="REPLAY_MISMATCH"):
        store.read(forged["content_hash"])
    (tmp_path / (data["cases"][0]["search_result"]["content_hash"] + ".json")).write_text("{}")
    with pytest.raises(InventoryInputError):
        store.read(result["content_hash"])


def test_cli_actual_input_and_artifact_roundtrip(tmp_path):
    data = golden_data()
    path = tmp_path / "request.json"
    path.write_text(json.dumps(data))
    output = io.StringIO()
    with redirect_stdout(output):
        assert main(["--request", str(path), "--artifact-root", str(tmp_path)]) == 0
    receipt = json.loads(output.getvalue())["artifact"]
    assert receipt["artifact_id"].startswith("PSENS-")
    assert LocalPolicySensitivityArtifactStore(tmp_path, DEPLOYMENT).read(
        receipt["content_hash"]
    ) == run(data)
    data["reference_case_id"] = "unknown"
    path.write_text(json.dumps(data))
    with redirect_stdout(io.StringIO()):
        assert main(["--request", str(path)]) == 2


def test_actual_13_26_52_104_week_evaluation_cost_change_and_new_seed_holdouts():
    data = long_experiment()
    result = run(data)
    schema("request").validate(data)
    schema("result").validate(result)
    # These are reproducibility regressions, not independent proofs of the metric formulas.
    assert (
        result["content_hash"] == "7549c32c95e59267407a82b89be93b3c7121fc85783c4da11875202ecb3082d3"
    )

    def selected(case, cost="base"):
        return next(
            r["selected_candidate_id"]
            for r in result["matrix"]
            if r["case_id"] == case and r["cost_profile_id"] == cost
        )

    assert selected("validation-13") != selected("validation-52")
    assert selected("validation-13") != selected("validation-13", "order-x20")
    assert selected("validation-52") == selected("validation-104") == selected("validation-108")
    holdouts = [r for r in result["holdout_evaluations"] if r["cost_profile_id"] == "base"]
    assert len(holdouts) == 2
    assert all(
        r["metrics"]["horizon_weeks"] == 104 and not r["metrics"]["service_feasible"]
        for r in holdouts
    )
    assert [r["metrics"]["current_demand_fulfilled_qty"] for r in holdouts] == ["948", "976"]
    assert len(result["terminal_diagnostics"]) == 16
    assert sum(d["horizon_dependent_actions"] for d in result["terminal_diagnostics"]) == 13
    assert all(
        r["selected_candidate_id"] is None
        for r in result["matrix"]
        if r["cost_profile_id"] == "not-bound"
    )
    data["reference_case_id"] = "validation-52"
    longer = run(data)
    assert (
        longer["content_hash"] == "1edcc1c0fc129e80b01614639007c02fb802c07c605d3bd819dcde0f41982f15"
    )
    longer_holdouts = [r for r in longer["holdout_evaluations"] if r["cost_profile_id"] == "base"]
    assert all(
        r["metrics"]["current_demand_fill_rate"] == "1" and r["metrics"]["service_feasible"]
        for r in longer_holdouts
    )
    assert [r["metrics"]["total_cost"] for r in longer_holdouts] == ["6089", "6105"]

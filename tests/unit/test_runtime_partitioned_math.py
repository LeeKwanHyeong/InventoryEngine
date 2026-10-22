"""Formal Runs retain Guard limits and exact item results under bounded work."""

import copy
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from dsio_inventory_engine.inventory_contracts.observation_storage import (
    CONTRACT,
    expand_observation,
)
from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from dsio_inventory_engine.recommend_replenishment.application import run as common
from dsio_inventory_engine.recommend_replenishment.application.mathematical import (
    prepare_mathematical_strategy,
)
from dsio_inventory_engine.recommend_replenishment.application.partition import control_partitions
from dsio_inventory_engine.run_inventory.result_artifacts import (
    build_psi_child_artifact,
    seal_action_observation_source,
    validate_psi_child_build,
)
from test_runtime_effective_policy_v2 import _requests, DEPLOYMENT


def test_exact_v100_shape_exceeds_single_work_but_parts_retain_limits():
    start = date(2025, 3, 17)
    calendar = [
        {
            "start_date": (start + timedelta(weeks=i)).isoformat(),
            "yyyyww": f"2025{i + 12:02}",
            "seq": i,
        }
        for i in range(26)
    ]
    prepared = {
        "master": [{"item_id": f"ITEM-{i}", "uom": "EA"} for i in range(3619)],
        "calendar": calendar,
        "context": {},
        "manifest": {"quantity_rules": [{"uom": "EA", "scale": 0}]},
        "positions": [],
        "demands": [],
        "policies": [],
        "receipt_decisions": [],
    }
    execution = {
        "item_controls": [
            {
                "item_id": r["item_id"],
                "uom": "EA",
                "max_order_qty": "1000",
                "min_target_qty": "0",
                "max_target_qty": "1000",
                "order_dates": [b["start_date"] for b in calendar],
            }
            for r in prepared["master"]
        ]
    }
    with pytest.raises(InventoryInputError, match="RECOMMENDATION_OBSERVATION_LIMIT"):
        common.validate_controls(prepared, execution)
    ids = []
    for part, controls in control_partitions(prepared, execution):
        common.validate_controls(part, controls)
        assert len(part["master"]) <= 128
        ids.extend(r["item_id"] for r in part["master"])
    assert ids == [r["item_id"] for r in prepared["master"]]


def partitioned_fixture():
    math, runtime = _requests()
    recommendation, prepared, report, strategy = prepare_mathematical_strategy(
        math, DEPLOYMENT, runtime_request=runtime
    )
    normal = common.RunRecommendedPsiUseCase(DEPLOYMENT).execute(
        recommendation, strategy, runtime_request=runtime
    )
    original_validate = common.validate_controls
    called = 0

    def constrained(data, execution):
        nonlocal called
        called += 1
        if called == 1:
            raise InventoryInputError("RECOMMENDATION_OBSERVATION_LIMIT")
        return original_validate(data, execution)

    with patch.object(common, "validate_controls", side_effect=constrained):
        split = common.RunRecommendedPsiUseCase(DEPLOYMENT).execute(
            recommendation, strategy, runtime_request=runtime
        )
    source = seal_action_observation_source(
        prepared_input=split["prepared_input"],
        execution=split["execution"],
        observation_input_hash=recommendation.input_hash,
        admissions_by_item={r["item_id"]: r for r in split["effective_policy_admission_evidence"]},
    )
    return normal, split, source, recommendation, runtime


def test_partitioned_math_has_exact_psi_orders_guard_and_observation_hashes():
    normal, split, source, _request, _runtime = partitioned_fixture()
    for field in (
        "psi_rows",
        "psi_content_hash",
        "recommended_orders",
        "orders_content_hash",
        "effective_policy_admission_evidence",
    ):
        assert split[field] == normal[field]
    for original, compact in zip(
        normal["decision_evidence"], split["decision_evidence"], strict=True
    ):
        assert compact["observation"]["contract_id"] == CONTRACT
        expanded = expand_observation(compact["observation"], source)
        assert expanded == original["observation"]
        assert digest(expanded) == compact["observation_hash"] == original["observation_hash"]
        assert compact["validation"] == original["validation"]
    assert split["automatic_publish_allowed"] is False
    assert split["automatic_order_allowed"] is False


def compact_build():
    _normal, result, source, request, runtime = partitioned_fixture()
    canonical = CanonicalInputRequest.from_dict(request.to_dict()["canonical_input"])
    execution = result["execution"]
    return build_psi_child_artifact(
        engine_run_id=runtime.engine_run_id,
        attempt_no=1,
        canonical_input_hash=canonical.input_hash,
        result_kind="RECOMMENDED_PSI",
        execution_role="OPERATIONAL",
        strategy_type="MATHEMATICAL",
        scenario_id="BASE",
        scenario_content_hash=runtime.strategy_execution_plan["base_scenario_content_hash"],
        psi_rows=result["psi_rows"],
        decision_evidence=result["decision_evidence"],
        expected_strategy_descriptor=execution["strategy"],
        expected_execution_approval_reference=execution["approval_reference"],
        expected_observation_input_hash=request.input_hash,
        expected_strategy_input_binding=execution["strategy_input_binding"],
        expected_admissions_by_item=source["admissions_by_item"],
        expected_observation_source=source,
    )


def test_compact_action_artifact_roundtrip_and_tampering():
    built = compact_build()
    assert built["action_artifacts"]["raw_action"]["artifact"]["contract_version"] == "1.1.0"
    assert validate_psi_child_build(built) == built
    bad = copy.deepcopy(built)
    bad["action_artifacts"]["raw_action"]["artifact"]["rows"][0]["observation"]["state"][
        "available_qty"
    ] = "999"
    with pytest.raises(InventoryInputError):
        validate_psi_child_build(bad)


def test_unrelated_control_errors_are_not_retried_as_partition_work():
    math, runtime = _requests()
    recommendation, _, _, strategy = prepare_mathematical_strategy(
        math, DEPLOYMENT, runtime_request=runtime
    )
    with patch.object(
        common, "validate_controls", side_effect=InventoryInputError("ORDER_DATE_NOT_BUCKET_START")
    ):
        with pytest.raises(InventoryInputError, match="ORDER_DATE_NOT_BUCKET_START"):
            common.RunRecommendedPsiUseCase(DEPLOYMENT).execute(
                recommendation, strategy, runtime_request=runtime
            )

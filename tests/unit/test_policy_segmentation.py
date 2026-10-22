"""Research model checks, independent loss examples, and closed intake boundaries."""

import copy
from dataclasses import replace
from decimal import Decimal, Inexact, ROUND_FLOOR, localcontext

import pytest

from dsio_inventory_engine.training.policy_segmentation import (
    fit_profile_tree,
    predict_profile,
    routing_loss,
)
from dsio_inventory_engine.evaluate_replenishment.policy_segmentation import (
    _validate_case,
    run_experiment,
)
from tests.support.policy_segmentation_fixtures import ABC_PROFILES, PROFILES, experiment_cases


def learning_rows():
    return [
        {
            "row_id": f"TRAIN-{i}",
            "features": {"ordering_cost": str(i)},
            "profile_outcomes": {
                "SMALL": {"feasible": True, "cost": "10" if i < 4 else "30"},
                "LARGE": {"feasible": True, "cost": "40" if i < 4 else "20"},
            },
        }
        for i in range(8)
    ]


def test_tree_learns_profile_cost_not_item_id_and_is_order_invariant():
    rows = learning_rows()
    original = copy.deepcopy(rows)
    model = fit_profile_tree(rows, max_depth=1)
    assert model["tree"]["feature"] == "ordering_cost"
    assert model["tree"]["threshold"] == "3.5"
    assert predict_profile(model, {"ordering_cost": "2"}) == "SMALL"
    assert predict_profile(model, {"ordering_cost": "7"}) == "LARGE"
    assert fit_profile_tree(list(reversed(rows)), max_depth=1) == model
    assert rows == original
    assert not model["operational_approved"] and model["development_only"]


def test_infeasible_cheap_route_loses_before_cost_regret():
    rows = learning_rows()
    for row in rows:
        row["profile_outcomes"]["SMALL"] = {"feasible": False, "cost": "0"}
    assert routing_loss(rows, ["SMALL"] * 8)[0] == 8
    assert routing_loss(rows, ["LARGE"] * 8) == (0, Decimal(0))
    model = fit_profile_tree(rows)
    assert predict_profile(model, {"ordering_cost": "0"}) == "LARGE"


def test_model_does_not_depend_on_caller_decimal_precision_or_traps():
    expected = fit_profile_tree(learning_rows())
    with localcontext() as context:
        context.prec = 4
        context.rounding = ROUND_FLOOR
        context.traps[Inexact] = True
        assert fit_profile_tree(learning_rows()) == expected


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", 1.0, None])
def test_invalid_features_fail_closed(value):
    rows = learning_rows()
    rows[0]["features"]["ordering_cost"] = value
    with pytest.raises(ValueError, match="SEGMENTATION_NUMBER"):
        fit_profile_tree(rows)


def test_tampered_model_missing_features_and_all_infeasible_rows_fail():
    model = fit_profile_tree(learning_rows())
    model["tree"]["threshold"] = "100"
    with pytest.raises(ValueError, match="MODEL_HASH"):
        predict_profile(model, {"ordering_cost": "2"})
    with pytest.raises(ValueError, match="FEATURE_CONTRACT"):
        predict_profile(fit_profile_tree(learning_rows()), {"future_demand": "10"})
    rows = learning_rows()
    for outcome in rows[0]["profile_outcomes"].values():
        outcome["feasible"] = False
    with pytest.raises(ValueError, match="NO_FEASIBLE_TRAINING"):
        fit_profile_tree(rows)


def test_overlapping_period_and_temporal_split_relabeling_fail_before_simulation():
    cases = experiment_cases()
    with pytest.raises(ValueError, match="OVERLAPPING_PERIODS"):
        run_experiment(
            [cases[0], replace(cases[0], case_id="DUPLICATE-PERIOD"), *cases[1:]],
            PROFILES,
            ABC_PROFILES,
        )
    cases[1] = replace(cases[1], split="HOLDOUT")
    with pytest.raises(ValueError, match="SPLIT_ORDER"):
        run_experiment(cases, PROFILES, ABC_PROFILES)


def test_replay_cannot_change_guard_limits_or_source_constraints():
    item = experiment_cases()[0]
    bad = copy.deepcopy(item.replay_request)
    bad["recommendation"]["execution"]["item_controls"][0]["max_order_qty"] = "999999"
    with pytest.raises(ValueError, match="REPLAY_EXECUTION_GUARD"):
        _validate_case(replace(item, replay_request=bad))
    bad = copy.deepcopy(item.replay_request)
    bad["recommendation"]["canonical_input"]["snapshots"]["policies"]["rows"][0][
        "lead_time_days"
    ] = 999
    # Even an internally re-sealed input must not change the controlled comparison.
    from fixtures import reseal
    from math_fixtures import reseal_math
    from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest

    canonical = bad["recommendation"]["canonical_input"]
    reseal(canonical)
    input_hash = CanonicalInputRequest.from_dict(canonical).input_hash
    bad["recommendation"]["execution"]["canonical_input_hash"] = input_hash
    bad["policy_input"]["context"]["canonical_input_hash"] = input_hash
    bad = reseal_math(bad)
    with pytest.raises(ValueError, match="REPLAY_IMMUTABLE_INPUT"):
        _validate_case(replace(item, replay_request=bad))


def test_unknown_cost_is_not_zero_cost():
    item = experiment_cases()[0]
    values = copy.deepcopy(item.classification_inputs.config_values)
    values["policy_search"]["cost_assumptions"] = None
    from dsio_inventory_engine.classify_inventory.application import config_hash

    changed = replace(
        item.classification_inputs, config_values=values, config_hash=config_hash(values)
    )
    with pytest.raises(ValueError, match="COST_REQUIRED"):
        _validate_case(replace(item, classification_inputs=changed))

from copy import deepcopy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from dsio_inventory_engine.evaluate_replenishment.robustness.split_preflight import (
    build_split_work_plan,
    project_split_resources,
)
from tests.support.split_preflight_fixtures import protocol, samples

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "schemas/robustness_split_preflight.schema.json").read_text())


def report():
    source = protocol()
    return project_split_resources(source, build_split_work_plan(source), samples())


def test_closed_schema_accepts_complete_population_but_not_claim_of_experiment_readiness():
    Draft202012Validator.check_schema(SCHEMA)
    Draft202012Validator(SCHEMA).validate(report())


@pytest.mark.parametrize(
    "field,wrong",
    [
        ("status", "PASSED"),
        ("full_raw_bytes", 0),
        ("full_wall_ns", 1),
        ("official_execution_allowed", True),
        ("automatic_publish_allowed", True),
        ("database_writes", True),
        ("estimate_is_hard_upper_bound", True),
        ("margin_percent", False),
        ("margin_percent", 24),
        ("unknown", 1),
        ("unmeasured_components", []),
        ("sample_hashes", []),
        ("work_population_counts", {"PRIMARY": 198}),
    ],
)
def test_schema_rejects_missing_work_unknown_zero_imputation_and_safety_escalation(field, wrong):
    value = deepcopy(report())
    value[field] = wrong
    assert list(Draft202012Validator(SCHEMA).iter_errors(value))

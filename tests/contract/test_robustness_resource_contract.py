import pytest
import json
from pathlib import Path
from jsonschema import Draft202012Validator

from dsio_inventory_engine.inventory_contracts.robustness_resources import (
    ResourceLimits,
    invocation_seconds,
)
from dsio_inventory_engine.inventory_contracts.robustness_storage import (
    MAX_EXPERIMENT_RAW_BYTES,
    MAX_INVOCATION_SECONDS,
    MAX_RAW_BYTES,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError


def test_original_raw_document_and_global_limits_are_not_discounted_by_compression():
    policy = ResourceLimits().policy()
    assert policy["raw_limit_bytes"] == MAX_EXPERIMENT_RAW_BYTES == 20_000_000_000
    assert policy["max_invocation_seconds"] == MAX_INVOCATION_SECONDS == 3600
    assert MAX_RAW_BYTES == 128_000_000
    assert policy["quarantine_retention_seconds"] == 86_400
    assert policy["automatic_publish_allowed"] is policy["automatic_order_allowed"] is False
    assert policy["database_writes"] is False


@pytest.mark.parametrize("invalid", [True, False, 0, -1, 20_000_000_001, 20.0, "20000", None])
def test_raw_limit_cannot_be_increased_or_implicitly_coerced(invalid):
    with pytest.raises(InventoryInputError, match="RAW_LIMIT_INVALID"):
        ResourceLimits(invalid)


@pytest.mark.parametrize(
    "invalid", [True, False, 0, -1, 3601, float("inf"), float("nan"), "3600", None]
)
def test_invocation_deadline_cannot_be_disabled_or_extended(invalid):
    with pytest.raises(InventoryInputError, match="DEADLINE_INVALID"):
        invocation_seconds(invalid)


def test_smaller_test_budgets_and_deadlines_do_not_change_the_production_maximum():
    assert ResourceLimits(18_000).raw_bytes == 18_000
    assert invocation_seconds(0.1) == 0.1
    assert ResourceLimits().raw_bytes == 20_000_000_000
    assert invocation_seconds(3600) == 3600


def test_resource_policy_has_a_closed_schema_and_no_approval_escalation():
    schema = json.loads(
        (
            Path(__file__).resolve().parents[2] / "schemas/robustness_resource_policy.schema.json"
        ).read_text()
    )
    validator = Draft202012Validator(schema)
    validator.validate(ResourceLimits().policy())
    validator.validate(ResourceLimits(18_000).policy())
    for field, wrong in (
        ("contract_version", "2.0.0"),
        ("raw_limit_bytes", True),
        ("raw_limit_bytes", 20_000_000_001),
        ("max_invocation_seconds", 3601),
        ("max_cpu_workers", 2),
        ("automatic_publish_allowed", True),
        ("database_writes", True),
        ("unknown", "extra"),
    ):
        assert list(validator.iter_errors({**ResourceLimits().policy(), field: wrong}))

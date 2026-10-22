"""Closed Python/Schema request surface; no extra identifiers or permission flags."""

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from dsio_inventory_engine.inventory_contracts.order_planning import (
    BINDING_FIELDS,
    MATHEMATICAL_BINDING_FIELDS,
    SelectedPolicyOrderPlanBinding,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from tests.support.order_plan_fixtures import order_plan_case

SCHEMA = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "schemas/selected_policy_order_plan_binding.schema.json"
    ).read_text()
)


def validator():
    Draft202012Validator.check_schema(SCHEMA)
    return Draft202012Validator(SCHEMA)


def test_python_and_schema_field_sets_match():
    assert set(SCHEMA["required"]) == set(SCHEMA["properties"]) == BINDING_FIELDS
    policy = SCHEMA["properties"]["mathematical_input_binding"]
    assert set(policy["required"]) == set(policy["properties"]) == set(MATHEMATICAL_BINDING_FIELDS)


@pytest.mark.parametrize(
    "mode", ["ABC_V1", "ABC_XYZ_SHARED_BUDGET", "LEGACY_STYLE", "WIDE_REFERENCE"]
)
def test_actual_selection_binding_matches_schema(mode):
    value = order_plan_case(mode)[3].to_dict()
    validator().validate(value)
    assert SelectedPolicyOrderPlanBinding.from_dict(value).to_dict() == value


@pytest.mark.parametrize(
    "field,value",
    [
        ("order_plan_id", "new-id"),
        ("binding_id", "new-id"),
        ("content_hash", "f" * 64),
        ("automatic_publish_allowed", True),
        ("automatic_order_allowed", True),
        ("operational_policy_approved", True),
        ("database_writes", True),
        ("erp_transmission_allowed", True),
        ("selected_policies", []),
        ("execution_mode", "OPERATIONAL"),
        ("contract_version", "9.0.0"),
        ("selection_result_reference", "PSEL-not-a-hash"),
        ("canonical_input_hash", True),
    ],
)
def test_extra_identity_permission_or_bad_wire_value_fails_both_contracts(field, value):
    invalid = copy.deepcopy(order_plan_case()[3].to_dict())
    invalid[field] = value
    with pytest.raises(InventoryInputError):
        SelectedPolicyOrderPlanBinding.from_dict(invalid)
    with pytest.raises(ValidationError):
        validator().validate(invalid)


def test_reference_hash_equality_is_a_python_cross_field_check():
    invalid = order_plan_case()[3].to_dict()
    invalid["selection_result_reference"] = "OCPSEL-" + "f" * 64
    validator().validate(invalid)
    with pytest.raises(InventoryInputError, match="ORDER_PLAN_SELECTION_REFERENCE_MISMATCH"):
        SelectedPolicyOrderPlanBinding.from_dict(invalid)


def test_nested_binding_does_not_accept_a_new_identity_or_version():
    invalid = order_plan_case()[3].to_dict()
    invalid["mathematical_input_binding"]["order_plan_id"] = "unnecessary"
    with pytest.raises(InventoryInputError):
        SelectedPolicyOrderPlanBinding.from_dict(invalid)
    with pytest.raises(ValidationError):
        validator().validate(invalid)


@pytest.mark.parametrize("field", ["selection_result_reference", "selection_result_content_hash"])
def test_trailing_newline_is_not_a_valid_reference_or_hash(field):
    invalid = order_plan_case()[3].to_dict()
    invalid[field] += "\n"
    with pytest.raises(InventoryInputError):
        SelectedPolicyOrderPlanBinding.from_dict(invalid)
    with pytest.raises(ValidationError):
        validator().validate(invalid)


@pytest.mark.parametrize("version", ["2.0.0", True])
def test_other_mathematical_binding_version_fails_both_contracts(version):
    invalid = order_plan_case()[3].to_dict()
    invalid["mathematical_input_binding"]["contract_version"] = version
    with pytest.raises(InventoryInputError):
        SelectedPolicyOrderPlanBinding.from_dict(invalid)
    with pytest.raises(ValidationError):
        validator().validate(invalid)

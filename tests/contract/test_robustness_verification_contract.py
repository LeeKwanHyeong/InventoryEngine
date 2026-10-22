from copy import deepcopy
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest

from dsio_inventory_engine.inventory_contracts.robustness_verification import (
    normalize_verification_view,
    verification_view,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest

SCHEMA = json.loads(
    (
        Path(__file__).resolve().parents[2] / "schemas/robustness_verification_view.schema.json"
    ).read_text()
)
VALIDATOR = Draft202012Validator(SCHEMA)


def sample(level="ARTIFACT_INTEGRITY"):
    return verification_view(
        level=level,
        execution_id="HAND",
        input_hash="a" * 64,
        manifest_hash="b" * 64,
        result_hash="c" * 64,
        context={
            "company_cd": "DSE",
            "subs_cd": "C100",
            "site_cd": "V101",
            "plan_id": "PLAN-GOLDEN",
            "engine_run_id": "IO-GOLDEN",
            "plan_yyyyww": "205501",
            "plan_start_date": "2055-01-04",
        },
        counts={
            "candidate_count": 32,
            "successful_count": 31,
            "failed_count": 1,
            "psi_row_count": 2015,
            "leaf_count": 2,
        },
    )


@pytest.mark.parametrize("level", ["ARTIFACT_INTEGRITY", "FULL_PSI_RECALCULATION"])
def test_both_closed_views_are_valid_but_neither_authorizes_publication(level):
    value = sample(level)
    VALIDATOR.validate(value)
    assert normalize_verification_view(value) == value
    assert value["automatic_publish_allowed"] is value["automatic_order_allowed"] is False
    assert (
        value["counts"]["failed_count"] == 1
    )  # Verified replay does not mean all candidates succeeded.


@pytest.mark.parametrize("level", ["ARTIFACT_INTEGRITY", "FULL_PSI_RECALCULATION"])
@pytest.mark.parametrize(
    "field",
    ["source_recalculation_status", "psi_recalculation_status", "metrics_recalculation_status"],
)
def test_fast_cannot_claim_recalculation_or_full_claim_omit_a_stage(level, field):
    value = sample(level)
    value[field] = "VERIFIED" if level == "ARTIFACT_INTEGRITY" else "NOT_RUN"
    value["content_hash"] = digest({k: v for k, v in value.items() if k != "content_hash"})
    assert list(VALIDATOR.iter_errors(value))
    with pytest.raises(InventoryInputError, match="LEVEL_STATUS"):
        normalize_verification_view(value)


@pytest.mark.parametrize(
    "field,wrong",
    [
        ("contract_id", "OTHER"),
        ("contract_version", "2.0.0"),
        ("verification_level", "FAST_APPROVED"),
        ("artifact_integrity_status", "SKIPPED"),
        ("result_hash", "../../bad"),
        ("automatic_publish_allowed", True),
        ("automatic_order_allowed", True),
        ("operational_policy_approved", True),
        ("database_writes", True),
        ("unknown", "extra"),
    ],
)
def test_version_hash_status_and_authority_escalation_rejected_by_both(field, wrong):
    value = {**sample(), field: wrong}
    assert list(VALIDATOR.iter_errors(value))
    with pytest.raises(InventoryInputError):
        normalize_verification_view(value)


@pytest.mark.parametrize(
    "field,wrong",
    [
        ("candidate_count", True),
        ("leaf_count", 6),
        ("successful_count", -1),
        ("psi_row_count", 9999),
    ],
)
def test_invalid_counts_rejected_by_schema_and_python(field, wrong):
    value = deepcopy(sample())
    value["counts"][field] = wrong
    assert list(VALIDATOR.iter_errors(value))
    with pytest.raises(InventoryInputError):
        normalize_verification_view(value)


def test_semantic_hash_and_count_equations_require_python_validation_in_addition_to_schema():
    value = sample()
    value["counts"]["psi_row_count"] -= 1
    value["content_hash"] = digest({k: v for k, v in value.items() if k != "content_hash"})
    with pytest.raises(InventoryInputError, match="COUNTS"):
        normalize_verification_view(value)
    value = sample()
    value["content_hash"] = "0" * 64
    with pytest.raises(InventoryInputError, match="HASH_MISMATCH"):
        normalize_verification_view(value)

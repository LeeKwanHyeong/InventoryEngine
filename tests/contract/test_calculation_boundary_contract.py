"""Declarations only: never execute a DLL or change an existing calculation path."""

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest
from jsonschema import Draft202012Validator, ValidationError

from dsio_inventory_engine.inventory_contracts.calculation_boundary import (
    BINDING_FIELDS,
    EXECUTION_BINDING_FIELDS,
    LEGACY_PARAMETER_FIELDS,
    SIMULATOR_FIELDS,
    CalculationBoundaryBinding,
)
from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest, ROW_FIELDS
from dsio_inventory_engine.inventory_contracts.development_universe import seal_development_universe
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, canonical_json
from tests.support.math_fixtures import build_math
from tests.support.fixtures import reseal

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads(
    (ROOT / "schemas/inventory_calculation_boundary_binding.schema.json").read_text()
)


def fixture(backend="PYTHON_EXISTING", profile="TARGET_GUARDED", scenario="RECOMMENDED"):
    math = build_math()
    canonical = CanonicalInputRequest.from_dict(math["recommendation"]["canonical_input"])
    legacy = profile == "LEGACY_REPLAY"
    # These are fixture references, NOT installed/approved simulator identities.
    implementation = (
        "inventory.psi.legacy_replay"
        if legacy
        else "inventory.psi.target_guarded.csharp"
        if backend == "CSHARP_PYTHONNET"
        else "inventory.psi.weekly_roll_forward"
    )
    kwargs = dict(
        prepared_content_hash="d" * 64,
        psi_simulator={
            "implementation_id": implementation,
            "version": "1.0.0",
            "implementation_content_hash": "a" * 64,
        },
        calculation_backend=backend,
        semantics_profile=profile,
        psi_scenario_type=scenario,
        execution=math["recommendation"]["execution"]
        if not legacy and scenario == "RECOMMENDED"
        else None,
        legacy_parameters_binding={
            "reference": "fixture:explicit-legacy-parameters",
            "content_hash": "b" * 64,
        }
        if legacy
        else None,
    )
    return canonical, kwargs, CalculationBoundaryBinding.from_sources(canonical, **kwargs)


def validator():
    Draft202012Validator.check_schema(SCHEMA)
    return Draft202012Validator(SCHEMA)


def test_schema_and_python_closed_fields_match():
    assert set(SCHEMA["properties"]) == set(SCHEMA["required"]) == set(BINDING_FIELDS)
    assert set(SCHEMA["properties"]["input_bindings"]["required"]) == set(ROW_FIELDS)
    for name, fields in (
        ("executionBinding", EXECUTION_BINDING_FIELDS),
        ("legacyParametersBinding", LEGACY_PARAMETER_FIELDS),
    ):
        assert set(SCHEMA["$defs"][name]["required"]) == set(fields)
    assert set(SCHEMA["properties"]["psi_simulator"]["required"]) == set(SIMULATOR_FIELDS)


@pytest.mark.parametrize(
    "backend,profile,scenario",
    [
        ("CSHARP_PYTHONNET", "LEGACY_REPLAY", "RECOMMENDED"),
        ("PYTHON_REFERENCE", "LEGACY_REPLAY", "RECOMMENDED"),
        ("PYTHON_EXISTING", "TARGET_GUARDED", "BASELINE"),
        ("PYTHON_EXISTING", "TARGET_GUARDED", "RECOMMENDED"),
        ("CSHARP_PYTHONNET", "TARGET_GUARDED", "BASELINE"),
        ("CSHARP_PYTHONNET", "TARGET_GUARDED", "RECOMMENDED"),
    ],
)
def test_declared_profiles_and_source_roundtrip_not_runtime_capability(backend, profile, scenario):
    canonical, kwargs, binding = fixture(backend, profile, scenario)
    value = binding.verify_sources(canonical, **kwargs)
    validator().validate(value)
    assert CalculationBoundaryBinding.from_dict(value) == binding
    assert value["canonical_input_hash"] == canonical.input_hash
    assert value["engine_run_id"] == canonical.to_dict()["context"]["engine_run_id"]
    assert "content_hash" not in value and "binding_id" not in value


@pytest.mark.parametrize(
    "field,value",
    [
        ("binding_id", "unnecessary-id"),
        ("content_hash", "c" * 64),
        ("automatic_publish_allowed", True),
        ("automatic_order_allowed", True),
        ("database_writes", True),
        ("operational_io_eligible", True),
        ("execution_mode", "OPERATIONAL"),
        ("contract_version", "9.0.0"),
        ("calculation_backend", "DEEP_RL"),
        ("semantics_profile", "LEGACY_STYLE"),
        ("guard_order", "POSTPROCESS_ORDERS_ONLY"),
        ("prepared_content_hash", True),
        ("engine_run_id", "run\n"),
    ],
)
def test_unknown_fields_permissions_or_invalid_codes_fail_both(field, value):
    raw = fixture()[2].to_dict()
    raw[field] = value
    with pytest.raises(InventoryInputError):
        CalculationBoundaryBinding.from_dict(raw)
    with pytest.raises(ValidationError):
        validator().validate(raw)


@pytest.mark.parametrize(
    "mutate",
    [
        "legacy_baseline",
        "legacy_external_strategy",
        "legacy_no_parameters",
        "target_legacy_parameters",
        "target_no_strategy",
        "baseline_with_strategy",
        "legacy_wrong_guard",
        "target_wrong_guard",
        "reference_target",
        "existing_legacy",
        "old_id_on_csharp",
        "legacy_id_on_target",
        "bad_model_binding",
        "missing_snapshot",
        "extra_snapshot",
    ],
)
def test_semantic_profile_cross_field_rejections_match_schema(mutate):
    legacy = fixture("CSHARP_PYTHONNET", "LEGACY_REPLAY")[2].to_dict()
    target = fixture()[2].to_dict()
    raw = copy.deepcopy(
        legacy
        if mutate
        in {
            "legacy_baseline",
            "legacy_external_strategy",
            "legacy_no_parameters",
            "legacy_wrong_guard",
        }
        else target
    )
    if mutate == "legacy_baseline":
        raw["psi_scenario_type"] = "BASELINE"
    elif mutate == "legacy_external_strategy":
        raw["replenishment_execution_binding"] = target["replenishment_execution_binding"]
    elif mutate == "legacy_no_parameters":
        raw["legacy_parameters_binding"] = None
    elif mutate == "target_legacy_parameters":
        raw["legacy_parameters_binding"] = legacy["legacy_parameters_binding"]
    elif mutate == "target_no_strategy":
        raw["replenishment_execution_binding"] = None
    elif mutate == "baseline_with_strategy":
        raw["psi_scenario_type"], raw["guard_order"] = "BASELINE", "NO_NEW_ACTIONS"
    elif mutate == "legacy_wrong_guard":
        raw["guard_order"] = "VALIDATE_BEFORE_STATE_COMMIT"
    elif mutate == "target_wrong_guard":
        raw["guard_order"] = "DIAGNOSTIC_POSTCHECK_NO_MUTATION"
    elif mutate == "reference_target":
        raw["calculation_backend"] = "PYTHON_REFERENCE"
    elif mutate == "existing_legacy":
        raw = legacy
        raw["calculation_backend"] = "PYTHON_EXISTING"
    elif mutate == "old_id_on_csharp":
        raw["calculation_backend"] = "CSHARP_PYTHONNET"
    elif mutate == "legacy_id_on_target":
        raw["psi_simulator"]["implementation_id"] = "inventory.psi.legacy_replay"
    elif mutate == "bad_model_binding":
        raw["replenishment_execution_binding"]["strategy"]["strategy_type"] = "DEEP_RL"
    elif mutate == "missing_snapshot":
        raw["input_bindings"].pop("policies")
    elif mutate == "extra_snapshot":
        raw["input_bindings"]["latest"] = raw["input_bindings"]["forecast"]
    with pytest.raises(InventoryInputError):
        CalculationBoundaryBinding.from_dict(raw)
    with pytest.raises(ValidationError):
        validator().validate(raw)


@pytest.mark.parametrize(
    "field", ["engine_run_id", "plan_id", "site_cd", "plan_yyyyww", "configuration_revision"]
)
def test_changed_run_plan_site_w0_config_cannot_reuse_binding(field):
    canonical, kwargs, binding = fixture()
    changed = canonical.to_dict()
    changed["context"][field] = "202641" if field == "plan_yyyyww" else "DIFFERENT"
    changed = CanonicalInputRequest.from_dict(changed)
    if field == "engine_run_id":
        assert changed.input_hash == canonical.input_hash  # Existing deliberate hash semantics.
    with pytest.raises(InventoryInputError):
        binding.verify_sources(changed, **kwargs)


@pytest.mark.parametrize("kind", list(ROW_FIELDS))
@pytest.mark.parametrize("mutation", ["status", "hash", "count", "binding"])
def test_each_snapshot_seal_and_reference_is_checked(kind, mutation):
    canonical, kwargs, _ = fixture()
    changed = canonical.to_dict()
    if mutation == "status":
        changed["snapshots"][kind]["status"] = "COLLECTING"
    elif mutation == "hash":
        changed["snapshots"][kind]["content_hash"] = "f" * 64
    elif mutation == "count":
        changed["snapshots"][kind]["row_count"] += 1
    else:
        changed["input_bindings"][kind]["snapshot_id"] = "DIFFERENT"
    with pytest.raises(InventoryInputError):
        CalculationBoundaryBinding.from_sources(CanonicalInputRequest.from_dict(changed), **kwargs)


@pytest.mark.parametrize("mutation", ["prepared", "build", "config", "strategy_input", "strategy"])
def test_derived_references_and_guard_configuration_cannot_be_substituted(mutation):
    canonical, kwargs, binding = fixture()
    expected = copy.deepcopy(kwargs)
    if mutation == "prepared":
        expected["prepared_content_hash"] = "e" * 64
    elif mutation == "build":
        expected["psi_simulator"]["implementation_content_hash"] = "e" * 64
    elif mutation == "config":
        expected["execution"]["item_controls"][0]["max_order_qty"] = "999"
    elif mutation == "strategy_input":
        expected["execution"]["strategy_input_binding"]["content_hash"] = "e" * 64
    else:
        expected["execution"]["strategy"]["implementation_id"] = "OTHER"
    with pytest.raises(InventoryInputError):
        binding.verify_sources(canonical, **expected)


def test_same_sources_deterministic_and_no_caller_mutation():
    canonical, kwargs, binding = fixture()
    before = canonical_json(kwargs)
    for _ in range(3):
        assert CalculationBoundaryBinding.from_sources(canonical, **kwargs) == binding
    assert canonical_json(kwargs) == before
    value = binding.to_dict()
    value["input_bindings"]["forecast"]["content_hash"] = "f" * 64
    assert binding.to_dict()["input_bindings"]["forecast"]["content_hash"] != "f" * 64


@pytest.mark.parametrize("version", ["2.0.0", "2.1.0"])
def test_v2_and_v21_bind_original_and_projected_inputs_without_changing_canonical(version):
    canonical, kwargs, _ = fixture(scenario="BASELINE")
    raw = canonical.to_dict()
    original = copy.deepcopy(raw["input_bindings"])
    raw["contract_id"], raw["contract_version"] = "io-canonical-input-v2", version
    for rule in raw["quantity_rules"]:
        rule["planning_scale"] = rule.pop("scale")
        rule["physical_scale"] = 0
    if version == "2.1.0":
        raw["snapshots"]["forecast"]["snapshot_id"] += ".canonical"
        raw = reseal(raw)
        raw["upstream_bindings"] = {
            key: original[key] for key in ("calendar", "master", "forecast")
        }
        raw["development_universe"] = seal_development_universe(
            {
                "contract_id": "io-development-calculation-universe-v1",
                "environment_scope": "DEVELOPMENT",
                "demand_run_id": raw["context"]["demand_run_id"],
                "plan_yyyyww": raw["context"]["plan_yyyyww"],
                "original_master_content_hash": original["master"]["content_hash"],
                "original_forecast_content_hash": original["forecast"]["content_hash"],
                "source_demand_window_file_sha256": "a" * 64,
                "approval_reference": "TEST-ONLY-NO-APPROVAL",
                "included_item_ids": [r["item_id"] for r in raw["snapshots"]["master"]["rows"]],
                "exclusions": [],
            }
        )
    source = CanonicalInputRequest.from_dict(raw)
    before = source.document_json
    bound = CalculationBoundaryBinding.from_sources(source, **kwargs)
    validator().validate(bound.to_dict())
    assert bound.verify_sources(source, **kwargs)["canonical_input_hash"] == source.input_hash
    assert source.document_json == before
    if version == "2.1.0":
        assert bound.to_dict()["input_bindings"]["forecast"]["snapshot_id"].endswith(".canonical")
        assert source.to_dict()["upstream_bindings"]["forecast"] == original["forecast"]


def test_import_contract_does_not_load_runtime_calculators_or_pythonnet():
    command = "import sys; from dsio_inventory_engine.inventory_contracts.calculation_boundary import CalculationBoundaryBinding; assert 'clr' not in sys.modules; assert 'pythonnet' not in sys.modules; assert not any('.application.' in name for name in sys.modules if name.startswith('dsio_inventory_engine.'))"
    result = subprocess.run([sys.executable, "-c", command], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

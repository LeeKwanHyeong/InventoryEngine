import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from dsio_inventory_engine.entrypoints.approved_local_runtime import (
    main,
    read_document,
    validate_v100_request,
)
from dsio_inventory_engine.infrastructure.runtime_build import (
    installed_runtime_build,
    package_source_rows,
    registered_installed_implementations,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError


def test_installed_source_build_is_reproducible_and_registers_only_math_and_psi():
    build = installed_runtime_build()
    assert installed_runtime_build() == build
    registry = registered_installed_implementations(build)
    assert registry.mathematical_content_hash != registry.psi_simulator_content_hash
    assert not registry.ppo_implementations


@pytest.mark.parametrize("key", ["package_version", "package_source_content_hash", "content_hash"])
def test_uninstalled_or_tampered_build_cannot_register_itself(key):
    build = copy.deepcopy(installed_runtime_build())
    build[key] = "a" * 64
    with pytest.raises(InventoryInputError, match="RUNTIME_INSTALLED_BUILD_MISMATCH"):
        registered_installed_implementations(build)


def test_package_symlink_rejected(tmp_path):
    path = tmp_path / "source.py"
    path.symlink_to(Path(__file__).resolve())
    with pytest.raises(InventoryInputError, match="RUNTIME_BUILD_SOURCE_SYMLINK"):
        package_source_rows(tmp_path)


def test_source_tree_empty_rejected(tmp_path):
    with pytest.raises(InventoryInputError, match="RUNTIME_BUILD_SOURCE_EMPTY"):
        package_source_rows(tmp_path)


def test_runtime_source_document_requires_absolute_non_symlink_path(tmp_path):
    with pytest.raises(InventoryInputError, match="LOCAL_RUNTIME_DOCUMENT_INVALID"):
        read_document(Path("relative.json"))


def test_inspection_performs_no_execution(capsys):
    assert main(["--inspect-build"]) == 0
    assert '"contract_id":"inventory-installed-runtime-build"' in capsys.readouterr().out
    assert main(["--inspect-build", "--execute"]) == 2


def local_request():
    return SimpleNamespace(
        tenant_id="default",
        project_id="9ed62f41",
        value={
            "claim": {
                "scope": {
                    "company_cd": "DSE",
                    "subs_cd": "C100",
                    "plant_cd": "V100",
                    "site_cd": "V100",
                }
            }
        },
        strategy_execution_plan={
            "contract_version": "1.4.0",
            "operational_strategy": {"strategy_type": "MATHEMATICAL"},
            "shadow_challenger_bindings": [],
            "stress_scenario_bindings": [],
            "cost_profile_binding": None,
            "landed_cost_binding": None,
        },
    )


def test_local_runtime_scope_uses_claim_and_strategy_type():
    validate_v100_request(local_request())


@pytest.mark.parametrize(
    "field,value",
    [
        ("contract_version", "1.3.0"),
        ("shadow_challenger_bindings", [{"algorithm": "PPO"}]),
        ("stress_scenario_bindings", [{"scenario_id": "x"}]),
        ("cost_profile_binding", {"cost_profile_id": "x"}),
        ("landed_cost_binding", {"input_id": "x"}),
    ],
)
def test_deferred_or_old_plan_cannot_enter_local_runtime(field, value):
    request = local_request()
    request.strategy_execution_plan[field] = value
    with pytest.raises(InventoryInputError):
        validate_v100_request(request)


def test_another_site_or_owner_cannot_enter_local_runtime():
    request = local_request()
    request.project_id = "project-a"
    with pytest.raises(InventoryInputError, match="LOCAL_RUNTIME_PROJECT_MISMATCH"):
        validate_v100_request(request)
    request = local_request()
    request.value["claim"]["scope"]["site_cd"] = "V101"
    with pytest.raises(InventoryInputError, match="LOCAL_RUNTIME_SITE_MISMATCH"):
        validate_v100_request(request)

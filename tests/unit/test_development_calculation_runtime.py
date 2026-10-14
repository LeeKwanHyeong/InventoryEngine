"""Runtime deployment is local-only, exactly pinned, and recoverable without Source."""

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from dsio_inventory_engine.entrypoints.development_calculation_runtime import (
    load_runtime_spec,
    load_pinned_source,
    create_runtime_app,
)
from dsio_inventory_engine.infrastructure.runtime_build import installed_runtime_build
from dsio_inventory_engine.inventory_contracts.development_calculation import content_hash
from tests.integration.test_development_calculation_offline import inputs


def local_spec(tmp_path):
    command, bundle = inputs()
    records = {}
    for kind in ("mathematical_request", "config_values", "classification_snapshot"):
        value = {"config_values": bundle[kind]} if kind == "config_values" else bundle[kind]
        path = tmp_path / (kind + ".json")
        path.write_text(json.dumps(value))
        records[kind] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    body = {
        "contract_id": "inventory-local-calculation-deployment",
        "contract_version": "1.0.0",
        "environment": "DEVELOPMENT",
        "backend_url": "http://127.0.0.1:18111",
        "runtime_url": "http://127.0.0.1:18112",
        "evidence_path": str(tmp_path / "evidence.sqlite3"),
        "command": command,
        "runtime_build": installed_runtime_build(),
        "source_files": records,
    }
    spec = {**body, "content_hash": content_hash(body)}
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec))
    return path, spec, bundle


@pytest.mark.parametrize(
    "mutation", ["build", "command", "remote", "environment", "escape", "unknown"]
)
def test_invalid_runtime_binding_never_starts(tmp_path, mutation):
    path, spec, _ = local_spec(tmp_path)
    assert load_runtime_spec(path) == spec
    if mutation == "build":
        spec["runtime_build"]["content_hash"] = "a" * 64
    elif mutation == "command":
        spec["command"]["mathematical_implementation_hash"] = "a" * 64
    elif mutation == "remote":
        spec["runtime_url"] = "http://192.168.0.71:18112"
    elif mutation == "environment":
        spec["environment"] = "PRODUCTION"
    elif mutation == "escape":
        spec["evidence_path"] = str(tmp_path.parent / "outside.sqlite3")
    else:
        spec["automatic_order_allowed"] = True
    spec["content_hash"] = content_hash({k: v for k, v in spec.items() if k != "content_hash"})
    path.write_text(json.dumps(spec))
    with pytest.raises(ValueError):
        load_runtime_spec(path)


def test_pinned_source_requires_original_file_hash(tmp_path):
    _, spec, bundle = local_spec(tmp_path)
    assert load_pinned_source(spec) == json.loads(json.dumps(bundle))
    Path(spec["source_files"]["config_values"]["path"]).write_text("{}")
    with pytest.raises(ValueError, match="SOURCE_FILE_HASH_MISMATCH"):
        load_pinned_source(spec)


def test_recovery_factory_never_loads_original_source(tmp_path, monkeypatch):
    path, _, _ = local_spec(tmp_path)
    for key, value in {
        "DSAI_ENV": "development",
        "DSAI_PRODUCTION": "false",
        "DSAI_INVENTORY_LOCAL_SPEC": str(path),
        "DSAI_INVENTORY_LOCAL_RECOVERY_ONLY": "1",
        "DSAI_INVENTORY_LOCAL_RUNTIME_TOKEN": "unit-test-not-secret",
        "DSAI_INVENTORY_LOCAL_INTERRUPT_AFTER_PARTS": "0",
    }.items():
        monkeypatch.setenv(key, value)
    with patch(
        "dsio_inventory_engine.entrypoints.development_calculation_runtime.load_pinned_source",
        side_effect=AssertionError("must not load Source"),
    ):
        app = create_runtime_app()
    assert "/api/v1/development-calculations/execute" in {r.path for r in app.routes}


def test_fault_injection_is_not_allowed_in_recovery_mode(tmp_path, monkeypatch):
    path, _, _ = local_spec(tmp_path)
    for key, value in {
        "DSAI_ENV": "development",
        "DSAI_PRODUCTION": "false",
        "DSAI_INVENTORY_LOCAL_SPEC": str(path),
        "DSAI_INVENTORY_LOCAL_RECOVERY_ONLY": "1",
        "DSAI_INVENTORY_LOCAL_RUNTIME_TOKEN": "unit-test-not-secret",
        "DSAI_INVENTORY_LOCAL_INTERRUPT_AFTER_PARTS": "2",
    }.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match="FAULT_SETTING_INVALID"):
        create_runtime_app()

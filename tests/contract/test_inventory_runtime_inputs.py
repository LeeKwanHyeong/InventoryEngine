"""Plan1.4, source Manifest and JSON Schema mirrors form one closed contract."""

import copy
import ast
import json
import os
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from dsio_inventory_engine.inventory_contracts.result_bundle import (
    validate_strategy_execution_plan,
)
from dsio_inventory_engine.inventory_contracts.runtime_inputs import (
    content_hash,
    validate_runtime_input_manifest,
)
from dsio_inventory_engine.infrastructure.runtime_input_files import (
    LocalRuntimeInputManifestCatalog,
)
from tests.integration.test_approved_runtime_inputs_offline import input_manifest

ROOT = Path(__file__).resolve().parents[2]


def test_plan_manifest_template_validate_with_matching_schema():
    request, manifest, _, _ = input_manifest(landed=True)
    plan_schema = json.loads((ROOT / "schemas/inventory_result_bundle.schema.json").read_text())
    Draft202012Validator(plan_schema).validate(request.strategy_execution_plan)
    manifest_schema = json.loads(
        (ROOT / "schemas/inventory_runtime_input_manifest.schema.json").read_text()
    )
    Draft202012Validator(manifest_schema).validate(manifest)
    template_schema = json.loads(
        (ROOT / "schemas/inventory_mathematical_request_template.schema.json").read_text()
    )
    template = next(
        r["document"] for r in manifest["resources"] if r["kind"] == "MATHEMATICAL_TEMPLATE"
    )
    Draft202012Validator(template_schema).validate(template)
    for doc, schema in ((manifest, manifest_schema), (template, template_schema)):
        raw = copy.deepcopy(doc)
        raw["unexpected"] = True
        assert list(Draft202012Validator(schema).iter_errors(raw))


@pytest.mark.parametrize("kind", ["missing", "contract", "null", "old_version"])
def test_python_and_schema_reject_unbound_or_forged_plan(kind):
    runtime, _, _, _ = input_manifest()
    plan = copy.deepcopy(runtime.strategy_execution_plan)
    plan.pop("content_hash")
    if kind == "missing":
        plan.pop("runtime_input_binding")
    elif kind == "contract":
        plan["runtime_input_binding"]["contract_key"] = "other"
    elif kind == "null":
        plan["runtime_input_binding"] = None
    else:
        plan["contract_version"] = "1.3.0"
    plan["content_hash"] = content_hash(plan)
    with pytest.raises(ValueError):
        validate_strategy_execution_plan(plan)
    schema = json.loads((ROOT / "schemas/inventory_result_bundle.schema.json").read_text())
    assert list(Draft202012Validator(schema).iter_errors(plan))


def test_original_manifest_append_only_and_full_hash_conflict(tmp_path):
    _, doc, _, _ = input_manifest()
    catalog = LocalRuntimeInputManifestCatalog(tmp_path)
    assert catalog.publish(doc) == catalog.publish(doc)
    changed = copy.deepcopy(doc)
    changed["tenant_id"] = "other"
    changed["content_hash"] = content_hash(
        {k: v for k, v in changed.items() if k != "content_hash"}
    )
    with pytest.raises(ValueError, match="CONFLICT"):
        catalog.publish(changed)
    changed = copy.deepcopy(doc)
    changed["resources"].append(changed["resources"][0])
    with pytest.raises(ValueError, match="DUPLICATE"):
        validate_runtime_input_manifest(changed)


@pytest.mark.skipif(
    not os.environ.get("DSAI_PLATFORM_ROOT"), reason="Platform source not explicitly provided"
)
def test_runtime_contract_and_schema_are_identical_across_repositories():
    root = Path(os.environ["DSAI_PLATFORM_ROOT"])
    assert ast.dump(
        ast.parse(
            (ROOT / "src/dsio_inventory_engine/inventory_contracts/runtime_inputs.py").read_text()
        )
    ) == ast.dump(
        ast.parse((root / "backend/platform_api/inventory_runtime_inputs.py").read_text())
    )
    for name in (
        "inventory_runtime_input_manifest.schema.json",
        "inventory_mathematical_request_template.schema.json",
    ):
        assert (ROOT / "schemas" / name).read_bytes() == (
            root / "backend/platform_api/contracts" / name.replace("_", "-")
        ).read_bytes()

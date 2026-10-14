"""Closed, pre-Claim sealed Runtime inputs. Mirrored between Platform and Engine."""

import copy
import hashlib
import json
import re

RUN_ID_PLACEHOLDER = "PLATFORM_ASSIGNED_RUN_ID"
KINDS = {
    "CONFIG",
    "CLASSIFICATION",
    "MATHEMATICAL_TEMPLATE",
    "MATHEMATICAL_EVALUATION",
    "PPO_MODEL",
    "COST_PROFILE",
    "STRESS_SCENARIO",
    "RUN_COST_INPUT",
}
MAX_BYTES = 128_000_000


def _check(condition, code):
    if not condition:
        raise ValueError(code)


def content_hash(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _id(value):
    _check(
        type(value) is str
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", value) is not None,
        "RUNTIME_INPUT_IDENTIFIER_INVALID",
    )
    return value


def _hash(value):
    _check(
        type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
        "RUNTIME_INPUT_HASH_INVALID",
    )
    return value


def input_manifest_binding(value):
    _check(
        type(value) is dict
        and set(value) == {"contract_key", "contract_version", "snapshot_id", "content_hash"},
        "RUNTIME_INPUT_BINDING_FIELDS_INVALID",
    )
    _check(
        value["contract_key"] == "inventory.runtime_input_manifest"
        and value["contract_version"] == "1.0.0",
        "RUNTIME_INPUT_BINDING_CONTRACT_INVALID",
    )
    _id(value["snapshot_id"])
    _hash(value["content_hash"])
    return copy.deepcopy(value)


def plan_resources_content_hash(plan):
    """Omit only the self-referential Manifest binding and additive Plan version."""
    return content_hash(
        {
            k: v
            for k, v in plan.items()
            if k not in {"content_hash", "contract_version", "runtime_input_binding"}
        }
    )


def seal_mathematical_template(request):
    raw = copy.deepcopy(request)
    raw["recommendation"]["canonical_input"]["context"]["engine_run_id"] = RUN_ID_PLACEHOLDER
    body = {
        "contract_id": "inventory-mathematical-request-template",
        "contract_version": "1.0.0",
        "mathematical_request": raw,
    }
    return {**body, "content_hash": content_hash(body)}


def validate_mathematical_template(value):
    _check(
        type(value) is dict
        and set(value)
        == {"contract_id", "contract_version", "mathematical_request", "content_hash"},
        "MATHEMATICAL_TEMPLATE_FIELDS_INVALID",
    )
    _check(
        value["contract_id"] == "inventory-mathematical-request-template"
        and value["contract_version"] == "1.0.0"
        and value["mathematical_request"]["recommendation"]["canonical_input"]["context"][
            "engine_run_id"
        ]
        == RUN_ID_PLACEHOLDER
        and value["content_hash"]
        == content_hash({k: v for k, v in value.items() if k != "content_hash"}),
        "MATHEMATICAL_TEMPLATE_HASH_MISMATCH",
    )
    return copy.deepcopy(value)


def instantiate_mathematical_template(value, engine_run_id):
    value = validate_mathematical_template(value)
    raw = value["mathematical_request"]
    raw["recommendation"]["canonical_input"]["context"]["engine_run_id"] = _id(engine_run_id)
    return raw


def resource(kind, snapshot_id, binding_content_hash, document, *, approval_reference=None):
    row = {
        "kind": kind,
        "snapshot_id": snapshot_id,
        "binding_content_hash": binding_content_hash,
        "document": copy.deepcopy(document),
        "document_content_hash": content_hash(document),
        "approval_reference": approval_reference,
    }
    return _resource(row)


def _resource(row):
    _check(
        type(row) is dict
        and set(row)
        == {
            "kind",
            "snapshot_id",
            "binding_content_hash",
            "document",
            "document_content_hash",
            "approval_reference",
        },
        "RUNTIME_RESOURCE_FIELDS_INVALID",
    )
    _check(row["kind"] in KINDS and type(row["document"]) is dict, "RUNTIME_RESOURCE_KIND_INVALID")
    _id(row["snapshot_id"])
    _hash(row["binding_content_hash"])
    _check(
        row["document_content_hash"] == content_hash(row["document"]),
        "RUNTIME_RESOURCE_DOCUMENT_HASH_MISMATCH",
    )
    if row["approval_reference"] is not None:
        _id(row["approval_reference"])
    if row["kind"] in {"PPO_MODEL", "COST_PROFILE", "STRESS_SCENARIO"}:
        _check(row["approval_reference"] is not None, "RUNTIME_RESOURCE_APPROVAL_REQUIRED")
    return copy.deepcopy(row)


def seal_runtime_input_manifest(value):
    keys = {
        "contract_id",
        "contract_version",
        "environment_scope",
        "snapshot_id",
        "tenant_id",
        "project_id",
        "planning_cycle_id",
        "planning_cycle_revision_id",
        "demand_run_id",
        "scope",
        "config_id",
        "config_revision_id",
        "config_hash",
        "canonical_input_hash",
        "plan_resources_content_hash",
        "resources",
    }
    _check(type(value) is dict and set(value) == keys, "RUNTIME_INPUT_MANIFEST_FIELDS_INVALID")
    body = copy.deepcopy(value)
    _check(
        body["contract_id"] == "inventory-runtime-input-manifest"
        and body["contract_version"] == "1.0.0"
        and body["environment_scope"] == "DEVELOPMENT",
        "RUNTIME_INPUT_MANIFEST_CONTRACT_INVALID",
    )
    for key in (
        "snapshot_id",
        "tenant_id",
        "project_id",
        "planning_cycle_id",
        "planning_cycle_revision_id",
        "demand_run_id",
        "config_id",
        "config_revision_id",
    ):
        _id(body[key])
    for key in ("config_hash", "canonical_input_hash", "plan_resources_content_hash"):
        _hash(body[key])
    _check(
        type(body["scope"]) is dict
        and set(body["scope"]) == {"company_cd", "subs_cd", "plant_cd", "site_cd"},
        "RUNTIME_INPUT_SCOPE_INVALID",
    )
    for value in body["scope"].values():
        _id(value)
    _check(
        type(body["resources"]) is list and 3 <= len(body["resources"]) <= 128,
        "RUNTIME_RESOURCE_COUNT_INVALID",
    )
    rows = [_resource(row) for row in body["resources"]]
    _check(
        len({(r["kind"], r["snapshot_id"]) for r in rows}) == len(rows),
        "RUNTIME_RESOURCE_DUPLICATE",
    )
    body["resources"] = sorted(rows, key=lambda r: (r["kind"], r["snapshot_id"]))
    _check(
        len(json.dumps(body, ensure_ascii=False).encode()) <= MAX_BYTES, "RUNTIME_INPUT_SIZE_LIMIT"
    )
    return {**body, "content_hash": content_hash(body)}


def validate_runtime_input_manifest(value):
    _check(type(value) is dict and "content_hash" in value, "RUNTIME_INPUT_MANIFEST_INVALID")
    body = copy.deepcopy(value)
    expected = _hash(body.pop("content_hash"))
    result = seal_runtime_input_manifest(body)
    _check(result["content_hash"] == expected, "RUNTIME_INPUT_MANIFEST_HASH_MISMATCH")
    return result


def expected_resources(plan):
    spec = plan["policy_analysis_spec"]
    expected = {
        ("CONFIG", spec["config_revision_id"]): (spec["config_hash"], None),
        ("CLASSIFICATION", spec["classification_snapshot_id"]): (
            spec["classification_snapshot_content_hash"],
            None,
        ),
        (
            "MATHEMATICAL_TEMPLATE",
            plan["operational_strategy"]["strategy_input_binding"]["snapshot_id"],
        ): (plan["operational_strategy"]["strategy_input_binding"]["content_hash"], None),
    }
    if spec["evaluation_spec"]["mode"] != "DISABLED":
        for case in spec["evaluation_spec"]["cases"]:
            if case["input_source"] != "CURRENT_RUN":
                expected[("MATHEMATICAL_EVALUATION", case["input_snapshot_id"])] = (
                    case["input_content_hash"],
                    None,
                )
    for row in plan["shadow_challenger_bindings"]:
        key = ("PPO_MODEL", row["challenger_id"])
        expected[key] = (row["model"]["content_hash"], row["approval_reference"])
    cost = plan.get("cost_profile_binding")
    if cost is not None:
        expected[("COST_PROFILE", cost["cost_profile_id"])] = (
            cost["profile_content_hash"],
            cost["approval_reference"],
        )
    for row in plan["stress_scenario_bindings"]:
        expected[("STRESS_SCENARIO", row["scenario_id"])] = (row["scenario_content_hash"], None)
    landed = plan.get("landed_cost_binding")
    if landed is not None:
        expected[("RUN_COST_INPUT", landed["input_snapshot_id"])] = (
            landed["input_content_hash"],
            None,
        )
    return expected


def validate_manifest_plan_resources(manifest, plan):
    manifest = validate_runtime_input_manifest(manifest)
    _check(
        manifest["plan_resources_content_hash"] == plan_resources_content_hash(plan),
        "RUNTIME_INPUT_PLAN_RESOURCES_MISMATCH",
    )
    expected = expected_resources(plan)
    actual = {(r["kind"], r["snapshot_id"]): r for r in manifest["resources"]}
    _check(set(actual) == set(expected), "RUNTIME_INPUT_RESOURCE_SET_MISMATCH")
    for key, (digest, approval) in expected.items():
        _check(
            actual[key]["binding_content_hash"] == digest
            and (approval is None or actual[key]["approval_reference"] == approval),
            "RUNTIME_INPUT_RESOURCE_BINDING_MISMATCH",
        )
    return manifest


def bind_runtime_input_manifest(plan, manifest):
    manifest = validate_manifest_plan_resources(manifest, plan)
    body = copy.deepcopy(plan)
    body.pop("content_hash", None)
    body.update(
        contract_version="1.4.0",
        runtime_input_binding={
            "contract_key": "inventory.runtime_input_manifest",
            "contract_version": "1.0.0",
            "snapshot_id": manifest["snapshot_id"],
            "content_hash": manifest["content_hash"],
        },
    )
    return body

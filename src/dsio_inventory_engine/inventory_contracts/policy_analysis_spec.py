"""Closed, development-only automatic analysis specification (mirrored with Engine)."""

import copy
import hashlib
import json
import re

from .policy_search import normalize_policy_search, normalize_cost_assumptions, policy_search_hash


def _check(condition, code):
    if not condition:
        raise ValueError(code)


def _id(value):
    _check(
        type(value) is str
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value) is not None,
        "POLICY_ANALYSIS_IDENTIFIER_INVALID",
    )
    return value


def _hash(value):
    _check(
        type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
        "POLICY_ANALYSIS_HASH_INVALID",
    )
    return value


def _shape(value, fields):
    _check(type(value) is dict and set(value) == set(fields), "POLICY_ANALYSIS_SPEC_FIELDS_INVALID")
    return {key: normalizer(value[key]) for key, normalizer in fields.items()}


def _choice(*values):
    def validate(value):
        _check(type(value) is str and value in values, "POLICY_ANALYSIS_SPEC_VALUE_INVALID")
        return value

    return validate


def _seed(value):
    _check(type(value) is int and 0 <= value <= 9223372036854775807, "POLICY_ANALYSIS_SEED_INVALID")
    return value


def _optional(normalizer):
    return lambda value: None if value is None else normalizer(value)


def _case(value):
    result = _shape(
        value,
        {
            "case_id": _id,
            "trace_id": _id,
            "split": _choice("VALIDATION", "HOLDOUT"),
            "seed": _seed,
            "input_source": _choice("CURRENT_RUN", "SEALED_MATHEMATICAL_REQUEST"),
            "input_snapshot_id": _optional(_id),
            "input_content_hash": _optional(_hash),
        },
    )
    current = result["input_source"] == "CURRENT_RUN"
    _check(
        (current and result["input_snapshot_id"] is None and result["input_content_hash"] is None)
        or (
            not current
            and result["input_snapshot_id"] is not None
            and result["input_content_hash"] is not None
        ),
        "POLICY_ANALYSIS_CASE_BINDING_INVALID",
    )
    return result


def _rows(value, normalize, limit, *, nonempty=True):
    _check(
        type(value) is list and (1 if nonempty else 0) <= len(value) <= limit,
        "POLICY_ANALYSIS_LIST_INVALID",
    )
    return [normalize(row) for row in value]


def normalize_analysis_evaluation(value):
    _check(type(value) is dict, "POLICY_ANALYSIS_EVALUATION_INVALID")
    if value.get("mode") == "DISABLED":
        return _shape(value, {"mode": _choice("DISABLED")})
    result = _shape(
        value,
        {
            "mode": _choice("SEALED_SYNTHETIC_CASES"),
            "evaluation_id": _id,
            "source_type": _choice("SYNTHETIC_PLANNING_INPUT"),
            "reference_case_id": _id,
            "reference_cost_profile_id": _id,
            "cases": lambda v: _rows(v, _case, 24),
            "cost_profiles": lambda v: _rows(
                v,
                lambda row: _shape(
                    row,
                    {
                        "profile_id": _id,
                        "cost_assumptions": normalize_cost_assumptions,
                    },
                ),
                12,
            ),
            "terminal_pairs": lambda v: _rows(
                v,
                lambda row: _shape(
                    row,
                    {
                        "short_case_id": _id,
                        "extended_case_id": _id,
                    },
                ),
                24,
                nonempty=False,
            ),
        },
    )
    cases = {row["case_id"]: row for row in result["cases"]}
    profiles = {row["profile_id"]: row for row in result["cost_profiles"]}
    _check(
        len(cases) == len(result["cases"]) and len(profiles) == len(result["cost_profiles"]),
        "POLICY_ANALYSIS_EVALUATION_DUPLICATE",
    )
    reference = cases.get(result["reference_case_id"])
    _check(
        reference is not None
        and reference["input_source"] == "CURRENT_RUN"
        and reference["split"] == "VALIDATION"
        and sum(c["input_source"] == "CURRENT_RUN" for c in cases.values()) == 1,
        "POLICY_ANALYSIS_REFERENCE_CASE_INVALID",
    )
    _check(
        result["reference_cost_profile_id"] in profiles, "POLICY_ANALYSIS_REFERENCE_COST_INVALID"
    )
    identities = [
        (c["input_snapshot_id"], c["input_content_hash"])
        for c in cases.values()
        if c["input_source"] != "CURRENT_RUN"
    ]
    _check(len(identities) == len(set(identities)), "POLICY_ANALYSIS_CASE_INPUT_DUPLICATE")
    pairs = [(p["short_case_id"], p["extended_case_id"]) for p in result["terminal_pairs"]]
    _check(
        len(pairs) == len(set(pairs))
        and all(a in cases and b in cases and a != b for a, b in pairs),
        "POLICY_ANALYSIS_TERMINAL_PAIR_INVALID",
    )
    result["cases"] = sorted(result["cases"], key=lambda c: c["case_id"])
    result["cost_profiles"] = sorted(result["cost_profiles"], key=lambda c: c["profile_id"])
    result["terminal_pairs"] = sorted(
        result["terminal_pairs"], key=lambda c: (c["short_case_id"], c["extended_case_id"])
    )
    return result


def _body(value):
    result = _shape(
        value,
        {
            "contract_id": _choice("inventory-policy-analysis-spec"),
            "contract_version": _choice("1.0.0"),
            "execution_mode": _choice("DEVELOPMENT_SHADOW"),
            "config_schema_version": _choice("2.1.0"),
            "config_id": _id,
            "config_revision_id": _id,
            "config_hash": _hash,
            "classification_snapshot_id": _id,
            "classification_snapshot_content_hash": _hash,
            "effective_policy_content_hash": _hash,
            "search_spec": normalize_policy_search,
            "search_spec_content_hash": _hash,
            "evaluation_spec": normalize_analysis_evaluation,
        },
    )
    _check(
        result["search_spec_content_hash"] == policy_search_hash(result["search_spec"]),
        "POLICY_ANALYSIS_SEARCH_SPEC_HASH_MISMATCH",
    )
    if not result["search_spec"]["enabled"]:
        _check(
            result["evaluation_spec"]["mode"] == "DISABLED",
            "POLICY_ANALYSIS_DISABLED_SEARCH_EVALUATION_FORBIDDEN",
        )
    elif result["evaluation_spec"]["mode"] != "DISABLED":
        reference = next(
            p
            for p in result["evaluation_spec"]["cost_profiles"]
            if p["profile_id"] == result["evaluation_spec"]["reference_cost_profile_id"]
        )
        _check(
            reference["cost_assumptions"] == result["search_spec"]["cost_assumptions"],
            "POLICY_ANALYSIS_REFERENCE_COST_MISMATCH",
        )
    return result


def analysis_spec_content_hash(body):
    return hashlib.sha256(
        json.dumps(
            body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def seal_policy_analysis_spec(value):
    body = _body(copy.deepcopy(value))
    return {**body, "content_hash": analysis_spec_content_hash(body)}


def validate_policy_analysis_spec(value):
    _check(type(value) is dict and "content_hash" in value, "POLICY_ANALYSIS_SPEC_INVALID")
    body = copy.deepcopy(value)
    expected = _hash(body.pop("content_hash"))
    result = seal_policy_analysis_spec(body)
    _check(result["content_hash"] == expected, "POLICY_ANALYSIS_SPEC_HASH_MISMATCH")
    return result


def bind_policy_analysis_spec(plan, spec):
    """Upgrade a sealed 1.1/1.2 plan; its caller reseals the parent plan hash."""
    result = copy.deepcopy(plan)
    spec = validate_policy_analysis_spec(spec)
    _check(
        result["classification_config_hash"] == spec["config_hash"]
        and result["effective_policy_content_hash"] == spec["effective_policy_content_hash"],
        "POLICY_ANALYSIS_PLAN_POLICY_MISMATCH",
    )
    result.pop("content_hash", None)
    result.update(contract_version="1.3.0", policy_analysis_spec=spec)
    return result

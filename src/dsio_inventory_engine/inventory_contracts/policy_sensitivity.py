"""Explicit local horizon/re-pricing experiment, not an operational policy approval."""

from dataclasses import dataclass

from .policy_search import normalize_cost_assumptions
from .values import (
    InventoryInputError,
    canonical_json,
    choice,
    digest,
    hash_value,
    identifier,
    integer,
    read_json,
    records,
    require,
    shape,
)

RESULT_FIELDS = frozenset(
    (
        "contract_id",
        "contract_version",
        "status",
        "evaluation_spec",
        "input_hash",
        "comparison_mode",
        "holdout_mode",
        "terminal_mode",
        "matrix",
        "reference_selection",
        "selection_changes",
        "holdout_evaluations",
        "terminal_diagnostics",
        "content_hash",
        "automatic_publish_allowed",
        "automatic_order_allowed",
        "operational_policy_approved",
        "database_writes",
    )
)


def _cost(value: dict | None) -> dict | None:
    try:
        return normalize_cost_assumptions(value)
    except ValueError as exc:
        raise InventoryInputError("POLICY_SENSITIVITY_COST_INVALID") from exc


def _source(value: dict) -> dict:
    require(type(value) is dict, "POLICY_SENSITIVITY_SOURCE_INVALID")
    hash_value(value.get("content_hash"))
    return value


@dataclass(frozen=True)
class PolicySensitivityRequest:
    document_json: str

    @classmethod
    def from_dict(cls, value: dict) -> "PolicySensitivityRequest":
        data = shape(
            value,
            {
                "contract_id": choice("inventory-policy-sensitivity-request"),
                "contract_version": choice("1.0.0"),
                "evaluation_id": identifier,
                "source_type": choice("SYNTHETIC_PLANNING_INPUT"),
                "reference_case_id": identifier,
                "reference_cost_profile_id": identifier,
                "cases": lambda v: records(
                    v,
                    {
                        "case_id": identifier,
                        "trace_id": identifier,
                        "split": choice("VALIDATION", "HOLDOUT"),
                        "seed": integer,
                        "search_result": _source,
                    },
                    limit=24,
                ),
                "cost_profiles": lambda v: records(
                    v,
                    {
                        "profile_id": identifier,
                        "cost_assumptions": _cost,
                    },
                    limit=12,
                ),
                "terminal_pairs": lambda v: records(
                    v,
                    {
                        "short_case_id": identifier,
                        "extended_case_id": identifier,
                    },
                    limit=24,
                ),
            },
        )
        require(data["cases"] and data["cost_profiles"], "POLICY_SENSITIVITY_EMPTY_REQUEST")
        for key, identity in (("cases", "case_id"), ("cost_profiles", "profile_id")):
            require(
                len({r[identity] for r in data[key]}) == len(data[key]),
                "POLICY_SENSITIVITY_DUPLICATE",
            )
            data[key].sort(key=lambda r: r[identity])
        data["terminal_pairs"].sort(key=lambda r: (r["short_case_id"], r["extended_case_id"]))
        require(
            len({(r["short_case_id"], r["extended_case_id"]) for r in data["terminal_pairs"]})
            == len(data["terminal_pairs"]),
            "POLICY_SENSITIVITY_DUPLICATE",
        )
        document = canonical_json(data)
        require(len(document.encode()) <= 128_000_000, "POLICY_SENSITIVITY_SIZE_LIMIT")
        return cls(document)

    def to_dict(self) -> dict:
        return read_json(self.document_json, max_bytes=128_000_000)

    @property
    def specification(self) -> dict:
        data = self.to_dict()
        for case in data["cases"]:
            source = case.pop("search_result")
            case["search_content_hash"] = source["content_hash"]
            case["search_result_reference"] = "PSR-" + source["content_hash"]
        return data

    @property
    def input_hash(self) -> str:
        return digest(self.specification)


def verify_policy_sensitivity_result(value: dict) -> dict:
    require(
        type(value) is dict
        and set(value) == RESULT_FIELDS
        and value.get("contract_id") == "inventory-policy-sensitivity-result"
        and value.get("contract_version") == "1.0.0"
        and value.get("status") == "POLICY_SENSITIVITY_EVALUATED_LOCALLY",
        "POLICY_SENSITIVITY_RESULT_CONTRACT_INVALID",
    )
    require(
        value["comparison_mode"] == "ACTUAL_HORIZON_SIMULATION_WITH_IMMUTABLE_PSI_REPRICING"
        and value["holdout_mode"]
        == "NEW_SEED_PLANNING_PATH_FIXED_PRE_W0_POLICY_NOT_CHRONOLOGICAL_BACKTEST"
        and value["terminal_mode"]
        == "COMMON_PREFIX_WITH_CONTINUED_POLICY_EXTENSION_NO_SALVAGE_VALUE",
        "POLICY_SENSITIVITY_MODE_INVALID",
    )
    require(
        type(value["evaluation_spec"]) is dict
        and all(
            type(value[k]) is list
            for k in (
                "matrix",
                "reference_selection",
                "selection_changes",
                "holdout_evaluations",
                "terminal_diagnostics",
            )
        )
        and bool(value["matrix"])
        and bool(value["reference_selection"]),
        "POLICY_SENSITIVITY_RESULT_FIELDS_INVALID",
    )
    require(
        all(
            value.get(k) is False
            for k in (
                "automatic_publish_allowed",
                "automatic_order_allowed",
                "operational_policy_approved",
                "database_writes",
            )
        ),
        "POLICY_SENSITIVITY_PUBLICATION_FORBIDDEN",
    )
    require(
        value.get("input_hash") == digest(value["evaluation_spec"]),
        "POLICY_SENSITIVITY_INPUT_HASH_MISMATCH",
    )
    require(
        value.get("content_hash")
        == digest({k: v for k, v in value.items() if k != "content_hash"}),
        "POLICY_SENSITIVITY_RESULT_HASH_MISMATCH",
    )
    return value

"""Opt-in calculation-only linkage; reuse selection and mathematical input identities.

This is a reference contract, not a PSI runner, policy approval, or source replay.
The execution adapter must verify the original selection with its existing source
verifier before applying its policies through the shared strategy/Guard/PSI.
"""

from dataclasses import dataclass
import re

from .canonical import CanonicalInputRequest
from .catalog_selection import verify_catalog_selection_result
from .mathematical import MathematicalPolicyRequest, policy_input_binding
from .policy_search import POLICY_KINDS
from .policy_selection import verify_policy_selection_result
from .values import canonical_json, choice, hash_value, identifier, read_json, require, shape

CONTRACT_ID = "inventory-selected-policy-order-plan-binding"
VERSION = "1.0.0"
BINDING_FIELDS = frozenset(
    (
        "contract_id",
        "contract_version",
        "execution_mode",
        "selection_result_reference",
        "selection_result_content_hash",
        "canonical_input_hash",
        "mathematical_input_binding",
    )
)
MATHEMATICAL_BINDING_FIELDS = {
    "contract_id": choice("io-mathematical-policy-input-v1"),
    "contract_version": choice("1.0.0"),
    "snapshot_id": identifier,
    "content_hash": hash_value,
}
SELECTION_TYPES = {
    "inventory-policy-selection-result": ("PSEL-", verify_policy_selection_result),
    "inventory-offline-catalog-policy-selection-result": (
        "OCPSEL-",
        verify_catalog_selection_result,
    ),
}


def _reference(value: str) -> str:
    require(
        type(value) is str and re.fullmatch(r"(?:PSEL|OCPSEL)-[0-9a-f]{64}", value) is not None,
        "ORDER_PLAN_SELECTION_REFERENCE_INVALID",
    )
    return value


def _selection(value: dict, expected_hash: str) -> tuple[dict, str]:
    hash_value(expected_hash)
    require(
        type(value) is dict and value.get("contract_id") in SELECTION_TYPES,
        "ORDER_PLAN_SELECTION_CONTRACT_INVALID",
    )
    prefix, verifier = SELECTION_TYPES[value["contract_id"]]
    verified = verifier(value)
    require(verified["content_hash"] == expected_hash, "ORDER_PLAN_SELECTION_HASH_MISMATCH")
    for row in verified["item_selections"]:
        if row["status"] == "SELECTED":
            require(
                row["selected_policy"].get("policy_kind") in POLICY_KINDS,
                "ORDER_PLAN_POLICY_KIND_UNSUPPORTED",
            )
    return verified, prefix


@dataclass(frozen=True)
class SelectedPolicyOrderPlanBinding:
    """No new plan ID, binding Hash, candidate list, or duplicated policy payload."""

    document_json: str

    @classmethod
    def from_dict(cls, value: dict) -> "SelectedPolicyOrderPlanBinding":
        normalized = shape(
            value,
            {
                "contract_id": choice(CONTRACT_ID),
                "contract_version": choice(VERSION),
                "execution_mode": choice("CALCULATION_ONLY"),
                "selection_result_reference": _reference,
                "selection_result_content_hash": hash_value,
                "canonical_input_hash": hash_value,
                "mathematical_input_binding": lambda v: shape(v, MATHEMATICAL_BINDING_FIELDS),
            },
        )
        require(
            normalized["selection_result_reference"].split("-", 1)[1]
            == normalized["selection_result_content_hash"],
            "ORDER_PLAN_SELECTION_REFERENCE_MISMATCH",
        )
        return cls(canonical_json(normalized))

    @classmethod
    def from_sources(
        cls,
        selection_result: dict,
        mathematical_request: MathematicalPolicyRequest,
        *,
        expected_selection_hash: str,
    ) -> "SelectedPolicyOrderPlanBinding":
        selection, prefix = _selection(selection_result, expected_selection_hash)
        raw = MathematicalPolicyRequest.from_dict(mathematical_request.to_dict()).to_dict()
        canonical = CanonicalInputRequest.from_dict(raw["recommendation"]["canonical_input"])
        inputs = canonical.to_dict()
        require(
            raw["recommendation"]["execution"]["execution_mode"] == "LOCAL_SHADOW",
            "ORDER_PLAN_LOCAL_SHADOW_INPUT_REQUIRED",
        )
        require(
            raw["policy_input"]["status"] == "SEALED"
            and all(s["status"] == "SEALED" for s in inputs["snapshots"].values()),
            "ORDER_PLAN_SEALED_INPUT_REQUIRED",
        )
        require(
            canonical_json(selection["context"]) == canonical_json(inputs["context"]),
            "ORDER_PLAN_CONTEXT_MISMATCH",
        )
        require(
            selection["canonical_input_hash"] == canonical.input_hash,
            "ORDER_PLAN_CANONICAL_INPUT_MISMATCH",
        )
        require(
            selection["mathematical_input_hash"] == raw["policy_input"]["content_hash"],
            "ORDER_PLAN_MATHEMATICAL_INPUT_MISMATCH",
        )
        master = {
            row["item_id"]: row
            for row in inputs["snapshots"]["master"]["rows"]
            if row["active"] and row["stock_managed"]
        }
        if inputs.get("development_universe") is not None:
            from .development_universe import admitted_development_master
            from .network import DeploymentScope

            master = admitted_development_master(
                inputs, master, DeploymentScope(inputs["context"]["company_cd"], "DEVELOPMENT")
            )
        universe = set(master)
        require(
            {row["item_id"] for row in selection["item_selections"]} == universe,
            "ORDER_PLAN_ITEM_COVERAGE_MISMATCH",
        )
        return cls.from_dict(
            {
                "contract_id": CONTRACT_ID,
                "contract_version": VERSION,
                "execution_mode": "CALCULATION_ONLY",
                "selection_result_reference": prefix + selection["content_hash"],
                "selection_result_content_hash": selection["content_hash"],
                "canonical_input_hash": canonical.input_hash,
                "mathematical_input_binding": policy_input_binding(raw["policy_input"]),
            }
        )

    def to_dict(self) -> dict:
        return read_json(self.document_json)

    def verify_sources(
        self,
        selection_result: dict,
        mathematical_request: MathematicalPolicyRequest,
        *,
        expected_selection_hash: str,
    ) -> dict:
        actual = type(self).from_dict(self.to_dict())
        expected = type(self).from_sources(
            selection_result,
            mathematical_request,
            expected_selection_hash=expected_selection_hash,
        )
        require(actual == expected, "ORDER_PLAN_SOURCE_BINDING_MISMATCH")
        return actual.to_dict()

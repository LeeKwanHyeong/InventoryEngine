"""Packaging only; evaluation expectations are independent literal quantities."""

import copy

from training_fixtures import training_fixture
from dsio_inventory_engine.evaluate_replenishment.demo import build_evaluation_request, seal_fixture
from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.mathematical import (
    normalize_policy_input,
    policy_input_content,
    policy_input_binding,
)
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.training import TrainingRequest
from dsio_inventory_engine.inventory_contracts.values import digest

DEV = DeploymentScope("DSE", "DEVELOPMENT")


def fixture(training=None, scenario="BASE"):
    return build_evaluation_request(
        TrainingRequest.from_dict(training or training_fixture()), DEV, scenario_id=scenario
    ).to_dict()


def reseal_evaluation(data):
    data = copy.deepcopy(data)
    math = data["mathematical_request"]
    canonical = math["recommendation"]["canonical_input"]
    seal_fixture(canonical)
    input_hash = CanonicalInputRequest.from_dict(canonical).input_hash
    math["recommendation"]["execution"]["canonical_input_hash"] = input_hash
    math["policy_input"]["context"]["canonical_input_hash"] = input_hash
    policy = normalize_policy_input(math["policy_input"])
    policy["history_row_count"] = len(policy["history"])
    policy["content_hash"] = digest(policy_input_content(policy))
    math["policy_input"] = policy
    math["recommendation"]["execution"]["strategy_input_binding"] = policy_input_binding(policy)
    return data


def override(data, *, kind="APPROVED_OVERRIDE", start_index=0, target="50"):
    math = data["mathematical_request"]
    source = math["recommendation"]["canonical_input"]
    calendar = sorted(source["snapshots"]["calendar"]["rows"], key=lambda r: r["seq"])
    math["policy_input"]["adjustments"] = [
        {
            "adjustment_id": "TEST-OVERRIDE",
            "item_id": "A",
            "uom": "EA",
            "kind": kind,
            "effective_from": calendar[start_index]["start_date"],
            "effective_to": source["snapshots"]["policies"]["rows"][0]["effective_to"],
            "values": {"safety_stock_qty": "0", "rop_qty": target, "target_inventory_qty": target},
            "reason": "Hand-reviewed test case",
            "approved_by": "TEST",
            "approved_at": source["context"]["inventory_cutoff_at"],
            "approval_reference": "TEST-ONLY",
            "source_document_id": "TEST-POLICY",
        }
    ]
    return data

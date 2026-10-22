"""Source/selection packaging only; hand-calculated expectations are separate."""

import copy

from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.order_planning import SelectedPolicyOrderPlanBinding
from dsio_inventory_engine.recommend_replenishment.application.policy_search import (
    RunPolicySearchUseCase,
)
from dsio_inventory_engine.recommend_replenishment.application.policy_selection import (
    RunPolicySelectionUseCase,
)
from dsio_inventory_engine.recommend_replenishment.application.selected_order_plan import (
    RunSelectedPolicyOrderPlanUseCase,
)
from tests.support.catalog_selection_fixtures import DEPLOYMENT, select, selection_case
from tests.support.policy_search_fixtures import GOLDEN, bundle
from math_fixtures import build_math


def execution_case(parts=None, *, mode="ABC_V1"):
    if mode == "ABC_V1":
        raw, config, classification = parts or bundle()
        request = MathematicalPolicyRequest.from_dict(raw)
        source = RunPolicySearchUseCase(DEPLOYMENT).execute(
            request, config_values=config, classification_snapshot=classification
        )
        selection = RunPolicySelectionUseCase(DEPLOYMENT).execute(source)
        runner = RunSelectedPolicyOrderPlanUseCase(DEPLOYMENT)
        extra = {"config_values": config, "classification_snapshot": classification}
    else:
        case = selection_case(mode=mode, parts=parts)
        source, selector, request, bindings = case
        selection = select(case)
        runner = RunSelectedPolicyOrderPlanUseCase(DEPLOYMENT, selector.search.producer)
        extra = {k: v for k, v in bindings.items() if k != "expected_source_hash"}
    binding = SelectedPolicyOrderPlanBinding.from_sources(
        selection, request, expected_selection_hash=selection["content_hash"]
    )
    return runner, {
        "binding": binding,
        "selection_result": selection,
        "source": source,
        "request": request,
        **extra,
    }


def golden_parts(case):
    raw = build_math(
        {
            "id": "selected-plan-" + case["id"],
            "history": "constant",
            "boh": case.get("boh", "10"),
            "forecast": case.get("forecast", ["10", "10", "10"]),
            "policy": case.get("policy", {}),
        }
    )
    search = copy.deepcopy(GOLDEN["search_config"])
    search["minimum_fill_rate"] = case.get("minimum_fill_rate", "0.95")
    search["group_profiles"][0].update(
        policy_kinds=[case["kind"]],
        review_cycle_weeks=[case.get("review_cycle_weeks", 1)],
    )
    return bundle(raw, search)


def v2_parts(parts):
    """Package the existing effective-policy contract, never create new policies."""
    from dsio_inventory_engine.inventory_contracts.effective_policy_v2 import (
        EFFECTIVE_POLICY_V2_FIELDS,
        effective_policy_content_hash,
    )

    raw, config, classification = copy.deepcopy(parts)
    raw["recommendation"]["execution"].update(
        contract_version="2.0.0",
        execution_mode="LOCAL_SHADOW",
        execution_purpose="SHADOW",
        model_approval=None,
        effective_policy_binding={
            "contract_id": "inventory-effective-policy-run-binding-v2",
            "contract_version": "2.0.0",
            "classification_snapshot_id": classification["classification_snapshot_id"],
            "classification_config_hash": classification["config_hash"],
            "effective_policy_content_hash": effective_policy_content_hash(classification["items"]),
        },
        effective_item_policies=[
            {"item_id": row["item_id"], **{k: row[k] for k in EFFECTIVE_POLICY_V2_FIELDS}}
            for row in classification["items"]
        ],
    )
    return raw, config, classification

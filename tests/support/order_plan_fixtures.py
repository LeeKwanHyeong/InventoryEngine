"""Real, cached small selection sources, never operational or database fixtures."""

import copy
from functools import lru_cache

from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.order_planning import SelectedPolicyOrderPlanBinding
from dsio_inventory_engine.recommend_replenishment.application.policy_search import (
    RunPolicySearchUseCase,
)
from dsio_inventory_engine.recommend_replenishment.application.policy_selection import (
    RunPolicySelectionUseCase,
)
from tests.support.catalog_selection_fixtures import DEPLOYMENT, select, selection_case
from tests.support.policy_search_fixtures import bundle


@lru_cache(maxsize=4)
def _case(mode):
    if mode == "ABC_V1":
        raw, config, classification = bundle()
        request = MathematicalPolicyRequest.from_dict(raw)
        source = RunPolicySearchUseCase(DEPLOYMENT).execute(
            request, config_values=config, classification_snapshot=classification
        )
        selection = RunPolicySelectionUseCase(DEPLOYMENT).execute(source)
    else:
        case = selection_case(mode=mode)
        source, _, request, _ = case
        selection = select(case)
    return source, selection, request.to_dict()


def order_plan_case(mode="LEGACY_STYLE"):
    source, selection, raw = copy.deepcopy(_case(mode))
    request = MathematicalPolicyRequest.from_dict(raw)
    binding = SelectedPolicyOrderPlanBinding.from_sources(
        selection, request, expected_selection_hash=selection["content_hash"]
    )
    return source, selection, request, binding

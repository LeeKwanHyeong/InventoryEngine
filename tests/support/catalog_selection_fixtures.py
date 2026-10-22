"""Small independent calculation inputs; no approved configuration is modified."""

from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.recommend_replenishment.application.catalog_search import (
    RunCatalogPolicySearchUseCase,
)
from dsio_inventory_engine.recommend_replenishment.application.catalog_selection import (
    RunCatalogPolicySelectionUseCase,
)
from math_fixtures import build_math
from tests.support.catalog_search_fixtures import catalog_bundle, run_bundle
from tests.support.policy_search_fixtures import bundle

DEPLOYMENT = DeploymentScope("DSE", "DEVELOPMENT")


def feasible_parts(*, boh="10", forecast=None, values=None):
    raw = build_math(
        {
            "id": "catalog-select-golden",
            "history": "constant",
            "boh": boh,
            "forecast": ["10", "10", "10"] if forecast is None else forecast,
            "profile": {"lookback_weeks": 26, "stddev_ddof": 0},
        }
    )
    if values is None:
        _, values, _ = run_bundle()
    return bundle(raw, values["policy_search"], config_values=values)


def selection_case(*, mode="LEGACY_STYLE", parts=None, feature_changes=None):
    parts, producer, catalog = catalog_bundle(
        mode=mode,
        parts=parts or feasible_parts(),
        feature_changes=feature_changes,
    )
    request = MathematicalPolicyRequest.from_dict(parts[0])
    bindings = {
        "catalog": catalog,
        "expected_catalog_hash": catalog.content_hash,
        "config_values": parts[1],
        "classification_snapshot": parts[2],
    }
    source = RunCatalogPolicySearchUseCase(DEPLOYMENT, producer).execute(request, **bindings)
    selector = RunCatalogPolicySelectionUseCase(DEPLOYMENT, producer)
    return source, selector, request, {"expected_source_hash": source["content_hash"], **bindings}


def select(case=None):
    source, selector, request, bindings = case or selection_case()
    return selector.execute(source, request, **bindings)

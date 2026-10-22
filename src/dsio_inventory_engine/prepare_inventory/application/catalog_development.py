"""Prepare exact development Catalog inputs without Forecast, PSI, or DB execution.

Preserve the full parent classification and Master; an explicit development sample
only narrows numerical admission. Search settings are a new local draft, not an
activation, approval, Claim, or result.
"""

import copy
from datetime import date, timedelta
from decimal import Decimal
import uuid

from dsio_inventory_engine.classify_inventory.application import (
    SNAPSHOT_NAMESPACE,
    _snapshot_hash,
    config_hash,
    validate_config,
)
from dsio_inventory_engine.classify_inventory.candidate_catalog import (
    MODES,
    CandidateCatalogProducer,
    VerifiedCandidateCatalog,
)
from dsio_inventory_engine.classify_inventory.legacy_features import (
    LegacyFeatureProducer,
    feature_input_binding,
)
from dsio_inventory_engine.classify_inventory.legacy_rules import LegacyStyleRules
from dsio_inventory_engine.classify_inventory.legacy_style import LegacyFactorSegmentation
from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.development_universe import seal_development_universe
from dsio_inventory_engine.inventory_contracts.effective_policy_v2 import (
    EFFECTIVE_POLICY_V2_FIELDS,
    derive_effective_item_policy_v2,
    effective_policy_content_hash,
)
from dsio_inventory_engine.inventory_contracts.mathematical import (
    MathematicalPolicyRequest,
    normalize_policy_input,
    policy_input_binding,
    policy_input_content,
)
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.policy_search import normalize_policy_search
from dsio_inventory_engine.inventory_contracts.segmentation import (
    SearchGroupAssignment,
    SegmentationResult,
)
from dsio_inventory_engine.inventory_contracts.values import digest, hash_value, require
from dsio_inventory_engine.prepare_inventory.application.development_input import snapshot
from dsio_inventory_engine.prepare_inventory.application.inventory_input import (
    PrepareInventoryInputUseCase,
)
from dsio_inventory_engine.recommend_replenishment.application.math_input import validate_math_input


def _classification(parent, old_values, values, revision_id=None):
    body = copy.deepcopy(parent)
    unsigned = {
        k: v for k, v in body.items() if k not in {"content_hash", "classification_snapshot_id"}
    }
    require(_snapshot_hash(unsigned) == body["content_hash"], "DEV_CATALOG_PARENT_HASH")
    require(
        body["config_hash"] == config_hash(old_values)
        and body["classification_snapshot_id"]
        == str(uuid.uuid5(SNAPSHOT_NAMESPACE, body["content_hash"]))
        and body["effective_policy_content_hash"] == effective_policy_content_hash(body["items"]),
        "DEV_CATALOG_PARENT_BINDING",
    )
    new_hash = config_hash(values)
    body["config_hash"] = new_hash
    body["config_revision_id"] = str(
        uuid.uuid5(
            SNAPSHOT_NAMESPACE,
            "development-search:"
            + parent["config_id"]
            + ":"
            + parent["config_revision_id"]
            + ":"
            + new_hash,
        )
    )
    if revision_id is not None:
        body["config_revision_id"] = str(uuid.UUID(revision_id))
    cells = {r["segment_key"]: r for r in values["policy_matrix"]["cells"]}
    for row in body["items"]:
        policy = derive_effective_item_policy_v2(
            item_id=row["item_id"],
            classification_config_hash=new_hash,
            classification_status=row["classification_status"],
            segment_key=row["segment_key"],
            unclassified_reason_code=row["unclassified_reason_code"],
            policy_cell=cells.get(row["segment_key"]),
            ved_service_level_floor=values["policy_matrix"]["ved_service_level_floor"],
            axis_results=row["axis_results"],
            policy_overlays=values["policy_overlays"],
        )
        require(
            policy["effective_policy_hash"] == row["effective_policy_hash"],
            "DEV_CATALOG_GATE_CHANGED",
        )
        row.update(policy)
    body["effective_policy_content_hash"] = effective_policy_content_hash(body["items"])
    require(
        body["effective_policy_content_hash"] == parent["effective_policy_content_hash"],
        "DEV_CATALOG_GATE_CHANGED",
    )
    body.pop("classification_snapshot_id")
    body.pop("content_hash")
    body["content_hash"] = _snapshot_hash(body)
    body["classification_snapshot_id"] = str(uuid.uuid5(SNAPSHOT_NAMESPACE, body["content_hash"]))
    return body


def _sample(canonical, classification, ids):
    raw = copy.deepcopy(canonical)
    old = raw["development_universe"]
    require(
        old["contract_id"] == "io-development-calculation-universe-v2",
        "DEV_CATALOG_PARENT_UNIVERSE",
    )
    require(
        ids and len(ids) <= 16 and ids <= set(old["included_item_ids"]),
        "DEV_CATALOG_SAMPLE_INVALID",
    )
    by_item = {r["item_id"]: r for r in classification["items"]}
    require(
        all(by_item[i]["recommendation_calculation_allowed"] for i in ids),
        "DEV_CATALOG_SAMPLE_BLOCKED",
    )
    exclusions = copy.deepcopy(old["exclusions"])
    exclusions.extend(
        {"item_id": i, "reason": "EXCLUDED_DEVELOPMENT_SAMPLE"}
        for i in sorted(set(old["included_item_ids"]) - ids)
    )
    universe = {k: v for k, v in old.items() if k != "content_hash"}
    universe.update(
        contract_id="io-development-calculation-universe-v3",
        included_item_ids=sorted(ids),
        exclusions=exclusions,
        classification_snapshot_id=classification["classification_snapshot_id"],
        classification_content_hash=classification["content_hash"],
        approval_reference="DEV-LOCAL-SAMPLE-NOT-OPERATIONAL-APPROVAL",
    )
    raw["development_universe"] = seal_development_universe(universe)
    raw["context"]["configuration_revision"] = classification["config_revision_id"]
    for kind in (
        "forecast",
        "prior_inventory",
        "inventory",
        "policies",
        "receipts",
        "customer_orders",
    ):
        source = raw["snapshots"][kind]
        metadata = copy.deepcopy(source["metadata"])
        if kind == "inventory":
            prior = raw["snapshots"]["prior_inventory"]
            metadata.update(
                prior_snapshot_id=prior["snapshot_id"], prior_content_hash=prior["content_hash"]
            )
            for field in ("adjustments", "source_events"):
                metadata[field] = [r for r in metadata[field] if r["item_id"] in ids]
        identity = (
            "DEV-SAMPLE-"
            + kind
            + "-"
            + digest({"parent": source["content_hash"], "items": sorted(ids)})[:24]
        )
        if kind == "forecast":
            identity = (
                raw["upstream_bindings"]["forecast"]["snapshot_id"]
                + ".canonical."
                + raw["development_universe"]["content_hash"][:16]
            )
        raw["snapshots"][kind] = snapshot(
            kind, identity, metadata, [r for r in source["rows"] if r["item_id"] in ids]
        )
        current = raw["snapshots"][kind]
        raw["input_bindings"][kind] = {k: current[k] for k in ("snapshot_id", "content_hash")}
    return CanonicalInputRequest.from_dict(raw)


def prepare_development_catalog_inputs(
    *,
    mathematical_request,
    config_values,
    classification_snapshot,
    item_ids,
    history_source,
    cost_assumptions,
    rules_document,
    reference_calculator_build_hash,
    deployment: DeploymentScope,
    configuration_revision_id=None,
    execution_context=None,
    catalog_modes=MODES,
):
    """Return a new local draft, preserving original grades, policy hashes and all gates."""
    require(deployment.environment == "DEVELOPMENT", "DEV_CATALOG_DEVELOPMENT_ONLY")
    require(
        type(item_ids) is list and len(set(item_ids)) == len(item_ids), "DEV_CATALOG_SAMPLE_INVALID"
    )
    ids = set(item_ids)
    original = MathematicalPolicyRequest.from_dict(mathematical_request).to_dict()
    parent_canonical = CanonicalInputRequest.from_dict(
        original["recommendation"]["canonical_input"]
    )
    PrepareInventoryInputUseCase(deployment).execute(parent_canonical)
    require(
        original["recommendation"]["canonical_input"]["context"]["configuration_revision"]
        == classification_snapshot["config_revision_id"],
        "DEV_CATALOG_PARENT_BINDING",
    )
    original_context = parent_canonical.to_dict()["context"]
    original_binding = original["recommendation"]["execution"]["effective_policy_binding"]
    require(
        all(
            classification_snapshot["scope"][k] == original_context[k]
            for k in ("company_cd", "subs_cd", "site_cd")
        )
        and original_binding["classification_snapshot_id"]
        == classification_snapshot["classification_snapshot_id"]
        and original_binding["classification_config_hash"] == classification_snapshot["config_hash"]
        and original_binding["effective_policy_content_hash"]
        == classification_snapshot["effective_policy_content_hash"],
        "DEV_CATALOG_PARENT_BINDING",
    )
    values = copy.deepcopy(config_values)
    require(bool(catalog_modes) and set(catalog_modes) <= set(MODES), "DEV_CATALOG_MODES")
    values["policy_search"].update(
        enabled=True,
        lookback_weeks=26,
        max_candidates_per_item=88 if "WIDE_REFERENCE" in catalog_modes else 32,
        cost_assumptions=copy.deepcopy(cost_assumptions),
    )
    values = validate_config(values)
    search = normalize_policy_search(values["policy_search"])
    require(search["cost_assumptions"] is not None, "DEV_CATALOG_COST_REQUIRED")
    classification = _classification(
        classification_snapshot, config_values, values, configuration_revision_id
    )
    canonical = _sample(parent_canonical.to_dict(), classification, ids)
    data = canonical.to_dict()
    if execution_context is not None:
        require(
            set(execution_context)
            == {
                "engine_run_id",
                "planning_cycle_id",
                "planning_cycle_revision_id",
                "cycle_site_execution_id",
            },
            "DEV_CATALOG_EXECUTION_CONTEXT",
        )
        data["context"].update(execution_context)
        canonical = CanonicalInputRequest.from_dict(data)
    context = data["context"]
    w0 = date.fromisoformat(context["plan_start_date"])
    calendar = [
        {
            "yyyyww": (w0 - timedelta(weeks=26 - i)).strftime("%G%V"),
            "start_date": (w0 - timedelta(weeks=26 - i)).isoformat(),
            "end_date": (w0 - timedelta(weeks=26 - i) + timedelta(days=6)).isoformat(),
        }
        for i in range(26)
    ]
    source = copy.deepcopy(history_source)
    require(
        source["scope"] == {k: context[k] for k in ("company_cd", "subs_cd", "site_cd")}
        and source["decision_yyyyww"] == context["plan_yyyyww"],
        "DEV_CATALOG_HISTORY_SCOPE",
    )
    require(source["evidence_kind"] == "SYNTHETIC_PINNED", "DEV_CATALOG_HISTORY_PROVENANCE")
    source["rows"] = [r for r in source["rows"] if r["item_id"] in ids]
    expected = {(i, w["yyyyww"]) for i in ids for w in calendar}
    require(
        len(source["rows"]) == len(expected)
        and {(r["item_id"], r["yyyyww"]) for r in source["rows"]} == expected,
        "DEV_CATALOG_HISTORY_COVERAGE",
    )
    require(
        all(
            Decimal(r["demand_qty"]).is_finite() and Decimal(r["demand_qty"]) >= 0
            for r in source["rows"]
        ),
        "DEV_CATALOG_HISTORY_QUANTITY",
    )
    raw = original
    raw["recommendation"]["canonical_input"] = data
    policy = raw["policy_input"]
    policy.update(
        snapshot_id="DEV-MATH-"
        + digest({"canonical": canonical.input_hash, "history": source})[:32],
        calendar=calendar,
        history=[{**r, "uom": "EA"} for r in source["rows"]],
        history_row_count=len(source["rows"]),
    )
    policy["context"].update(
        canonical_input_hash=canonical.input_hash,
        configuration_revision=context["configuration_revision"],
        history_source_type="SYNTHETIC_DEMAND",
        available_at=source["available_at"],
    )
    policy["profile"]["lookback_weeks"] = 26
    policy["adjustments"] = [r for r in policy["adjustments"] if r["item_id"] in ids]
    policy = normalize_policy_input(policy)
    policy["content_hash"] = digest(policy_input_content(policy))
    raw["policy_input"] = policy
    execution = raw["recommendation"]["execution"]
    execution.update(
        configuration_revision=context["configuration_revision"],
        canonical_input_hash=canonical.input_hash,
        execution_mode="LOCAL_SHADOW",
        execution_purpose="SHADOW",
        strategy_input_binding=policy_input_binding(policy),
    )
    execution["effective_policy_binding"].update(
        classification_config_hash=classification["config_hash"],
        classification_snapshot_id=classification["classification_snapshot_id"],
        effective_policy_content_hash=classification["effective_policy_content_hash"],
    )
    execution["effective_item_policies"] = [
        {"item_id": r["item_id"], **{k: r[k] for k in EFFECTIVE_POLICY_V2_FIELDS}}
        for r in classification["items"]
    ]
    execution["item_controls"] = [r for r in execution["item_controls"] if r["item_id"] in ids]
    request = MathematicalPolicyRequest.from_dict(raw)
    prepared = PrepareInventoryInputUseCase(deployment).execute(canonical).to_dict()
    validate_math_input(
        request.to_dict()["policy_input"], request.to_dict()["recommendation"], prepared
    )
    feature = _feature_input(
        request.to_dict(), classification, source, reference_calculator_build_hash
    )
    rules = LegacyStyleRules.from_document(rules_document)
    producer = LegacyFeatureProducer(
        rules, reference_calculator_build_hash=reference_calculator_build_hash
    )
    classifier = LegacyFactorSegmentation(
        rules, reference_calculator_build_hash=reference_calculator_build_hash
    )
    detailed = classifier.classify(
        producer.produce(feature, expected_binding=feature_input_binding(feature))
    )
    catalogs = {}
    catalog_producer = CandidateCatalogProducer(classifier)
    for mode in catalog_modes:
        catalog = catalog_producer.produce(
            detailed, mode=mode, expected_result_hash=detailed.content_hash
        )
        verified = VerifiedCandidateCatalog.bind(
            catalog, catalog_producer, expected_catalog_hash=catalog.content_hash
        )
        verified.validate_inputs(request.to_dict(), prepared, classification, search)
        require(
            all(r["status"] == "READY" for r in catalog.to_dict()["items"]),
            "DEV_CATALOG_FEATURE_NOT_READY",
        )
        catalogs[mode] = catalog.to_dict()
    return {
        "config_values": values,
        "classification_snapshot": classification,
        "mathematical_request": request.to_dict(),
        "feature_input": feature,
        "catalogs": catalogs,
        "original_bindings": {
            "canonical_input_hash": parent_canonical.input_hash,
            "classification_snapshot_id": classification_snapshot["classification_snapshot_id"],
            "classification_content_hash": classification_snapshot["content_hash"],
            "history_source_hash": digest(history_source),
        },
        "psi_calculated": False,
        "forecast_calculated": False,
        "database_write": False,
        "runtime_claimed": False,
    }


def _feature_input(raw, classification, history, reference_build):
    hash_value(reference_build)
    data = raw["recommendation"]["canonical_input"]
    context = data["context"]
    ids = set(data["development_universe"]["included_item_ids"])
    parents = {r["item_id"]: r for r in classification["items"]}
    common = {
        k: history[k]
        for k in (
            "revision",
            "scope",
            "decision_yyyyww",
            "evidence_kind",
            "available_at",
            "generator",
        )
    }
    lead_rows = [
        r
        for r in data["snapshots"]["policies"]["rows"]
        if r["effective_from"] <= context["plan_start_date"] < r["effective_to"]
    ]
    return {
        "contract_version": "1.0.0",
        "context": {
            **{
                k: context[k]
                for k in (
                    "company_cd",
                    "subs_cd",
                    "site_cd",
                    "master_as_of_date",
                    "master_snapshot_revision",
                )
            },
            "decision_yyyyww": context["plan_yyyyww"],
            "input_cutoff": context["inventory_cutoff_at"],
            "calendar_snapshot_id": data["snapshots"]["calendar"]["snapshot_id"],
            "base_configuration_hash": classification["config_hash"],
            "classification_snapshot_id": classification["classification_snapshot_id"],
            "classification_content_hash": classification["content_hash"],
            "reference_calculator_build_hash": reference_build,
        },
        "universe": [{"item_id": i, "uom": "EA"} for i in sorted(ids)],
        "calendar": [
            {k: r[k] for k in ("yyyyww", "start_date")} for r in raw["policy_input"]["calendar"]
        ],
        "abc_result": SegmentationResult(
            "ABC_XYZ",
            "1.0.0",
            classification["source_content_hash"],
            classification["config_hash"],
            tuple(
                SearchGroupAssignment(
                    i, parents[i]["segment_key"], "ASSIGNED", None, parents[i]["segment_key"]
                )
                for i in sorted(ids)
            ),
        ).to_dict(),
        "item_exceptions": [],
        "sources": {
            "history": history,
            "lead_time": {
                **common,
                "source_id": "DEV-PINNED-PHYSICAL-LT",
                "time_uom": "DAY",
                "rows": [
                    {"item_id": r["item_id"], "lead_time_days": r["lead_time_days"]}
                    for r in lead_rows
                ],
            },
            "unit_cost": {
                **common,
                "source_id": "DEV-SYNTHETIC-UNIT-PRICE-1-USD",
                "price_uom": "EA",
                "rows": [
                    {
                        "item_id": i,
                        "unit_cost": "1",
                        "currency": "USD",
                        "price_as_of": context["master_as_of_date"],
                    }
                    for i in sorted(ids)
                ],
            },
        },
    }

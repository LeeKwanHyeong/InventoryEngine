"""Bind a one-item execution to the unmodified parent and complete verified Catalog.

This is an explicit local development projection, never a new classification or an
approval. In particular, the parent's hash must not be attached to a trimmed copy.
"""

import hashlib
import json
import uuid

from dsio_inventory_engine.classify_inventory.application import SNAPSHOT_NAMESPACE, config_hash
from dsio_inventory_engine.classify_inventory.candidate_catalog import (
    CandidateCatalogProducer,
    OfflineCandidateCatalog,
    VerifiedCandidateCatalog,
)
from dsio_inventory_engine.inventory_contracts.effective_policy_v2 import (
    EFFECTIVE_POLICY_V2_FIELDS,
    effective_policy_content_hash,
    validate_effective_item_policy_v2,
)
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.policy_search import normalize_policy_search
from dsio_inventory_engine.inventory_contracts.replenishment import RecommendationRequest
from dsio_inventory_engine.inventory_contracts.values import digest, hash_value, require
from dsio_inventory_engine.recommend_replenishment.application.mathematical import (
    prepare_mathematical_strategy,
)
from dsio_inventory_engine.recommend_replenishment.application.policy_search import DESCRIPTOR
from .contracts import binding, clone, input_errors, number, sealed
from .source_binding import validate_development_projection


def _parent(document, expected_hash, values, context):
    parent = clone(document)
    hash_value(expected_hash)
    unsigned = {
        k: v for k, v in parent.items() if k not in {"content_hash", "classification_snapshot_id"}
    }
    actual = hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    require(actual == expected_hash == parent["content_hash"], "ITEM_PARENT_HASH_MISMATCH")
    require(
        parent["classification_snapshot_id"] == str(uuid.uuid5(SNAPSHOT_NAMESPACE, actual)),
        "ITEM_PARENT_ID_MISMATCH",
    )
    require(
        parent["config_hash"] == config_hash(values)
        and parent["config_revision_id"] == context["configuration_revision"],
        "ITEM_PARENT_CONFIG_MISMATCH",
    )
    require(
        all(parent["scope"][k] == context[k] for k in ("company_cd", "subs_cd", "site_cd"))
        and parent["window_end_exclusive"] == context["plan_start_date"]
        and parent["as_of_yyyyww"] == context["plan_yyyyww"],
        "ITEM_PARENT_SCOPE_OR_W0_MISMATCH",
    )
    rows = parent["items"]
    require(
        type(rows) is list and rows and len({r["item_id"] for r in rows}) == len(rows),
        "ITEM_PARENT_UNIVERSE_INVALID",
    )
    require(parent["effective_policy_contract_version"] == "2.0.0", "ITEM_PARENT_V2_REQUIRED")
    for row in rows:
        validate_effective_item_policy_v2(row)
    require(
        parent["effective_policy_content_hash"] == effective_policy_content_hash(rows),
        "ITEM_PARENT_POLICY_HASH_MISMATCH",
    )
    return parent


def _catalog_inputs(catalog, parent, raw, prepared, source, search):
    """Validate the full parent universe, then only the selected item's physical input.

    Unlike the old whole-run Catalog adapter, this does not require a 65-week
    multi-item PSI execution. It never fabricates a smaller parent Snapshot.
    """
    feature = catalog["legacy_result"]["feature_snapshot"]["input"]
    fc, context = feature["context"], prepared["context"]
    canonical = raw["recommendation"]["canonical_input"]
    require(
        all(
            fc[k] == context[k]
            for k in (
                "company_cd",
                "subs_cd",
                "site_cd",
                "master_as_of_date",
                "master_snapshot_revision",
            )
        )
        and fc["decision_yyyyww"] == context["plan_yyyyww"]
        and fc["input_cutoff"] == context["inventory_cutoff_at"]
        and fc["calendar_snapshot_id"] == canonical["snapshots"]["calendar"]["snapshot_id"],
        "ITEM_CATALOG_CONTEXT_MISMATCH",
    )
    require(
        fc["base_configuration_hash"] == parent["config_hash"]
        and fc["classification_snapshot_id"] == parent["classification_snapshot_id"]
        and fc["classification_content_hash"] == parent["content_hash"],
        "ITEM_CATALOG_PARENT_MISMATCH",
    )
    parents = {r["item_id"]: r for r in parent["items"]}
    universe = feature["universe"]
    require(
        {r["item_id"] for r in universe} == set(parents)
        and len(universe) == len(parents)
        and all(r["uom"] == "EA" for r in universe)
        and {r["item_id"] for r in catalog["items"]} == set(parents),
        "ITEM_CATALOG_PARENT_UNIVERSE_MISMATCH",
    )
    abc = feature["abc_result"]
    require(
        abc["configuration_hash"] == parent["config_hash"]
        and abc["source_content_hash"] == parent["source_content_hash"]
        and {r["item_id"] for r in abc["assignments"]} == set(parents),
        "ITEM_CATALOG_ABC_BINDING_MISMATCH",
    )
    for row in abc["assignments"]:
        original = parents[row["item_id"]]
        require(
            row["abc_xyz_class"] == original["segment_key"]
            and (row["status"] == "ASSIGNED") == (original["classification_status"] == "CLASSIFIED")
            and row["reason_code"] == original["unclassified_reason_code"],
            "ITEM_CATALOG_ABC_BINDING_MISMATCH",
        )
    item = prepared["master"][0]["item_id"]
    math = raw["policy_input"]
    require(
        math["profile"]["lookback_weeks"] == 26
        and math["profile"]["stddev_ddof"] == 0
        and math["profile"]["allow_legacy_fallback"] is False,
        "ITEM_HISTORY_PROFILE_MISMATCH",
    )
    calendar = sorted(math["calendar"], key=lambda r: r["start_date"])
    require(
        feature["calendar"] == [{k: r[k] for k in ("yyyyww", "start_date")} for r in calendar],
        "ITEM_HISTORY_CALENDAR_MISMATCH",
    )
    expected = [
        (b["yyyyww"], number(h["gross_demand_qty"]))
        for b, h in zip(calendar, source["uncensored_history"][-26:], strict=True)
    ]
    math_rows = sorted((r["yyyyww"], number(r["demand_qty"])) for r in math["history"])
    history = feature["sources"]["history"]
    feature_rows = (
        []
        if history is None
        else sorted(
            (r["yyyyww"], number(r["demand_qty"])) for r in history["rows"] if r["item_id"] == item
        )
    )
    require(expected == math_rows == feature_rows, "ITEM_HISTORY_SOURCE_MISMATCH")
    ready = next(
        r for r in catalog["legacy_result"]["feature_snapshot"]["features"] if r["item_id"] == item
    )
    require(
        ready["physical_lead_time_days"] == source["physical_constraints"]["lead_time_days"],
        "ITEM_CATALOG_PHYSICAL_LEAD_TIME_MISMATCH",
    )
    require(
        search["enabled"]
        and not search["item_overrides"]
        and search["lookback_weeks"] == 26
        and number(search["minimum_fill_rate"]) == number(catalog["minimum_fill_rate"])
        and catalog["max_candidates_per_item"] <= search["max_candidates_per_item"],
        "ITEM_CATALOG_SEARCH_CONFIG_MISMATCH",
    )


class PrepareItemCatalogUseCase:
    def __init__(self, deployment: DeploymentScope, producer: CandidateCatalogProducer):
        self.deployment, self.producer = deployment, producer

    @input_errors
    def execute(
        self,
        request: MathematicalPolicyRequest,
        *,
        parent_classification: dict,
        expected_parent_hash: str,
        catalog: OfflineCandidateCatalog,
        expected_catalog_hash: str,
        config_values: dict,
        source: dict,
        source_request: dict,
        expected_source_hash: str,
    ) -> dict:
        require(self.deployment.environment == "DEVELOPMENT", "ITEM_DEVELOPMENT_ONLY")
        normalized = MathematicalPolicyRequest.from_dict(request.to_dict())
        raw = normalized.to_dict()
        rec = RecommendationRequest.from_dict(raw["recommendation"])
        execution = raw["recommendation"]["execution"]
        require(
            execution["contract_version"] == "2.0.0"
            and execution["execution_mode"] == "LOCAL_SHADOW"
            and execution["execution_purpose"] == "SHADOW",
            "ITEM_LOCAL_V2_SHADOW_REQUIRED",
        )
        source = validate_development_projection(
            source, source_request, rec, expected_source_hash=expected_source_hash
        )
        values = clone(config_values)
        context = raw["recommendation"]["canonical_input"]["context"]
        parent = _parent(parent_classification, expected_parent_hash, values, context)
        verified = VerifiedCandidateCatalog.bind(
            catalog, self.producer, expected_catalog_hash=expected_catalog_hash
        )
        complete_catalog = verified.to_dict()
        item = source["scope"]["item_id"]
        require(item in {r["item_id"] for r in parent["items"]}, "ITEM_PARENT_ITEM_MISSING")
        assignment = next(r for r in parent["items"] if r["item_id"] == item)
        projection = {"item_id": item, **{k: assignment[k] for k in EFFECTIVE_POLICY_V2_FIELDS}}
        require(
            execution["effective_item_policies"] == [projection]
            and execution["effective_policy_binding"]["classification_snapshot_id"]
            == parent["classification_snapshot_id"]
            and execution["effective_policy_binding"]["classification_config_hash"]
            == parent["config_hash"],
            "ITEM_PARENT_POLICY_PROJECTION_MISMATCH",
        )
        # Production math input/Cut-off/admission validates the one-item Canonical view.
        _, prepared, report, _ = prepare_mathematical_strategy(normalized, self.deployment)
        require(len(prepared["calendar"]) == 65, "ITEM_CONTINUOUS_65_WEEKS_REQUIRED")
        _catalog_inputs(
            complete_catalog,
            parent,
            raw,
            prepared,
            source,
            normalize_policy_search(values["policy_search"]),
        )
        candidates = verified.candidates(item, assignment)
        require(
            len(candidates) == verified.max_candidates_per_item, "ITEM_COMPLETE_CANDIDATES_REQUIRED"
        )
        require(65 * 66 // 2 * len(candidates) <= 200_000, "POLICY_SEARCH_WORKLOAD_LIMIT")
        stat = report["history_statistics"][0]
        require(
            not stat["missing_weeks"] and number(stat["mean_weekly_demand"]) > 0,
            "ITEM_SEARCH_HISTORY_INELIGIBLE",
        )
        require(
            not raw["policy_input"]["adjustments"]
            and not any(
                r["effective_policy_source"] == "APPROVED_OVERRIDE"
                for r in report["policy_evidence"]
            ),
            "ITEM_SEARCH_ADJUSTMENT_PINNED",
        )
        item_projection = sealed(
            "inventory-robustness-item-projection",
            {
                "parent_classification_snapshot_id": parent["classification_snapshot_id"],
                "parent_classification_hash": parent["content_hash"],
                "parent_item_ids": [r["item_id"] for r in parent["items"]],
                "item_id": item,
                "original_item": clone(assignment),
                "effective_item_policy": clone(projection),
            },
        )
        candidate_rec = rec.to_dict()
        candidate_rec["execution"].update(
            strategy=dict(DESCRIPTOR), strategy_input_binding=verified.input_binding
        )
        candidate_rec = RecommendationRequest.from_dict(candidate_rec).to_dict()
        costs = complete_catalog["legacy_result"]["feature_snapshot"]["input"]["sources"][
            "unit_cost"
        ]
        price_rows = [] if costs is None else [r for r in costs["rows"] if r["item_id"] == item]
        price = (
            None
            if not price_rows
            else {
                "unit_price": price_rows[0]["unit_cost"],
                "currency": price_rows[0]["currency"],
                "price_as_of": price_rows[0]["price_as_of"],
                "source_hash": digest(costs),
            }
        )
        evaluator_binding = binding(
            {
                **source["scope"],
                "w0": source["w0"],
                "immutable_input_hash": digest(
                    {
                        "planning_request_hash": rec.input_hash,
                        "mathematical_input_hash": raw["policy_input"]["content_hash"],
                        "source_hash": source["content_hash"],
                        "parent_hash": parent["content_hash"],
                    }
                ),
                "candidate_mode": complete_catalog["mode"],
                "catalog_hash": complete_catalog["content_hash"],
                "unit_price": price,
            }
        )
        return sealed(
            "inventory-robustness-item-assembly",
            {
                "mathematical_request": raw,
                "parent_classification": parent,
                "candidate_catalog": complete_catalog,
                "config_values": values,
                "development_source": source,
                "source_request": clone(source_request),
                "item_projection": item_projection,
                "complete_candidates": candidates,
                "candidate_recommendation": candidate_rec,
                "history_statistics": stat,
                "evaluation_binding": evaluator_binding,
                "observation_work_bound": 65 * 66 // 2 * len(candidates),
                "official_paths_generated": False,
                "durable_artifact_persisted": False,
            },
        )

    @input_errors
    def verify(self, assembly: dict, *, expected_assembly_hash: str) -> dict:
        value = clone(assembly)
        hash_value(expected_assembly_hash)
        require(
            value.get("content_hash") == expected_assembly_hash
            and digest({k: v for k, v in value.items() if k != "content_hash"})
            == expected_assembly_hash,
            "ITEM_ASSEMBLY_HASH_MISMATCH",
        )
        rebuilt = self.execute(
            MathematicalPolicyRequest.from_dict(value["mathematical_request"]),
            parent_classification=value["parent_classification"],
            expected_parent_hash=value["item_projection"]["parent_classification_hash"],
            catalog=OfflineCandidateCatalog.from_dict(value["candidate_catalog"]),
            expected_catalog_hash=value["evaluation_binding"]["catalog_hash"],
            config_values=value["config_values"],
            source=value["development_source"],
            source_request=value["source_request"],
            expected_source_hash=value["development_source"]["content_hash"],
        )
        require(value == rebuilt, "ITEM_ASSEMBLY_REPLAY_MISMATCH")
        return rebuilt

"""Explicit development candidate lists; never reinterpret neutral groups as ABC grades."""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from itertools import product
import json
from types import MappingProxyType
from typing import Mapping

from dsio_inventory_engine.classify_inventory.legacy_rules import SealedLegacyDocument
from dsio_inventory_engine.classify_inventory.legacy_style import (
    LegacyFactorSegmentation,
    LegacySegmentationResult,
)
from dsio_inventory_engine.inventory_contracts.policy_search import SEGMENTS
from dsio_inventory_engine.inventory_contracts.segmentation import CandidateRange
from dsio_inventory_engine.inventory_contracts.values import canonical_json, digest, require

MODES = ("ABC_XYZ_SHARED_BUDGET", "LEGACY_STYLE", "WIDE_REFERENCE")
PARAMETERS = ("policy_kind", "rmos", "emos", "target_service_level", "review_cycle_weeks")
PROFILE_ORDER = (
    "policy_kinds",
    "rmos_values",
    "emos_values",
    "service_level_values",
    "review_cycle_weeks",
)


class OfflineCandidateCatalog(SealedLegacyDocument):
    __slots__ = ()


class CandidateCatalogProducer:
    def __init__(self, classifier: LegacyFactorSegmentation):
        self.classifier = classifier

    def produce(
        self, result: LegacySegmentationResult, *, mode: str, expected_result_hash: str
    ) -> OfflineCandidateCatalog:
        require(mode in MODES, "CATALOG_MODE_INVALID")
        require(result.content_hash == expected_result_hash, "CATALOG_SOURCE_HASH_MISMATCH")
        value = self.classifier.verify(result).to_dict()
        rules = self.classifier.rules.to_dict()
        # Exact union of the three rectangles, not a Cartesian product of their columns.
        profiles = {
            name: CandidateRange.from_dict(spec).to_dict()
            for name, spec in rules["candidate_profiles"].items()
        }
        expanded = {
            name: set(product(*(spec[key] for key in PROFILE_ORDER)))
            for name, spec in profiles.items()
        }
        union = set().union(*expanded.values())
        require(
            all(len(rows) == 32 for rows in expanded.values()) and len(union) == 88,
            "CATALOG_CANDIDATE_COUNT_MISMATCH",
        )
        items = []
        for item in value["items"]:
            reason = item["primary_reason"]
            if reason is None and (
                item["abc_classification_status"] != "ASSIGNED"
                or item["abc_xyz_class"] not in SEGMENTS
            ):
                reason = "POLICY_SEARCH_ITEM_UNCLASSIFIED"
            profile = item["profile_id"]
            if (
                mode == "ABC_XYZ_SHARED_BUDGET"
                and reason is None
                and item["range_source"] != "ITEM_OVERRIDE"
            ):
                profile = rules["comparison"]["abc_profile_by_xyz"][item["abc_xyz_class"][1]]
            group = (
                "LS1:WIDE_REFERENCE"
                if mode == "WIDE_REFERENCE"
                else "ABC:" + item["abc_xyz_class"]
                if mode == "ABC_XYZ_SHARED_BUDGET" and reason is None
                else item["search_group_id"]
            )
            specs = union if mode == "WIDE_REFERENCE" else expanded.get(profile, set())
            candidates = []
            if reason is None:
                for params in sorted(specs):
                    semantic = dict(zip(PARAMETERS, params, strict=True))
                    body = {
                        "item_id": item["item_id"],
                        "search_group_id": group,
                        **semantic,
                        "semantic_policy_hash": digest(semantic),
                        "range_source": "EXACT_SHARED_UNION"
                        if mode == "WIDE_REFERENCE"
                        else item["range_source"],
                        "override_reason": None
                        if mode == "WIDE_REFERENCE" or item["item_exception"] is None
                        else item["item_exception"]["reason"],
                    }
                    candidates.append({**body, "candidate_id": "OCCP-" + digest(body)})
            items.append(
                {
                    "item_id": item["item_id"],
                    "abc_xyz_class": item["abc_xyz_class"],
                    "factor_group_id": item["factor_group_id"],
                    "profile_id": None
                    if reason is not None or mode == "WIDE_REFERENCE"
                    else profile,
                    "status": "EXCLUDED" if reason else "READY",
                    "reason_code": reason,
                    "candidates": sorted(candidates, key=lambda row: row["candidate_id"]),
                }
            )
        return OfflineCandidateCatalog.seal(
            {
                "contract_id": "inventory-offline-candidate-catalog",
                "contract_version": "1.0.0",
                "mode": mode,
                "rules_profile_hash": value["rules_profile_hash"],
                "legacy_result": value,
                "legacy_result_hash": result.content_hash,
                "feature_snapshot_id": value["feature_snapshot"]["feature_snapshot_id"],
                "feature_content_hash": value["feature_snapshot"]["content_hash"],
                "max_candidates_per_item": 88 if mode == "WIDE_REFERENCE" else 32,
                "minimum_fill_rate": rules["comparison"]["minimum_fill_rate"],
                "items": items,
                "development_only": True,
                "operational_approved": False,
            }
        )

    def verify(
        self, catalog: OfflineCandidateCatalog, *, expected_catalog_hash: str
    ) -> OfflineCandidateCatalog:
        value = catalog.to_dict()
        require(catalog.content_hash == expected_catalog_hash, "CATALOG_HASH_MISMATCH")
        require(
            value.get("contract_id") == "inventory-offline-candidate-catalog"
            and value.get("contract_version") == "1.0.0",
            "CATALOG_CONTRACT_MISMATCH",
        )
        rebuilt = self.produce(
            LegacySegmentationResult.from_dict(value["legacy_result"]),
            mode=value["mode"],
            expected_result_hash=value["legacy_result_hash"],
        )
        require(rebuilt.content_hash == catalog.content_hash, "CATALOG_REPLAY_MISMATCH")
        return rebuilt


@dataclass(frozen=True, slots=True)
class VerifiedCandidateCatalog:
    """One verified run-scoped catalog, indexed once. Not a persisted Run authority."""

    _json: str
    _items: Mapping[str, str] = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        body = OfflineCandidateCatalog.from_dict(json.loads(self._json)).to_dict()
        object.__setattr__(
            self,
            "_items",
            MappingProxyType({row["item_id"]: canonical_json(row) for row in body["items"]}),
        )

    @classmethod
    def bind(
        cls,
        catalog: OfflineCandidateCatalog,
        producer: CandidateCatalogProducer,
        *,
        expected_catalog_hash: str,
    ) -> "VerifiedCandidateCatalog":
        checked = producer.verify(catalog, expected_catalog_hash=expected_catalog_hash)
        return cls(canonical_json(checked.to_dict()))

    def to_dict(self) -> dict:
        return json.loads(self._json)

    @property
    def input_binding(self) -> dict:
        value = self.to_dict()
        return {key: value[key] for key in ("contract_id", "contract_version", "content_hash")} | {
            "snapshot_id": "OCC-" + value["content_hash"]
        }

    @property
    def max_candidates_per_item(self) -> int:
        return self.to_dict()["max_candidates_per_item"]

    def candidates(self, item_id: str, assignment: dict) -> list[dict]:
        row = json.loads(self._items[item_id])
        require(
            assignment.get("classification_status") == "CLASSIFIED"
            and assignment.get("segment_key") in SEGMENTS,
            "POLICY_SEARCH_ITEM_UNCLASSIFIED",
        )
        require(row["status"] == "READY", row["reason_code"] or "CATALOG_ITEM_EXCLUDED")
        return row["candidates"]

    def validate_inputs(
        self, raw: dict, prepared: dict, classification: dict, search: dict
    ) -> None:
        catalog = self.to_dict()
        request = catalog["legacy_result"]["feature_snapshot"]["input"]
        feature_context, context = request["context"], prepared["context"]
        require(
            all(
                feature_context[key] == context[key]
                for key in (
                    "company_cd",
                    "subs_cd",
                    "site_cd",
                    "master_as_of_date",
                    "master_snapshot_revision",
                )
            )
            and feature_context["decision_yyyyww"] == prepared["calendar"][0]["yyyyww"]
            and feature_context["input_cutoff"] == context["inventory_cutoff_at"],
            "CATALOG_CONTEXT_BINDING_MISMATCH",
        )
        require(
            feature_context["base_configuration_hash"] == classification["config_hash"]
            and feature_context["classification_snapshot_id"]
            == classification["classification_snapshot_id"]
            and feature_context["classification_content_hash"] == classification["content_hash"],
            "CATALOG_CLASSIFICATION_BINDING_MISMATCH",
        )
        canonical = raw["recommendation"]["canonical_input"]
        require(
            feature_context["calendar_snapshot_id"]
            == canonical["snapshots"]["calendar"]["snapshot_id"],
            "CATALOG_CALENDAR_BINDING_MISMATCH",
        )
        universe = {row["item_id"]: row["uom"] for row in prepared["master"]}
        require(
            {row["item_id"]: row["uom"] for row in request["universe"]} == universe
            and set(self._items) == set(universe),
            "CATALOG_UNIVERSE_MISMATCH",
        )
        original = {row["item_id"]: row for row in classification["items"]}
        for row in request["abc_result"]["assignments"]:
            source = original[row["item_id"]]
            require(
                row["abc_xyz_class"] == source.get("segment_key")
                and (row["status"] == "ASSIGNED")
                == (source["classification_status"] == "CLASSIFIED")
                and row["reason_code"] == source.get("unclassified_reason_code"),
                "CATALOG_ABC_RESULT_MISMATCH",
            )
        math = raw["policy_input"]
        require(math["profile"]["lookback_weeks"] == 26, "CATALOG_HISTORY_LOOKBACK_MISMATCH")
        require(
            request["calendar"]
            == sorted(
                ({"yyyyww": r["yyyyww"], "start_date": r["start_date"]} for r in math["calendar"]),
                key=lambda r: r["start_date"],
            ),
            "CATALOG_HISTORY_CALENDAR_MISMATCH",
        )
        history = request["sources"]["history"]
        if history is not None:
            feature_rows = sorted(
                (r["item_id"], r["yyyyww"], Decimal(r["demand_qty"]))
                for r in history["rows"]
                if isinstance(r["demand_qty"], str) and _valid_decimal(r["demand_qty"])
            )
            math_rows = sorted(
                (r["item_id"], r["yyyyww"], Decimal(r["demand_qty"])) for r in math["history"]
            )
            # Missing/invalid feature rows remain exclusions, but no valid row may be substituted.
            math_by_key = {(item, week): qty for item, week, qty in math_rows}
            require(
                all(math_by_key.get((item, week)) == qty for item, week, qty in feature_rows),
                "CATALOG_HISTORY_BINDING_MISMATCH",
            )
            ready = {row["item_id"] for row in catalog["items"] if row["status"] == "READY"}
            require(
                [r for r in feature_rows if r[0] in ready]
                == [r for r in math_rows if r[0] in ready],
                "CATALOG_HISTORY_BINDING_MISMATCH",
            )
        for feature in catalog["legacy_result"]["feature_snapshot"]["features"]:
            if feature["status"] != "READY":
                continue
            policy = next(
                r
                for r in prepared["policies"]
                if r["item_id"] == feature["item_id"]
                and r["effective_from"] <= context["plan_start_date"] < r["effective_to"]
            )
            require(
                policy["lead_time_days"] == feature["physical_lead_time_days"],
                "CATALOG_LEAD_TIME_BINDING_MISMATCH",
            )
        require(not search["item_overrides"], "CATALOG_CONFIG_RANGE_OVERRIDE_UNSUPPORTED")
        require(
            Decimal(search["minimum_fill_rate"]) == Decimal(catalog["minimum_fill_rate"]),
            "CATALOG_SERVICE_RULE_MISMATCH",
        )
        require(
            catalog["max_candidates_per_item"] <= search["max_candidates_per_item"],
            "POLICY_SEARCH_CANDIDATE_LIMIT",
        )


def _valid_decimal(value: str) -> bool:
    try:
        number = Decimal(value)
        return number.is_finite() and 0 <= number <= Decimal("1000000000000")
    except (ValueError, InvalidOperation):
        return False

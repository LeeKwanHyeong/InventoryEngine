"""Neutral in-process bridges only; no Config, Run, PSI, FSN or publication changes."""

from typing import Any, Mapping

from dsio_inventory_engine.classify_inventory.legacy_rules import LegacyStyleRules
from dsio_inventory_engine.classify_inventory.legacy_style import (
    LegacyFactorSegmentation,
    LegacySegmentationResult,
)
from dsio_inventory_engine.inventory_contracts.segmentation import (
    CandidateRange,
    CandidateRangeBinding,
    SearchGroupAssignment,
    SegmentationResult,
)
from dsio_inventory_engine.inventory_contracts.values import require


class LegacyStyleSegmentationAdapter:
    def __init__(self, classifier: LegacyFactorSegmentation):
        self.classifier = classifier

    def segment(
        self, request: LegacySegmentationResult, *, configuration_hash: str
    ) -> SegmentationResult:
        value = self.classifier.verify(request).to_dict()
        require(configuration_hash == value["configuration_hash"], "LS_SOURCE_BINDING_MISMATCH")
        return SegmentationResult(
            method_id=value["method_id"],
            implementation_version=value["implementation_version"],
            source_content_hash=value["feature_snapshot"]["content_hash"],
            configuration_hash=configuration_hash,
            assignments=tuple(
                SearchGroupAssignment(
                    item_id=row["item_id"],
                    search_group_id=row["search_group_id"],
                    status=row["status"],
                    reason_code=row["primary_reason"],
                    abc_xyz_class=row["abc_xyz_class"],
                )
                for row in value["items"]
            ),
        )


class LegacyStyleCandidateRangeAdapter:
    def __init__(self, classifier: LegacyFactorSegmentation, rules: LegacyStyleRules):
        self.classifier = classifier
        require(classifier.rules.content_hash == rules.content_hash, "LS_RULES_HASH_MISMATCH")
        self.rules = rules

    def resolve(
        self, search_config: Mapping[str, Any], *, assignment: SearchGroupAssignment
    ) -> CandidateRangeBinding:
        result = self.classifier.verify(LegacySegmentationResult.from_dict(dict(search_config)))
        body = result.to_dict()
        neutral = LegacyStyleSegmentationAdapter(self.classifier).segment(
            result, configuration_hash=body["configuration_hash"]
        )
        require(
            assignment in neutral.assignments and assignment.status == "ASSIGNED",
            "LS_SOURCE_BINDING_MISMATCH",
        )
        row = next(r for r in body["items"] if r["item_id"] == assignment.item_id)
        return CandidateRangeBinding(
            assignment=assignment,
            candidate_range=CandidateRange.from_dict(
                self.rules.to_dict()["candidate_profiles"][row["profile_id"]]
            ),
            search_config_hash=body["configuration_hash"],
            range_source=row["range_source"],
            override_reason=None
            if row["item_exception"] is None
            else row["item_exception"]["reason"],
        )

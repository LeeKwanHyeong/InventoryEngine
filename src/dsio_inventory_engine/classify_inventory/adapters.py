"""ABC-XYZ bridges to neutral search groups; no runtime strategy selection or approval."""

from dsio_inventory_engine.classify_inventory.abc_xyz import AbcXyzSegmentation, SEGMENTS
from dsio_inventory_engine.classify_inventory.models import ClassificationResult
from dsio_inventory_engine.inventory_contracts.policy_search import (
    PROFILE_FIELDS,
    expand_candidate_profile,
    policy_search_hash,
    resolve_candidate_profile,
)
from dsio_inventory_engine.inventory_contracts.segmentation import (
    CandidateRange,
    CandidateRangeBinding,
    SearchGroupAssignment,
    SegmentationResult,
)
from dsio_inventory_engine.inventory_contracts.values import require


class AbcXyzSegmentationAdapter:
    """Translate an existing pure classification result without assigning fake grades."""

    def segment(
        self, request: ClassificationResult, *, configuration_hash: str
    ) -> SegmentationResult:
        require(
            request.eligible_sku_count == len(request.items)
            and request.classified_sku_count + request.unclassified_sku_count
            == request.eligible_sku_count,
            "SEGMENTATION_ITEM_COVERAGE_MISMATCH",
        )
        assignments = []
        for item in request.items:
            classified = item["classification_status"] == "CLASSIFIED"
            require(
                item["classification_status"] in {"CLASSIFIED", "UNCLASSIFIED"},
                "SEGMENTATION_STATUS_INVALID",
            )
            segment = item["segment_key"]
            require(
                (
                    classified
                    and segment in SEGMENTS
                    and segment == f"{item['abc_class']}{item['xyz_class']}"
                    and item["unclassified_reason_code"] is None
                )
                or (not classified and segment is None),
                "SEGMENTATION_ABC_XYZ_RESULT_MISMATCH",
            )
            assignments.append(
                SearchGroupAssignment(
                    item_id=item["item_id"],
                    search_group_id=segment,
                    status="ASSIGNED" if classified else "UNCLASSIFIED",
                    reason_code=item["unclassified_reason_code"],
                    abc_xyz_class=segment,
                )
            )
        require(
            sum(row.status == "ASSIGNED" for row in assignments) == request.classified_sku_count,
            "SEGMENTATION_ITEM_COVERAGE_MISMATCH",
        )
        return SegmentationResult(
            method_id=AbcXyzSegmentation.method_id,
            implementation_version=AbcXyzSegmentation.implementation_version,
            source_content_hash=request.source_content_hash,
            configuration_hash=configuration_hash,
            assignments=tuple(assignments),
        )


class AbcXyzCandidateRangeAdapter:
    """Keep existing ABC gating, override precedence and V1 candidate hashes intact."""

    def resolve(self, search_config, *, assignment: SearchGroupAssignment) -> CandidateRangeBinding:
        profile, source, reason = resolve_candidate_profile(
            search_config,
            item_id=assignment.item_id,
            segment_key=assignment.search_group_id,
        )
        require(
            assignment.status == "ASSIGNED"
            and assignment.abc_xyz_class == assignment.search_group_id,
            "SEGMENTATION_ABC_XYZ_ASSIGNMENT_REQUIRED",
        )
        return CandidateRangeBinding(
            assignment=assignment,
            candidate_range=CandidateRange.from_dict(
                {key: profile[key] for key in PROFILE_FIELDS},
                max_candidates=search_config["max_candidates_per_item"],
            ),
            search_config_hash=policy_search_hash(search_config),
            range_source=source,
            override_reason=reason,
        )

    def candidates(self, search_config, *, item_id, segment_key):
        assignment = SearchGroupAssignment(
            item_id=item_id,
            search_group_id=segment_key,
            status="ASSIGNED" if segment_key is not None else "UNCLASSIFIED",
            reason_code=None if segment_key is not None else "POLICY_SEARCH_ITEM_UNCLASSIFIED",
            abc_xyz_class=segment_key if segment_key in SEGMENTS else None,
        )
        binding = self.resolve(search_config, assignment=assignment)
        return expand_candidate_profile(
            binding.candidate_range.to_dict(),
            item_id=item_id,
            segment_key=segment_key,
            range_source=binding.range_source,
            override_reason=binding.override_reason,
        )

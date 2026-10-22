"""Additive in-process segmentation/search-range contract, not an approval contract.

Search groups need not be ABC-XYZ grades. None of these objects grants eligibility,
changes an effective policy, or extends the persisted Config/Run/Candidate schemas.
"""

from dataclasses import dataclass
import re
from typing import Any, Mapping, Protocol, TypeVar

from dsio_inventory_engine.inventory_contracts.policy_search import _profile
from dsio_inventory_engine.inventory_contracts.values import (
    digest,
    hash_value,
    item_identifier,
    require,
)

SEGMENTATION_CONTRACT_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class SearchGroupAssignment:
    item_id: str
    search_group_id: str | None
    status: str
    reason_code: str | None
    abc_xyz_class: str | None = None

    def __post_init__(self):
        item_identifier(self.item_id)
        require(self.status in {"ASSIGNED", "UNCLASSIFIED"}, "SEGMENTATION_STATUS_INVALID")
        if self.abc_xyz_class is not None:
            require(
                self.abc_xyz_class in ("AX", "AY", "AZ", "BX", "BY", "BZ", "CX", "CY", "CZ"),
                "SEGMENTATION_ABC_XYZ_INVALID",
            )
        if self.status == "ASSIGNED":
            item_identifier(self.search_group_id)
            require(self.reason_code is None, "SEGMENTATION_ASSIGNED_REASON_UNEXPECTED")
        else:
            require(
                self.search_group_id is None
                and isinstance(self.reason_code, str)
                and bool(self.reason_code.strip()),
                "SEGMENTATION_UNCLASSIFIED_REASON_REQUIRED",
            )

    def to_dict(self):
        return {
            "item_id": self.item_id,
            "search_group_id": self.search_group_id,
            "status": self.status,
            "reason_code": self.reason_code,
            "abc_xyz_class": self.abc_xyz_class,
        }


@dataclass(frozen=True, slots=True)
class SegmentationResult:
    method_id: str
    implementation_version: str
    source_content_hash: str
    configuration_hash: str
    assignments: tuple[SearchGroupAssignment, ...]

    def __post_init__(self):
        item_identifier(self.method_id)
        require(
            isinstance(self.implementation_version, str)
            and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", self.implementation_version),
            "SEGMENTATION_IMPLEMENTATION_VERSION_INVALID",
        )
        hash_value(self.source_content_hash)
        hash_value(self.configuration_hash)
        rows = tuple(self.assignments)
        require(
            bool(rows) and all(isinstance(row, SearchGroupAssignment) for row in rows),
            "SEGMENTATION_ASSIGNMENTS_REQUIRED",
        )
        require(len({row.item_id for row in rows}) == len(rows), "SEGMENTATION_DUPLICATE_ITEM")
        object.__setattr__(self, "assignments", tuple(sorted(rows, key=lambda row: row.item_id)))

    def to_dict(self):
        return {
            "contract_version": SEGMENTATION_CONTRACT_VERSION,
            "method_id": self.method_id,
            "implementation_version": self.implementation_version,
            "source_content_hash": self.source_content_hash,
            "configuration_hash": self.configuration_hash,
            "assignments": [row.to_dict() for row in self.assignments],
        }

    @property
    def content_hash(self):
        return digest(self.to_dict())


@dataclass(frozen=True, slots=True)
class CandidateRange:
    policy_kinds: tuple[str, ...]
    rmos_values: tuple[str, ...]
    emos_values: tuple[str, ...]
    service_level_values: tuple[str, ...]
    review_cycle_weeks: tuple[int, ...]

    def __post_init__(self):
        normalized = _profile(self.to_dict(), set(), 256)
        for key, value in normalized.items():
            object.__setattr__(self, key, tuple(value))

    @classmethod
    def from_dict(cls, value: dict, *, max_candidates: int = 256):
        require(
            type(max_candidates) is int and 1 <= max_candidates <= 256,
            "POLICY_SEARCH_CANDIDATE_LIMIT",
        )
        normalized = _profile(value, set(), max_candidates)
        return cls(**{key: tuple(values) for key, values in normalized.items()})

    def to_dict(self):
        return {
            key: list(getattr(self, key))
            for key in (
                "policy_kinds",
                "rmos_values",
                "emos_values",
                "service_level_values",
                "review_cycle_weeks",
            )
        }

    @property
    def content_hash(self):
        return digest(self.to_dict())


@dataclass(frozen=True, slots=True)
class CandidateRangeBinding:
    assignment: SearchGroupAssignment
    candidate_range: CandidateRange
    search_config_hash: str
    range_source: str
    override_reason: str | None = None

    def __post_init__(self):
        require(
            isinstance(self.assignment, SearchGroupAssignment)
            and self.assignment.status == "ASSIGNED"
            and isinstance(self.candidate_range, CandidateRange),
            "SEGMENTATION_CANDIDATE_ASSIGNMENT_REQUIRED",
        )
        hash_value(self.search_config_hash)
        require(
            self.range_source in {"GROUP_PROFILE", "ITEM_OVERRIDE"},
            "SEGMENTATION_RANGE_SOURCE_INVALID",
        )
        require(
            (self.range_source == "GROUP_PROFILE" and self.override_reason is None)
            or (
                self.range_source == "ITEM_OVERRIDE"
                and isinstance(self.override_reason, str)
                and 0 < len(self.override_reason.strip()) <= 500
            ),
            "SEGMENTATION_OVERRIDE_REASON_INVALID",
        )

    def to_dict(self):
        return {
            "contract_version": SEGMENTATION_CONTRACT_VERSION,
            "assignment": self.assignment.to_dict(),
            "candidate_range": self.candidate_range.to_dict(),
            "search_config_hash": self.search_config_hash,
            "range_source": self.range_source,
            "override_reason": self.override_reason,
        }

    @property
    def content_hash(self):
        return digest(self.to_dict())


RequestT = TypeVar("RequestT", contravariant=True)


class SegmentationProvider(Protocol[RequestT]):
    def segment(self, request: RequestT, *, configuration_hash: str) -> SegmentationResult: ...


class CandidateRangeProvider(Protocol):
    def resolve(
        self, search_config: Mapping[str, Any], *, assignment: SearchGroupAssignment
    ) -> CandidateRangeBinding: ...

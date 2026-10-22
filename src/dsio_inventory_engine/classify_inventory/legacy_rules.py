"""Pinned development rules. No filesystem lookup, active configuration or approval."""

from dataclasses import dataclass
import json
from typing import Any

from dsio_inventory_engine.inventory_contracts.values import canonical_json, digest, require

RULES_PROFILE_HASH = "a3121dfa885f8b4d2e0df54605789ad841661693b0f386329fe4fa60946ecbae"


@dataclass(frozen=True, slots=True)
class LegacyStyleRules:
    _profile_json: str

    @classmethod
    def from_document(cls, document: dict) -> "LegacyStyleRules":
        require(
            isinstance(document, dict)
            and set(document) == {"lifecycle_status", "profile_content_hash", "profile"},
            "LS_RULES_HASH_MISMATCH",
        )
        require(
            document["profile_content_hash"] == RULES_PROFILE_HASH
            and digest(document["profile"]) == RULES_PROFILE_HASH,
            "LS_RULES_HASH_MISMATCH",
        )
        return cls(canonical_json(document["profile"]))

    def to_dict(self) -> dict:
        profile = json.loads(self._profile_json)
        require(digest(profile) == RULES_PROFILE_HASH, "LS_RULES_HASH_MISMATCH")
        return profile

    @property
    def content_hash(self) -> str:
        self.to_dict()
        return RULES_PROFILE_HASH


@dataclass(frozen=True, slots=True)
class SealedLegacyDocument:
    """Defensive immutable JSON value; a content seal is never an operational approval."""

    _json: str

    @classmethod
    def seal(cls, body: dict) -> "SealedLegacyDocument":
        require("content_hash" not in body, "LS_SOURCE_BINDING_MISMATCH")
        return cls(canonical_json({**body, "content_hash": digest(body)}))

    @classmethod
    def from_dict(cls, value: dict) -> "SealedLegacyDocument":
        require(isinstance(value, dict), "LS_SOURCE_BINDING_MISMATCH")
        body = {key: val for key, val in value.items() if key != "content_hash"}
        require(value.get("content_hash") == digest(body), "LS_SOURCE_BINDING_MISMATCH")
        return cls(canonical_json(value))

    def to_dict(self) -> dict[str, Any]:
        value = json.loads(self._json)
        body = {key: val for key, val in value.items() if key != "content_hash"}
        require(value.get("content_hash") == digest(body), "LS_SOURCE_BINDING_MISMATCH")
        return value

    @property
    def content_hash(self) -> str:
        return self.to_dict()["content_hash"]

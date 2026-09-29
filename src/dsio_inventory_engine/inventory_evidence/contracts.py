"""Storage-neutral contracts for append-only Inventory result evidence."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from dsio_inventory_engine.inventory_contracts.values import (
    canonical_json,
    digest,
    hash_value,
    identifier,
    read_json,
    require,
)


def evidence_reference(value: Any) -> str:
    require(
        isinstance(value, str)
        and value == value.strip()
        and 1 <= len(value) <= 512
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", value) is not None,
        "INVALID_EVIDENCE_REFERENCE",
    )
    return value


@dataclass(frozen=True, slots=True)
class ArtifactObject:
    reference: str
    contract_key: str
    content_hash: str
    byte_content_hash: str
    document_bytes: bytes

    def __post_init__(self) -> None:
        evidence_reference(self.reference)
        identifier(self.contract_key)
        hash_value(self.content_hash)
        hash_value(self.byte_content_hash)
        require(type(self.document_bytes) is bytes and bool(self.document_bytes), "ARTIFACT_EMPTY")
        require(
            hashlib.sha256(self.document_bytes).hexdigest() == self.byte_content_hash,
            "ARTIFACT_BYTE_HASH_MISMATCH",
        )
        document = self.document()
        require(document.get("content_hash") == self.content_hash, "ARTIFACT_HASH_MISMATCH")
        body = {key: value for key, value in document.items() if key != "content_hash"}
        require(digest(body) == self.content_hash, "ARTIFACT_HASH_MISMATCH")
        require(
            self.document_bytes == canonical_json(document).encode("utf-8"),
            "ARTIFACT_BYTES_NOT_CANONICAL",
        )

    @classmethod
    def from_document(
        cls,
        *,
        reference: str,
        contract_key: str,
        document: Mapping[str, Any],
    ) -> ArtifactObject:
        normalized = dict(document)
        payload = canonical_json(normalized).encode("utf-8")
        return cls(
            reference=evidence_reference(reference),
            contract_key=identifier(contract_key),
            content_hash=hash_value(normalized.get("content_hash")),
            byte_content_hash=hashlib.sha256(payload).hexdigest(),
            document_bytes=payload,
        )

    def document(self) -> dict[str, Any]:
        return read_json(self.document_bytes.decode("utf-8"), max_bytes=512_000_000)


@dataclass(frozen=True, slots=True)
class OutboxMessage:
    outbox_id: str
    engine_run_id: str
    attempt_no: int
    payload_content_hash: str
    payload_bytes: bytes

    def __post_init__(self) -> None:
        identifier(self.outbox_id)
        identifier(self.engine_run_id)
        require(type(self.attempt_no) is int and self.attempt_no > 0, "INVALID_ATTEMPT_NO")
        hash_value(self.payload_content_hash)
        require(
            type(self.payload_bytes) is bytes and bool(self.payload_bytes), "OUTBOX_PAYLOAD_EMPTY"
        )
        payload = self.payload()
        require(
            payload.get("outbox_id") == self.outbox_id
            and payload.get("engine_run_id") == self.engine_run_id
            and payload.get("attempt_no") == self.attempt_no
            and payload.get("content_hash") == self.payload_content_hash,
            "OUTBOX_BINDING_MISMATCH",
        )
        body = {key: value for key, value in payload.items() if key != "content_hash"}
        require(digest(body) == self.payload_content_hash, "OUTBOX_PAYLOAD_HASH_MISMATCH")
        require(
            self.payload_bytes == canonical_json(payload).encode("utf-8"),
            "OUTBOX_PAYLOAD_NOT_CANONICAL",
        )

    @classmethod
    def from_body(cls, body: Mapping[str, Any]) -> OutboxMessage:
        normalized_body = dict(body)
        content_hash = digest(normalized_body)
        payload = {**normalized_body, "content_hash": content_hash}
        return cls(
            outbox_id=identifier(payload["outbox_id"]),
            engine_run_id=identifier(payload["engine_run_id"]),
            attempt_no=payload["attempt_no"],
            payload_content_hash=content_hash,
            payload_bytes=canonical_json(payload).encode("utf-8"),
        )

    def payload(self) -> dict[str, Any]:
        return read_json(self.payload_bytes.decode("utf-8"), max_bytes=64_000_000)


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    message: OutboxMessage
    status: str
    row_version: int

    def __post_init__(self) -> None:
        require(
            self.status in {"PENDING", "WITHHELD_FOR_REVIEW", "PUBLISHED"},
            "OUTBOX_STATUS_INVALID",
        )
        require(type(self.row_version) is int and self.row_version > 0, "OUTBOX_VERSION_INVALID")


@dataclass(frozen=True, slots=True)
class EvidenceCommitReceipt:
    artifact_count: int
    outbox: OutboxRecord
    replayed: bool

    def __post_init__(self) -> None:
        require(
            type(self.artifact_count) is int and self.artifact_count > 0, "ARTIFACT_COUNT_INVALID"
        )
        require(type(self.replayed) is bool, "EVIDENCE_REPLAY_FLAG_INVALID")


class InventoryEvidenceUnitOfWork(Protocol):
    """Atomic append-only Artifact and publication-Outbox boundary."""

    def commit(
        self,
        artifacts: Sequence[ArtifactObject],
        outbox: OutboxMessage,
    ) -> EvidenceCommitReceipt: ...

    def read_artifact(self, reference: str) -> ArtifactObject: ...

    def read_outbox(self, outbox_id: str) -> OutboxRecord: ...

    def find_outbox(self, engine_run_id: str, attempt_no: int) -> OutboxRecord | None: ...

    def list_pending(self, *, limit: int = 100) -> tuple[OutboxRecord, ...]: ...

    def mark_published(
        self,
        outbox_id: str,
        *,
        expected_payload_content_hash: str,
    ) -> OutboxRecord: ...


__all__ = [
    "ArtifactObject",
    "EvidenceCommitReceipt",
    "InventoryEvidenceUnitOfWork",
    "OutboxMessage",
    "OutboxRecord",
    "evidence_reference",
]

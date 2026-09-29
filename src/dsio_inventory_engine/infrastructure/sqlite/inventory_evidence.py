"""SQLite append-only Artifact store and durable publication Outbox."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Sequence

from dsio_inventory_engine.inventory_contracts.values import hash_value, identifier, require
from dsio_inventory_engine.inventory_evidence.contracts import (
    ArtifactObject,
    EvidenceCommitReceipt,
    OutboxMessage,
    OutboxRecord,
    evidence_reference,
)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory_result_artifact (
    artifact_reference TEXT PRIMARY KEY,
    contract_key TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    byte_content_hash TEXT NOT NULL,
    document_bytes BLOB NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS inventory_result_publication_outbox (
    outbox_id TEXT PRIMARY KEY,
    engine_run_id TEXT NOT NULL,
    attempt_no INTEGER NOT NULL CHECK (attempt_no > 0),
    payload_content_hash TEXT NOT NULL,
    payload_bytes BLOB NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'WITHHELD_FOR_REVIEW', 'PUBLISHED')),
    row_version INTEGER NOT NULL CHECK (row_version > 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at TEXT,
    UNIQUE (engine_run_id, attempt_no)
);
CREATE INDEX IF NOT EXISTS ix_inventory_result_outbox_pending
ON inventory_result_publication_outbox (status, created_at, outbox_id);
"""


class SqliteInventoryEvidenceUnitOfWork:
    """Local transactional implementation; production PostgreSQL remains a separate adapter."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

    def commit(
        self,
        artifacts: Sequence[ArtifactObject],
        outbox: OutboxMessage,
    ) -> EvidenceCommitReceipt:
        require(bool(artifacts), "ARTIFACT_BATCH_EMPTY")
        require(
            len({artifact.reference for artifact in artifacts}) == len(artifacts),
            "ARTIFACT_REFERENCE_DUPLICATE",
        )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            replay_flags = [self._write_artifact(connection, artifact) for artifact in artifacts]
            outbox_replayed = self._write_outbox(connection, outbox)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        record = self.read_outbox(outbox.outbox_id)
        return EvidenceCommitReceipt(
            artifact_count=len(artifacts),
            outbox=record,
            replayed=all(replay_flags) and outbox_replayed,
        )

    def read_artifact(self, reference: str) -> ArtifactObject:
        reference = evidence_reference(reference)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT artifact_reference, contract_key, content_hash,
                       byte_content_hash, document_bytes
                FROM inventory_result_artifact
                WHERE artifact_reference = ?
                """,
                (reference,),
            ).fetchone()
        require(row is not None, "ARTIFACT_NOT_FOUND")
        return ArtifactObject(
            reference=row["artifact_reference"],
            contract_key=row["contract_key"],
            content_hash=row["content_hash"],
            byte_content_hash=row["byte_content_hash"],
            document_bytes=bytes(row["document_bytes"]),
        )

    def read_outbox(self, outbox_id: str) -> OutboxRecord:
        outbox_id = identifier(outbox_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT outbox_id, engine_run_id, attempt_no, payload_content_hash,
                       payload_bytes, status, row_version
                FROM inventory_result_publication_outbox
                WHERE outbox_id = ?
                """,
                (outbox_id,),
            ).fetchone()
        require(row is not None, "OUTBOX_NOT_FOUND")
        return self._outbox_record(row)

    def find_outbox(self, engine_run_id: str, attempt_no: int) -> OutboxRecord | None:
        engine_run_id = identifier(engine_run_id)
        require(type(attempt_no) is int and attempt_no > 0, "INVALID_ATTEMPT_NO")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT outbox_id, engine_run_id, attempt_no, payload_content_hash,
                       payload_bytes, status, row_version
                FROM inventory_result_publication_outbox
                WHERE engine_run_id = ? AND attempt_no = ?
                """,
                (engine_run_id, attempt_no),
            ).fetchone()
        return None if row is None else self._outbox_record(row)

    def list_pending(self, *, limit: int = 100) -> tuple[OutboxRecord, ...]:
        require(type(limit) is int and 1 <= limit <= 1000, "OUTBOX_LIMIT_INVALID")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT outbox_id, engine_run_id, attempt_no, payload_content_hash,
                       payload_bytes, status, row_version
                FROM inventory_result_publication_outbox
                WHERE status = 'PENDING'
                ORDER BY created_at, outbox_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return tuple(self._outbox_record(row) for row in rows)

    def mark_published(
        self,
        outbox_id: str,
        *,
        expected_payload_content_hash: str,
    ) -> OutboxRecord:
        outbox_id = identifier(outbox_id)
        expected_hash = hash_value(expected_payload_content_hash)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT payload_content_hash, status
                FROM inventory_result_publication_outbox
                WHERE outbox_id = ?
                """,
                (outbox_id,),
            ).fetchone()
            require(row is not None, "OUTBOX_NOT_FOUND")
            require(row["payload_content_hash"] == expected_hash, "OUTBOX_HASH_CONFLICT")
            require(
                row["status"] in {"PENDING", "PUBLISHED"},
                "OUTBOX_PUBLICATION_NOT_ALLOWED",
            )
            if row["status"] == "PENDING":
                updated = connection.execute(
                    """
                    UPDATE inventory_result_publication_outbox
                    SET status = 'PUBLISHED', row_version = row_version + 1,
                        published_at = CURRENT_TIMESTAMP
                    WHERE outbox_id = ? AND status = 'PENDING'
                    """,
                    (outbox_id,),
                )
                require(updated.rowcount == 1, "OUTBOX_STATUS_CONFLICT")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.read_outbox(outbox_id)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    @staticmethod
    def _write_artifact(connection: sqlite3.Connection, artifact: ArtifactObject) -> bool:
        row = connection.execute(
            """
            SELECT contract_key, content_hash, byte_content_hash, document_bytes
            FROM inventory_result_artifact
            WHERE artifact_reference = ?
            """,
            (artifact.reference,),
        ).fetchone()
        if row is not None:
            require(
                row["contract_key"] == artifact.contract_key
                and row["content_hash"] == artifact.content_hash
                and row["byte_content_hash"] == artifact.byte_content_hash
                and bytes(row["document_bytes"]) == artifact.document_bytes,
                "ARTIFACT_WRITE_ONCE_CONFLICT",
            )
            return True
        connection.execute(
            """
            INSERT INTO inventory_result_artifact (
                artifact_reference, contract_key, content_hash,
                byte_content_hash, document_bytes
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                artifact.reference,
                artifact.contract_key,
                artifact.content_hash,
                artifact.byte_content_hash,
                artifact.document_bytes,
            ),
        )
        return False

    @staticmethod
    def _write_outbox(connection: sqlite3.Connection, outbox: OutboxMessage) -> bool:
        initial_status = (
            "PENDING"
            if outbox.payload().get("automatic_publish_allowed") is True
            else "WITHHELD_FOR_REVIEW"
        )
        row = connection.execute(
            """
            SELECT outbox_id, engine_run_id, attempt_no, payload_content_hash, payload_bytes
            FROM inventory_result_publication_outbox
            WHERE outbox_id = ? OR (engine_run_id = ? AND attempt_no = ?)
            """,
            (outbox.outbox_id, outbox.engine_run_id, outbox.attempt_no),
        ).fetchone()
        if row is not None:
            require(
                row["outbox_id"] == outbox.outbox_id
                and row["engine_run_id"] == outbox.engine_run_id
                and row["attempt_no"] == outbox.attempt_no
                and row["payload_content_hash"] == outbox.payload_content_hash
                and bytes(row["payload_bytes"]) == outbox.payload_bytes,
                "OUTBOX_WRITE_ONCE_CONFLICT",
            )
            return True
        connection.execute(
            """
            INSERT INTO inventory_result_publication_outbox (
                outbox_id, engine_run_id, attempt_no, payload_content_hash,
                payload_bytes, status, row_version
            ) VALUES (?, ?, ?, ?, ?, ?, 1)
            """,
            (
                outbox.outbox_id,
                outbox.engine_run_id,
                outbox.attempt_no,
                outbox.payload_content_hash,
                outbox.payload_bytes,
                initial_status,
            ),
        )
        return False

    @staticmethod
    def _outbox_record(row: sqlite3.Row) -> OutboxRecord:
        message = OutboxMessage(
            outbox_id=row["outbox_id"],
            engine_run_id=row["engine_run_id"],
            attempt_no=row["attempt_no"],
            payload_content_hash=row["payload_content_hash"],
            payload_bytes=bytes(row["payload_bytes"]),
        )
        return OutboxRecord(message=message, status=row["status"], row_version=row["row_version"])


__all__ = ["SqliteInventoryEvidenceUnitOfWork"]

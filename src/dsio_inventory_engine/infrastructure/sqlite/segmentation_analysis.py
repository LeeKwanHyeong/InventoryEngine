"""Offline two-table transaction emulator. Not PostgreSQL DDL/FK/lock validation."""

import json
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from dsio_inventory_engine.inventory_contracts.segmentation_storage import HEADER_TABLE, ITEM_TABLE
from dsio_inventory_engine.inventory_contracts.values import canonical_json, require


class SqliteOfflineSegmentationUnitOfWork:
    def __init__(self, path, *, authority, item_ids):
        self.path = Path(path)
        with sqlite3.connect(self.path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS segmentation_authority (
                    snapshot_id TEXT PRIMARY KEY, body TEXT NOT NULL, items TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS segmentation_header (
                    analysis_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL,
                    analysis_hash TEXT NOT NULL, body TEXT NOT NULL,
                    UNIQUE(snapshot_id, analysis_hash)
                );
                CREATE TABLE IF NOT EXISTS segmentation_item (
                    analysis_id TEXT NOT NULL, item_id TEXT NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY(analysis_id, item_id)
                );
            """)
            old = conn.execute(
                "SELECT body,items FROM segmentation_authority WHERE snapshot_id=?",
                (authority["classification_snapshot_id"],),
            ).fetchone()
            values = (canonical_json(authority), canonical_json(sorted(item_ids)))
            if old is None:
                conn.execute(
                    "INSERT INTO segmentation_authority VALUES (?,?,?)",
                    (authority["classification_snapshot_id"], *values),
                )
            else:
                require(old == values, "SEGMENTATION_AUTHORITY_CONFLICT")

    @asynccontextmanager
    async def transaction(self, binding):
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield _Transaction(conn, binding)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()


class _Transaction:
    def __init__(self, connection, binding):
        self.connection, self.binding = connection, binding

    async def read_authority(self, item_ids):
        row = self.connection.execute(
            "SELECT body,items FROM segmentation_authority WHERE snapshot_id=?",
            (self.binding["classification_snapshot_id"],),
        ).fetchone()
        require(
            row is not None and set(item_ids) <= set(json.loads(row[1])),
            "SEGMENTATION_CLASSIFICATION_NOT_FOUND",
        )
        return json.loads(row[0])

    async def read_header(self, header):
        row = self.connection.execute(
            "SELECT body FROM segmentation_header WHERE snapshot_id=? AND analysis_hash=?",
            (header["classification_snapshot_id"], header["analysis_content_hash"]),
        ).fetchone()
        return json.loads(row[0]) if row else None

    async def read_items(self, analysis_id):
        return [
            json.loads(row[0])
            for row in self.connection.execute(
                "SELECT body FROM segmentation_item WHERE analysis_id=? ORDER BY item_id",
                (analysis_id,),
            )
        ]

    async def insert_rows(self, table, rows):
        require(table in (HEADER_TABLE, ITEM_TABLE), "SEGMENTATION_TABLE_INVALID")
        for row in rows:
            if table == HEADER_TABLE:
                self.connection.execute(
                    "INSERT INTO segmentation_header VALUES (?,?,?,?)",
                    (
                        row["segmentation_analysis_id"],
                        row["classification_snapshot_id"],
                        row["analysis_content_hash"],
                        canonical_json(row),
                    ),
                )
            else:
                self.connection.execute(
                    "INSERT INTO segmentation_item VALUES (?,?,?)",
                    (row["segmentation_analysis_id"], row["item_id"], canonical_json(row)),
                )

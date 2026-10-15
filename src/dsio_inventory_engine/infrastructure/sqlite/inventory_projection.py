"""SQLite-only Offline emulator for the DSIM writer transaction protocol.

It does NOT execute PostgreSQL Migration DDL or emulate its FK/trigger/lock
semantics. Never registered as a Platform production dependency.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from dsio_inventory_engine.inventory_contracts.values import canonical_json, require
from dsio_inventory_engine.inventory_evidence.projection_source import RECEIPT_TABLE


class SqliteOfflineProjectionUnitOfWork:
    def __init__(self, path: str | Path, *, catalog: dict, authority: dict):
        self.path, self.catalog = Path(path), catalog
        with sqlite3.connect(self.path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS offline_projection_authority (
                    engine_run_id TEXT PRIMARY KEY, body TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS offline_projection_row (
                    engine_run_id TEXT NOT NULL, table_name TEXT NOT NULL,
                    row_key TEXT NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY(engine_run_id, table_name, row_key)
                );
                CREATE TABLE IF NOT EXISTS offline_projection_receipt (
                    engine_run_id TEXT PRIMARY KEY, body TEXT NOT NULL
                );
            """)
            old = conn.execute(
                "SELECT body FROM offline_projection_authority WHERE engine_run_id=?",
                (authority["engine_run_id"],),
            ).fetchone()
            if old is None:
                conn.execute(
                    "INSERT INTO offline_projection_authority VALUES (?,?)",
                    (authority["engine_run_id"], canonical_json(authority)),
                )
            else:
                require(old[0] == canonical_json(authority), "PROJECTION_AUTHORITY_CONFLICT")

    @asynccontextmanager
    async def transaction(self, request):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield _Transaction(conn, request, self.catalog)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()


class _Transaction:
    def __init__(self, connection, request, catalog):
        self.connection, self.request, self.catalog = connection, request, catalog

    async def read_binding(self):
        row = self.connection.execute(
            "SELECT body FROM offline_projection_authority WHERE engine_run_id=?",
            (self.request.engine_run_id,),
        ).fetchone()
        require(row is not None, "PROJECTION_RUN_NOT_FOUND")
        value = json.loads(row[0])
        require(
            value["tenant_id"] == self.request.tenant_id
            and value["project_id"] == self.request.project_id,
            "PROJECTION_SCOPE_MISMATCH",
        )
        return value

    async def read_receipt(self):
        row = self.connection.execute(
            "SELECT body FROM offline_projection_receipt WHERE engine_run_id=?",
            (self.request.engine_run_id,),
        ).fetchone()
        return json.loads(row[0]) if row else None

    async def read_tables(self):
        result = {t["name"]: [] for t in self.catalog["tables"] if t["name"] != RECEIPT_TABLE}
        for name, body in self.connection.execute(
            "SELECT table_name,body FROM offline_projection_row WHERE engine_run_id=? ORDER BY table_name,row_key",
            (self.request.engine_run_id,),
        ):
            require(name in result, "PROJECTION_PERSISTED_UNKNOWN_TABLE")
            result[name].append(json.loads(body))
        return result

    async def insert_rows(self, table, rows):
        spec = next(t for t in self.catalog["tables"] if t["name"] == table)
        require(await self.read_receipt() is None, "PROJECTION_ALREADY_FINALIZED")
        self.connection.executemany(
            "INSERT INTO offline_projection_row VALUES (?,?,?,?)",
            [
                (
                    self.request.engine_run_id,
                    table,
                    canonical_json([row[k] for k in spec["primary_key"]]),
                    canonical_json(row),
                )
                for row in rows
            ],
        )

    async def insert_receipt(self, row):
        self.connection.execute(
            "INSERT INTO offline_projection_receipt VALUES (?,?)",
            (self.request.engine_run_id, canonical_json(row)),
        )

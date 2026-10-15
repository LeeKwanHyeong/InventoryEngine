"""Append-only DSIM Projection package, verifier and transactional writer.

The Artifact gateway and relational Unit of Work are injected. No connection is
opened by importing this module; no migration, publish or ordering API is called.
"""

from __future__ import annotations

import copy
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from dsio_inventory_engine.inventory_contracts.runtime import InventoryRuntimeExecutionRequest
from dsio_inventory_engine.inventory_contracts.values import canonical_json, digest, require
from .contracts import ArtifactObject
from .projection_source import DsimSourceProjector, RECEIPT_TABLE, normalize_tables

KEY = "inventory.dsim_projection"
CHUNK_KEY = "inventory.dsim_projection_chunk"
RECEIPT_KEY = "inventory.dsim_projection_receipt"
VERSION = "1.0.0"
MAX_TOTAL_ROWS = 5_000_000


class ProjectionArtifactStore(Protocol):
    def read_artifact(self, reference: str) -> ArtifactObject: ...
    def commit_artifacts(self, artifacts) -> bool: ...


def _seal(key: str, body: dict, prefix: str) -> ArtifactObject:
    document = {**body, "content_hash": digest(body)}
    return ArtifactObject.from_document(
        reference=prefix + ":" + document["content_hash"],
        contract_key=key,
        document=document,
    )


def _root(request) -> str:
    return f"artifact:inventory:{request.engine_run_id}:{request.value['attempt_no']}:dsim"


def binding_identity(request) -> dict:
    raw = request.to_dict()
    return {
        "engine_run_id": raw["engine_run_id"],
        "attempt_no": raw["attempt_no"],
        "tenant_id": raw["claim"]["tenant_id"],
        "project_id": raw["claim"]["project_id"],
        "claim_hash": digest(raw["claim"]),
        "allocated_site_row_version": raw["site_row_version"],
        "site_binding_hash": raw["claim"]["site_binding_hash"],
        "input_bindings": raw["claim"]["input_bindings"],
    }


@dataclass(frozen=True)
class VerifiedDsimProjection:
    manifest_json: str
    tables_json: str

    @property
    def manifest(self) -> dict:
        import json

        return json.loads(self.manifest_json)

    @property
    def tables(self) -> dict:
        import json

        return json.loads(self.tables_json)


class PersistDsimProjectionUseCase:
    def __init__(self, *, projector: DsimSourceProjector, store: ProjectionArtifactStore):
        self.projector, self.store = projector, store

    def execute(self, *, request, bundle_reference: str, chunk_size: int = 500) -> dict:
        require(
            type(chunk_size) is int and 1 <= chunk_size <= 1000, "PROJECTION_CHUNK_SIZE_INVALID"
        )
        request = InventoryRuntimeExecutionRequest.from_dict(request.to_dict())
        source = self.projector.build(request=request, bundle_reference=bundle_reference)
        tables = source.pop("tables")
        require(sum(map(len, tables.values())) <= MAX_TOTAL_ROWS, "PROJECTION_ROW_LIMIT")
        artifacts, manifests = [], {}
        for table in sorted(tables):
            rows = tables[table]
            chunks = []
            for ordinal, start in enumerate(range(0, len(rows), chunk_size)):
                batch = rows[start : start + chunk_size]
                obj = _seal(
                    CHUNK_KEY,
                    {
                        "contract_id": "inventory-dsim-projection-chunk-v1",
                        "contract_version": VERSION,
                        "engine_run_id": request.engine_run_id,
                        "table_name": table,
                        "ordinal": ordinal,
                        "row_count": len(batch),
                        "rows": batch,
                    },
                    _root(request) + ":chunk:" + table.split(".")[1],
                )
                artifacts.append(obj)
                chunks.append(
                    {
                        "artifact_reference": obj.reference,
                        "content_hash": obj.content_hash,
                        "byte_content_hash": obj.byte_content_hash,
                        "ordinal": ordinal,
                        "row_count": len(batch),
                    }
                )
            manifests[table] = {"row_count": len(rows), "chunks": chunks}
        counts = {t.split(".")[1]: len(rows) for t, rows in tables.items()}
        counts[RECEIPT_TABLE.split(".")[1]] = 1
        manifest = _seal(
            KEY,
            {
                "contract_id": "inventory-dsim-projection-v1",
                "contract_version": VERSION,
                "catalog_hash": digest(self.projector.catalog),
                "binding": binding_identity(request),
                "runtime_request_content_hash": request.canonical_hash,
                **source,
                "projection_content_hash": digest(tables),
                "table_row_counts": counts,
                "tables": manifests,
            },
            _root(request) + ":manifest",
        )
        artifacts.append(manifest)
        replayed = self.store.commit_artifacts(tuple(artifacts))
        return {
            "artifact_reference": manifest.reference,
            "content_hash": manifest.content_hash,
            "projection_content_hash": manifest.document()["projection_content_hash"],
            "replayed": replayed,
        }


class VerifyDsimProjectionUseCase:
    def __init__(self, *, projector: DsimSourceProjector, store: ProjectionArtifactStore):
        self.projector, self.store = projector, store

    def execute(self, *, request, reference: str, expected_hash: str) -> VerifiedDsimProjection:
        request = InventoryRuntimeExecutionRequest.from_dict(request.to_dict())
        obj = self.store.read_artifact(reference)
        require(
            obj.contract_key == KEY and obj.content_hash == expected_hash,
            "PROJECTION_MANIFEST_HASH_MISMATCH",
        )
        manifest = obj.document()
        expected_fields = {
            "contract_id",
            "contract_version",
            "catalog_hash",
            "binding",
            "runtime_request_content_hash",
            "bundle_reference",
            "bundle_content_hash",
            "canonical_input_hash",
            "prepared_input_hash",
            "source_artifacts",
            "projection_content_hash",
            "table_row_counts",
            "tables",
            "content_hash",
        }
        require(
            set(manifest) == expected_fields
            and manifest["contract_id"] == "inventory-dsim-projection-v1"
            and manifest["contract_version"] == VERSION
            and manifest["catalog_hash"] == digest(self.projector.catalog),
            "PROJECTION_CONTRACT_MISMATCH",
        )
        require(
            reference == _root(request) + ":manifest:" + obj.content_hash,
            "PROJECTION_REFERENCE_MISMATCH",
        )
        require(
            manifest["binding"] == binding_identity(request)
            and manifest["runtime_request_content_hash"] == request.canonical_hash,
            "PROJECTION_CLAIM_MISMATCH",
        )
        expected_names = {
            t["name"] for t in self.projector.catalog["tables"] if t["name"] != RECEIPT_TABLE
        }
        require(set(manifest["tables"]) == expected_names, "PROJECTION_TABLE_SET_MISMATCH")
        tables, seen, total = {}, set(), 0
        for name in sorted(expected_names):
            spec = manifest["tables"][name]
            require(
                set(spec) == {"row_count", "chunks"}
                and type(spec["row_count"]) is int
                and 0 <= spec["row_count"] <= MAX_TOTAL_ROWS
                and type(spec["chunks"]) is list
                and len(spec["chunks"]) <= spec["row_count"],
                "PROJECTION_CHUNK_MANIFEST_INVALID",
            )
            rows = []
            for ordinal, binding in enumerate(spec["chunks"]):
                require(
                    set(binding)
                    == {
                        "artifact_reference",
                        "content_hash",
                        "byte_content_hash",
                        "ordinal",
                        "row_count",
                    }
                    and type(binding["ordinal"]) is int
                    and binding["ordinal"] == ordinal
                    and type(binding["row_count"]) is int
                    and 1 <= binding["row_count"] <= 1000,
                    "PROJECTION_CHUNK_SEQUENCE_MISMATCH",
                )
                ref = binding["artifact_reference"]
                require(ref not in seen, "PROJECTION_DUPLICATE_CHUNK")
                seen.add(ref)
                chunk = self.store.read_artifact(ref)
                require(
                    chunk.contract_key == CHUNK_KEY
                    and chunk.content_hash == binding["content_hash"]
                    and chunk.byte_content_hash == binding["byte_content_hash"]
                    and ref
                    == _root(request) + ":chunk:" + name.split(".")[1] + ":" + chunk.content_hash,
                    "PROJECTION_CHUNK_HASH_MISMATCH",
                )
                doc = chunk.document()
                require(
                    set(doc)
                    == {
                        "contract_id",
                        "contract_version",
                        "engine_run_id",
                        "table_name",
                        "ordinal",
                        "row_count",
                        "rows",
                        "content_hash",
                    }
                    and doc["contract_id"] == "inventory-dsim-projection-chunk-v1"
                    and doc["contract_version"] == VERSION
                    and doc["engine_run_id"] == request.engine_run_id
                    and doc["table_name"] == name
                    and doc["ordinal"] == ordinal
                    and doc["row_count"] == binding["row_count"]
                    and type(doc["rows"]) is list
                    and len(doc["rows"]) == doc["row_count"],
                    "PROJECTION_CHUNK_BINDING_MISMATCH",
                )
                total += len(doc["rows"])
                require(total <= MAX_TOTAL_ROWS, "PROJECTION_ROW_LIMIT")
                rows.extend(doc["rows"])
            require(len(rows) == spec["row_count"], "PROJECTION_ROW_COUNT_MISMATCH")
            tables[name] = rows
        normalized = normalize_tables(self.projector.catalog, tables)
        require(normalized == tables, "PROJECTION_ROWS_NOT_CANONICAL")
        require(
            all(
                row["engine_run_id"] == request.engine_run_id
                for rows in tables.values()
                for row in rows
            ),
            "PROJECTION_CROSS_RUN_ROW",
        )
        counts = {t.split(".")[1]: len(rows) for t, rows in tables.items()}
        counts[RECEIPT_TABLE.split(".")[1]] = 1
        require(
            type(manifest["table_row_counts"]) is dict
            and all(type(v) is int for v in manifest["table_row_counts"].values())
            and manifest["table_row_counts"] == counts
            and manifest["projection_content_hash"] == digest(tables),
            "PROJECTION_COUNTS_OR_HASH_MISMATCH",
        )
        # Re-sealing forged rows is insufficient: rebuild from authoritative source bytes.
        source = self.projector.build(
            request=request, bundle_reference=manifest["bundle_reference"]
        )
        expected_tables = source.pop("tables")
        require(tables == expected_tables, "PROJECTION_SOURCE_ROWS_MISMATCH")
        require(
            all(manifest[k] == v for k, v in source.items()), "PROJECTION_SOURCE_BINDING_MISMATCH"
        )
        return VerifiedDsimProjection(canonical_json(manifest), canonical_json(tables))


class ProjectionTransaction(Protocol):
    async def read_binding(self) -> Mapping[str, Any]: ...
    async def read_receipt(self) -> dict | None: ...
    async def read_tables(self) -> dict: ...
    async def insert_rows(self, table: str, rows: list[dict]) -> None: ...
    async def insert_receipt(self, row: dict) -> None: ...


class WriteDsimProjectionUseCase:
    """One atomic transaction; exact replay verifies persisted rows, not just a receipt."""

    def __init__(self, *, verifier: VerifyDsimProjectionUseCase, unit_of_work, clock=None):
        self.verifier, self.uow = verifier, unit_of_work
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    async def execute(self, *, request, reference: str, expected_hash: str) -> dict:
        request = InventoryRuntimeExecutionRequest.from_dict(request.to_dict())
        async with self.uow.transaction(request) as tx:
            binding = dict(await tx.read_binding())
            require(binding == binding_identity(request), "PROJECTION_LEDGER_BINDING_MISMATCH")
        # Source replay can be expensive; never hold a DB transaction/Run lock during it.
        # No gateway access until the authoritative tenant/project Claim has matched.
        verified = await asyncio.to_thread(
            self.verifier.execute, request=request, reference=reference, expected_hash=expected_hash
        )
        manifest, tables = verified.manifest, verified.tables
        async with self.uow.transaction(request) as tx:
            require(
                dict(await tx.read_binding()) == binding_identity(request),
                "PROJECTION_LEDGER_BINDING_MISMATCH",
            )
            old = await tx.read_receipt()
            if old is not None:
                require(
                    old["projection_content_hash"] == manifest["projection_content_hash"]
                    and old["bundle_content_hash"] == manifest["bundle_content_hash"]
                    and old["projection_contract_version"] == VERSION,
                    "PROJECTION_WRITE_ONCE_CONFLICT",
                )
                require(await tx.read_tables() == tables, "PROJECTION_PERSISTED_ROWS_MISMATCH")
                obj = self.verifier.store.read_artifact(old["receipt_artifact_reference"])
                require(
                    obj.contract_key == RECEIPT_KEY
                    and obj.content_hash == old["receipt_artifact_hash"]
                    and obj.document().get("catalog_hash") == manifest["catalog_hash"]
                    and obj.document()["receipt"]
                    == {
                        k: v
                        for k, v in old.items()
                        if k not in ("receipt_artifact_reference", "receipt_artifact_hash")
                    },
                    "PROJECTION_RECEIPT_ARTIFACT_MISMATCH",
                )
                return {"receipt": copy.deepcopy(old), "replayed": True}
            require(
                all(not rows for rows in (await tx.read_tables()).values()),
                "PROJECTION_PARTIAL_ROWS_WITHOUT_RECEIPT",
            )
            for name in _insert_order(self.verifier.projector.catalog):
                rows = tables[name]
                for offset in range(0, len(rows), 500):
                    await tx.insert_rows(name, rows[offset : offset + 500])
            require(await tx.read_tables() == tables, "PROJECTION_WRITE_READBACK_MISMATCH")
            row = {
                "engine_run_id": request.engine_run_id,
                "receipt_id": "IOPR-" + manifest["projection_content_hash"],
                "bundle_id": tables["dsim.tb_io_result_bundle"][0]["bundle_id"],
                "bundle_content_hash": manifest["bundle_content_hash"],
                "projection_contract_version": VERSION,
                "projection_content_hash": manifest["projection_content_hash"],
                "table_row_counts": manifest["table_row_counts"],
                "verified_at": self.clock()
                .astimezone(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
            }
            artifact = _seal(
                RECEIPT_KEY,
                {
                    "contract_id": "inventory-dsim-projection-receipt-v1",
                    "contract_version": VERSION,
                    "catalog_hash": manifest["catalog_hash"],
                    "projection_reference": reference,
                    "projection_manifest_hash": expected_hash,
                    "receipt": row,
                },
                _root(request) + ":receipt",
            )
            self.verifier.store.commit_artifacts((artifact,))
            row.update(
                receipt_artifact_reference=artifact.reference,
                receipt_artifact_hash=artifact.content_hash,
            )
            await tx.insert_receipt(row)
            require(await tx.read_receipt() == row, "PROJECTION_RECEIPT_READBACK_MISMATCH")
            return {"receipt": copy.deepcopy(row), "replayed": False}


def _insert_order(catalog: dict) -> list[str]:
    # Only Bundle -> effective Child is deliberately deferred. All other FKs are immediate.
    pending = {t["name"]: t for t in catalog["tables"] if t["name"] != RECEIPT_TABLE}
    emitted = []
    while pending:
        ready = sorted(
            name
            for name, spec in pending.items()
            if all(f["table"] not in pending or f.get("deferred") for f in spec["foreign_keys"])
        )
        require(bool(ready), "PROJECTION_FK_CYCLE")
        for name in ready:
            pending.pop(name)
            emitted.append(name)
    return emitted

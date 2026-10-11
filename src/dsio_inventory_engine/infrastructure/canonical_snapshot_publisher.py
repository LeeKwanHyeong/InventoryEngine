"""Development-only publication of validated Source → Canonical Site snapshots.

The Source preparation use case performs all Canonical admission checks first.
Publication stores the five Inventory-owned snapshots by exact ID; it does not
attest the ERP source, approve a Platform input set, or claim an Inventory Run.
"""

import os
import re
import tempfile
from pathlib import Path
from typing import Protocol

from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.source import SourceInputRequest, SourceSnapshot
from dsio_inventory_engine.inventory_contracts.values import canonical_json, digest, require
from dsio_inventory_engine.prepare_inventory.application.source_input import (
    PrepareSourceInputUseCase,
)


KINDS = ("inventory", "prior_inventory", "policies", "receipts", "customer_orders")
MAX_BYTES = 128_000_000
SNAPSHOT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")


class SourceSnapshotReader(Protocol):
    async def read_snapshot(self, kind: str, snapshot_id: str) -> SourceSnapshot: ...


class LocalCanonicalSnapshotPublisher:
    """Append-only exact-ID artifact writer; safe to retry after a partial write."""

    def __init__(self, root: Path) -> None:
        root = Path(root)
        require(
            root.is_absolute() and root.is_dir() and not root.is_symlink(), "CATALOG_ROOT_INVALID"
        )
        self.root = root.resolve(strict=True)

    def publish(self, canonical: CanonicalInputRequest, deployment: DeploymentScope) -> dict:
        require(deployment.environment == "DEVELOPMENT", "DEVELOPMENT_CATALOG_ONLY")
        # Revalidate the complete eight-snapshot request and all operational
        # cut-off, universe, UOM, receipt and policy constraints before writes.
        from dsio_inventory_engine.prepare_inventory.application.inventory_input import (
            PrepareInventoryInputUseCase,
        )

        normalized = CanonicalInputRequest.from_dict(canonical.to_dict())
        PrepareInventoryInputUseCase(deployment).execute(normalized).to_dict()
        snapshots = normalized.to_dict()["snapshots"]
        references: dict[str, dict[str, str]] = {}
        for kind in KINDS:
            snapshot = snapshots[kind]
            snapshot_id = snapshot["snapshot_id"]
            require(SNAPSHOT_ID.fullmatch(snapshot_id) is not None, "SNAPSHOT_ID_INVALID")
            content = canonical_json(snapshot).encode("utf-8")
            require(0 < len(content) <= MAX_BYTES, "SNAPSHOT_SIZE_LIMIT")
            self._write_once(self.root / f"{snapshot_id}.json", content)
            references[kind] = {
                "snapshot_id": snapshot_id,
                "content_hash": snapshot["content_hash"],
            }
        return {
            "status": "CANONICAL_SITE_SNAPSHOTS_SEALED",
            "canonical_input_hash": normalized.input_hash,
            "snapshot_bindings": references,
            "database_writes": False,
            "source_attestation": "TRUSTED_UPSTREAM_REQUIRED_NOT_AUTHENTICATED_BY_HASH",
        }

    def _write_once(self, target: Path, content: bytes) -> None:
        descriptor, temporary = tempfile.mkstemp(prefix=".partial-", dir=self.root)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, target, follow_symlinks=False)
            except FileExistsError:
                descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(descriptor, "rb") as existing:
                    require(existing.read(MAX_BYTES + 1) == content, "SNAPSHOT_ID_CONFLICT")
            else:
                directory = os.open(self.root, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            os.unlink(temporary)


class SourceToCanonicalSnapshotProducer:
    """Consume pinned, sealed SourceSnapshots and publish admitted Canonical data."""

    def __init__(
        self,
        *,
        reader: SourceSnapshotReader,
        publisher: LocalCanonicalSnapshotPublisher,
        deployment: DeploymentScope,
    ) -> None:
        self._prepare = PrepareSourceInputUseCase(reader, deployment)
        self._publisher = publisher
        self._deployment = deployment

    async def produce(self, request: SourceInputRequest) -> dict:
        prepared = await self._prepare.execute(request)
        canonical = CanonicalInputRequest.from_dict(prepared["canonical_request"])
        published = self._publisher.publish(canonical, self._deployment)
        return {
            **published,
            "source_bindings_hash": prepared["source_bindings_hash"],
            "source_mapping_evidence_hash": digest(prepared["source_mapping_evidence"]),
            "source_mapping_evidence": prepared["source_mapping_evidence"],
        }

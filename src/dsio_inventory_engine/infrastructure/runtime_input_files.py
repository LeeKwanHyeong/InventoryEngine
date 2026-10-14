"""Append-only original Runtime source documents. Reading does not attest approval."""

import asyncio
import os
from pathlib import Path
import tempfile

from dsio_inventory_engine.inventory_contracts.runtime_inputs import (
    MAX_BYTES,
    _id,
    validate_runtime_input_manifest,
)
from dsio_inventory_engine.inventory_contracts.values import canonical_json, read_json, require


class LocalRuntimeInputManifestCatalog:
    def __init__(self, root: Path):
        require(
            root.is_absolute() and root.is_dir() and not root.is_symlink(),
            "RUNTIME_INPUT_ROOT_INVALID",
        )
        self.root = root.resolve(strict=True)

    def publish(self, manifest):
        doc = validate_runtime_input_manifest(manifest)
        payload = canonical_json(doc).encode()
        target = self.root / ("runtime-inputs-" + _id(doc["snapshot_id"]) + ".json")
        fd, temporary = tempfile.mkstemp(prefix=".runtime-input-", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, target, follow_symlinks=False)
            except FileExistsError:
                with os.fdopen(os.open(target, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
                    require(
                        stream.read(MAX_BYTES + 1) == payload, "RUNTIME_INPUT_SNAPSHOT_CONFLICT"
                    )
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            os.unlink(temporary)
        return {
            "snapshot_id": doc["snapshot_id"],
            "content_hash": doc["content_hash"],
            "database_writes": False,
        }

    async def read(self, snapshot_id, content_hash):
        return await asyncio.to_thread(self._read, snapshot_id, content_hash)

    def _read(self, snapshot_id, content_hash):
        path = self.root / ("runtime-inputs-" + _id(snapshot_id) + ".json")
        require(path.is_file() and not path.is_symlink(), "RUNTIME_INPUT_SOURCE_NOT_FOUND")
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        doc = validate_runtime_input_manifest(read_json(raw.decode(), max_bytes=MAX_BYTES))
        require(
            doc["snapshot_id"] == snapshot_id and doc["content_hash"] == content_hash,
            "RUNTIME_INPUT_SOURCE_HASH_MISMATCH",
        )
        return doc

    async def resolve(self, request):
        binding = request.strategy_execution_plan["runtime_input_binding"]
        return await self.read(binding["snapshot_id"], binding["content_hash"])

"""Exact, scope-checked local pricing snapshot reader for development Runtime."""

import asyncio
import os
from pathlib import Path

from dsio_inventory_engine.inventory_contracts.run_cost import validate_run_cost_input
from dsio_inventory_engine.inventory_contracts.values import (
    hash_value,
    identifier,
    read_json,
    require,
)


class FileRunCostInputReader:
    def __init__(self, root: Path):
        self.root = root.resolve(strict=True)
        require(self.root.is_dir(), "SOURCE_ROOT_REQUIRED")

    async def read(
        self, *, snapshot_id: str, content_hash: str, tenant_id: str, project_id: str
    ) -> dict:
        return await asyncio.to_thread(self._read, snapshot_id, content_hash, tenant_id, project_id)

    def _read(self, snapshot_id, content_hash, tenant_id, project_id):
        path = self.root / f"{identifier(snapshot_id)}.json"
        expected_hash = hash_value(content_hash)
        require(path.is_file() and not path.is_symlink(), "RUN_COST_INPUT_NOT_FOUND")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            raw = stream.read(8_000_001)
        require(len(raw) <= 8_000_000, "SOURCE_FILE_SIZE_LIMIT")
        document = validate_run_cost_input(read_json(raw.decode("utf-8")))
        require(
            document["snapshot_id"] == snapshot_id
            and document["content_hash"] == expected_hash
            and document["tenant_id"] == tenant_id
            and document["project_id"] == project_id,
            "RUN_COST_INPUT_IDENTITY_MISMATCH",
        )
        return document

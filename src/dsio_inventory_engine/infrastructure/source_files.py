"""Read supplied immutable JSON snapshots from an operator-configured local root."""

import os
from pathlib import Path

from dsio_inventory_engine.inventory_contracts.source import SourceSnapshot
from dsio_inventory_engine.inventory_contracts.values import identifier, read_json, require


class FileSourceSnapshotReader:
    def __init__(self, root: Path):
        self.root = root.resolve(strict=True)
        require(self.root.is_dir(), "SOURCE_ROOT_REQUIRED")

    async def read_snapshot(self, kind: str, snapshot_id: str) -> SourceSnapshot:
        path = self.root / f"{identifier(snapshot_id)}.json"
        require(path.is_file() and not path.is_symlink(), "SOURCE_SNAPSHOT_NOT_FOUND")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            raw = stream.read(8_000_001)
        require(len(raw) <= 8_000_000, "SOURCE_FILE_SIZE_LIMIT")
        source = SourceSnapshot.from_dict(read_json(raw.decode("utf-8")))
        data = source.to_dict()
        require(
            data["snapshot_type"] == kind and data["snapshot_id"] == snapshot_id,
            "SOURCE_IDENTITY_MISMATCH",
        )
        return source

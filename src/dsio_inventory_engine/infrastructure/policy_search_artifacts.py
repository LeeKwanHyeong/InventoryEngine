"""Append-only local policy-search evidence. No database or Platform publication."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

from dsio_inventory_engine.inventory_contracts.values import (
    canonical_json,
    read_json,
    require,
)
from dsio_inventory_engine.inventory_contracts.policy_search_result import (
    verify_policy_search_result,
)


class _LocalPolicyArtifactStore:
    """Exclusive content-addressed storage, shared by search and selection artifacts."""

    artifact_prefix = "PSR-"

    def __init__(self, root: Path):
        require(root.is_dir() and not root.is_symlink(), "POLICY_SEARCH_ARTIFACT_ROOT_INVALID")
        self.root = root.resolve(strict=True)

    def verify(self, value: dict) -> dict:
        raise NotImplementedError

    def write(self, result: dict) -> dict:
        result = self.verify(read_json(canonical_json(result), max_bytes=128_000_000))
        payload = (canonical_json(result) + "\n").encode()
        require(len(payload) <= 128_000_000, "POLICY_SEARCH_ARTIFACT_SIZE_LIMIT")
        path = self.root / (result["content_hash"] + ".json")
        with tempfile.NamedTemporaryFile(
            dir=self.root, prefix=".policy-search-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    self.read(result["content_hash"])
                    require(path.read_bytes() == payload, "POLICY_SEARCH_ARTIFACT_CONFLICT")
                directory = os.open(self.root, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                temporary.unlink(missing_ok=True)
        return {
            "artifact_id": self.artifact_prefix + result["content_hash"],
            "content_hash": result["content_hash"],
            "path": str(path),
            "durable_local_artifact": True,
            "database_writes": False,
        }

    def read(self, content_hash: str) -> dict:
        from dsio_inventory_engine.inventory_contracts.values import hash_value

        path = self.root / (hash_value(content_hash) + ".json")
        require(path.is_file() and not path.is_symlink(), "POLICY_SEARCH_ARTIFACT_NOT_FOUND")
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
            payload = stream.read(128_000_001)
        require(len(payload) <= 128_000_000, "POLICY_SEARCH_ARTIFACT_SIZE_LIMIT")
        value = self.verify(read_json(payload.decode(), max_bytes=128_000_000))
        require(value["content_hash"] == content_hash, "POLICY_SEARCH_ARTIFACT_REFERENCE_MISMATCH")
        return value


class LocalPolicySearchArtifactStore(_LocalPolicyArtifactStore):
    def verify(self, value: dict) -> dict:
        return verify_policy_search_result(value)

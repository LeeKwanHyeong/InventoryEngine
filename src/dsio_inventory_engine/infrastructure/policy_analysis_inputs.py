"""Append-only, exact-ID development input catalog; hashes do not attest source authority."""

import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid

from dsio_inventory_engine.classify_inventory.application import (
    SNAPSHOT_NAMESPACE,
    config_hash,
    validate_config,
)
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.values import (
    canonical_json,
    digest,
    hash_value,
    identifier,
    read_json,
    require,
)

MAX_BYTES = 128_000_000
KINDS = {"CONFIG", "CLASSIFICATION", "MATHEMATICAL_BASE", "MATHEMATICAL_REQUEST"}


def normalized_payload(kind, snapshot_id, payload):
    require(kind in KINDS, "POLICY_ANALYSIS_CATALOG_KIND_INVALID")
    snapshot_id = identifier(snapshot_id)
    if kind == "CONFIG":
        value = validate_config(payload)
        require("policy_search" in value, "POLICY_ANALYSIS_CONFIG_21_REQUIRED")
        return value, config_hash(value)
    if kind == "CLASSIFICATION":
        value = copy.deepcopy(payload)
        unsigned = {
            k: v
            for k, v in value.items()
            if k not in ("content_hash", "classification_snapshot_id")
        }
        actual = hashlib.sha256(
            json.dumps(
                unsigned, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()
        require(
            value.get("content_hash") == actual
            and value.get("classification_snapshot_id") == snapshot_id
            and snapshot_id == str(uuid.uuid5(SNAPSHOT_NAMESPACE, actual)),
            "POLICY_ANALYSIS_CLASSIFICATION_INVALID",
        )
        return value, actual
    value = MathematicalPolicyRequest.from_dict(payload).to_dict()
    if kind == "MATHEMATICAL_BASE":
        require(
            value["policy_input"]["snapshot_id"] == snapshot_id, "POLICY_ANALYSIS_BASE_ID_MISMATCH"
        )
        return value, value["policy_input"]["content_hash"]
    return value, digest(value)


class LocalPolicyAnalysisInputCatalog:
    """Readers are scope-checked and reject missing files, duplicate JSON keys and symlinks."""

    def __init__(self, root: Path, deployment):
        require(deployment.environment == "DEVELOPMENT", "POLICY_ANALYSIS_DEVELOPMENT_ONLY")
        require(
            root.is_absolute() and root.is_dir() and not root.is_symlink(), "CATALOG_ROOT_INVALID"
        )
        self.root = root.resolve(strict=True)

    def publish(self, *, kind, snapshot_id, tenant_id, project_id, payload):
        value, payload_hash = normalized_payload(kind, snapshot_id, payload)
        body = {
            "contract_id": "inventory-policy-analysis-source",
            "contract_version": "1.0.0",
            "environment": "DEVELOPMENT",
            "kind": kind,
            "snapshot_id": snapshot_id,
            "tenant_id": identifier(tenant_id),
            "project_id": identifier(project_id),
            "payload": value,
            "payload_content_hash": payload_hash,
        }
        document = {**body, "content_hash": digest(body)}
        content = canonical_json(document).encode()
        require(len(content) <= MAX_BYTES, "POLICY_ANALYSIS_SOURCE_SIZE_LIMIT")
        target = self.root / f"{kind}-{snapshot_id}.json"
        descriptor, temporary = tempfile.mkstemp(prefix=".analysis-partial-", dir=self.root)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, target, follow_symlinks=False)
            except FileExistsError:
                fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(fd, "rb") as existing:
                    require(
                        existing.read(MAX_BYTES + 1) == content, "POLICY_ANALYSIS_SNAPSHOT_CONFLICT"
                    )
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            os.unlink(temporary)
        return {"snapshot_id": snapshot_id, "content_hash": payload_hash, "database_writes": False}

    async def read(self, *, kind, snapshot_id, content_hash, tenant_id, project_id):
        return await asyncio.to_thread(
            self._read, kind, snapshot_id, content_hash, tenant_id, project_id
        )

    def _read(self, kind, snapshot_id, content_hash, tenant_id, project_id):
        require(kind in KINDS, "POLICY_ANALYSIS_CATALOG_KIND_INVALID")
        expected_hash = hash_value(content_hash)
        path = self.root / f"{kind}-{identifier(snapshot_id)}.json"
        require(path.is_file() and not path.is_symlink(), "POLICY_ANALYSIS_SOURCE_NOT_FOUND")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            payload = stream.read(MAX_BYTES + 1)
        require(len(payload) <= MAX_BYTES, "POLICY_ANALYSIS_SOURCE_SIZE_LIMIT")
        doc = read_json(payload.decode(), max_bytes=MAX_BYTES)
        require(
            set(doc)
            == {
                "contract_id",
                "contract_version",
                "environment",
                "kind",
                "snapshot_id",
                "tenant_id",
                "project_id",
                "payload",
                "payload_content_hash",
                "content_hash",
            },
            "POLICY_ANALYSIS_SOURCE_FIELDS_INVALID",
        )
        require(
            doc["contract_id"] == "inventory-policy-analysis-source"
            and doc["contract_version"] == "1.0.0"
            and doc["environment"] == "DEVELOPMENT"
            and all(
                doc[k] == v
                for k, v in dict(
                    kind=kind, snapshot_id=snapshot_id, tenant_id=tenant_id, project_id=project_id
                ).items()
            )
            and digest({k: v for k, v in doc.items() if k != "content_hash"})
            == doc["content_hash"],
            "POLICY_ANALYSIS_SOURCE_IDENTITY_MISMATCH",
        )
        value, actual_hash = normalized_payload(kind, snapshot_id, doc["payload"])
        require(
            actual_hash == doc["payload_content_hash"] == expected_hash,
            "POLICY_ANALYSIS_SOURCE_HASH_MISMATCH",
        )
        return value

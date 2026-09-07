"""Compose pinned snapshots, retaining raw evidence; never creates a source seal."""

from typing import Protocol

from dsio_inventory_engine.inventory_contracts.canonical import (
    CanonicalInputRequest,
    SCOPE,
    normalize_snapshot,
    snapshot_content,
)
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.source import SourceInputRequest, SourceSnapshot
from dsio_inventory_engine.inventory_contracts.values import digest, require
from .inventory_input import PrepareInventoryInputUseCase
from .source_mapping import map_rows


class SourceSnapshotReader(Protocol):
    async def read_snapshot(self, kind: str, snapshot_id: str) -> SourceSnapshot: ...


ORDER = (
    "calendar",
    "master",
    "prior_inventory",
    "forecast",
    "customer_orders",
    "receipts",
    "policies",
    "inventory",
)


class PrepareSourceInputUseCase:
    def __init__(self, reader: SourceSnapshotReader, deployment: DeploymentScope):
        self.reader = reader
        self.deployment = deployment

    async def execute(self, request: SourceInputRequest) -> dict:
        data = SourceInputRequest.from_dict(request.to_dict()).to_dict()
        context = data["context"]
        require(context["company_cd"] == self.deployment.company_cd, "DEPLOYMENT_COMPANY_MISMATCH")
        sources: dict[str, dict] = {}
        snapshots: dict[str, dict] = {}
        evidence: list[dict] = []
        master: dict[str, dict] = {}
        for kind in ORDER:
            binding = data["source_bindings"][kind]
            source = SourceSnapshot.from_dict(
                (await self.reader.read_snapshot(kind, binding["snapshot_id"])).to_dict()
            ).to_dict()
            require(
                source["snapshot_type"] == kind and source["snapshot_id"] == binding["snapshot_id"],
                "SOURCE_IDENTITY_MISMATCH",
            )
            require(source["content_hash"] == binding["content_hash"], "PINNED_SOURCE_MISMATCH")
            require(source["status"] == "SEALED", "UNSEALED_SOURCE")
            require(
                all(source["metadata"].get(k) == context[k] for k in SCOPE), "SOURCE_SCOPE_MISMATCH"
            )
            require(source["origin_type"] != "UNKNOWN_SOURCE", "UNKNOWN_SOURCE_ORIGIN")
            require(
                self.deployment.environment == "DEVELOPMENT"
                or source["origin_type"] == "ACTUAL_SOURCE",
                "DEVELOPMENT_SOURCE_FORBIDDEN",
            )
            if kind in ("master", "policies"):
                require(
                    source["source_as_of_date"] == context["master_as_of_date"],
                    "SOURCE_MASTER_AS_OF_MISMATCH",
                )
            if kind == "inventory":
                require(
                    source["source_as_of_date"] == context["plan_start_date"],
                    "SOURCE_POSITION_AS_OF_MISMATCH",
                )
            if kind in ("inventory", "prior_inventory"):
                if kind == "prior_inventory":
                    require(
                        source["source_as_of_date"] == source["metadata"]["as_of_date"],
                        "SOURCE_PRIOR_AS_OF_MISMATCH",
                    )
                is_synthetic = source["metadata"].get("position_source_type") == "SYNTHETIC_BOH"
                require(
                    is_synthetic == (source["origin_type"] == "SYNTHETIC_SOURCE")
                    or source["origin_type"] == "DEVELOPMENT_FIXTURE",
                    "SOURCE_ORIGIN_LINEAGE_MISMATCH",
                )
            rows = map_rows(source, master, context)
            metadata = dict(source["metadata"])
            if kind == "forecast":
                for parent in ("calendar", "master"):
                    require(
                        metadata[f"{parent}_content_hash"] == sources[parent]["content_hash"],
                        "SOURCE_PARENT_HASH_MISMATCH",
                    )
                    metadata[f"{parent}_content_hash"] = snapshots[parent]["content_hash"]
            if kind == "inventory":
                require(
                    metadata["prior_content_hash"] == sources["prior_inventory"]["content_hash"],
                    "SOURCE_PARENT_HASH_MISMATCH",
                )
                metadata["prior_content_hash"] = snapshots["prior_inventory"]["content_hash"]
            snapshot = normalize_snapshot(
                kind,
                {
                    "snapshot_id": source["snapshot_id"],
                    "content_hash": "0" * 64,
                    "status": source["status"],
                    "row_count": len(rows),
                    "metadata": metadata,
                    "rows": rows,
                },
            )
            snapshot["content_hash"] = digest(snapshot_content(kind, snapshot))
            sources[kind], snapshots[kind] = source, snapshot
            if kind == "master":
                require(len({r["item_id"] for r in rows}) == len(rows), "DUPLICATE_MASTER_ITEM")
                master = {r["item_id"]: r for r in rows}
            evidence.append(
                {
                    "snapshot_type": kind,
                    "source_snapshot_id": source["snapshot_id"],
                    "source_content_hash": source["content_hash"],
                    "canonical_content_hash": snapshot["content_hash"],
                    "adapter": source["adapter"],
                    "origin_type": source["origin_type"],
                    "source_reference": source["source_reference"],
                    "source_as_of_date": source["source_as_of_date"],
                    "row_count": len(rows),
                }
            )
        canonical = CanonicalInputRequest.from_dict(
            {
                "contract_id": "io-canonical-input-v1",
                "contract_version": "1.0.0",
                "context": context,
                "quantity_rules": data["quantity_rules"],
                "snapshots": snapshots,
                "input_bindings": {
                    k: {f: s[f] for f in ("snapshot_id", "content_hash")}
                    for k, s in snapshots.items()
                },
            }
        )
        prepared = PrepareInventoryInputUseCase(self.deployment).execute(canonical).to_dict()
        return {
            "status": "SOURCE_INPUT_PREPARED_LOCALLY",
            "canonical_request": canonical.to_dict(),
            "canonical_input_hash": canonical.input_hash,
            "source_bindings": data["source_bindings"],
            "source_bindings_hash": digest(data["source_bindings"]),
            "source_mapping_evidence": evidence,
            "source_snapshots": sources,
            "receipt_decisions": prepared["receipt_decisions"],
            "reconciliation": prepared["reconciliation"],
            "database_writes": False,
            "artifact_sealed": False,
            "evidence_persisted": False,
            "run_claimed": False,
            "psi_computed": False,
            "source_attestation": "TRUSTED_UPSTREAM_REQUIRED_NOT_AUTHENTICATED_BY_HASH",
        }

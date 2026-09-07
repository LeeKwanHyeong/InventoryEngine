"""Read-only, repeatable-read adapter. Never import dsai-platform or Neo4j."""

from uuid import UUID

from dsio_inventory_engine.inventory_contracts.network import (
    HEADER_KEYS,
    LANE_KEYS,
    NODE_KEYS,
    NetworkRevisionRecord,
    canonical_json,
    normalize_network,
)

HEADER_SQL = f"SELECT {', '.join(HEADER_KEYS)},content_sha256,status,row_version,approval_evidence FROM dsim.tb_mst_inventory_network WHERE network_revision_id=$1"
NODES_SQL = f"SELECT {', '.join(NODE_KEYS)} FROM dsim.tb_mst_inventory_network_node WHERE network_revision_id=$1 ORDER BY site_cd LIMIT 501"
LANES_SQL = f"SELECT {', '.join(LANE_KEYS)} FROM dsim.tb_mst_inventory_network_lane WHERE network_revision_id=$1 ORDER BY lane_id LIMIT 2001"


class PostgresNetworkSnapshotReader:
    def __init__(self, connection):
        self.connection = connection

    async def read_revision(self, revision_id: str) -> NetworkRevisionRecord | None:
        key = UUID(revision_id)
        async with self.connection.transaction(isolation="repeatable_read", readonly=True):
            await self.connection.execute("SET LOCAL statement_timeout = '10s'")
            row = await self.connection.fetchrow(HEADER_SQL, key)
            if row is None:
                return None
            header = {k: row[k] for k in HEADER_KEYS}
            header["network_revision_id"] = str(header["network_revision_id"])
            for field in ("effective_from", "effective_to"):
                if header[field] is not None:
                    header[field] = header[field].isoformat()
            nodes = [dict(r) for r in await self.connection.fetch(NODES_SQL, key)]
            lanes = [dict(r) for r in await self.connection.fetch(LANES_SQL, key)]
            evidence = row["approval_evidence"]
            return NetworkRevisionRecord(
                canonical_content=normalize_network(
                    {"header": header, "nodes": nodes, "lanes": lanes}
                ),
                stored_content_hash=row["content_sha256"],
                status=row["status"],
                row_version=row["row_version"],
                approval_evidence_json=evidence
                if isinstance(evidence, str)
                else canonical_json(evidence),
            )

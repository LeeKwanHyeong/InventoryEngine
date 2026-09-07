from typing import Protocol

from dsio_inventory_engine.inventory_contracts.network import NetworkRevisionRecord


class NetworkSnapshotReader(Protocol):
    async def read_revision(self, revision_id: str) -> NetworkRevisionRecord | None:
        """One consistent read of the exact revision; never resolve latest or use Neo4j."""
        ...

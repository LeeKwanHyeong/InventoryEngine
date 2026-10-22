"""Resource-enforced adapters; original V1/V2/V3 bytes and references stay unchanged."""

from dsio_inventory_engine.infrastructure.robustness_artifacts import LocalRobustnessArtifactStore
from dsio_inventory_engine.infrastructure.robustness_compression import (
    CompressedRobustnessArtifactStore,
)
from dsio_inventory_engine.infrastructure.robustness_shared_artifacts import (
    SharedInputRobustnessArtifactStore,
)
from dsio_inventory_engine.inventory_contracts.values import require


class _ResourceBoundStore:
    def __init__(self, root, *, resources):
        resources._require_store(root)
        super().__init__(root)
        self.resources = resources

    def _publish(self, name: str, payload: bytes):
        original_publish = super()._publish
        return self.resources.publish(
            self.root, name, payload, lambda: original_publish(name, payload)
        )


class ResourceBoundJsonStore(_ResourceBoundStore, LocalRobustnessArtifactStore):
    pass


class ResourceBoundCompressedStore(_ResourceBoundStore, CompressedRobustnessArtifactStore):
    pass


class ResourceBoundSharedStore(_ResourceBoundStore, SharedInputRobustnessArtifactStore):
    pass


def resource_store(resources, store_id: str, version="3.0.0"):
    classes = {
        "1.0.0": ResourceBoundJsonStore,
        "2.0.0": ResourceBoundCompressedStore,
        "3.0.0": ResourceBoundSharedStore,
    }
    require(version in classes, "RESOURCE_STORAGE_VERSION_INVALID")
    return classes[version](resources.store_root(store_id), resources=resources)

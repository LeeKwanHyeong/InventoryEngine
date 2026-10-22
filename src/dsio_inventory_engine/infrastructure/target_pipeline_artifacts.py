"""Target-pipeline records on the existing POSIX append-only publication primitive.

Checksums identify storage records, not new Run/selection/order-plan identities.
Use a private caller-owned root. This does not authenticate a hostile filesystem.
"""

from dsio_inventory_engine.infrastructure.robustness_artifacts import (
    MAX_BYTES,
    LocalRobustnessArtifactStore,
)
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    canonical_json,
    digest,
    hash_value,
    identifier,
    read_json,
    require,
)

KINDS = frozenset({"INPUT", "PART", "MANIFEST", "SLOT"})


def target_record_payload(kind, value):
    """Shared exact encoding for preflight and publication; existing bytes unchanged."""
    require(kind in KINDS, "TARGET_STORAGE_KIND_INVALID")
    body = {
        "contract_id": "inventory-target-pipeline-storage",
        "contract_version": "1.0.0",
        "kind": kind,
        "value": value,
    }
    return (canonical_json({**body, "content_hash": digest(body)}) + "\n").encode()


def target_record_reference(kind, value):
    payload = target_record_payload(kind, value)
    return _reference(
        {
            "kind": kind,
            "content_hash": digest(
                {
                    "contract_id": "inventory-target-pipeline-storage",
                    "contract_version": "1.0.0",
                    "kind": kind,
                    "value": value,
                }
            ),
            "byte_count": len(payload),
        }
    )


def _reference(value):
    require(
        type(value) is dict and set(value) == {"kind", "content_hash", "byte_count"},
        "TARGET_STORAGE_REFERENCE_INVALID",
    )
    require(type(value["kind"]) is str and value["kind"] in KINDS, "TARGET_STORAGE_KIND_INVALID")
    hash_value(value["content_hash"])
    require(
        type(value["byte_count"]) is int and 0 < value["byte_count"] <= MAX_BYTES,
        "TARGET_STORAGE_SIZE_LIMIT",
    )
    return dict(value)


class LocalTargetPipelineArtifactStore(LocalRobustnessArtifactStore):
    """Reuse no-follow bounded reads, fsync and exclusive hard-link publication."""

    reference = staticmethod(target_record_reference)

    def put(self, kind, value):
        ref = target_record_reference(kind, value)
        self._publish(
            "target-object-" + ref["content_hash"] + ".json", target_record_payload(kind, value)
        )
        return ref

    def read(self, reference):
        ref = _reference(reference)
        with self._directory() as directory:
            payload = self._read_at(directory, "target-object-" + ref["content_hash"] + ".json")
        require(len(payload) == ref["byte_count"], "TARGET_STORAGE_BYTE_COUNT_MISMATCH")
        value = read_json(payload.decode(), max_bytes=MAX_BYTES)
        require(
            set(value) == {"contract_id", "contract_version", "kind", "value", "content_hash"}
            and value["contract_id"] == "inventory-target-pipeline-storage"
            and value["contract_version"] == "1.0.0"
            and value["kind"] == ref["kind"],
            "TARGET_STORAGE_CONTRACT_INVALID",
        )
        require(
            value["content_hash"]
            == ref["content_hash"]
            == digest({k: v for k, v in value.items() if k != "content_hash"}),
            "TARGET_STORAGE_HASH_MISMATCH",
        )
        require((canonical_json(value) + "\n").encode() == payload, "TARGET_STORAGE_NONCANONICAL")
        return value["value"]

    @staticmethod
    def _target_slot(run_id, phase):
        identifier(run_id)
        require(phase in {"INPUT", "COMPLETE"}, "TARGET_STORAGE_PHASE_INVALID")
        return "target-slot-" + digest([run_id, phase]) + ".json"

    def bind(self, run_id, phase, reference):
        ref = _reference(reference)
        require(
            ref["kind"] == {"INPUT": "INPUT", "COMPLETE": "MANIFEST"}.get(phase),
            "TARGET_STORAGE_PHASE_INVALID",
        )
        self.read(ref)
        slot = self.put("SLOT", {"engine_run_id": run_id, "phase": phase, "reference": ref})
        self._publish(self._target_slot(run_id, phase), (canonical_json(slot) + "\n").encode())

    def lookup(self, run_id, phase):
        name = self._target_slot(run_id, phase)
        with self._directory() as directory:
            try:
                payload = self._read_at(directory, name)
            except InventoryInputError as error:
                if str(error) == "DURABLE_ARTIFACT_NOT_FOUND":
                    return None
                raise
        ref = _reference(read_json(payload.decode()))
        require(
            ref["kind"] == "SLOT" and (canonical_json(ref) + "\n").encode() == payload,
            "TARGET_STORAGE_SLOT_INVALID",
        )
        value = self.read(ref)
        require(
            type(value) is dict
            and set(value) == {"engine_run_id", "phase", "reference"}
            and value["engine_run_id"] == run_id
            and value["phase"] == phase,
            "TARGET_STORAGE_SLOT_MISMATCH",
        )
        result = _reference(value["reference"])
        require(
            result["kind"] == {"INPUT": "INPUT", "COMPLETE": "MANIFEST"}[phase],
            "TARGET_STORAGE_SLOT_INVALID",
        )
        return result

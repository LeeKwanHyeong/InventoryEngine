"""Detached Repository snapshots for read-only/tamper tests, never a Source authority."""

from copy import deepcopy

from dsio_inventory_engine.evaluate_replenishment.robustness.contracts import sealed
from dsio_inventory_engine.evaluate_replenishment.robustness.durable import _slot
from dsio_inventory_engine.inventory_contracts.values import canonical_json


class SnapshotRepository:
    def __init__(self, repository, receipt):
        self.slots, self.objects, self.reads = {}, {}, []
        execution = receipt["execution_id"]
        manifest = repository.read(receipt["manifest_reference"])
        refs = [
            receipt["input_reference"],
            receipt["manifest_reference"],
            manifest["header_reference"],
        ]
        refs += [e["reference"] for e in manifest["leaves"]]
        for ref in refs:
            self.objects[ref["content_hash"]] = repository.read(ref)
        self.slots[_slot(execution, "INPUT")] = receipt["input_reference"]
        self.slots[_slot(execution, "COMPLETE")] = receipt["manifest_reference"]
        for entry in manifest["leaves"]:
            self.slots[_slot(execution, "LEAF", entry["leaf_index"])] = entry["reference"]

    def read(self, ref):
        self.reads.append(ref["content_hash"])
        return deepcopy(self.objects[ref["content_hash"]])

    def lookup(self, key):
        return deepcopy(self.slots.get(key))

    def put(self, _):
        raise AssertionError("INSPECTION_MUST_NOT_WRITE")

    def bind(self, *_):
        raise AssertionError("INSPECTION_MUST_NOT_WRITE")


def replace_document(repository, value):
    value = sealed(value["contract_id"], {k: v for k, v in value.items() if k != "content_hash"})
    ref = {
        "contract_id": value["contract_id"],
        "content_hash": value["content_hash"],
        "byte_count": len((canonical_json(value) + "\n").encode()),
    }
    repository.objects[ref["content_hash"]] = value
    return ref


def replace_manifest(repository, manifest):
    repository.slots[_slot("HAND", "COMPLETE")] = replace_document(repository, manifest)

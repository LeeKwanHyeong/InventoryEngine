"""A flat, non-recursive dependency layout with exact bounded JSON reconstruction.

Only explicitly named input fields are extracted; PSI/actions/policy gates are
never summarized or replaced by a derived value. Chunks contain plain values,
not executable references. Null placeholders are interpreted only by a closed
path table outside the original document, avoiding reserved-marker collisions.
"""

from copy import deepcopy
from typing import Callable, Iterator

from dsio_inventory_engine.inventory_contracts.robustness_shared_inputs import (
    DOCUMENT_KIND,
    MAX_DEPTH,
    MAX_REFERENCES,
    MIN_SHARED_BYTES,
    SHARED_FIELDS,
    shared_reference,
)
from dsio_inventory_engine.inventory_contracts.robustness_storage import MAX_RAW_BYTES
from dsio_inventory_engine.inventory_contracts.values import canonical_json, require


def selected_values(value: object, path: tuple = ()) -> Iterator[tuple[tuple, object, bytes]]:
    require(len(path) <= MAX_DEPTH, "SHARED_PATH_DEPTH_LIMIT")
    if type(value) is dict:
        for key in sorted(value):
            child = value[key]
            if key in SHARED_FIELDS and type(child) in (dict, list):
                raw = canonical_json(child).encode()
                if len(raw) >= MIN_SHARED_BYTES:
                    yield path + (key,), child, raw
                    continue
            yield from selected_values(child, path + (key,))
    elif type(value) is list:
        for index, child in enumerate(value):
            yield from selected_values(child, path + (index,))


def location(document: dict, path: list) -> tuple[object, object]:
    require(
        type(path) is list
        and 0 < len(path) <= MAX_DEPTH
        and type(path[-1]) is str
        and path[-1] in SHARED_FIELDS,
        "SHARED_PATH_INVALID",
    )
    current = document
    for index, key in enumerate(path):
        require(
            (type(current) is dict and type(key) is str and key in current)
            or (type(current) is list and type(key) is int and 0 <= key < len(current)),
            "SHARED_PATH_INVALID",
        )
        if index == len(path) - 1:
            return current, key
        current = current[key]
    raise AssertionError("unreachable")


def pack_document(document: dict, persist: Callable[[object, bytes], dict]) -> dict:
    body, entries = deepcopy(document), []
    for path, value, raw in selected_values(document):
        require(len(entries) < MAX_REFERENCES, "SHARED_REFERENCE_COUNT_LIMIT")
        reference = shared_reference(persist(value, raw))
        parent, key = location(body, list(path))
        parent[key] = None
        entries.append({"path": list(path), "reference": reference})
    return {
        "contract_id": DOCUMENT_KIND,
        "contract_version": "1.0.0",
        "document": body,
        "shared_inputs": entries,
    }


def restore_document(layout: dict, load: Callable[[dict], object], *, expected_bytes: int) -> dict:
    require(
        type(layout) is dict
        and set(layout) == {"contract_id", "contract_version", "document", "shared_inputs"}
        and layout["contract_id"] == DOCUMENT_KIND
        and layout["contract_version"] == "1.0.0"
        and type(layout["document"]) is dict
        and type(layout["shared_inputs"]) is list,
        "SHARED_LAYOUT_INVALID",
    )
    require(len(layout["shared_inputs"]) <= MAX_REFERENCES, "SHARED_REFERENCE_COUNT_LIMIT")
    require(
        type(expected_bytes) is int and 0 < expected_bytes <= MAX_RAW_BYTES,
        "SHARED_STORAGE_SIZE_LIMIT",
    )
    # Reject fan-out amplification before reading chunks or expanding values.
    predicted = len((canonical_json(layout["document"]) + "\n").encode())
    seen, entries = set(), []
    for entry in layout["shared_inputs"]:
        require(type(entry) is dict and set(entry) == {"path", "reference"}, "SHARED_ENTRY_FIELDS")
        parent, key = location(layout["document"], entry["path"])
        require(parent[key] is None, "SHARED_PLACEHOLDER_INVALID")
        path = tuple(entry["path"])
        require(path not in seen, "SHARED_DUPLICATE_PATH")
        seen.add(path)
        ref = shared_reference(entry["reference"])
        predicted += ref["value_byte_count"] - 4  # null is exactly four UTF-8 bytes.
        require(predicted <= MAX_RAW_BYTES, "SHARED_EXPANSION_LIMIT")
        entries.append((entry["path"], ref))
    require(predicted == expected_bytes, "SHARED_EXPANDED_SIZE_MISMATCH")
    value, cached = deepcopy(layout["document"]), {}
    for path, ref in entries:
        if ref["value_hash"] in cached:
            original_ref, shared = cached[ref["value_hash"]]
            require(original_ref == ref, "SHARED_REFERENCE_CONFLICT")
        else:
            shared = load(ref)
            require(type(shared) in (dict, list), "SHARED_VALUE_TYPE_INVALID")
            cached[ref["value_hash"]] = (ref, shared)
        parent, key = location(value, path)
        parent[key] = deepcopy(shared)  # No mutable aliases between original observations.
    require(
        [list(path) for path, _, _ in selected_values(value)] == [path for path, _ in entries],
        "SHARED_LAYOUT_NONCANONICAL",
    )
    return value

"""Read all immutable artifacts without running Source/Catalog/Guard/PSI/metrics.

This verifies stored bytes, hashes, complete membership and bindings. A coherent
but incorrect stored computation can pass: ONLY actual replay establishes its
correctness. One leaf at a time is streamed into the original result hash.
"""

from hashlib import sha256

from dsio_inventory_engine.inventory_contracts.robustness_verification import verification_view
from dsio_inventory_engine.inventory_contracts.values import (
    canonical_json,
    digest,
    hash_value,
    identifier,
    require,
)
from .contracts import PROFILES, PROTOCOL_HASH, SAFETY, POLICY_FIELDS, ROW_FIELDS, input_errors


def _base(value, kind):
    require(
        type(value) is dict
        and value.get("contract_id") == kind
        and value.get("contract_version") == "1.0.0"
        and value.get("protocol_hash") == PROTOCOL_HASH
        and all(value.get(k) is False for k in SAFETY),
        "INTEGRITY_DOCUMENT_CONTRACT_INVALID",
    )
    hash_value(value.get("content_hash"))


def _sealed(value, kind):
    _base(value, kind)
    require(
        digest({k: v for k, v in value.items() if k != "content_hash"}) == value["content_hash"],
        "INTEGRITY_DOCUMENT_HASH_MISMATCH",
    )
    return value


def _same(actual, expected, code="INTEGRITY_BINDING_MISMATCH"):
    require(canonical_json(actual) == canonical_json(expected), code)


def _count(value, minimum=0, maximum=5720):
    require(type(value) is int and minimum <= value <= maximum, "INTEGRITY_COUNT_INVALID")


class InspectDurableItemCatalogUseCase:
    def __init__(self, repository):
        self.repository = repository

    def _read(self, ref, kind):
        value = _sealed(self.repository.read(ref), kind)
        require(value["content_hash"] == ref["content_hash"], "INTEGRITY_REFERENCE_MISMATCH")
        return value

    @input_errors
    def execute(
        self,
        execution_id: str,
        *,
        expected_input_hash: str,
        expected_result_hash: str | None = None,
    ) -> dict:
        from .durable import _slot

        identifier(execution_id)
        hash_value(expected_input_hash)
        if expected_result_hash is not None:
            hash_value(expected_result_hash)
        input_ref = self.repository.lookup(_slot(execution_id, "INPUT"))
        require(input_ref is not None, "DURABLE_INPUT_NOT_FOUND")
        require(input_ref["content_hash"] == expected_input_hash, "DURABLE_INPUT_BINDING_MISMATCH")
        complete_ref = self.repository.lookup(_slot(execution_id, "COMPLETE"))
        require(complete_ref is not None, "DURABLE_EXECUTION_INCOMPLETE")
        source = self._read(input_ref, "inventory-robustness-durable-input")
        manifest = self._read(complete_ref, "inventory-robustness-durable-manifest")
        header = self._read(manifest["header_reference"], "inventory-robustness-durable-header")
        _same(manifest["input_reference"], input_ref)
        require(manifest["execution_id"] == execution_id, "DURABLE_MANIFEST_BINDING_MISMATCH")
        root = header["result_header"]
        _base(root, "inventory-robustness-item-evaluation")
        require("leaves" not in root, "INTEGRITY_HEADER_CONTAINS_LEAVES")
        require(
            header["result_hash"] == manifest["result_hash"] == root["content_hash"],
            "DURABLE_RESULT_BINDING_MISMATCH",
        )
        if expected_result_hash is not None:
            require(root["content_hash"] == expected_result_hash, "DURABLE_RESULT_BINDING_MISMATCH")
        assembly = _sealed(source["assembly"], "inventory-robustness-item-assembly")
        require(
            source["assembly_hash"] == assembly["content_hash"],
            "INTEGRITY_ASSEMBLY_BINDING_MISMATCH",
        )
        _same(root["assembly"], assembly)
        _same(root["realization"], source["realization"])
        require(
            source["realization_hash"] == root["realization_hash"] == digest(source["realization"])
            and source["profile_id"] == root["profile_id"] in PROFILES,
            "INTEGRITY_REALIZATION_BINDING_MISMATCH",
        )
        candidates = assembly["complete_candidates"]
        require(
            type(candidates) is list and len(candidates) in (32, 88),
            "INTEGRITY_CANDIDATE_COUNT_INVALID",
        )
        ids = [c["candidate_id"] for c in candidates]
        require(len(set(ids)) == len(ids), "INTEGRITY_CANDIDATE_DUPLICATE")
        _same(manifest["expected_candidate_ids"], ids)
        require(
            manifest["assembly_hash"] == assembly["content_hash"]
            and manifest["parent_classification_hash"]
            == assembly["parent_classification"]["content_hash"]
            and manifest["item_projection_hash"] == assembly["item_projection"]["content_hash"]
            and manifest["complete_catalog_hash"] == assembly["candidate_catalog"]["content_hash"]
            and manifest["realization_hash"] == source["realization_hash"]
            and manifest["profile_id"] == source["profile_id"],
            "INTEGRITY_MANIFEST_BINDING_MISMATCH",
        )
        entries = manifest["leaves"]
        count = (len(candidates) + 15) // 16
        require(type(entries) is list and len(entries) == count, "DURABLE_MANIFEST_INCOMPLETE")
        self._header_bindings(root, source, assembly, candidates)
        counts = {
            "candidate_count": 0,
            "successful_count": 0,
            "failed_count": 0,
            "psi_row_count": 0,
            "leaf_count": count,
        }
        leaf_hashes, child_hashes = [], []

        def leaves():
            from .durable import _entry

            for index, entry in enumerate(entries):
                _count(entry["leaf_index"], 0, 5)
                require(entry["leaf_index"] == index, "DURABLE_MANIFEST_LEAF_ORDER_INVALID")
                _same(
                    self.repository.lookup(_slot(execution_id, "LEAF", index)),
                    entry["reference"],
                    "DURABLE_CHECKPOINT_BINDING_MISMATCH",
                )
                leaf = self._read(entry["reference"], "inventory-robustness-item-leaf")
                self._leaf(leaf, index, assembly, source, candidates[index * 16 : (index + 1) * 16])
                for offset, child in enumerate(leaf["candidate_executions"], index * 16):
                    self._child_links(
                        child,
                        root["evaluator_bank_fragment"]["traces"][offset],
                        root["path_scorecards"][offset],
                        root,
                        assembly,
                    )
                computed = _entry(leaf, entry["reference"])
                for k in ("candidate_count", "successful_count", "failed_count", "psi_row_count"):
                    _count(entry[k])
                    counts[k] += computed[k]
                _same(entry, computed, "INTEGRITY_ENTRY_COUNT_OR_HASH_MISMATCH")
                leaf_hashes.append(leaf["content_hash"])
                child_hashes.extend(c["content_hash"] for c in leaf["candidate_executions"])
                yield leaf
                del leaf

        require(
            self._result_hash(root, leaves()) == root["content_hash"],
            "INTEGRITY_RESULT_HASH_MISMATCH",
        )
        for k, v in counts.items():
            _count(manifest[k])
            require(manifest[k] == v, "INTEGRITY_MANIFEST_COUNTS_MISMATCH")
        provenance = _sealed(root["source_manifest"], "inventory-robustness-item-source-manifest")
        _same(provenance["leaf_hashes"], leaf_hashes)
        _same(provenance["candidate_execution_hashes"], child_hashes)
        for k in (
            "assembly_hash",
            "parent_classification_hash",
            "item_projection_hash",
            "complete_catalog_hash",
            "realization_hash",
        ):
            require(provenance[k] == manifest[k], "INTEGRITY_PROVENANCE_MISMATCH")
        _same(
            self.repository.lookup(_slot(execution_id, "INPUT")),
            input_ref,
            "INTEGRITY_POINTER_CHANGED",
        )
        _same(
            self.repository.lookup(_slot(execution_id, "COMPLETE")),
            complete_ref,
            "INTEGRITY_POINTER_CHANGED",
        )
        return verification_view(
            level="ARTIFACT_INTEGRITY",
            execution_id=execution_id,
            input_hash=expected_input_hash,
            manifest_hash=complete_ref["content_hash"],
            result_hash=root["content_hash"],
            context=assembly["candidate_recommendation"]["canonical_input"]["context"],
            counts=counts,
        )

    @staticmethod
    def _header_bindings(root, source, assembly, candidates):
        bank = root["evaluator_bank_fragment"]
        require(
            type(bank["traces"]) is list
            and len(bank["traces"]) == len(candidates)
            and type(root["path_scorecards"]) is list
            and len(root["path_scorecards"]) == len(candidates),
            "INTEGRITY_EVALUATION_COVERAGE_INVALID",
        )
        _same(bank["binding"], assembly["evaluation_binding"])
        _same(bank["candidates"], [{k: c[k] for k in POLICY_FIELDS} for c in candidates])
        path = {
            **{k: source["realization"][k] for k in ("path_id", "split", "seed")},
            "path_content_hash": source["realization_hash"],
        }
        _same(bank["expected_paths"], [path])

    @staticmethod
    def _child_links(child, trace, scorecard, root, assembly):
        # These are stored record bindings/projections, not re-computed service,
        # cost, roll-forward, strategy authenticity or business authorization.
        for name, value in (
            ("candidate", {k: child["candidate"][k] for k in POLICY_FIELDS}),
            ("binding", assembly["evaluation_binding"]),
            ("source_result_hash", root["source_manifest"]["content_hash"]),
            ("status", child["status"]),
            ("failure_reason", child["failure_reason"]),
            ("path", root["evaluator_bank_fragment"]["expected_paths"][0]),
        ):
            _same(trace[name], value)
        causal = child["causal_result"]
        _same(
            trace["rows"],
            [{k: r[k] for k in ROW_FIELDS} for r in causal["psi_rows"]] if causal else None,
        )
        _same(
            trace["orders"],
            [
                {k: r[k] for k in ("order_id", "order_date", "receipt_date", "quantity")}
                for r in causal["recommended_orders"]
            ]
            if causal
            else None,
        )
        _same(
            trace["initial_receipts"],
            [
                {k: r[k] for k in ("receipt_id", "receipt_date", "quantity")}
                for r in assembly["development_source"]["w0_due_in"]
            ],
        )
        _sealed(scorecard, "inventory-robustness-path-scorecard")
        require(
            scorecard["input_trace_hash"] == digest(trace), "INTEGRITY_SCORECARD_TRACE_MISMATCH"
        )
        for name in (
            "candidate",
            "binding",
            "path",
            "source_result_hash",
            "status",
            "failure_reason",
        ):
            _same(scorecard[name], trace[name])
        require(
            scorecard["profile_id"] == root["profile_id"], "INTEGRITY_SCORECARD_PROFILE_MISMATCH"
        )

    @staticmethod
    def _leaf(leaf, index, assembly, source, expected_candidates):
        _count(leaf["leaf_index"], 0, 5)
        require(
            leaf["leaf_index"] == index
            and leaf["assembly_hash"] == assembly["content_hash"]
            and leaf["realization_hash"] == source["realization_hash"]
            and type(leaf["complete_candidate_count"]) is int
            and leaf["complete_candidate_count"] == len(assembly["complete_candidates"]),
            "INTEGRITY_LEAF_BINDING_MISMATCH",
        )
        children = leaf["candidate_executions"]
        require(
            type(children) is list and len(children) == len(expected_candidates),
            "INTEGRITY_LEAF_COVERAGE_INVALID",
        )
        _same(
            [c["candidate"] for c in children],
            expected_candidates,
            "INTEGRITY_CANDIDATE_MEMBERSHIP_MISMATCH",
        )
        _same(leaf["candidate_ids"], [c["candidate_id"] for c in expected_candidates])
        for child in children:
            _sealed(child, "inventory-robustness-candidate-execution")
            require(child["status"] in ("SUCCEEDED", "FAILED"), "INTEGRITY_CHILD_STATUS_INVALID")
            if child["status"] == "FAILED":
                require(
                    child["causal_result"] is None and type(child["failure_reason"]) is str,
                    "INTEGRITY_FAILED_CHILD_INVALID",
                )
                continue
            causal = _sealed(child["causal_result"], "inventory-robustness-causal-psi")
            require(
                child["failure_reason"] is None
                and type(causal["psi_rows"]) is list
                and len(causal["psi_rows"]) == 65
                and len(causal["decision_evidence"]) == 65,
                "INTEGRITY_PSI_COVERAGE_INVALID",
            )
            canonical = assembly["candidate_recommendation"]["canonical_input"]
            _same(causal["context"], canonical["context"])
            require(
                causal["development_source_hash"] == assembly["development_source"]["content_hash"],
                "INTEGRITY_SOURCE_BINDING_MISMATCH",
            )
            calendar = sorted(canonical["snapshots"]["calendar"]["rows"], key=lambda r: r["seq"])
            fields = ("seq", "yyyyww", "start_date", "end_date")
            _same(
                [{k: r[k] for k in fields} for r in causal["psi_rows"]],
                [{k: r[k] for k in fields} for r in calendar],
                "INTEGRITY_PSI_BUCKET_COVERAGE_INVALID",
            )

    @staticmethod
    def _result_hash(header: dict, leaves) -> str:
        # Same canonical UTF-8 hash as digest(full_result_without_content_hash),
        # without collecting or serializing the >128MB 88-candidate parent at once.
        result = sha256()

        def add(value):
            # C JSON encoder, bounded by one stored leaf/header field; never
            # serialize the full multi-leaf parent into one giant string.
            result.update(canonical_json(value).encode())

        result.update(b"{")
        for position, key in enumerate(sorted((set(header) - {"content_hash"}) | {"leaves"})):
            if position:
                result.update(b",")
            add(key)
            result.update(b":")
            if key == "leaves":
                result.update(b"[")
                for index, leaf in enumerate(leaves):
                    if index:
                        result.update(b",")
                    add(leaf)
                    del leaf
                result.update(b"]")
            else:
                add(header[key])
        result.update(b"}")
        return result.hexdigest()

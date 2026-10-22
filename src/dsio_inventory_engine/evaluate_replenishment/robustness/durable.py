"""Local durable one-item execution: immutable input -> leaves -> manifest -> commit.

No database, latest-Source lookup or new path generation. A completed parent is
visible only after every candidate is persisted. Existing calculation documents
and hashes remain unchanged; a separate receipt acknowledges local durability.
"""

from typing import Protocol

from dsio_inventory_engine.inventory_contracts.values import (
    canonical_json,
    hash_value,
    identifier,
    require,
)
from .contracts import clone, input_errors, sealed
from .item_execution import RunItemCatalogUseCase
from .item_execution_steps import (
    ItemExecutionContext,
    prepare_context,
    execute_leaf,
    verify_leaf,
    finish_result,
)
from .source_binding import RealizedDemandPath


class RobustnessArtifactRepository(Protocol):
    def put(self, document: dict) -> dict: ...
    def read(self, reference: dict) -> dict: ...
    def bind(self, key: str, reference: dict) -> None: ...
    def lookup(self, key: str) -> dict | None: ...


def _slot(execution_id: str, kind: str, index: int | None = None) -> str:
    identifier(execution_id)
    return (
        "ROBUSTNESS:" + execution_id + ":" + kind + (":" + str(index) if index is not None else "")
    )


def _input(context: ItemExecutionContext) -> dict:
    return sealed(
        "inventory-robustness-durable-input",
        {
            "assembly": context.assembly,
            "assembly_hash": context.assembly["content_hash"],
            "realization": context.external.to_dict(),
            "realization_hash": context.external.content_hash,
            "profile_id": context.profile_id,
        },
    )


def _header(result: dict) -> dict:
    return sealed(
        "inventory-robustness-durable-header",
        {
            "result_hash": result["content_hash"],
            "result_header": {k: v for k, v in result.items() if k != "leaves"},
        },
    )


def _entry(leaf: dict, reference: dict) -> dict:
    children = leaf["candidate_executions"]
    return {
        "leaf_index": leaf["leaf_index"],
        "reference": reference,
        "candidate_ids": leaf["candidate_ids"],
        "candidate_count": len(children),
        "candidate_execution_hashes": [c["content_hash"] for c in children],
        "successful_count": sum(c["status"] == "SUCCEEDED" for c in children),
        "failed_count": sum(c["status"] == "FAILED" for c in children),
        "psi_row_count": sum(
            len(c["causal_result"]["psi_rows"]) for c in children if c["status"] == "SUCCEEDED"
        ),
    }


def _manifest(
    execution_id: str, input_ref: dict, header_ref: dict, result: dict, references: list[dict]
) -> dict:
    entries = [_entry(leaf, ref) for leaf, ref in zip(result["leaves"], references, strict=True)]
    return sealed(
        "inventory-robustness-durable-manifest",
        {
            "execution_id": execution_id,
            "input_reference": input_ref,
            "header_reference": header_ref,
            "assembly_hash": result["assembly"]["content_hash"],
            "parent_classification_hash": result["assembly"]["parent_classification"][
                "content_hash"
            ],
            "item_projection_hash": result["assembly"]["item_projection"]["content_hash"],
            "complete_catalog_hash": result["assembly"]["candidate_catalog"]["content_hash"],
            "realization_hash": result["realization_hash"],
            "profile_id": result["profile_id"],
            "expected_candidate_ids": [
                r["candidate_id"] for r in result["assembly"]["complete_candidates"]
            ],
            "candidate_count": sum(e["candidate_count"] for e in entries),
            "successful_count": sum(e["successful_count"] for e in entries),
            "failed_count": sum(e["failed_count"] for e in entries),
            "psi_row_count": sum(e["psi_row_count"] for e in entries),
            "leaf_count": len(entries),
            "leaves": entries,
            "result_hash": result["content_hash"],
            "durable_local_artifact": True,
        },
    )


def _same(actual: dict, expected: dict, code: str) -> None:
    require(canonical_json(actual) == canonical_json(expected), code)


class RunDurableItemCatalogUseCase:
    def __init__(
        self, runner: RunItemCatalogUseCase, repository: RobustnessArtifactRepository
    ) -> None:
        self.runner, self.repository = runner, repository

    def _context(self, document: dict, expected_input_hash: str) -> ItemExecutionContext:
        hash_value(expected_input_hash)
        value = clone(document)
        require(value["content_hash"] == expected_input_hash, "DURABLE_INPUT_BINDING_MISMATCH")
        context = prepare_context(
            self.runner.preparer,
            self.runner.deployment,
            value["assembly"],
            expected_assembly_hash=value["assembly_hash"],
            realization=RealizedDemandPath.from_dict(value["realization"]),
            expected_realization_hash=value["realization_hash"],
            profile_id=value["profile_id"],
            reuse_preparation=self.runner.reuse_preparation,
        )
        _same(value, _input(context), "DURABLE_INPUT_REPLAY_MISMATCH")
        return context

    def _bound_input(
        self, execution_id: str, expected_input_hash: str
    ) -> tuple[dict, ItemExecutionContext]:
        hash_value(expected_input_hash)
        reference = self.repository.lookup(_slot(execution_id, "INPUT"))
        require(reference is not None, "DURABLE_INPUT_NOT_FOUND")
        require(reference["content_hash"] == expected_input_hash, "DURABLE_INPUT_BINDING_MISMATCH")
        return reference, self._context(self.repository.read(reference), expected_input_hash)

    @input_errors
    def run(
        self,
        execution_id: str,
        assembly: dict,
        *,
        expected_assembly_hash: str,
        realization: RealizedDemandPath,
        expected_realization_hash: str,
        profile_id: str = "PRICE_BASE",
    ) -> dict:
        identifier(execution_id)
        context = prepare_context(
            self.runner.preparer,
            self.runner.deployment,
            assembly,
            expected_assembly_hash=expected_assembly_hash,
            realization=realization,
            expected_realization_hash=expected_realization_hash,
            profile_id=profile_id,
            reuse_preparation=self.runner.reuse_preparation,
        )
        reference = self.repository.put(_input(context))
        self.repository.bind(_slot(execution_id, "INPUT"), reference)
        return self._resume(execution_id, reference, context)

    @input_errors
    def recover(self, execution_id: str, *, expected_input_hash: str) -> dict:
        """Use only persisted input; no assembly, live Source or latest lookup required."""
        reference, context = self._bound_input(execution_id, expected_input_hash)
        return self._resume(execution_id, reference, context)

    def _persist_leaf(self, execution_id: str, index: int, leaf: dict) -> dict:
        reference = self.repository.put(leaf)
        self.repository.bind(_slot(execution_id, "LEAF", index), reference)
        return reference

    def _resume(
        self, execution_id: str, input_reference: dict, context: ItemExecutionContext
    ) -> dict:
        completed = self.repository.lookup(_slot(execution_id, "COMPLETE"))
        if completed is not None:
            _, receipt = self._load(execution_id, input_reference, context, completed)
            return receipt
        leaves = []
        for index in range(context.leaf_count):
            reference = self.repository.lookup(_slot(execution_id, "LEAF", index))
            if reference is None:
                leaf = execute_leaf(context, index)
                self._persist_leaf(execution_id, index, leaf)
            else:
                leaf = verify_leaf(
                    context,
                    self.repository.read(reference),
                    index=index,
                    expected_hash=reference["content_hash"],
                )
            leaves.append(leaf)
        return self._complete(execution_id, input_reference, finish_result(context, leaves))

    def _complete(self, execution_id: str, input_reference: dict, result: dict) -> dict:
        references = [
            self._persist_leaf(execution_id, i, leaf) for i, leaf in enumerate(result["leaves"])
        ]
        header_reference = self.repository.put(_header(result))
        manifest = _manifest(execution_id, input_reference, header_reference, result, references)
        manifest_reference = self.repository.put(manifest)
        self.repository.bind(_slot(execution_id, "COMPLETE"), manifest_reference)
        return self._receipt(
            execution_id, input_reference, manifest_reference, result["content_hash"]
        )

    @staticmethod
    def _receipt(
        execution_id: str, input_reference: dict, manifest_reference: dict, result_hash: str
    ) -> dict:
        return sealed(
            "inventory-robustness-durable-receipt",
            {
                "execution_id": execution_id,
                "input_reference": input_reference,
                "manifest_reference": manifest_reference,
                "result_hash": result_hash,
                "status": "COMPLETE",
                "durable_local_artifact": True,
                "official_paths_generated": False,
            },
        )

    @input_errors
    def write(
        self,
        execution_id: str,
        result: dict,
        *,
        expected_result_hash: str,
        expected_assembly_hash: str,
        expected_realization_hash: str,
    ) -> dict:
        """Publish or rehydrate an already computed result, with full real replay first."""
        identifier(execution_id)
        self.runner.verify(
            result,
            expected_result_hash=expected_result_hash,
            expected_assembly_hash=expected_assembly_hash,
            expected_realization_hash=expected_realization_hash,
        )
        context = prepare_context(
            self.runner.preparer,
            self.runner.deployment,
            result["assembly"],
            expected_assembly_hash=expected_assembly_hash,
            realization=RealizedDemandPath.from_dict(result["realization"]),
            expected_realization_hash=expected_realization_hash,
            profile_id=result["profile_id"],
        )
        reference = self.repository.put(_input(context))
        self.repository.bind(_slot(execution_id, "INPUT"), reference)
        return self._complete(execution_id, reference, result)

    def _load(
        self,
        execution_id: str,
        input_reference: dict,
        context: ItemExecutionContext,
        manifest_reference: dict,
    ) -> tuple[dict, dict]:
        manifest = self.repository.read(manifest_reference)
        require(
            manifest["contract_id"] == "inventory-robustness-durable-manifest"
            and type(manifest["leaves"]) is list
            and len(manifest["leaves"]) == context.leaf_count,
            "DURABLE_MANIFEST_INCOMPLETE",
        )
        require(
            manifest["execution_id"] == execution_id
            and manifest["input_reference"] == input_reference,
            "DURABLE_MANIFEST_BINDING_MISMATCH",
        )
        leaves, references = [], []
        for index, entry in enumerate(manifest["leaves"]):
            require(entry["leaf_index"] == index, "DURABLE_MANIFEST_LEAF_ORDER_INVALID")
            reference = entry["reference"]
            require(
                self.repository.lookup(_slot(execution_id, "LEAF", index)) == reference,
                "DURABLE_CHECKPOINT_BINDING_MISMATCH",
            )
            leaves.append(
                verify_leaf(
                    context,
                    self.repository.read(reference),
                    index=index,
                    expected_hash=reference["content_hash"],
                )
            )
            references.append(reference)
        result = finish_result(context, leaves)
        header = self.repository.read(manifest["header_reference"])
        _same(header, _header(result), "DURABLE_HEADER_REPLAY_MISMATCH")
        _same(
            manifest,
            _manifest(
                execution_id, input_reference, manifest["header_reference"], result, references
            ),
            "DURABLE_MANIFEST_REPLAY_MISMATCH",
        )
        return result, self._receipt(
            execution_id, input_reference, manifest_reference, result["content_hash"]
        )

    @input_errors
    def read(
        self,
        execution_id: str,
        *,
        expected_input_hash: str,
        expected_result_hash: str | None = None,
    ) -> dict:
        reference, context = self._bound_input(execution_id, expected_input_hash)
        completed = self.repository.lookup(_slot(execution_id, "COMPLETE"))
        require(completed is not None, "DURABLE_EXECUTION_INCOMPLETE")
        result, _ = self._load(execution_id, reference, context, completed)
        if expected_result_hash is not None:
            hash_value(expected_result_hash)
            require(
                result["content_hash"] == expected_result_hash, "DURABLE_RESULT_BINDING_MISMATCH"
            )
        return result

    @input_errors
    def inspect(
        self,
        execution_id: str,
        *,
        expected_input_hash: str,
        expected_result_hash: str | None = None,
    ) -> dict:
        """Read-time artifact integrity only; no Source or PSI recalculation."""
        from .integrity_read import InspectDurableItemCatalogUseCase

        return InspectDurableItemCatalogUseCase(self.repository).execute(
            execution_id,
            expected_input_hash=expected_input_hash,
            expected_result_hash=expected_result_hash,
        )

    @input_errors
    def verify_recalculation(
        self,
        execution_id: str,
        *,
        expected_input_hash: str,
        expected_result_hash: str,
    ) -> dict:
        """Only an actual, complete replay can produce the stronger read-time view.

        Does not accept a previous view as proof, persist a verification flag,
        change the original result or grant operational publication permission.
        """
        from dsio_inventory_engine.inventory_contracts.robustness_verification import (
            verification_view,
        )

        before = self.inspect(
            execution_id,
            expected_input_hash=expected_input_hash,
            expected_result_hash=expected_result_hash,
        )
        self.read(
            execution_id,
            expected_input_hash=expected_input_hash,
            expected_result_hash=expected_result_hash,
        )
        after = self.inspect(
            execution_id,
            expected_input_hash=expected_input_hash,
            expected_result_hash=expected_result_hash,
        )
        _same(before, after, "DURABLE_VERIFICATION_BINDING_CHANGED")
        return verification_view(
            level="FULL_PSI_RECALCULATION",
            execution_id=execution_id,
            input_hash=after["input_hash"],
            manifest_hash=after["manifest_hash"],
            result_hash=after["result_hash"],
            context=after["context"],
            counts=after["counts"],
        )

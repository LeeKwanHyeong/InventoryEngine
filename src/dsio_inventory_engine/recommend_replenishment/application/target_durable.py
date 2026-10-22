"""Calculation-only input -> parts -> manifest -> complete, with pinned-DLL replay.

Recovery recomputes the full pipeline from sealed local input. It does not resume
the numerical loop or trust a partially stored candidate as a completed result.
"""

import copy

from dsio_inventory_engine.classify_inventory.candidate_catalog import (
    CandidateCatalogProducer,
    OfflineCandidateCatalog,
)
from dsio_inventory_engine.classify_inventory.legacy_rules import LegacyStyleRules
from dsio_inventory_engine.classify_inventory.legacy_style import LegacyFactorSegmentation
from dsio_inventory_engine.inventory_contracts.calculation_boundary import (
    CalculationBoundaryBinding,
)
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.replenishment import RecommendationRequest
from dsio_inventory_engine.inventory_contracts.target_result_parts import (
    LIST_FIELDS,
    MAX_ARTIFACT_BYTES,
    MAX_RAW_RESULT_BYTES,
    result_parts as _parts,
)
from dsio_inventory_engine.inventory_contracts.values import (
    digest,
    identifier,
    require,
)
from .target_execution import RunTargetPolicyPipelineUseCase, _TargetNumerics

MAX_RESULT_BYTES = MAX_RAW_RESULT_BYTES


def _counts(result):
    search, plan = result["search_result"], result["order_plan"]
    children = search["candidate_evaluations"]
    keys = [(c["candidate"]["item_id"], c["candidate"]["candidate_id"]) for c in children]
    require(len(keys) == len(set(keys)), "TARGET_STORAGE_DUPLICATE_CANDIDATE")
    return {
        "candidate_count": len(children),
        "candidate_hashes": [c["content_hash"] for c in children],
        "candidate_keys": [list(k) for k in keys],
        "candidate_psi_row_count": sum(len(c["psi_rows"] or []) for c in children),
        "selection_count": len(result["selection_result"]["item_selections"]),
        "selected_item_count": plan["selected_item_count"],
        "not_selected_item_count": plan["not_selected_item_count"],
        "baseline_row_count": len(plan["baseline_psi_rows"]),
        "recommended_row_count": len(plan["psi_rows"]),
        "decision_count": len(plan["decision_evidence"]),
        "order_count": len(plan["recommended_orders"]),
        "search_content_hash": search["content_hash"],
        "selection_content_hash": result["selection_result"]["content_hash"],
        **{
            k: plan[k] for k in ("psi_content_hash", "decision_content_hash", "orders_content_hash")
        },
    }


class RunDurableTargetPolicyPipelineUseCase:
    """No DB/latest lookup, new business identity, automatic publication or fallback."""

    def __init__(self, deployment, kernel, repository):
        self.deployment, self.kernel, self.repository = deployment, kernel, repository

    def _restore(self, inputs):
        require(
            type(inputs) is dict
            and set(inputs)
            == {
                "request",
                "binding",
                "config_values",
                "classification_snapshot",
                "catalog",
                "expected_catalog_hash",
                "catalog_recipe",
            },
            "TARGET_STORAGE_INPUT_INVALID",
        )
        request = MathematicalPolicyRequest.from_dict(inputs["request"])
        binding = CalculationBoundaryBinding.from_dict(inputs["binding"])
        # Same canonical projection, gates, environment and actual installed DLL as execution.
        _TargetNumerics(
            self.deployment,
            self.kernel,
            RecommendationRequest.from_dict(inputs["request"]["recommendation"]),
            binding,
        )
        producer, catalog = None, None
        if inputs["catalog"] is not None:
            recipe = inputs["catalog_recipe"]
            require(
                type(recipe) is dict
                and set(recipe) == {"rules", "reference_calculator_build_hash"},
                "TARGET_STORAGE_RECIPE_INVALID",
            )
            rules = LegacyStyleRules.from_document(recipe["rules"])
            producer = CandidateCatalogProducer(
                LegacyFactorSegmentation(
                    rules, reference_calculator_build_hash=recipe["reference_calculator_build_hash"]
                )
            )
            catalog = producer.verify(
                OfflineCandidateCatalog.from_dict(inputs["catalog"]),
                expected_catalog_hash=inputs["expected_catalog_hash"],
            )
        else:
            require(
                inputs["catalog_recipe"] is None and inputs["expected_catalog_hash"] is None,
                "TARGET_STORAGE_RECIPE_INVALID",
            )
        runner = RunTargetPolicyPipelineUseCase(self.deployment, self.kernel, producer)
        return (
            runner,
            request,
            binding,
            {
                "config_values": copy.deepcopy(inputs["config_values"]),
                "classification_snapshot": copy.deepcopy(inputs["classification_snapshot"]),
                "catalog": catalog,
                "expected_catalog_hash": inputs["expected_catalog_hash"],
            },
        )

    def _input(
        self,
        request,
        binding,
        *,
        config_values,
        classification_snapshot,
        catalog=None,
        expected_catalog_hash=None,
        producer=None,
    ):
        recipe = None
        if catalog is not None:
            require(producer is not None, "TARGET_STORAGE_PRODUCER_REQUIRED")
            rules = producer.classifier.rules
            recipe = {
                "rules": {
                    "lifecycle_status": "DRAFT",
                    "profile_content_hash": rules.content_hash,
                    "profile": rules.to_dict(),
                },
                "reference_calculator_build_hash": producer.classifier.producer.reference_calculator_build_hash,
            }
        else:
            require(producer is None, "TARGET_STORAGE_UNEXPECTED_PRODUCER")
        inputs = {
            "request": request.to_dict(),
            "binding": binding.to_dict(),
            "config_values": copy.deepcopy(config_values),
            "classification_snapshot": copy.deepcopy(classification_snapshot),
            "catalog": catalog.to_dict() if catalog is not None else None,
            "expected_catalog_hash": expected_catalog_hash,
            "catalog_recipe": recipe,
        }
        self._restore(inputs)
        return inputs

    @staticmethod
    def _run_id(inputs):
        return identifier(inputs["binding"]["engine_run_id"])

    def _bind_input(self, inputs):
        ref = self.repository.put("INPUT", inputs)
        self.repository.bind(self._run_id(inputs), "INPUT", ref)
        return ref

    def execute(self, request, binding, **sources):
        inputs = self._input(request, binding, **sources)
        ref = self._bind_input(inputs)  # durable before the first numerical call
        return self._execute(inputs, ref)

    def seal_input(self, request, binding, **sources):
        """Prepare an exact INPUT reference without executing any numerical transition."""
        return self._bind_input(self._input(request, binding, **sources))

    def read_input(self, reference):
        require(
            type(reference) is dict and reference.get("kind") == "INPUT",
            "TARGET_STORAGE_INPUT_REFERENCE_INVALID",
        )
        inputs = self.repository.read(reference)
        self._restore(inputs)
        require(
            self.repository.lookup(self._run_id(inputs), "INPUT") == reference,
            "TARGET_STORAGE_EXPECTED_INPUT_MISMATCH",
        )
        return copy.deepcopy(inputs)

    def _execute(self, inputs, input_ref):
        runner, request, binding, sources = self._restore(inputs)
        result = runner.execute(request, binding, **sources)
        return self._publish(inputs, input_ref, result)

    def persist(self, result, request, binding, **sources):
        """External View must pass full same-Build replay before any result publication."""
        result = copy.deepcopy(result)
        inputs = self._input(request, binding, **sources)
        runner, request, binding, restored = self._restore(inputs)
        runner.verify_result(result, request, binding, **restored)
        ref = self._bind_input(inputs)
        return self._publish(inputs, ref, result)

    def _publish(self, inputs, input_ref, result):
        # Preflight exact existing record bytes, without publishing any PART.
        entries = []
        total = input_ref["byte_count"]
        for role, value, index in _parts(result):
            part = {"role": role, "index": index, "value": value}
            ref = self.repository.reference("PART", part)
            total += ref["byte_count"]
            require(total <= MAX_RESULT_BYTES, "TARGET_STORAGE_RESULT_SIZE_LIMIT")
            entries.append(
                {
                    "role": role,
                    "index": index,
                    "row_count": len(value) if index is not None else None,
                    "reference": ref,
                }
            )
        manifest = {
            "engine_run_id": self._run_id(inputs),
            "input_reference": input_ref,
            "counts": _counts(result),
            "parts": entries,
        }
        manifest_ref = self.repository.reference("MANIFEST", manifest)
        require(
            total + manifest_ref["byte_count"] <= MAX_RESULT_BYTES,
            "TARGET_STORAGE_RESULT_SIZE_LIMIT",
        )
        for (role, value, index), entry in zip(_parts(result), entries, strict=True):
            ref = self.repository.put("PART", {"role": role, "index": index, "value": value})
            require(ref == entry["reference"], "TARGET_STORAGE_PART_BINDING_MISMATCH")
        ref = self.repository.put("MANIFEST", manifest)
        require(ref == manifest_ref, "TARGET_STORAGE_MANIFEST_BINDING_MISMATCH")
        # Verify every persisted part before making the complete set visible.
        restored, _ = self._read_manifest(manifest)
        require(restored == result, "TARGET_STORAGE_RESTORE_MISMATCH")
        self.repository.bind(self._run_id(inputs), "COMPLETE", ref)
        return self._view(result, manifest, "RECOMPUTED_VERIFIED")

    @staticmethod
    def _view(result, manifest, status):
        return {
            "verification_status": status,
            "input_reference": manifest["input_reference"],
            "counts": manifest["counts"],
            "result": result,
            "durable_local_artifact": True,
            "automatic_publish_allowed": False,
            "automatic_order_allowed": False,
            "database_writes": False,
        }

    def _read_manifest(self, manifest):
        require(
            type(manifest) is dict
            and set(manifest) == {"engine_run_id", "input_reference", "counts", "parts"},
            "TARGET_STORAGE_MANIFEST_INVALID",
        )
        require(
            manifest["input_reference"].get("kind") == "INPUT" and type(manifest["parts"]) is list,
            "TARGET_STORAGE_MANIFEST_INVALID",
        )
        # Reject oversized aggregate references before reading or assembling rows.
        total = self.repository.reference("MANIFEST", manifest)["byte_count"]
        for ref in [
            manifest["input_reference"],
            *(e.get("reference") for e in manifest["parts"] if type(e) is dict),
        ]:
            require(
                type(ref) is dict
                and type(ref.get("byte_count")) is int
                and 0 < ref["byte_count"] <= MAX_ARTIFACT_BYTES,
                "TARGET_STORAGE_SIZE_LIMIT",
            )
            total += ref["byte_count"]
            require(total <= MAX_RESULT_BYTES, "TARGET_STORAGE_RESULT_SIZE_LIMIT")
        inputs = self.repository.read(manifest["input_reference"])
        self._restore(inputs)
        require(self._run_id(inputs) == manifest["engine_run_id"], "TARGET_STORAGE_RUN_MISMATCH")
        values = {}
        for entry in manifest["parts"]:
            require(
                type(entry) is dict and set(entry) == {"role", "index", "row_count", "reference"},
                "TARGET_STORAGE_PART_INVALID",
            )
            require(entry["reference"]["kind"] == "PART", "TARGET_STORAGE_PART_INVALID")
            part = self.repository.read(entry["reference"])
            require(
                type(part) is dict
                and set(part) == {"role", "index", "value"}
                and part["role"] == entry["role"]
                and part["index"] == entry["index"],
                "TARGET_STORAGE_PART_BINDING_MISMATCH",
            )
            key = (part["role"], part["index"])
            require(
                type(part["role"]) is str
                and (part["index"] is None or type(part["index"]) is int and part["index"] >= 0),
                "TARGET_STORAGE_PART_INVALID",
            )
            require(key not in values, "TARGET_STORAGE_DUPLICATE_PART")
            require(
                (
                    entry["row_count"] is None
                    if part["index"] is None
                    else type(entry["row_count"]) is int
                    and entry["row_count"] == len(part["value"])
                ),
                "TARGET_STORAGE_ROW_COUNT_MISMATCH",
            )
            values[key] = part["value"]
        try:
            result = dict(values[("evidence", None)])
            result["selection_result"] = values[("selection_result", None)]
            for section, fields in LIST_FIELDS.items():
                result[section] = dict(values[(section, None)])
                for field in fields:
                    rows = []
                    batch = 1 if field == "candidate_evaluations" else 128
                    while (section + "." + field, len(rows)) in values:
                        chunk = values[(section + "." + field, len(rows))]
                        require(
                            type(chunk) is list and 0 < len(chunk) <= batch,
                            "TARGET_STORAGE_CHUNK_INVALID",
                        )
                        rows.extend(chunk)
                    result[section][field] = rows
        except (KeyError, TypeError):
            require(False, "TARGET_STORAGE_PART_MISSING")
        expected = [(role, index) for role, _, index in _parts(result)]
        require(
            expected == [(e["role"], e["index"]) for e in manifest["parts"]],
            "TARGET_STORAGE_PART_COVERAGE_MISMATCH",
        )
        require(_counts(result) == manifest["counts"], "TARGET_STORAGE_COUNTS_MISMATCH")
        require(
            result["calculation_binding"] == inputs["binding"], "TARGET_STORAGE_BINDING_MISMATCH"
        )
        require(
            all(
                result[k] is False
                for k in (
                    "automatic_publish_allowed",
                    "automatic_order_allowed",
                    "database_writes",
                    "run_claimed",
                    "runtime_registered",
                )
            ),
            "TARGET_STORAGE_PUBLICATION_FORBIDDEN",
        )
        for value in result["execution_bindings"]:
            actual = CalculationBoundaryBinding.from_dict(value).to_dict()
            require(
                all(
                    actual[k] == inputs["binding"][k]
                    for k in (
                        "engine_run_id",
                        "canonical_input_hash",
                        "prepared_content_hash",
                        "input_bindings",
                        "psi_simulator",
                        "semantics_profile",
                        "calculation_backend",
                        "execution_mode",
                    )
                ),
                "TARGET_STORAGE_BINDING_MISMATCH",
            )
        # Storage checksums cover all bytes; existing semantic hashes remain independently checked.
        for value in (
            result["search_result"],
            result["selection_result"],
            *result["search_result"]["candidate_evaluations"],
        ):
            require(
                value["content_hash"]
                == digest({k: v for k, v in value.items() if k != "content_hash"}),
                "TARGET_STORAGE_RESULT_HASH_MISMATCH",
            )
        plan = result["order_plan"]
        for field, key in (
            ("psi_rows", "psi_content_hash"),
            ("decision_evidence", "decision_content_hash"),
            ("recommended_orders", "orders_content_hash"),
        ):
            require(digest(plan[field]) == plan[key], "TARGET_STORAGE_RESULT_HASH_MISMATCH")
        return result, inputs

    def _read(self, run_id, expected_input_reference=None):
        input_ref = self.repository.lookup(run_id, "INPUT")
        require(input_ref is not None, "TARGET_STORAGE_INPUT_NOT_FOUND")
        require(
            expected_input_reference is None or input_ref == expected_input_reference,
            "TARGET_STORAGE_EXPECTED_INPUT_MISMATCH",
        )
        ref = self.repository.lookup(run_id, "COMPLETE")
        require(ref is not None, "TARGET_STORAGE_INCOMPLETE")
        manifest = self.repository.read(ref)
        require(
            manifest["engine_run_id"] == run_id and manifest["input_reference"] == input_ref,
            "TARGET_STORAGE_MANIFEST_BINDING_MISMATCH",
        )
        result, inputs = self._read_manifest(manifest)
        return result, inputs, manifest

    def read_integrity(self, run_id, *, expected_input_reference=None):
        result, _, manifest = self._read(run_id, expected_input_reference)
        return self._view(result, manifest, "INTEGRITY_VERIFIED")

    def read_verified(self, run_id, *, expected_input_reference=None):
        result, inputs, manifest = self._read(run_id, expected_input_reference)
        runner, request, binding, sources = self._restore(inputs)
        runner.verify_result(result, request, binding, **sources)
        return self._view(result, manifest, "RECOMPUTED_VERIFIED")

    def recover(self, run_id, *, expected_input_reference=None):
        ref = self.repository.lookup(run_id, "INPUT")
        require(ref is not None, "TARGET_STORAGE_INPUT_NOT_FOUND")
        require(
            expected_input_reference is None or ref == expected_input_reference,
            "TARGET_STORAGE_EXPECTED_INPUT_MISMATCH",
        )
        inputs = self.repository.read(ref)
        require(self._run_id(inputs) == run_id, "TARGET_STORAGE_RUN_MISMATCH")
        if self.repository.lookup(run_id, "COMPLETE") is not None:
            return self.read_verified(run_id, expected_input_reference=ref)
        return self._execute(inputs, ref)

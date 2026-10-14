"""Development-only partitioned PSI, policy diagnostics and durable recovery.

Operational Claim/Plan/Result Bundle/Outbox contracts are deliberately unchanged.
One validated parent input is indexed once. Parts never claim a different input
universe and no blocked/unclassified item can acquire a replenishment action.
"""

from __future__ import annotations

import copy
from collections import defaultdict
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from types import MappingProxyType

from dsio_inventory_engine.classify_inventory.application import config_hash, validate_config
from dsio_inventory_engine.infrastructure.policy_analysis_inputs import normalized_payload
from dsio_inventory_engine.infrastructure.runtime_build import installed_runtime_build
from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.development_calculation import (
    CONTRACT_KEY,
    MAX_PART_BYTES,
    check,
    root_reference,
    validate_command,
    validate_manifest,
)
from dsio_inventory_engine.inventory_contracts.effective_policy_v2 import EFFECTIVE_POLICY_V2_FIELDS
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    canonical_json,
    digest,
)
from dsio_inventory_engine.inventory_evidence.contracts import ArtifactObject
from dsio_inventory_engine.prepare_inventory.application.inventory_input import (
    PrepareInventoryInputUseCase,
)
from dsio_inventory_engine.recommend_replenishment.application.math_input import validate_math_input
from dsio_inventory_engine.recommend_replenishment.application.math_policy import (
    build_policy_report,
)
from dsio_inventory_engine.recommend_replenishment.application.mathematical import (
    MathematicalStrategy,
)
from dsio_inventory_engine.recommend_replenishment.application.run import (
    apply_effective_policy_controls,
    simulate,
    validate_controls,
)
from dsio_inventory_engine.simulate_inventory.application.baseline import _roll_forward
from .result_artifacts import summarize_psi_rows


SOURCE_KEY = "inventory.development_calculation_input"
PART_KEY = "inventory.development_calculation_part"


class PinnedDevelopmentCalculationSourceProvider:
    """Explicit owner + bundle ID/hash lookup; never search for latest input."""

    def __init__(self, entries):
        self.entries = {}
        for command, bundle in entries:
            command = validate_command(command)
            key = (
                command["tenant_id"],
                command["project_id"],
                command["source_binding"]["bundle_id"],
            )
            check(key not in self.entries, "CALCULATION_SOURCE_SELECTOR_DUPLICATE")
            check(
                bundle["content_hash"] == command["source_binding"]["content_hash"],
                "CALCULATION_SOURCE_HASH_MISMATCH",
            )
            self.entries[key] = (command, bundle)

    def __call__(self, command):
        command = validate_command(command)
        key = (command["tenant_id"], command["project_id"], command["source_binding"]["bundle_id"])
        check(key in self.entries, "CALCULATION_PINNED_SOURCE_NOT_FOUND")
        expected, bundle = self.entries[key]
        # Calculation identity/partitioning may change, Source and authority may not.
        for field in (
            "scope",
            "demand_run_id",
            "plan_yyyyww",
            "source_binding",
            "canonical_input_hash",
            "config_revision_id",
            "config_hash",
            "classification_snapshot_id",
            "classification_content_hash",
            "effective_policy_content_hash",
        ):
            check(command[field] == expected[field], "CALCULATION_PINNED_SOURCE_BINDING_MISMATCH")
        return bundle


def seal_source_bundle(*, mathematical_request, config_values, classification_snapshot):
    body = {
        "mathematical_request": mathematical_request,
        "config_values": config_values,
        "classification_snapshot": classification_snapshot,
    }
    return {**body, "content_hash": digest(body)}


def validate_source_bundle(command, bundle, deployment, *, verify_installed=True):
    check(deployment.environment == "DEVELOPMENT", "CALCULATION_DEVELOPMENT_ONLY")
    check(
        type(bundle) is dict
        and set(bundle)
        == {"mathematical_request", "config_values", "classification_snapshot", "content_hash"},
        "CALCULATION_SOURCE_FIELDS_INVALID",
    )
    check(
        digest({k: v for k, v in bundle.items() if k != "content_hash"})
        == bundle["content_hash"]
        == command["source_binding"]["content_hash"],
        "CALCULATION_SOURCE_HASH_MISMATCH",
    )
    request = MathematicalPolicyRequest.from_dict(bundle["mathematical_request"])
    raw = request.to_dict()
    canonical = CanonicalInputRequest.from_dict(raw["recommendation"]["canonical_input"])
    ctx = raw["recommendation"]["canonical_input"]["context"]
    execution = raw["recommendation"]["execution"]
    check(
        execution["contract_version"] == "2.0.0"
        and execution["execution_mode"] == "LOCAL_SHADOW"
        and execution["execution_purpose"] == "SHADOW"
        and execution["strategy"]["strategy_type"] == "MATHEMATICAL"
        and execution["model_approval"] is None,
        "CALCULATION_MATHEMATICAL_SHADOW_REQUIRED",
    )
    if verify_installed:
        build = installed_runtime_build()["implementations"]
        check(
            build["mathematical"]["implementation_content_hash"]
            == command["mathematical_implementation_hash"]
            and build["psi_simulator"]["implementation_content_hash"]
            == command["psi_implementation_hash"],
            "CALCULATION_IMPLEMENTATION_MISMATCH",
        )
    classification, _ = normalized_payload(
        "CLASSIFICATION", command["classification_snapshot_id"], bundle["classification_snapshot"]
    )
    config = validate_config(bundle["config_values"])
    check(
        canonical.input_hash == command["canonical_input_hash"]
        and ctx["configuration_revision"] == command["config_revision_id"]
        and ctx["demand_run_id"] == command["demand_run_id"]
        and ctx["plan_yyyyww"] == command["plan_yyyyww"]
        and all(ctx[k] == v for k, v in command["scope"].items()),
        "CALCULATION_INPUT_BINDING_MISMATCH",
    )
    check(
        classification["tenant_id"] == command["tenant_id"]
        and classification["project_id"] == command["project_id"]
        and classification["scope"]["plant_cd"] == command["scope"]["site_cd"]
        and all(classification["scope"][k] == v for k, v in command["scope"].items())
        and classification["as_of_yyyyww"] == command["plan_yyyyww"],
        "CALCULATION_SOURCE_SCOPE_MISMATCH",
    )
    check(
        config_hash(config) == command["config_hash"] == classification["config_hash"]
        and classification["config_revision_id"] == command["config_revision_id"]
        and classification["content_hash"] == command["classification_content_hash"]
        and classification["effective_policy_content_hash"]
        == command["effective_policy_content_hash"],
        "CALCULATION_POLICY_BINDING_MISMATCH",
    )
    expected = [
        {"item_id": i["item_id"], **{k: i[k] for k in EFFECTIVE_POLICY_V2_FIELDS}}
        for i in classification["items"]
    ]
    check(
        sorted(expected, key=lambda r: r["item_id"]) == execution["effective_item_policies"],
        "CALCULATION_POLICY_PROJECTION_MISMATCH",
    )
    prepared = PrepareInventoryInputUseCase(deployment).execute(canonical).to_dict()
    check(
        {r["item_id"] for r in prepared["master"]}
        == {r["item_id"] for r in classification["items"]}
        == {r["item_id"] for r in execution["item_controls"]},
        "CALCULATION_SOURCE_UNIVERSE_MISMATCH",
    )
    check({r["uom"] for r in prepared["master"]} == {"EA"}, "CALCULATION_EA_ONLY")
    validate_math_input(raw["policy_input"], raw["recommendation"], prepared)
    return raw, prepared


class DevelopmentCalculationExecutor:
    """Commit each bounded part once; publish a manifest only after full coverage.

    Repository is the existing append-only Evidence adapter's commit_artifacts
    capability. No publication Outbox is created, including after recovery.
    """

    def __init__(self, *, deployment, repository, source_provider=None):
        check(deployment.environment == "DEVELOPMENT", "CALCULATION_DEVELOPMENT_ONLY")
        self.deployment, self.repository, self.source_provider = (
            deployment,
            repository,
            source_provider,
        )

    def _optional(self, reference):
        try:
            return self.repository.read_artifact(reference)
        except InventoryInputError as error:
            if str(error) == "ARTIFACT_NOT_FOUND":
                return None
            raise

    def execute(self, command):
        command = validate_command(command)
        root = root_reference(command)
        if self._optional(root + ":manifest") is not None:
            return self.read(command)
        saved = self._optional(root + ":input")
        if saved is None:
            check(self.source_provider is not None, "CALCULATION_SOURCE_PROVIDER_REQUIRED")
            bundle = self.source_provider(command)
            raw, prepared = validate_source_bundle(command, bundle, self.deployment)
            saved = _artifact(
                root + ":input",
                SOURCE_KEY,
                {
                    "contract_id": "inventory-development-calculation-input",
                    "contract_version": "1.0.0",
                    "command": command,
                    "bundle": bundle,
                },
            )
            self.repository.commit_artifacts([saved])
        else:
            doc = saved.document()
            check(
                saved.contract_key == SOURCE_KEY and doc["command"] == command,
                "CALCULATION_SOURCE_CONFLICT",
            )
            raw, prepared = validate_source_bundle(command, doc["bundle"], self.deployment)
        items = sorted(r["item_id"] for r in prepared["master"])
        indexes = _indexes(prepared, raw)
        part_refs = []
        blocked = 0
        totals = {"baseline": defaultdict(Decimal), "recommended": defaultdict(Decimal)}
        for index, start in enumerate(range(0, len(items), command["max_items_per_part"])):
            ids = items[start : start + command["max_items_per_part"]]
            reference = root + f":part:{index:06d}"
            artifact = self._optional(reference)
            if artifact is None:
                part = _calculate_part(command, index, ids, prepared, raw, indexes)
                artifact = _artifact(reference, PART_KEY, part)
                check(len(artifact.document_bytes) <= MAX_PART_BYTES, "CALCULATION_PART_SIZE_LIMIT")
                self.repository.commit_artifacts([artifact])
            part = _verify_part(command, artifact, index, ids, prepared, indexes)
            part_refs.append(
                {
                    "reference": reference,
                    "content_hash": artifact.content_hash,
                    "index": index,
                    "item_ids": ids,
                    "row_count": len(part["baseline_rows"]),
                }
            )
            blocked += sum(g["disposition"] == "BLOCKED" for g in part["gate_evidence"])
            _add_metrics(totals, part)
        body = {
            "contract_id": "inventory-development-calculation-manifest",
            "contract_version": "1.0.0",
            "source_contract_key": CONTRACT_KEY,
            "artifact_reference": root + ":manifest",
            "status": "CALCULATED",
            "command": command,
            "source_artifact": {"reference": saved.reference, "content_hash": saved.content_hash},
            "parts": part_refs,
            "item_count": len(items),
            "row_count": len(items) * len(prepared["calendar"]),
            "blocked_item_count": blocked,
            "metrics": _metrics(totals),
            "automatic_publish_allowed": False,
            "automatic_order_allowed": False,
            "engine_run_claimed": False,
        }
        manifest = {**body, "content_hash": digest(body)}
        validate_manifest(manifest, command)
        self.repository.commit_artifacts(
            [
                ArtifactObject.from_document(
                    reference=root + ":manifest", contract_key=CONTRACT_KEY, document=manifest
                )
            ]
        )
        return manifest

    def read(self, command):
        """Verify stored parts, coverage and aggregates; no upstream lookup or PSI rerun."""
        command = validate_command(command)
        artifact = self.repository.read_artifact(root_reference(command) + ":manifest")
        check(artifact.contract_key == CONTRACT_KEY, "CALCULATION_MANIFEST_CONTRACT_MISMATCH")
        manifest = validate_manifest(artifact.document(), command)
        source = self.repository.read_artifact(manifest["source_artifact"]["reference"])
        source_document = source.document()
        check(
            source.content_hash == manifest["source_artifact"]["content_hash"]
            and source.contract_key == SOURCE_KEY
            and source_document["command"] == command,
            "CALCULATION_SOURCE_CONFLICT",
        )
        raw, prepared = validate_source_bundle(
            command, source_document["bundle"], self.deployment, verify_installed=False
        )
        indexes = _indexes(prepared, raw)
        items = sorted(r["item_id"] for r in prepared["master"])
        check(
            items == [i for part in manifest["parts"] for i in part["item_ids"]],
            "CALCULATION_SOURCE_COVERAGE_MISMATCH",
        )
        totals = {"baseline": defaultdict(Decimal), "recommended": defaultdict(Decimal)}
        blocked = 0
        for row in manifest["parts"]:
            artifact = self.repository.read_artifact(row["reference"])
            check(artifact.content_hash == row["content_hash"], "CALCULATION_PART_HASH_MISMATCH")
            part = _verify_part(command, artifact, row["index"], row["item_ids"], prepared, indexes)
            check(
                len(part["baseline_rows"]) == row["row_count"],
                "CALCULATION_PART_ROW_COUNT_MISMATCH",
            )
            blocked += sum(g["disposition"] == "BLOCKED" for g in part["gate_evidence"])
            _add_metrics(totals, part)
        check(
            manifest["metrics"] == _metrics(totals) and blocked == manifest["blocked_item_count"],
            "CALCULATION_METRICS_MISMATCH",
        )
        return manifest


def _artifact(reference, key, body):
    return ArtifactObject.from_document(
        reference=reference, contract_key=key, document={**body, "content_hash": digest(body)}
    )


def _indexes(prepared, raw):
    result = {}
    for name in ("master", "positions", "demands", "policies", "receipt_decisions"):
        groups = defaultdict(list)
        for row in prepared[name]:
            groups[row["item_id"]].append(row)
        result[name] = groups
    for name, rows in (
        ("history", raw["policy_input"]["history"]),
        ("adjustments", raw["policy_input"]["adjustments"]),
        ("controls", raw["recommendation"]["execution"]["item_controls"]),
        ("item_policies", raw["recommendation"]["execution"]["effective_item_policies"]),
    ):
        groups = defaultdict(list)
        for row in rows:
            groups[row["item_id"]].append(row)
        result[name] = groups
    return result


def _subset(prepared, indexes, ids):
    result = {
        "context": copy.deepcopy(prepared["context"]),
        "manifest": copy.deepcopy(prepared["manifest"]),
        "calendar": copy.deepcopy(prepared["calendar"]),
    }
    for name in ("master", "positions", "demands", "policies", "receipt_decisions"):
        result[name] = copy.deepcopy([r for item in ids for r in indexes[name][item]])
    return result


def _calculate_part(command, index, ids, prepared, raw, indexes):
    with localcontext() as ctx:
        ctx.prec = 40
        ctx.rounding = ROUND_HALF_EVEN
        return _calculate_part_precise(command, index, ids, prepared, raw, indexes)


def _calculate_part_precise(command, index, ids, prepared, raw, indexes):
    subset = _subset(prepared, indexes, ids)
    baseline = _roll_forward(subset)
    execution = {
        k: copy.deepcopy(v)
        for k, v in raw["recommendation"]["execution"].items()
        if k not in {"item_controls", "effective_item_policies"}
    }
    execution["item_controls"] = copy.deepcopy([r for i in ids for r in indexes["controls"][i]])
    execution["effective_item_policies"] = copy.deepcopy(
        [r for i in ids for r in indexes["item_policies"][i]]
    )
    # Reuse the core admission for classified items in SHADOW. Unclassified
    # items retain Source-only diagnostic policy; they never gain a strategy.
    classified = [
        r["item_id"]
        for r in execution["effective_item_policies"]
        if r["effective_strategy"] is not None
    ]
    adjusted = _subset(prepared, indexes, classified)
    if classified:
        class_execution = {
            **execution,
            "effective_item_policies": [
                r for r in execution["effective_item_policies"] if r["item_id"] in set(classified)
            ],
        }
        apply_effective_policy_controls(adjusted, class_execution)
        # Keep every dated policy row; do not collapse an item's policy periods.
        subset["policies"] = [
            p for p in subset["policies"] if p["item_id"] not in set(classified)
        ] + adjusted["policies"]
    history = {
        k: copy.deepcopy(v)
        for k, v in raw["policy_input"].items()
        if k not in {"history", "adjustments"}
    }
    history["history"] = copy.deepcopy([r for i in ids for r in indexes["history"][i]])
    history["history_row_count"] = len(history["history"])
    history["adjustments"] = copy.deepcopy([r for i in ids for r in indexes["adjustments"][i]])
    # Validate the bounded work against the existing limits, never relax them.
    validate_controls(subset, execution)
    report = build_policy_report(history, subset, digest(raw["recommendation"]))
    allowed = []
    gates = []
    for p in execution["effective_item_policies"]:
        can_calculate = (
            p["operational_io_eligible"]
            and p["recommendation_calculation_allowed"]
            and not p["no_order_gate"]
        )
        if can_calculate:
            allowed.append(p["item_id"])
        gates.append(_gate(p))
    blocked = set(ids) - set(allowed)
    recommended = []
    for r in baseline:
        if r["item_id"] in blocked:
            policy = next(
                p
                for p in subset["policies"]
                if p["item_id"] == r["item_id"]
                and p["effective_from"] <= r["start_date"] <= p["effective_to"]
            )
            cap = policy["physical_max_capacity"]
            peak = Decimal(r["available_qty"]) + Decimal(r["reserved_qty"])
            excess = max(Decimal("0"), peak - Decimal(cap)) if cap is not None else Decimal("0")
            recommended.append(
                {
                    **r,
                    "psi_scenario_type": "RECOMMENDED",
                    "strategy_type": "MATHEMATICAL",
                    "physical_capacity_excess_qty": format(excess, "f"),
                }
            )
    decisions, orders = [], []
    if allowed:
        allowed_subset = {
            **subset,
            **{
                name: [r for r in subset[name] if r["item_id"] in set(allowed)]
                for name in ("master", "positions", "demands", "policies", "receipt_decisions")
            },
        }
        allowed_execution = {
            **execution,
            "item_controls": [
                r for r in execution["item_controls"] if r["item_id"] in set(allowed)
            ],
            "effective_item_policies": [
                r for r in execution["effective_item_policies"] if r["item_id"] in set(allowed)
            ],
        }
        strategy = MathematicalStrategy(
            digest(raw["recommendation"]),
            canonical_json(execution["strategy_input_binding"]),
            MappingProxyType(
                {
                    r["decision_id"]: canonical_json(r)
                    for r in report["policy_evidence"]
                    if r["item_id"] in set(allowed)
                }
            ),
        )
        with localcontext() as ctx:
            ctx.prec = 40
            rows, decisions, orders = simulate(
                allowed_subset, allowed_execution, strategy.input_hash, strategy
            )
        recommended.extend(rows)
        orders = [
            {
                **o,
                "simulation_only": True,
                "automatic_publish_allowed": False,
                "automatic_order_allowed": False,
            }
            for o in orders
        ]
    return {
        "contract_id": "inventory-development-calculation-part",
        "contract_version": "1.0.0",
        "command_hash": command["content_hash"],
        "canonical_input_hash": command["canonical_input_hash"],
        "index": index,
        "item_ids": ids,
        "baseline_rows": baseline,
        "recommended_rows": sorted(recommended, key=lambda r: (r["item_id"], r["seq"])),
        "policy_report": report,
        "gate_evidence": gates,
        "decision_evidence": decisions,
        "simulated_orders": orders,
        "automatic_publish_allowed": False,
        "automatic_order_allowed": False,
    }


def _gate(p):
    allowed = (
        p["operational_io_eligible"]
        and p["recommendation_calculation_allowed"]
        and not p["no_order_gate"]
    )
    return {
        "item_id": p["item_id"],
        "effective_policy_hash": p["effective_policy_hash"],
        "disposition": "SIMULATION_ONLY" if allowed else "BLOCKED",
        "reason_codes": p["policy_gate_reason_codes"],
        "diagnostic_role": "SOURCE_ONLY" if p["effective_strategy"] is None else "EFFECTIVE_POLICY",
        "automatic_publish_allowed": False,
        "automatic_order_allowed": False,
    }


def _verify_part(command, artifact, index, ids, prepared, indexes):
    check(
        artifact.contract_key == PART_KEY and len(artifact.document_bytes) <= MAX_PART_BYTES,
        "CALCULATION_PART_CONTRACT_INVALID",
    )
    p = artifact.document()
    check(
        set(p)
        == {
            "contract_id",
            "contract_version",
            "command_hash",
            "canonical_input_hash",
            "index",
            "item_ids",
            "baseline_rows",
            "recommended_rows",
            "policy_report",
            "gate_evidence",
            "decision_evidence",
            "simulated_orders",
            "automatic_publish_allowed",
            "automatic_order_allowed",
            "content_hash",
        }
        and artifact.reference == root_reference(command) + f":part:{index:06d}",
        "CALCULATION_PART_FIELDS_INVALID",
    )
    check(
        p["contract_id"] == "inventory-development-calculation-part"
        and p["contract_version"] == "1.0.0"
        and p["command_hash"] == command["content_hash"]
        and p["canonical_input_hash"] == command["canonical_input_hash"]
        and p["index"] == index
        and p["item_ids"] == ids
        and p["automatic_publish_allowed"] is False
        and p["automatic_order_allowed"] is False,
        "CALCULATION_PART_BINDING_MISMATCH",
    )
    expected_scope = command["scope"]
    for name in ("baseline_rows", "recommended_rows"):
        check(
            {r["item_id"] for r in p[name]} == set(ids)
            and all(all(r[k] == v for k, v in expected_scope.items()) for r in p[name]),
            "CALCULATION_PART_UNIVERSE_MISMATCH",
        )
        summarize_psi_rows(p[name])
        check(
            len(p[name]) == len(ids) * len(prepared["calendar"])
            and {(r["item_id"], r["yyyyww"]) for r in p[name]}
            == {(i, c["yyyyww"]) for i in ids for c in prepared["calendar"]},
            "CALCULATION_SOURCE_HORIZON_MISMATCH",
        )
    check(
        {(r["item_id"], r["yyyyww"]) for r in p["baseline_rows"]}
        == {(r["item_id"], r["yyyyww"]) for r in p["recommended_rows"]},
        "CALCULATION_PART_HORIZON_MISMATCH",
    )
    check(
        {g["item_id"] for g in p["gate_evidence"]} == set(ids)
        and len(p["gate_evidence"]) == len(ids),
        "CALCULATION_GATE_COVERAGE_INVALID",
    )
    expected = [_gate(indexes["item_policies"][i][0]) for i in ids]
    check(p["gate_evidence"] == expected, "CALCULATION_ORIGINAL_GATE_MISMATCH")
    blocked = {g["item_id"] for g in p["gate_evidence"] if g["disposition"] == "BLOCKED"}
    check(
        all(
            o["item_id"] in set(ids) - blocked
            and o["simulation_only"] is True
            and o["automatic_publish_allowed"] is False
            and o["automatic_order_allowed"] is False
            for o in p["simulated_orders"]
        )
        and all(d["observation"]["item_id"] not in blocked for d in p["decision_evidence"]),
        "CALCULATION_BLOCKED_ORDER_FORBIDDEN",
    )
    original = {(r["item_id"], r["yyyyww"]): r for r in p["baseline_rows"]}
    for r in p["recommended_rows"]:
        if r["item_id"] in blocked:
            check(
                all(
                    r[k] == v
                    for k, v in original[(r["item_id"], r["yyyyww"])].items()
                    if k != "psi_scenario_type"
                ),
                "CALCULATION_BLOCKED_PSI_CHANGED",
            )
    return p


_METRIC_KEYS = (
    "horizon_demand_qty",
    "on_time_fulfilled_qty",
    "ending_available_inventory_qty",
    "ending_on_hand_inventory_qty",
    "ending_backorder_qty",
    "forecast_shortage_qty",
)


def _add_metrics(totals, part):
    with localcontext() as ctx:
        ctx.prec = 40
        ctx.rounding = ROUND_HALF_EVEN
        for kind, rows in (
            ("baseline", part["baseline_rows"]),
            ("recommended", part["recommended_rows"]),
        ):
            m = summarize_psi_rows(rows)
            for key in _METRIC_KEYS:
                totals[kind][key] += Decimal(m[key])


def _metrics(totals):
    result = {}
    with localcontext() as ctx:
        ctx.prec = 40
        ctx.rounding = ROUND_HALF_EVEN
        for kind, values in totals.items():
            demand = values["horizon_demand_qty"]
            result[kind] = {
                **{k: format(values[k], "f") for k in _METRIC_KEYS},
                "projected_service_level": format(
                    (values["on_time_fulfilled_qty"] / demand).quantize(Decimal("0.000001")), "f"
                )
                if demand
                else None,
                "service_status": "AVAILABLE" if demand else "NO_DEMAND_IN_HORIZON",
            }
    return {**result, "cost_status": "NOT_AVAILABLE", "cost_reason": "COST_PROFILE_NOT_BOUND"}

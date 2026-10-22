"""Typed DSIM projection from the existing sealed, independently replayed result graph.

No database connection or ledger/operational-state mutation. Missing authoritative
source data is rejected rather than replaced with manufactured hashes or values.
"""

from __future__ import annotations

import copy
from dsio_inventory_engine.inventory_contracts.observation_storage import expanded_action_rows
from decimal import Decimal, ROUND_CEILING, localcontext

from dsio_inventory_engine.inventory_contracts.runtime import InventoryRuntimeExecutionRequest
from dsio_inventory_engine.inventory_contracts.values import (
    canonical_json,
    digest,
    quantity_text,
    require,
)
from .application import ReadAndVerifyInventoryResultBundleUseCase


SNAPSHOT_TYPES = {
    "calendar": "CALENDAR",
    "master": "MASTER",
    "forecast": "DEMAND_FORECAST",
    "customer_orders": "CUSTOMER_ORDERS",
    "inventory": "INVENTORY_POSITION",
    "prior_inventory": "PRIOR_INVENTORY",
    "receipts": "SUPPLY_RECEIPTS",
    "policies": "REPLENISHMENT_POLICY",
}
RECEIPT_TABLE = "dsim.tb_io_publication_receipt"


def apply_claim_action_gates(claim, *, publish_allowed, order_allowed):
    """A row may narrow item permission, never escalate its enclosing Run."""
    publish = publish_allowed is True and claim.get("expected_automatic_publish_allowed") is True
    order = (
        publish and order_allowed is True and claim.get("expected_automatic_order_allowed") is True
    )
    return {"automatic_publish_allowed": publish, "automatic_order_allowed": order}


def snapshot_projection_source(canonical, kind, binding):
    """Keep Claim authority separate from the normalized calculation snapshot.

    Calendar/Master/Forecast can have upstream identities whose hashes cover
    their original representation. The Canonical document seals the exact
    transformed rows; neither identity is allowed to replace the other.
    """
    snapshot = canonical["snapshots"][kind]
    upstream = canonical.get("upstream_bindings", {})
    authority = (
        upstream.get(kind, snapshot) if kind in {"calendar", "master", "forecast"} else snapshot
    )
    require(
        authority["snapshot_id"] == binding["source_snapshot_id"]
        and authority["content_hash"] == binding["source_content_hash"],
        "PROJECTION_INPUT_BINDING_MISMATCH",
    )
    metadata = copy.deepcopy(snapshot["metadata"])
    arrays = {}
    for field in ("source_events", "adjustments"):
        if field in metadata:
            values = metadata.pop(field)
            arrays[field] = {"row_count": len(values), "content_hash": digest(values)}
    result = {
        "snapshot_type": kind,
        "status": snapshot["status"],
        "metadata": metadata,
        "array_evidence": arrays,
    }
    if authority is not snapshot:
        result.update(
            canonical_snapshot={
                key: snapshot[key] for key in ("snapshot_id", "content_hash", "row_count")
            },
            source_row_count_basis="CANONICAL_SNAPSHOT",
        )
    return result


class DsimSourceProjector:
    def __init__(
        self,
        *,
        catalog: dict,
        bundle_verifier: ReadAndVerifyInventoryResultBundleUseCase,
        target_result_reader=None,
    ):
        self.catalog = copy.deepcopy(catalog)
        self.verifier = bundle_verifier
        self.repository = bundle_verifier.repository
        self.target_result_reader = target_result_reader

    def build(self, *, request: InventoryRuntimeExecutionRequest, bundle_reference: str) -> dict:
        # Reconstruct the base class: never trust a mutable hostile subclass.
        request = InventoryRuntimeExecutionRequest.from_dict(request.to_dict())
        verified = self.verifier.execute(
            bundle_reference,
            strategy_execution_plan=request.strategy_execution_plan,
            runtime_request=request,
        )
        raw, bundle = request.to_dict(), verified.result_bundle
        claim, canonical = raw["claim"], verified.canonical_input.to_dict()
        prepared, context = verified.prepared_input.to_dict(), canonical["context"]
        require(
            all(bundle[k] == raw[k] for k in ("engine_run_id", "attempt_no"))
            and all(
                bundle[k] == claim[k]
                for k in (
                    "tenant_id",
                    "project_id",
                    "planning_cycle_id",
                    "planning_cycle_revision_id",
                    "cycle_site_execution_id",
                    "plan_id",
                    "site_binding_hash",
                )
            )
            and bundle["scope"] == claim["scope"]
            and bundle["strategy_execution_plan_content_hash"]
            == request.strategy_execution_plan["content_hash"],
            "PROJECTION_RUN_BINDING_MISMATCH",
        )
        run = request.engine_run_id
        tables = {t["name"]: [] for t in self.catalog["tables"] if t["name"] != RECEIPT_TABLE}
        specs = {t["name"]: t for t in self.catalog["tables"]}
        source_refs = verified.outbox.message.payload()
        artifacts = {}

        def read(reference):
            obj = self.repository.read_artifact(reference)
            artifacts[reference] = {
                "artifact_reference": obj.reference,
                "contract_key": obj.contract_key,
                "content_hash": obj.content_hash,
                "byte_content_hash": obj.byte_content_hash,
            }
            return obj, obj.document()

        def emit(suffix, **values):
            name = "dsim.tb_io_" + suffix
            row = {"engine_run_id": run, **values}
            for col in specs[name]["columns"]:
                key = col["name"]
                if key == "created_at":
                    continue
                if key == "row_content_hash":
                    continue
                if key not in row:
                    require(
                        col["nullable"], "PROJECTION_REQUIRED_FIELD_MISSING:" + name + "." + key
                    )
                    row[key] = None
            names = {c["name"] for c in specs[name]["columns"] if c["name"] != "created_at"}
            require(set(row) <= names, "PROJECTION_UNKNOWN_COLUMN")
            if "row_content_hash" in names:
                row["row_content_hash"] = digest(row)
            tables[name].append(row)

        for binding in source_refs["artifact_bindings"]:
            obj, _ = read(binding["artifact_reference"])
            require(
                obj.content_hash == binding["content_hash"]
                and obj.byte_content_hash == binding["byte_content_hash"]
                and obj.contract_key == binding["contract_key"],
                "PROJECTION_SOURCE_ARTIFACT_MISMATCH",
            )
        bundle_obj, _ = read(bundle_reference)
        canonical_obj, _ = read(source_refs["canonical_input_reference"])
        prepared_obj, _ = read(source_refs["prepared_input_reference"])
        bindings = {b["input_type"]: b for b in claim["input_bindings"]}
        analysis = verified.policy_analysis_evidence
        classification = None
        classification_object = verified.classification_source_artifact
        if classification_object is not None:
            read(classification_object.reference)
            classification = classification_object.document()["classification_snapshot"]
        analysis_docs = {}
        if analysis is not None:
            for child in analysis.manifest["children"]:
                obj, doc = read(child["artifact_reference"])
                analysis_docs.setdefault(child["role"], []).append((obj, doc))
            read(analysis.manifest["artifact_reference"])
            analysis_classification = analysis_docs["ACTUAL_INPUT"][0][1]["classification_snapshot"]
            require(
                classification is None or classification == analysis_classification,
                "CLASSIFICATION_ANALYSIS_SOURCE_MISMATCH",
            )
            classification = analysis_classification
            if classification_object is None:
                classification_object = analysis_docs["ACTUAL_INPUT"][0][0]
        if "INVENTORY_CLASSIFICATION" in bindings:
            require(classification is not None, "PROJECTION_CLASSIFICATION_HEADER_REQUIRED")
            require(
                all(
                    classification[k] == claim[k]
                    for k in (
                        "tenant_id",
                        "project_id",
                        "config_id",
                        "config_revision_id",
                        "config_hash",
                    )
                )
                and all(
                    classification["scope"][k] == claim["scope"][k]
                    for k in ("company_cd", "subs_cd", "site_cd")
                )
                and classification["as_of_yyyyww"] == context["plan_yyyyww"],
                "PROJECTION_CLASSIFICATION_SCOPE_MISMATCH",
            )
            bound = bindings["INVENTORY_CLASSIFICATION"]
            require(
                classification["classification_snapshot_id"] == bound["source_snapshot_id"]
                and classification["effective_policy_content_hash"] == bound["source_content_hash"],
                "PROJECTION_CLASSIFICATION_BINDING_MISMATCH",
            )
        emit(
            "plan_context",
            tenant_id=claim["tenant_id"],
            project_id=claim["project_id"],
            canonical_contract_version=canonical["contract_version"],
            canonical_input_hash=verified.canonical_input.input_hash,
            prepared_input_hash=prepared["manifest"]["prepared_content_hash"],
            **{
                k: context[k]
                for k in (
                    "plan_type",
                    "plan_yyyyww",
                    "plan_start_date",
                    "plan_end_date",
                    "master_as_of_date",
                    "business_timezone",
                    "inventory_cutoff_at",
                    "inventory_source_watermark",
                )
            },
            source_artifact_reference=canonical_obj.reference,
            source_artifact_hash=canonical_obj.content_hash,
            config_id=claim["config_id"],
            config_revision_id=claim["config_revision_id"],
            config_hash=claim["config_hash"],
            classification_snapshot_id=classification["classification_snapshot_id"]
            if classification
            else None,
            classification_content_hash=classification["content_hash"] if classification else None,
        )
        for kind, input_type in SNAPSHOT_TYPES.items():
            snapshot = canonical["snapshots"][kind]
            b = bindings[input_type]
            source_metadata = snapshot_projection_source(canonical, kind, b)
            emit(
                "snapshot_manifest",
                input_type=input_type,
                **{
                    k: b[k]
                    for k in (
                        "source_snapshot_id",
                        "source_content_hash",
                        "source_contract_key",
                        "source_contract_version",
                    )
                },
                artifact_reference=canonical_obj.reference,
                byte_content_hash=canonical_obj.byte_content_hash,
                source_row_count=snapshot["row_count"],
                sealed_at=snapshot["metadata"].get("sealed_at"),
                source_metadata=source_metadata,
            )
        if classification is not None:
            obj = classification_object
            b = bindings["INVENTORY_CLASSIFICATION"]
            emit(
                "snapshot_manifest",
                input_type="INVENTORY_CLASSIFICATION",
                **{
                    k: b[k]
                    for k in (
                        "source_snapshot_id",
                        "source_content_hash",
                        "source_contract_key",
                        "source_contract_version",
                    )
                },
                artifact_reference=obj.reference,
                byte_content_hash=obj.byte_content_hash,
                source_row_count=len(classification["items"]),
                sealed_at=None,
                source_metadata={
                    "header_content_hash": classification["content_hash"],
                    "binding_hash_kind": "EFFECTIVE_POLICY_CONTENT_HASH",
                },
            )
        for row in canonical["snapshots"]["calendar"]["rows"]:
            emit("calendar_bucket", source_input_type="CALENDAR", **row)
        site = context["site_cd"]
        emit("location", source_input_type="MASTER", location_id=site, site_cd=site)
        # A hash-derived Buffer ID avoids ambiguities in business keys containing '@'.
        buffers = {r["item_id"]: "BUF-" + digest([site, r["item_id"]]) for r in prepared["master"]}
        for row in prepared["master"]:
            item = row["item_id"]
            emit(
                "item",
                source_input_type="MASTER",
                **{k: row[k] for k in ("item_id", "uom", "active", "stock_managed")},
            )
            emit(
                "buffer",
                buffer_id=buffers[item],
                item_id=item,
                location_id=site,
                legacy_buffer_id=None,
            )
        for kind, demand_type, qty in (
            ("forecast", "FORECAST", "forecast_qty"),
            ("customer_orders", "CUSTOMER_ORDER", "confirmed_customer_order_qty"),
        ):
            for row in canonical["snapshots"][kind]["rows"]:
                emit(
                    "demand",
                    buffer_id=buffers[row["item_id"]],
                    yyyyww=row["yyyyww"],
                    demand_type=demand_type,
                    source_input_type=SNAPSHOT_TYPES[kind],
                    source_qty=row[qty],
                    forecast_netting_mode=row.get("forecast_netting_mode"),
                    upstream_gross_forecast_qty=row.get("upstream_gross_forecast_qty"),
                    upstream_consumed_qty=row.get("upstream_consumed_qty"),
                )
        for kind, position_type in (("inventory", "CURRENT"), ("prior_inventory", "PRIOR")):
            snapshot = canonical["snapshots"][kind]
            meta = snapshot["metadata"]
            for row in snapshot["rows"]:
                emit(
                    "inventory_position",
                    buffer_id=buffers[row["item_id"]],
                    position_type=position_type,
                    source_input_type=SNAPSHOT_TYPES[kind],
                    position_date=row.get("position_date", meta.get("as_of_date")),
                    position_source_type=meta["position_source_type"],
                    on_hand_qty=row.get("on_hand_qty", row.get("eoh_qty")),
                    reserved_qty=row.get("reserved_qty"),
                    available_qty=row.get("available_qty"),
                    backorder_qty=row.get("backorder_qty"),
                    simulation_run_id=meta.get("simulation_run_id"),
                    generator_version=meta.get("generator_version"),
                )
        decisions = {r["receipt_id"]: r for r in prepared["receipt_decisions"]}
        for row in canonical["snapshots"]["receipts"]["rows"]:
            d = decisions[row["receipt_id"]]
            included = (
                row["status"] in ("CONFIRMED", "IN_TRANSIT") and d["exclusion_reason"] is None
            )
            emit(
                "scheduled_receipt",
                source_input_type="SUPPLY_RECEIPTS",
                receipt_id=row["receipt_id"],
                buffer_id=buffers[row["item_id"]],
                due_date=row["due_date"],
                due_qty=row["due_qty"],
                source_status=row["status"],
                supply_type=row["supply_type"],
                included_in_baseline=included,
                exclusion_reason=None if included else d["exclusion_reason"],
            )
        for row in canonical["snapshots"]["policies"]["rows"]:
            emit(
                "inventory_policy",
                source_input_type="REPLENISHMENT_POLICY",
                buffer_id=buffers[row["item_id"]],
                **{
                    k: v
                    for k, v in row.items()
                    if k not in ("item_id", "company_cd", "subs_cd", "site_cd", "uom")
                },
            )
        recon_types = {"SOURCE_CUTOFF": "CUT_OFF_COMPLETENESS", "EOH_BOH": "EOH_BOH_CONTINUITY"}
        for n, row in enumerate(prepared["reconciliation"]):
            item = row.get("item_id")
            emit(
                "inventory_reconciliation",
                reconciliation_id="REC-" + digest([n, row]),
                buffer_id=buffers.get(item),
                subject_item_id=item,
                reconciliation_type=recon_types[row["reconciliation_type"]],
                status=row["status"],
                **{
                    k: row.get(k)
                    for k in (
                        "previous_actual_eoh_qty",
                        "approved_adjustment_qty",
                        "expected_boh_qty",
                        "reported_boh_qty",
                        "variance_qty",
                        "tolerance_qty",
                    )
                },
                late_event_count=row.get("late_event_count", 0),
                evidence_reference=prepared_obj.reference,
                evidence_hash=prepared_obj.content_hash,
            )
        emit(
            "result_bundle",
            bundle_id=bundle["result_bundle_id"],
            contract_version=bundle["contract_version"],
            canonical_input_hash=bundle["canonical_input_hash"],
            prepared_input_hash=prepared["manifest"]["prepared_content_hash"],
            bundle_content_hash=bundle["content_hash"],
            artifact_reference=bundle_reference,
            byte_content_hash=bundle_obj.byte_content_hash,
            effective_child_result_id=bundle["effective_child_result_id"],
        )
        models = {
            b["challenger_id"]: b["model"]
            for b in request.strategy_execution_plan["shadow_challenger_bindings"]
        }

        def registry(child_id, kind, obj, doc, count):
            require(count > 0, "PROJECTION_EMPTY_SUCCESS_CHILD")
            emit(
                "result_child",
                child_result_id=child_id,
                child_kind=kind,
                execution_role="EVIDENCE_ONLY",
                strategy_type="MATHEMATICAL",
                scenario_id="BASE",
                scenario_hash=request.strategy_execution_plan["base_scenario_content_hash"],
                status="SUCCEEDED",
                row_count=count,
                artifact_reference=obj.reference,
                content_hash=obj.content_hash,
                artifact_contract_key=obj.contract_key,
                artifact_contract_version=doc["contract_version"],
            )

        def psi(child_id, rows, simulation_id="BASE", candidate_id=None):
            expected = {(item, b["yyyyww"]) for item in buffers for b in prepared["calendar"]}
            if candidate_id is not None:
                expected = {(rows[0]["item_id"], b["yyyyww"]) for b in prepared["calendar"]}
            require(
                len(rows) == len(expected)
                and {(r["item_id"], r["yyyyww"]) for r in rows} == expected,
                "PROJECTION_PSI_COVERAGE_MISMATCH",
            )
            for row in rows:
                emit(
                    "psi_bucket",
                    child_result_id=child_id,
                    buffer_id=buffers[row["item_id"]],
                    simulation_id=simulation_id,
                    candidate_id=candidate_id,
                    yyyyww=row["yyyyww"],
                    **{
                        c["name"]: row[c["name"]]
                        for c in specs["dsim.tb_io_psi_bucket"]["columns"]
                        if c["type"] == "dsim.io_quantity"
                    },
                )

        def actions(child_id, evidence, artifact, role, simulation_id="BASE"):
            for row in evidence:
                observation, validation = row["observation"], row["validation"]
                admission = row.get("effective_policy_admission")
                source = observation["policy"]
                item, week = observation["item_id"], observation["bucket"]["yyyyww"]
                auto = (
                    admission is not None
                    and role == "OPERATIONAL"
                    and validation["status"] in ("ACCEPTED", "ADJUSTED")
                )
                emit(
                    "replenishment_proposal",
                    child_result_id=child_id,
                    proposal_id=(
                        observation["decision_id"]
                        if simulation_id == "BASE"
                        else "PROP-" + digest([simulation_id, observation["decision_id"]])
                    ),
                    buffer_id=buffers[item],
                    simulation_id=simulation_id,
                    yyyyww=week,
                    order_date=validation["decision_date"],
                    arrival_date=validation["due_date"],
                    raw_order_qty=validation["raw_order_qty"],
                    constrained_order_qty=validation["accepted_order_qty"],
                    disposition=validation["status"],
                    approval_level=admission["effective_approval_level"] if admission else None,
                    order_action=admission["effective_order_action"] if admission else None,
                    **apply_claim_action_gates(
                        claim,
                        publish_allowed=bool(auto and admission["automatic_publish_allowed"]),
                        order_allowed=bool(auto and admission["automatic_order_allowed"]),
                    ),
                    adjustment_reason_codes=validation["reason_codes"],
                    action_artifact_reference=artifact.reference,
                    action_content_hash=artifact.content_hash,
                )
                calculated = (
                    validation.get("calculated_policy")
                    or row["proposal"].get("calculated_policy")
                    or {}
                )
                available = all(
                    calculated.get(k) is not None
                    for k in ("safety_stock_qty", "rop_qty", "target_inventory_qty")
                )
                # Preserve a traceable null instead of manufacturing a static policy for HOLD/PPO.
                with localcontext() as ctx:
                    ctx.prec = 40
                    days = Decimal(
                        source.get("effective_protection_lead_time_days", source["lead_time_days"])
                    )
                    lead = (days / 7).to_integral_value(rounding=ROUND_CEILING)
                emit(
                    "policy_result",
                    child_result_id=child_id,
                    buffer_id=buffers[item],
                    simulation_id=simulation_id,
                    yyyyww=week,
                    effective_policy_hash=source.get(
                        "classification_effective_policy_hash", digest(observation["policy"])
                    ),
                    safety_stock_qty=calculated["safety_stock_qty"] if available else None,
                    rop_qty=calculated["rop_qty"] if available else None,
                    target_inventory_qty=calculated["target_inventory_qty"] if available else None,
                    physical_lead_time_days=source["lead_time_days"],
                    protection_period_weeks=quantity_text(lead),
                    policy_source="STRATEGY_CALCULATED" if available else "NOT_AVAILABLE",
                    unavailable_reason=None if available else "STATIC_POLICY_NOT_EMITTED",
                )
                for n, code in enumerate(validation["reason_codes"]):
                    emit(
                        "replenishment_diagnostic",
                        diagnostic_id="DIAG-"
                        + digest([child_id, simulation_id, observation["decision_id"], n]),
                        child_result_id=child_id,
                        buffer_id=buffers[item],
                        subject_item_id=item,
                        yyyyww=week,
                        severity="WARNING" if validation["status"] == "REJECTED" else "INFO",
                        reason_code=code,
                        detail_artifact_reference=artifact.reference,
                        detail_hash=artifact.content_hash,
                    )

        for child in bundle["result_children"]:
            model = models.get(child["challenger_id"])
            emit(
                "result_child",
                child_result_id=child["child_result_id"],
                child_kind=child["result_kind"],
                execution_role=child["execution_role"],
                strategy_type=child["strategy_type"],
                scenario_id=child["scenario_id"],
                scenario_hash=child["scenario_content_hash"],
                status=child["status"],
                row_count=child["row_count"],
                artifact_reference=child["artifact_reference"],
                content_hash=child["child_content_hash"],
                artifact_contract_key=child["artifact_contract_key"],
                artifact_contract_version=child["artifact_contract_version"],
                failure_reason=child["failure_reason_code"],
                model_id=model["model_id"] if model else None,
                model_hash=model["content_hash"] if model else None,
                model_version=model["version"] if model else None,
            )
            if child["status"] != "SUCCEEDED":
                continue
            _, doc = read(child["artifact_reference"])
            psi(child["child_result_id"], doc["psi_rows"])
            if child["strategy_type"] != "NONE":
                obj, raw_actions = read(child["action_evidence"]["raw_action_reference"])
                _, validations = read(child["action_evidence"]["constrained_action_reference"])
                by_id = {r["decision_id"]: r for r in validations["rows"]}
                actions(
                    child["child_result_id"],
                    (
                        {**row, "validation": by_id[row["observation"]["decision_id"]]}
                        for row in expanded_action_rows(raw_actions)
                    ),
                    obj,
                    child["execution_role"],
                )
        if analysis is not None:
            self._analysis(analysis_docs, registry, emit, psi, actions, buffers, run)
        if self.target_result_reader is not None:
            from .target_result_projection import project_target_result

            linked = self.target_result_reader.read(
                request=request, bundle_reference=bundle_reference
            )
            for obj in (*linked.source_artifacts, linked.artifact):
                stored, _ = read(obj.reference)
                require(stored == obj, "PROJECTION_TARGET_SOURCE_ARTIFACT_MISMATCH")
            project_target_result(
                self, linked, analysis_docs, registry, emit, psi, actions, buffers, run
            )
        if verified.run_cost_evidence is not None:
            # Existing pricing Assessment/Component stay authoritative, no duplicate monetary rows.
            for artifact in verified.run_cost_evidence.artifacts:
                read(artifact.reference)
            for entry in verified.run_cost_evidence.manifest["cost_children"]:
                obj, doc = read(entry["artifact_reference"])
                registry("COST-" + obj.content_hash, "LANDED_COST", obj, doc, doc["row_count"])
        rows = normalize_tables(self.catalog, tables)
        return {
            "tables": rows,
            "source_artifacts": sorted(artifacts.values(), key=lambda x: x["artifact_reference"]),
            "bundle_reference": bundle_reference,
            "bundle_content_hash": bundle["content_hash"],
            "canonical_input_hash": bundle["canonical_input_hash"],
            "prepared_input_hash": prepared["manifest"]["prepared_content_hash"],
        }

    def _analysis(self, docs, registry, emit, psi, actions, buffers, run_id):
        searches = docs.get("SEARCH", []) + docs.get("EVALUATION_SOURCE", [])
        source_map = {}
        evaluation_ids = {}
        for obj, search in searches:
            child_id = "AN-" + obj.content_hash
            source_map[obj.content_hash] = (child_id, search)
            registry(
                child_id,
                "POLICY_SEARCH",
                obj,
                search,
                len(search["candidate_evaluations"]) + len(search["excluded_items"]),
            )
            require(
                {r["item_id"] for r in search["baseline_items"]} == set(buffers),
                "PROJECTION_SEARCH_ITEM_COVERAGE_MISMATCH",
            )
            context_id = "CTX-" + digest(
                [
                    search["context"],
                    search["canonical_input_hash"],
                    search["prepared_input_hash"],
                    search["search_config_hash"],
                    search["terminal_condition"],
                ]
            )
            for candidate_row in search["candidate_evaluations"]:
                candidate = candidate_row["candidate"]
                buffer = buffers[candidate["item_id"]]
                emit(
                    "policy_candidate",
                    child_result_id=child_id,
                    buffer_id=buffer,
                    candidate_id=candidate["candidate_id"],
                    policy_kind=candidate["policy_kind"],
                    rmos=candidate["rmos"],
                    emos=candidate["emos"],
                    service_level=candidate["target_service_level"],
                    review_cycle_weeks=candidate["review_cycle_weeks"],
                    candidate_hash=digest(candidate),
                )
                if candidate_row["status"] == "SUCCEEDED":
                    if obj == docs["SEARCH"][0][0]:
                        psi(
                            child_id,
                            candidate_row["psi_rows"],
                            candidate["candidate_id"],
                            candidate["candidate_id"],
                        )
                        actions(
                            child_id,
                            candidate_row["decision_evidence"],
                            obj,
                            "EVIDENCE_ONLY",
                            candidate["candidate_id"],
                        )
                    eval_id = self._evaluation(
                        emit,
                        child_id,
                        child_id,
                        buffer,
                        candidate["candidate_id"],
                        context_id,
                        search,
                        candidate_row["metrics"],
                        search["search_config"]["cost_assumptions"],
                        run_id,
                    )
                    if obj == docs["SEARCH"][0][0]:
                        evaluation_ids[(candidate["item_id"], candidate["candidate_id"])] = eval_id
        for obj, selection in docs.get("SELECTION", []):
            child_id = "AN-" + obj.content_hash
            registry(
                child_id, "POLICY_SELECTION", obj, selection, len(selection["item_selections"])
            )
            for row in selection["item_selections"]:
                selected = row["selected_candidate_id"]
                emit(
                    "policy_selection",
                    child_result_id=child_id,
                    buffer_id=buffers[row["item_id"]],
                    status="SELECTED" if selected else "NOT_SELECTED",
                    evaluation_id=evaluation_ids[(row["item_id"], selected)] if selected else None,
                    selection_rule_hash=digest(
                        {
                            k: selection[k]
                            for k in (
                                "selection_algorithm",
                                "selection_rule",
                                "ranking_fields",
                                "minimum_fill_rate",
                            )
                        }
                    ),
                    reason_code=row["reason_codes"][0],
                )
        for obj, evaluation in docs.get("EVALUATION", []):
            child_id = "AN-" + obj.content_hash
            registry(child_id, "POLICY_SENSITIVITY", obj, evaluation, len(evaluation["matrix"]))
            cases = {c["case_id"]: c for c in evaluation["evaluation_spec"]["cases"]}
            profiles = {
                c["profile_id"]: c["cost_assumptions"]
                for c in evaluation["evaluation_spec"]["cost_profiles"]
            }
            for matrix in evaluation["matrix"]:
                case = cases[matrix["case_id"]]
                search_child, search = source_map[case["search_content_hash"]]
                for row in matrix["candidate_metrics"]:
                    if row.get("metrics") is None:
                        continue
                    self._evaluation(
                        emit,
                        child_id,
                        search_child,
                        buffers[matrix["item_id"]],
                        row["candidate_id"],
                        "CTX-"
                        + digest(
                            [
                                evaluation["evaluation_spec"],
                                matrix["case_id"],
                                matrix["cost_profile_id"],
                            ]
                        ),
                        search,
                        row["metrics"],
                        profiles[matrix["cost_profile_id"]],
                        run_id,
                    )

    def _evaluation(
        self,
        emit,
        child_id,
        search_id,
        buffer,
        candidate_id,
        context_id,
        search,
        metrics,
        cost,
        run_id,
    ):
        # Full cost/profile provenance is retained in the source artifact/context hash.
        fields = dict(
            engine_run_id=run_id,
            child_result_id=child_id,
            search_child_result_id=search_id,
            buffer_id=buffer,
            candidate_id=candidate_id,
            evaluation_context_id=context_id,
        )
        evaluation_id = digest(fields)
        emit(
            "policy_evaluation",
            evaluation_id=evaluation_id,
            child_result_id=child_id,
            search_child_result_id=search_id,
            buffer_id=buffer,
            candidate_id=candidate_id,
            evaluation_context_id=context_id,
            evaluation_context_hash=context_id.removeprefix("CTX-"),
            evaluation_start_yyyyww=search["context"]["plan_yyyyww"],
            evaluation_end_yyyyww=max(
                r["yyyyww"] for b in search["baseline_items"] for r in b["psi_rows"]
            ),
            **{
                k: metrics[k]
                for k in (
                    "service_status",
                    "current_demand_qty",
                    "current_demand_fulfilled_qty",
                    "current_demand_fill_rate",
                    "average_on_hand_eoh_qty",
                    "ending_on_hand_qty",
                    "ending_backorder_qty",
                    "order_count",
                    "service_feasible",
                    "cost_status",
                    "total_cost",
                    "currency",
                )
            },
            cost_profile_hash=digest(cost) if cost is not None else None,
        )
        return evaluation_id


def normalize_tables(catalog: dict, tables: dict) -> dict:
    """Closed columns, finite exact values, unique PK and deterministic ordering."""
    from datetime import date, datetime
    from uuid import UUID

    result = {}
    for spec in catalog["tables"]:
        if spec["name"] == RECEIPT_TABLE:
            continue
        expected = {c["name"] for c in spec["columns"] if c["name"] != "created_at"}
        values, keys = [], set()
        for raw in tables[spec["name"]]:
            require(set(raw) == expected, "PROJECTION_ROW_SHAPE_MISMATCH")
            row = dict(raw)
            for c in spec["columns"]:
                if c["name"] == "created_at":
                    continue
                key, value, kind = c["name"], row[c["name"]], c["type"]
                if value is None:
                    require(c["nullable"], "PROJECTION_NULL_REQUIRED:" + spec["name"] + "." + key)
                elif kind in (
                    "dsim.io_quantity",
                    "dsim.io_signed_quantity",
                    "dsim.io_ratio",
                    "NUMERIC",
                ):
                    require(isinstance(value, str), "PROJECTION_DECIMAL_STRING_REQUIRED")
                    number = Decimal(value)
                    require(number.is_finite(), "PROJECTION_NONFINITE")
                    require(kind == "dsim.io_signed_quantity" or number >= 0, "PROJECTION_NEGATIVE")
                    require(kind != "dsim.io_ratio" or number <= 1, "PROJECTION_RATIO_RANGE")
                    row[key] = quantity_text(number)
                elif kind == "BOOLEAN":
                    require(type(value) is bool, "PROJECTION_BOOLEAN_REQUIRED")
                elif kind in ("BIGINT", "INTEGER"):
                    require(type(value) is int and value >= 0, "PROJECTION_INTEGER_REQUIRED")
                elif kind == "JSONB":
                    limit = 1048576 if key == "source_metadata" else 65536
                    require(
                        len(canonical_json(value).encode("utf-8")) <= limit,
                        "PROJECTION_JSON_SIZE_LIMIT",
                    )
                    require(
                        type(value) is (dict if key == "source_metadata" else list),
                        "PROJECTION_JSON_SHAPE_MISMATCH",
                    )
                elif kind == "DATE":
                    require(
                        date.fromisoformat(value).isoformat() == value, "PROJECTION_DATE_INVALID"
                    )
                elif kind == "TIMESTAMPTZ":
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
                elif kind == "UUID":
                    require(str(UUID(value)) == value, "PROJECTION_UUID_INVALID")
                elif kind == "dsim.io_sha256":
                    require(
                        isinstance(value, str)
                        and len(value) == 64
                        and all(ch in "0123456789abcdef" for ch in value),
                        "PROJECTION_HASH_INVALID",
                    )
                else:
                    require(
                        isinstance(value, str) and value.strip() and "\x00" not in value,
                        "PROJECTION_TEXT_INVALID",
                    )
                    if "(" in kind:
                        require(
                            len(value) <= int(kind.split("(")[1].split(")")[0]),
                            "PROJECTION_TEXT_TOO_LONG",
                        )
            if "row_content_hash" in row:
                row["row_content_hash"] = digest(
                    {k: v for k, v in row.items() if k != "row_content_hash"}
                )
            pk = canonical_json([row[k] for k in spec["primary_key"]])
            require(pk not in keys, "PROJECTION_DUPLICATE_PK")
            keys.add(pk)
            values.append(row)
        result[spec["name"]] = sorted(
            values, key=lambda r: canonical_json([r[k] for k in spec["primary_key"]])
        )
    require(set(tables) == set(result), "PROJECTION_TABLE_SET_MISMATCH")
    return result

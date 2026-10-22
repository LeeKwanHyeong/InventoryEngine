"""Reuse existing DSIM grains/kinds; selected orders are calculation evidence only."""

from dsio_inventory_engine.inventory_contracts.values import digest, require

SELECTED_SIMULATION = "SELECTED_ORDER_PLAN"


def project_target_result(
    projector, linked, analysis_docs, registry, emit, psi, actions, buffers, run_id
):
    result = linked.result
    search, selection = result["search_result"], result["selection_result"]
    search_obj, selection_obj = linked.source_artifacts
    previous = {doc["content_hash"] for _, doc in analysis_docs.get("SEARCH", [])}
    previous_selection = {doc["content_hash"] for _, doc in analysis_docs.get("SELECTION", [])}
    if search["content_hash"] in previous:
        require(
            selection["content_hash"] in previous_selection,
            "TARGET_LINK_ANALYSIS_SELECTION_CONFLICT",
        )
    else:
        require(
            selection["content_hash"] not in previous_selection,
            "TARGET_LINK_ANALYSIS_SELECTION_CONFLICT",
        )
        projector._analysis(
            {"SEARCH": [(search_obj, search)], "SELECTION": [(selection_obj, selection)]},
            registry,
            emit,
            psi,
            actions,
            buffers,
            run_id,
        )
    # POLICY_SELECTION already exists in the Registry. Do not invent a new SQL
    # child kind or mislabel selected policies as the operational Mathematical PSI.
    child_id = "AN-" + selection["content_hash"]
    plan = result["order_plan"]
    psi(child_id, plan["psi_rows"], SELECTED_SIMULATION)
    actions(
        child_id, plan["decision_evidence"], linked.artifact, "EVIDENCE_ONLY", SELECTED_SIMULATION
    )
    for row in search["candidate_evaluations"]:
        if row["status"] == "FAILED":
            item = row["candidate"]["item_id"]
            emit(
                "replenishment_diagnostic",
                diagnostic_id="DIAG-"
                + digest(
                    [
                        search["content_hash"],
                        row["candidate"]["candidate_id"],
                        row["failure_reason"],
                    ]
                ),
                child_result_id="AN-" + search["content_hash"],
                buffer_id=buffers[item],
                subject_item_id=item,
                yyyyww=None,
                severity="WARNING",
                reason_code=row["failure_reason"],
                detail_artifact_reference=search_obj.reference,
                detail_hash=search_obj.content_hash,
            )

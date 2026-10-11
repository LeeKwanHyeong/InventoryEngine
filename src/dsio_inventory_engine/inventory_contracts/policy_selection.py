"""Frozen selection identity and fail-closed publication boundary."""

from dsio_inventory_engine.inventory_contracts.values import (
    digest,
    hash_value,
    item_identifier,
    require,
)

SELECTION_RULE = "SERVICE_FEASIBLE_THEN_LOWEST_COST"
RANK_FIELDS = ("total_cost", "average_on_hand_eoh_qty", "order_count", "candidate_id")
SELECTION_REASONS = (
    "SEARCH_ITEM_EXCLUDED",
    "NO_CANDIDATES_EVALUATED",
    "ALL_CANDIDATES_FAILED",
    "CANDIDATE_FAILED",
    "ORDER_GATE_BLOCKED",
    "RECOMMENDATION_CALCULATION_BLOCKED",
    "NO_DEMAND_IN_HORIZON",
    "FILL_RATE_BELOW_MINIMUM",
    "ENDING_BACKORDER_REMAINS",
    "COST_PROFILE_NOT_BOUND",
    "RANKED_FIRST",
    "HIGHER_TOTAL_COST",
    "HIGHER_AVERAGE_INVENTORY",
    "MORE_ORDERS",
    "CANDIDATE_ID_TIE_BREAK",
    "ONLY_ELIGIBLE_CANDIDATE",
    "LOWEST_TOTAL_COST",
    "TIE_RESOLVED_BY_AVERAGE_INVENTORY",
    "TIE_RESOLVED_BY_ORDER_COUNT",
    "TIE_RESOLVED_BY_CANDIDATE_ID",
)
RESULT_FIELDS = frozenset(
    (
        "contract_id",
        "contract_version",
        "status",
        "selection_algorithm",
        "selection_rule",
        "ranking_fields",
        "evaluation_mode",
        "optimality_scope",
        "context",
        "search_result_reference",
        "search_result_content_hash",
        "minimum_fill_rate",
        "terminal_condition",
        "item_selections",
        "selected_item_count",
        "not_selected_item_count",
        "automatic_publish_allowed",
        "automatic_order_allowed",
        "operational_policy_approved",
        "database_writes",
        "canonical_input_hash",
        "prepared_input_hash",
        "mathematical_input_hash",
        "search_config_hash",
        "config_hash",
        "classification_snapshot_id",
        "classification_content_hash",
        "content_hash",
    )
)
ITEM_FIELDS = frozenset(
    (
        "item_id",
        "status",
        "reason_codes",
        "search_exclusion_reason",
        "selected_candidate_id",
        "selected_candidate_result_hash",
        "selected_policy",
        "selected_score",
        "source_review_required",
        "candidate_decisions",
    )
)
DECISION_FIELDS = frozenset(
    (
        "candidate_id",
        "candidate_result_hash",
        "status",
        "rank",
        "reason_codes",
        "failure_reason",
        "score",
    )
)


def _reasons(value: list) -> None:
    require(
        type(value) is list
        and bool(value)
        and all(type(reason) is str and reason in SELECTION_REASONS for reason in value)
        and len(set(value)) == len(value),
        "POLICY_SELECTION_REASON_INVALID",
    )


def verify_policy_selection_result(value: dict) -> dict:
    require(
        type(value) is dict
        and set(value) == RESULT_FIELDS
        and value.get("contract_id") == "inventory-policy-selection-result"
        and value.get("contract_version") == "1.0.0"
        and value.get("status") == "POLICY_SELECTION_COMPUTED_LOCALLY"
        and value.get("selection_algorithm") == "SERVICE_COST_INVENTORY_ORDER_LEXICOGRAPHIC_V1"
        and value.get("selection_rule") == SELECTION_RULE
        and value.get("ranking_fields") == list(RANK_FIELDS),
        "POLICY_SELECTION_CONTRACT_INVALID",
    )
    require(
        value.get("automatic_publish_allowed") is False
        and value.get("automatic_order_allowed") is False
        and value.get("operational_policy_approved") is False
        and value.get("database_writes") is False
        and value.get("evaluation_mode") == "DEVELOPMENT_SHADOW",
        "POLICY_SELECTION_PUBLICATION_FORBIDDEN",
    )
    require(
        value["optimality_scope"] == "EVALUATED_CANDIDATE_SET_ONLY",
        "POLICY_SELECTION_SCOPE_INVALID",
    )
    require(
        value.get("content_hash")
        == digest({k: v for k, v in value.items() if k != "content_hash"}),
        "POLICY_SELECTION_HASH_MISMATCH",
    )
    hash_value(value["search_result_content_hash"])
    require(
        value["search_result_reference"] == "PSR-" + value["search_result_content_hash"],
        "POLICY_SELECTION_SOURCE_REFERENCE_MISMATCH",
    )
    items = value["item_selections"]
    require(
        type(items) is list
        and bool(items)
        and all(type(row) is dict and set(row) == ITEM_FIELDS for row in items),
        "POLICY_SELECTION_ITEM_FIELDS",
    )
    require(
        len({r["item_id"] for r in items}) == len(items),
        "POLICY_SELECTION_ITEM_DUPLICATE",
    )
    for item in items:
        item_identifier(item["item_id"])
        selected = item["status"] == "SELECTED"
        require(item["status"] in ("SELECTED", "NOT_SELECTED"), "POLICY_SELECTION_ITEM_STATUS")
        _reasons(item["reason_codes"])
        require(
            (item["selected_candidate_id"] is not None) == selected
            and (item["selected_candidate_result_hash"] is not None) == selected,
            "POLICY_SELECTION_POINTER_INVALID",
        )
        require(
            (item["selected_policy"] is not None) == selected
            and (item["selected_score"] is not None) == selected,
            "POLICY_SELECTION_EMPTY_SELECTION",
        )
        require(
            (
                type(item["source_review_required"]) is bool
                if selected
                else item["source_review_required"] is None
            ),
            "POLICY_SELECTION_REVIEW_INVALID",
        )
        decisions = item["candidate_decisions"]
        require(
            type(decisions) is list
            and len(decisions) <= 256
            and all(type(row) is dict and set(row) == DECISION_FIELDS for row in decisions),
            "POLICY_SELECTION_DECISION_FIELDS",
        )
        require(
            len({row["candidate_id"] for row in decisions}) == len(decisions),
            "POLICY_SELECTION_DECISION_DUPLICATE",
        )
        for row in decisions:
            _reasons(row["reason_codes"])
            hash_value(row["candidate_result_hash"])
            require(
                row["status"] in ("SELECTED", "NOT_SELECTED", "INELIGIBLE", "FAILED"),
                "POLICY_SELECTION_DECISION_STATUS",
            )
            ranked = row["status"] in ("SELECTED", "NOT_SELECTED")
            require(
                (type(row["rank"]) is int and row["rank"] >= 1 and type(row["score"]) is dict)
                if ranked
                else row["rank"] is None and row["score"] is None,
                "POLICY_SELECTION_RANK_INVALID",
            )
            require(
                (type(row["failure_reason"]) is str and bool(row["failure_reason"]))
                if row["status"] == "FAILED"
                else row["failure_reason"] is None,
                "POLICY_SELECTION_FAILURE_INVALID",
            )
        ranks = sorted(row["rank"] for row in decisions if row["rank"] is not None)
        require(ranks == list(range(1, len(ranks) + 1)), "POLICY_SELECTION_RANK_INVALID")
        chosen = [r for r in decisions if r["status"] == "SELECTED"]
        require(len(chosen) == int(selected), "POLICY_SELECTION_CHOSEN_COUNT")
        if selected:
            require(
                chosen[0]["candidate_id"] == item["selected_candidate_id"]
                and chosen[0]["candidate_result_hash"] == item["selected_candidate_result_hash"]
                and chosen[0]["rank"] == 1,
                "POLICY_SELECTION_POINTER_INVALID",
            )
            require(
                item["selected_score"] == chosen[0]["score"]
                and item["selected_policy"]["candidate_id"] == item["selected_candidate_id"]
                and item["selected_policy"]["item_id"] == item["item_id"],
                "POLICY_SELECTION_POLICY_POINTER_INVALID",
            )
        else:
            require(not ranks, "POLICY_SELECTION_RANK_INVALID")
    count = sum(r["status"] == "SELECTED" for r in items)
    require(
        type(value["selected_item_count"]) is int
        and type(value["not_selected_item_count"]) is int
        and value["selected_item_count"] == count
        and value["not_selected_item_count"] == len(items) - count,
        "POLICY_SELECTION_COUNT_MISMATCH",
    )
    return value

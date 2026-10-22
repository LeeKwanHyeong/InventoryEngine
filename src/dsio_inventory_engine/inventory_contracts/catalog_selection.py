"""Separate offline Catalog selection identity; never a PSR/PSEL publication contract."""

from decimal import Context, Decimal, DecimalException, ROUND_HALF_EVEN, localcontext
import re

from .policy_selection import DECISION_FIELDS, ITEM_FIELDS, RANK_FIELDS, SELECTION_REASONS
from .values import InventoryInputError, digest, hash_value, item_identifier, quantity_text, require

CONTRACT_ID = "inventory-offline-catalog-policy-selection-result"
VERSION = "1.0.0"
SOURCE_PREFIX = "OCPSI-"
RESULT_PREFIX = "OCPSEL-"
NUMBERS = Context(prec=40, rounding=ROUND_HALF_EVEN)
INPUT_HASH_FIELDS = (
    "canonical_input_hash",
    "prepared_input_hash",
    "mathematical_input_hash",
    "search_config_hash",
    "config_hash",
    "classification_content_hash",
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
        "verification_profile",
        "context",
        "source_result_reference",
        "source_result_content_hash",
        "candidate_catalog_reference",
        "candidate_catalog_hash",
        "candidate_mode",
        "minimum_fill_rate",
        "terminal_condition",
        "classification_snapshot_id",
        "item_selections",
        "selected_item_count",
        "not_selected_item_count",
        "automatic_publish_allowed",
        "automatic_order_allowed",
        "operational_policy_approved",
        "database_writes",
        "content_hash",
        *INPUT_HASH_FIELDS,
    )
)
QUANTITY_METRICS = (
    "current_demand_qty",
    "current_demand_fulfilled_qty",
    "average_on_hand_eoh_qty",
    "ending_on_hand_qty",
    "ending_backorder_qty",
    "backorder_unit_weeks",
    "forecast_shortage_qty",
    "ordered_qty",
)
METRIC_FIELDS = frozenset(
    (
        *QUANTITY_METRICS,
        "service_status",
        "current_demand_fill_rate",
        "stockout_free_active_bucket_ratio",
        "observed_average_on_hand_mos",
        "order_count",
        "guard_rejection_count",
        "service_feasible",
        "cost_status",
        "cost_components",
        "total_cost",
        "currency",
    )
)
COMPARISON_METRICS = (
    "current_demand_fill_rate",
    "average_on_hand_eoh_qty",
    "ending_on_hand_qty",
    "ending_backorder_qty",
    "backorder_unit_weeks",
    "forecast_shortage_qty",
    "order_count",
    "ordered_qty",
    "total_cost",
)
COST_COMPONENTS = frozenset(("holding", "backorder", "ordering_fixed", "ordering_variable"))
SCORE_FIELDS = frozenset(
    (
        "candidate_id",
        "total_cost",
        "currency",
        "average_on_hand_eoh_qty",
        "order_count",
        "current_demand_fill_rate",
        "ending_backorder_qty",
    )
)
PARAMETERS = ("policy_kind", "rmos", "emos", "target_service_level", "review_cycle_weeks")
CANDIDATE_FIELDS = frozenset(
    (
        *PARAMETERS,
        "candidate_id",
        "item_id",
        "search_group_id",
        "semantic_policy_hash",
        "range_source",
        "override_reason",
    )
)


def _number(value) -> Decimal:
    require(
        type(value) is str
        and len(value) <= 96
        and re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value) is not None,
        "CATALOG_SELECTION_NUMBER_INVALID",
    )
    return Decimal(value)


def _reasons(value) -> None:
    require(
        type(value) is list
        and bool(value)
        and all(type(reason) is str and reason in SELECTION_REASONS for reason in value)
        and len(value) == len(set(value)),
        "CATALOG_SELECTION_REASON_INVALID",
    )


def _candidate_id(value) -> None:
    require(
        type(value) is str and re.fullmatch(r"OCCP-[0-9a-f]{64}", value) is not None,
        "CATALOG_SELECTION_CANDIDATE_ID_INVALID",
    )


def compare_baseline_metrics(candidate: dict, baseline: dict) -> dict:
    """Signed candidate-minus-baseline changes, not a cross-mode superiority claim."""
    with localcontext(NUMBERS):
        return {
            key + "_delta": None
            if candidate[key] is None or baseline[key] is None
            else quantity_text(Decimal(candidate[key]) - Decimal(baseline[key]))
            for key in COMPARISON_METRICS
        }


def _metrics(value, minimum: Decimal, *, candidate: bool) -> None:
    require(
        type(value) is dict
        and set(value) == METRIC_FIELDS | ({"policy_selection_eligible"} if candidate else set()),
        "CATALOG_SELECTION_METRIC_FIELDS",
    )
    for key in QUANTITY_METRICS:
        _number(value[key])
    for key in ("order_count", "guard_rejection_count"):
        require(type(value[key]) is int and value[key] >= 0, "CATALOG_SELECTION_METRIC_INVALID")
    demand = _number(value["current_demand_qty"])
    fulfilled = _number(value["current_demand_fulfilled_qty"])
    require(fulfilled <= demand, "CATALOG_SELECTION_METRIC_INVALID")
    fill = fulfilled / demand if demand else None
    require(
        value["service_status"] == ("AVAILABLE" if demand else "NO_DEMAND_IN_HORIZON")
        and value["current_demand_fill_rate"]
        == (quantity_text(fill) if fill is not None else None),
        "CATALOG_SELECTION_SERVICE_INVALID",
    )
    ratio = value["stockout_free_active_bucket_ratio"]
    require(
        (ratio is None if not demand else ratio is not None and _number(ratio) <= 1),
        "CATALOG_SELECTION_SERVICE_INVALID",
    )
    if value["observed_average_on_hand_mos"] is not None:
        _number(value["observed_average_on_hand_mos"])
    feasible = fill is not None and fill >= minimum and _number(value["ending_backorder_qty"]) == 0
    require(
        type(value["service_feasible"]) is bool and value["service_feasible"] == feasible,
        "CATALOG_SELECTION_SERVICE_INVALID",
    )
    if candidate:
        require(
            type(value["policy_selection_eligible"]) is bool
            and (not value["policy_selection_eligible"] or feasible),
            "CATALOG_SELECTION_SERVICE_INVALID",
        )
    if value["cost_status"] == "COST_PROFILE_NOT_BOUND":
        require(
            all(value[key] is None for key in ("cost_components", "total_cost", "currency")),
            "CATALOG_SELECTION_COST_INVALID",
        )
    else:
        components = value["cost_components"]
        require(
            value["cost_status"] == "AVAILABLE_DEVELOPMENT_ASSUMPTION"
            and type(components) is dict
            and set(components) == COST_COMPONENTS
            and type(value["currency"]) is str
            and re.fullmatch(r"[A-Z]{3}", value["currency"]) is not None,
            "CATALOG_SELECTION_COST_INVALID",
        )
        require(
            _number(value["total_cost"])
            == sum((_number(v) for v in components.values()), Decimal(0)),
            "CATALOG_SELECTION_COST_INVALID",
        )


def verify_catalog_selection_result(value: dict) -> dict:
    """Validate closed shape and internal consistency; source replay is separately mandatory."""
    try:
        with localcontext(NUMBERS):
            _verify(value)
    except InventoryInputError:
        raise
    except (KeyError, TypeError, ValueError, DecimalException):
        raise InventoryInputError("CATALOG_SELECTION_CONTRACT_INVALID") from None
    return value


def _verify(value: dict) -> None:
    require(
        type(value) is dict
        and set(value) == RESULT_FIELDS
        and value["contract_id"] == CONTRACT_ID
        and value["contract_version"] == VERSION
        and value["status"] == "OFFLINE_CATALOG_POLICY_SELECTION_COMPUTED"
        and value["selection_algorithm"] == "SERVICE_COST_INVENTORY_ORDER_LEXICOGRAPHIC_V1"
        and value["selection_rule"] == "SERVICE_FEASIBLE_THEN_LOWEST_COST"
        and value["ranking_fields"] == list(RANK_FIELDS)
        and value["evaluation_mode"] == "DEVELOPMENT_SHADOW"
        and value["optimality_scope"] == "EVALUATED_CANDIDATE_SET_ONLY"
        and value["verification_profile"] == "EXACT_SOURCE_REPLAY_GUARD_PSI_METRICS_V1"
        and value["candidate_mode"] in ("ABC_XYZ_SHARED_BUDGET", "LEGACY_STYLE", "WIDE_REFERENCE")
        and value["terminal_condition"]
        == "FINITE_HORIZON_NO_TERMINAL_VALUE_ARRIVALS_OUTSIDE_HORIZON_REJECTED",
        "CATALOG_SELECTION_CONTRACT_INVALID",
    )
    require(
        all(
            value[key] is False
            for key in (
                "automatic_publish_allowed",
                "automatic_order_allowed",
                "operational_policy_approved",
                "database_writes",
            )
        ),
        "CATALOG_SELECTION_PUBLICATION_FORBIDDEN",
    )
    require(
        value["content_hash"] == digest({k: v for k, v in value.items() if k != "content_hash"}),
        "CATALOG_SELECTION_HASH_MISMATCH",
    )
    for key in (*INPUT_HASH_FIELDS, "source_result_content_hash", "candidate_catalog_hash"):
        hash_value(value[key])
    require(
        value["source_result_reference"] == SOURCE_PREFIX + value["source_result_content_hash"]
        and value["candidate_catalog_reference"] == "OCC-" + value["candidate_catalog_hash"],
        "CATALOG_SELECTION_REFERENCE_MISMATCH",
    )
    require(
        type(value["context"]) is dict
        and bool(value["context"])
        and type(value["classification_snapshot_id"]) is str
        and bool(value["classification_snapshot_id"]),
        "CATALOG_SELECTION_SCOPE_INVALID",
    )
    minimum = _number(value["minimum_fill_rate"])
    require(minimum <= 1, "CATALOG_SELECTION_SERVICE_INVALID")
    items = value["item_selections"]
    require(
        type(items) is list
        and bool(items)
        and all(
            type(row) is dict and set(row) == ITEM_FIELDS | {"baseline_metrics"} for row in items
        ),
        "CATALOG_SELECTION_ITEM_FIELDS",
    )
    require(len({r["item_id"] for r in items}) == len(items), "CATALOG_SELECTION_ITEM_DUPLICATE")
    for item in items:
        item_identifier(item["item_id"])
        _reasons(item["reason_codes"])
        _metrics(item["baseline_metrics"], minimum, candidate=False)
        require(item["status"] in ("SELECTED", "NOT_SELECTED"), "CATALOG_SELECTION_ITEM_STATUS")
        selected = item["status"] == "SELECTED"
        require(
            item["search_exclusion_reason"] is None
            or type(item["search_exclusion_reason"]) is str
            and bool(item["search_exclusion_reason"]),
            "CATALOG_SELECTION_EXCLUSION_INVALID",
        )
        decisions = item["candidate_decisions"]
        require(
            type(decisions) is list
            and len(decisions) <= 88
            and all(
                type(row) is dict
                and set(row) == DECISION_FIELDS | {"verified_metrics", "baseline_comparison"}
                for row in decisions
            ),
            "CATALOG_SELECTION_DECISION_FIELDS",
        )
        require(
            len({r["candidate_id"] for r in decisions}) == len(decisions),
            "CATALOG_SELECTION_CANDIDATE_DUPLICATE",
        )
        for row in decisions:
            _candidate_id(row["candidate_id"])
            hash_value(row["candidate_result_hash"])
            _reasons(row["reason_codes"])
            require(
                row["status"] in ("SELECTED", "NOT_SELECTED", "INELIGIBLE", "FAILED"),
                "CATALOG_SELECTION_DECISION_STATUS",
            )
            ranked = row["status"] in ("SELECTED", "NOT_SELECTED")
            require(
                (type(row["rank"]) is int and row["rank"] >= 1 and type(row["score"]) is dict)
                if ranked
                else row["rank"] is None and row["score"] is None,
                "CATALOG_SELECTION_RANK_INVALID",
            )
            if row["status"] == "FAILED":
                require(
                    type(row["failure_reason"]) is str
                    and bool(row["failure_reason"])
                    and row["verified_metrics"] is None
                    and row["baseline_comparison"] is None,
                    "CATALOG_SELECTION_FAILURE_INVALID",
                )
                continue
            require(row["failure_reason"] is None, "CATALOG_SELECTION_FAILURE_INVALID")
            metric = row["verified_metrics"]
            _metrics(metric, minimum, candidate=True)
            require(
                row["baseline_comparison"]
                == compare_baseline_metrics(metric, item["baseline_metrics"]),
                "CATALOG_SELECTION_COMPARISON_INVALID",
            )
            if ranked:
                score = row["score"]
                require(
                    set(score) == SCORE_FIELDS
                    and score["candidate_id"] == row["candidate_id"]
                    and all(score[key] == metric[key] for key in SCORE_FIELDS - {"candidate_id"})
                    and metric["policy_selection_eligible"]
                    and metric["total_cost"] is not None,
                    "CATALOG_SELECTION_SCORE_INVALID",
                )
        ranked = [r for r in decisions if r["rank"] is not None]
        require(
            sorted(r["rank"] for r in ranked) == list(range(1, len(ranked) + 1)),
            "CATALOG_SELECTION_RANK_INVALID",
        )
        chosen = [r for r in decisions if r["status"] == "SELECTED"]
        require(len(chosen) == int(selected), "CATALOG_SELECTION_POINTER_INVALID")
        if selected:
            row, policy = chosen[0], item["selected_policy"]
            require(
                row["rank"] == 1
                and row["candidate_id"] == item["selected_candidate_id"]
                and row["candidate_result_hash"] == item["selected_candidate_result_hash"]
                and item["selected_score"] == row["score"]
                and type(item["source_review_required"]) is bool
                and item["search_exclusion_reason"] is None,
                "CATALOG_SELECTION_POINTER_INVALID",
            )
            require(
                type(policy) is dict
                and set(policy) == CANDIDATE_FIELDS
                and policy["item_id"] == item["item_id"]
                and policy["candidate_id"] == row["candidate_id"]
                and policy["candidate_id"]
                == "OCCP-" + digest({k: v for k, v in policy.items() if k != "candidate_id"})
                and policy["semantic_policy_hash"] == digest({k: policy[k] for k in PARAMETERS}),
                "CATALOG_SELECTION_POLICY_POINTER_INVALID",
            )
        else:
            require(
                not ranked
                and all(
                    item[key] is None
                    for key in (
                        "selected_candidate_id",
                        "selected_candidate_result_hash",
                        "selected_policy",
                        "selected_score",
                        "source_review_required",
                    )
                ),
                "CATALOG_SELECTION_EMPTY_SELECTION",
            )
    selected_count = sum(row["status"] == "SELECTED" for row in items)
    require(
        type(value["selected_item_count"]) is int
        and type(value["not_selected_item_count"]) is int
        and value["selected_item_count"] == selected_count
        and value["not_selected_item_count"] == len(items) - selected_count,
        "CATALOG_SELECTION_COUNT_MISMATCH",
    )

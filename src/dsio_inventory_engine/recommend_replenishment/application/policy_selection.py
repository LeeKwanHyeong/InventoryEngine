"""Choose a bounded development policy per item, never approve or place an order."""

from __future__ import annotations

import copy
import re
from decimal import Decimal, DecimalException, localcontext
from typing import Any

from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.policy_search import (
    candidate_policies,
    normalize_policy_search,
    policy_search_hash,
)
from dsio_inventory_engine.inventory_contracts.policy_search_result import (
    verify_policy_search_result,
)
from dsio_inventory_engine.inventory_contracts.policy_selection import (
    RANK_FIELDS,
    SELECTION_RULE,
    verify_policy_selection_result,
)
from dsio_inventory_engine.inventory_contracts.replenishment import (
    ReplenishmentObservation,
    ReplenishmentProposal,
)
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    canonical_json,
    digest,
    hash_value,
    require,
)
from dsio_inventory_engine.simulate_inventory.application.step import InventoryState, advance_bucket
from .guard import validate_action
from .policy_search import DESCRIPTOR, NUMBERS, evaluate_rows

COORDINATES = (
    "company_cd",
    "subs_cd",
    "site_cd",
    "item_id",
    "uom",
    "yyyyww",
    "seq",
    "start_date",
    "end_date",
)
DEMAND_FIELDS = (
    "confirmed_customer_order_qty",
    "net_forecast_qty",
    "gross_forecast_qty",
    "forecast_consumed_qty",
    "forecast_netting_mode",
    "confirmed_supplier_receipt_qty",
)
LOSER_REASONS = (
    "HIGHER_TOTAL_COST",
    "HIGHER_AVERAGE_INVENTORY",
    "MORE_ORDERS",
    "CANDIDATE_ID_TIE_BREAK",
)
WINNER_REASONS = (
    "LOWEST_TOTAL_COST",
    "TIE_RESOLVED_BY_AVERAGE_INVENTORY",
    "TIE_RESOLVED_BY_ORDER_COUNT",
    "TIE_RESOLVED_BY_CANDIDATE_ID",
)


def _number(value: Any) -> Decimal:
    require(
        type(value) is str
        and len(value) <= 96
        and re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value) is not None,
        "POLICY_SELECTION_NUMBER_INVALID",
    )
    number = Decimal(value)
    require(number.is_finite() and number >= 0, "POLICY_SELECTION_NUMBER_INVALID")
    return number


def ranking_key(score: dict) -> tuple[Decimal, Decimal, int, str]:
    """Exact decimal ordering; input list order, tolerances and float rounding never decide a tie."""
    require(
        type(score["order_count"]) is int and score["order_count"] >= 0,
        "POLICY_SELECTION_ORDER_COUNT_INVALID",
    )
    require(
        type(score["candidate_id"]) is str
        and re.fullmatch(r"PC-[0-9a-f]{64}", score["candidate_id"]) is not None,
        "POLICY_SELECTION_CANDIDATE_ID_INVALID",
    )
    return (
        _number(score["total_cost"]),
        _number(score["average_on_hand_eoh_qty"]),
        score["order_count"],
        score["candidate_id"],
    )


def _first_difference(first: dict, second: dict) -> int:
    return next(
        index
        for index, (a, b) in enumerate(zip(ranking_key(first), ranking_key(second), strict=True))
        if a != b
    )


def _gate(admission: dict, source: dict, item: str) -> tuple[bool, bool, bool]:
    require(
        admission["item_id"] == item
        and admission["execution_purpose"] == "SHADOW"
        and admission["strategy"] == DESCRIPTOR,
        "POLICY_SELECTION_ADMISSION_MISMATCH",
    )
    hash_value(admission["effective_policy_hash"])
    require(
        admission.get("effective_policy_contract_version") == "2.0.0",
        "POLICY_SELECTION_GATE_VERSION_INVALID",
    )
    require(
        admission["classification_config_hash"] == source["config_hash"],
        "POLICY_SELECTION_CONFIG_BINDING_MISMATCH",
    )
    require(
        all(
            type(admission[key]) is bool
            for key in (
                "no_order_gate",
                "recommendation_calculation_allowed",
                "automatic_publish_allowed",
                "automatic_order_allowed",
            )
        ),
        "POLICY_SELECTION_GATE_INVALID",
    )
    require(
        not admission["automatic_publish_allowed"] and not admission["automatic_order_allowed"],
        "POLICY_SELECTION_PUBLICATION_FORBIDDEN",
    )
    require(
        admission["effective_order_action"] in ("ALLOW", "REVIEW", "BLOCK")
        and (admission["effective_order_action"] == "BLOCK") == admission["no_order_gate"],
        "POLICY_SELECTION_GATE_INVALID",
    )
    return (
        admission["no_order_gate"],
        admission["recommendation_calculation_allowed"],
        admission["effective_order_action"] == "REVIEW"
        or admission["effective_approval_level"] in ("STANDARD", "HIGH_VALUE"),
    )


def verify_candidate_evaluation(child: dict, baseline: dict, source: dict) -> dict:
    """Re-run common action guard and stock arithmetic before trusting service/cost flags."""
    item = baseline["item_id"]
    rows, base, decisions, orders = (
        child["psi_rows"],
        baseline["psi_rows"],
        child["decision_evidence"],
        child["orders"],
    )
    require(
        len(rows) == len(base) == len(decisions) and len(rows) > 0,
        "POLICY_SELECTION_HORIZON_MISMATCH",
    )
    require(
        all(
            row["seq"] == index and row["start_date"] < row["end_date"]
            for index, row in enumerate(base)
        ),
        "POLICY_SELECTION_CALENDAR_INVALID",
    )
    require(
        len({row["yyyyww"] for row in base}) == len(base)
        and [r["start_date"] for r in base] == sorted(r["start_date"] for r in base),
        "POLICY_SELECTION_CALENDAR_INVALID",
    )
    require(
        len({order["decision_id"] for order in orders}) == len(orders),
        "POLICY_SELECTION_ORDER_DUPLICATE",
    )
    require(
        len({decision["observation"]["decision_id"] for decision in decisions}) == len(decisions),
        "POLICY_SELECTION_DECISION_DUPLICATE",
    )
    order_by_decision = {order["decision_id"]: order for order in orders}
    state = InventoryState(
        _number(base[0]["boh_qty"]),
        _number(base[0]["reserved_qty"]),
        _number(base[0]["backorder_open_qty"]),
    )
    admission = decisions[0]["effective_policy_admission"]
    blocked, calculable, review = _gate(admission, source, item)
    monthly = _number(decisions[0]["candidate_policy_evidence"]["monthly_mean_demand_qty"])
    require(monthly > 0, "POLICY_SELECTION_MONTHLY_DEMAND_INVALID")
    accepted_ids = set()
    for row, reference, decision in zip(rows, base, decisions, strict=True):
        require(
            all(row[key] == reference[key] for key in COORDINATES + DEMAND_FIELDS)
            and row["item_id"] == item,
            "POLICY_SELECTION_INPUT_ROWS_MISMATCH",
        )
        require(
            all(row[key] == source["context"][key] for key in ("company_cd", "subs_cd", "site_cd")),
            "POLICY_SELECTION_SCOPE_MISMATCH",
        )
        require(
            canonical_json(decision["effective_policy_admission"]) == canonical_json(admission),
            "POLICY_SELECTION_GATE_CHANGED",
        )
        evidence = decision["candidate_policy_evidence"]
        require(
            evidence["candidate_id"] == child["candidate"]["candidate_id"]
            and _number(evidence["monthly_mean_demand_qty"]) == monthly,
            "POLICY_SELECTION_POLICY_EVIDENCE_MISMATCH",
        )
        obs = ReplenishmentObservation.from_dict(decision["observation"])
        observation = obs.to_dict()
        require(
            obs.content_hash == decision["observation_hash"]
            and observation["input_content_hash"] == child["candidate_execution_hash"]
            and observation["context"] == source["context"]
            and observation["item_id"] == item
            and observation["decision_date"] == row["start_date"]
            and observation["strategy"] == DESCRIPTOR,
            "POLICY_SELECTION_OBSERVATION_MISMATCH",
        )
        require(
            all(
                _number(observation["state"][key]) == value
                for key, value in (
                    ("available_qty", state.available_qty),
                    ("reserved_qty", state.reserved_qty),
                    ("backorder_qty", state.backorder_qty),
                )
            ),
            "POLICY_SELECTION_ROLL_FORWARD_MISMATCH",
        )
        proposal = ReplenishmentProposal.from_dict(decision["proposal"]).to_dict()
        validation = validate_action(observation, proposal)
        require(
            canonical_json(validation) == canonical_json(decision["validation"]),
            "POLICY_SELECTION_GUARD_REPLAY_MISMATCH",
        )
        require(
            not (blocked or not calculable) or proposal["action_type"] == "HOLD",
            "POLICY_SELECTION_GATE_BYPASS",
        )
        if _number(validation["accepted_order_qty"]) > 0:
            decision_id = observation["decision_id"]
            order = order_by_decision.get(decision_id)
            require(
                order is not None
                and order["item_id"] == item
                and order["order_id"] == "REC-" + decision_id
                and order["order_date"] == row["start_date"]
                and order["quantity"] == validation["accepted_order_qty"]
                and order["automatic_order_allowed"] is False
                and order["effective_policy_hash"] == admission["effective_policy_hash"],
                "POLICY_SELECTION_ORDER_MISMATCH",
            )
            require(
                all(
                    order[key] == validation[key]
                    for key in ("due_date", "receipt_date", "receipt_bucket")
                ),
                "POLICY_SELECTION_ORDER_MISMATCH",
            )
            accepted_ids.add(decision_id)
        incoming = sum(
            (
                _number(order["quantity"])
                for order in orders
                if order["receipt_bucket"] == row["yyyyww"]
            ),
            Decimal(0),
        )
        state, quantities = advance_bucket(
            state,
            confirmed_customer_order_qty=_number(row["confirmed_customer_order_qty"]),
            net_forecast_qty=_number(row["net_forecast_qty"]),
            confirmed_supplier_receipt_qty=_number(row["confirmed_supplier_receipt_qty"]),
            recommended_receipt_qty=incoming,
        )
        require(
            all(row[key] == value for key, value in quantities.items()),
            "POLICY_SELECTION_PSI_REPLAY_MISMATCH",
        )
    require(accepted_ids == set(order_by_decision), "POLICY_SELECTION_UNBACKED_ORDER")
    metrics = evaluate_rows(
        rows,
        decisions,
        orders,
        source["search_config"]["cost_assumptions"],
        source["search_config"]["minimum_fill_rate"],
        monthly,
    )
    metrics["policy_selection_eligible"] = (
        metrics["service_feasible"] and not blocked and calculable
    )
    require(
        canonical_json(metrics) == canonical_json(child["metrics"]),
        "POLICY_SELECTION_METRICS_REPLAY_MISMATCH",
    )
    return {"metrics": metrics, "blocked": blocked, "calculable": calculable, "review": review}


def assess_candidate(child: dict, verified: dict | None) -> dict:
    result = {
        "candidate_id": child["candidate"]["candidate_id"],
        "candidate_result_hash": child["content_hash"],
        "status": "FAILED",
        "rank": None,
        "reason_codes": ["CANDIDATE_FAILED"],
        "failure_reason": child["failure_reason"],
        "score": None,
    }
    if child["status"] == "FAILED":
        return result
    metrics = verified["metrics"]
    reasons = []
    if verified["blocked"]:
        reasons.append("ORDER_GATE_BLOCKED")
    if not verified["calculable"]:
        reasons.append("RECOMMENDATION_CALCULATION_BLOCKED")
    if metrics["service_status"] == "NO_DEMAND_IN_HORIZON":
        reasons.append("NO_DEMAND_IN_HORIZON")
    elif not metrics["service_feasible"]:
        # The threshold itself is checked independently of the persisted flag.
        if _number(metrics["current_demand_fill_rate"]) < verified["minimum_fill_rate"]:
            reasons.append("FILL_RATE_BELOW_MINIMUM")
    if _number(metrics["ending_backorder_qty"]) > 0:
        reasons.append("ENDING_BACKORDER_REMAINS")
    if metrics["cost_status"] == "COST_PROFILE_NOT_BOUND":
        reasons.append("COST_PROFILE_NOT_BOUND")
    score = (
        None
        if reasons
        else {
            "candidate_id": result["candidate_id"],
            "total_cost": metrics["total_cost"],
            "currency": metrics["currency"],
            "average_on_hand_eoh_qty": metrics["average_on_hand_eoh_qty"],
            "order_count": metrics["order_count"],
            "current_demand_fill_rate": metrics["current_demand_fill_rate"],
            "ending_backorder_qty": metrics["ending_backorder_qty"],
        }
    )
    if score is not None:
        ranking_key(score)
    result.update(
        status="INELIGIBLE" if reasons else "NOT_SELECTED", reason_codes=reasons, score=score
    )
    return result


def rank_candidate_assessments(assessments: list[dict]) -> tuple[list[dict], list[str]]:
    """Shared deterministic comparison; mutates only the caller's fresh assessment records."""
    eligible = sorted(
        (a for a in assessments if a["score"] is not None), key=lambda a: ranking_key(a["score"])
    )
    for rank, assessment in enumerate(eligible, 1):
        assessment["rank"] = rank
        assessment["status"] = "SELECTED" if rank == 1 else "NOT_SELECTED"
        assessment["reason_codes"] = (
            ["RANKED_FIRST"]
            if rank == 1
            else [LOSER_REASONS[_first_difference(eligible[0]["score"], assessment["score"])]]
        )
    reasons = (
        []
        if not eligible
        else [
            "RANKED_FIRST",
            "ONLY_ELIGIBLE_CANDIDATE"
            if len(eligible) == 1
            else WINNER_REASONS[_first_difference(eligible[0]["score"], eligible[1]["score"])],
        ]
    )
    return eligible, reasons


class RunPolicySelectionUseCase:
    def __init__(self, deployment: DeploymentScope):
        self.deployment = deployment

    def execute(self, search_result: dict) -> dict:
        return self._checked(search_result, verify_only=False)

    def verify_source(self, search_result: dict) -> None:
        """Replay input, Guard, PSI and metrics without ranking (including holdout intake)."""
        self._checked(search_result, verify_only=True)

    def _checked(self, search_result: dict, *, verify_only: bool) -> dict:
        require(self.deployment.environment == "DEVELOPMENT", "POLICY_SELECTION_DEVELOPMENT_ONLY")
        try:
            with localcontext(NUMBERS):
                return self._execute(copy.deepcopy(search_result), verify_only=verify_only)
        except InventoryInputError:
            raise
        except (KeyError, TypeError, ValueError, DecimalException):
            raise InventoryInputError("POLICY_SELECTION_SOURCE_INVALID") from None

    def _execute(self, source: dict, *, verify_only: bool = False) -> dict:
        verify_policy_search_result(source)
        require(
            source["status"] == "POLICY_CANDIDATES_EVALUATED_LOCALLY"
            and source["database_writes"] is False
            and source["context"]["company_cd"] == self.deployment.company_cd,
            "POLICY_SELECTION_SOURCE_SCOPE_INVALID",
        )
        require(
            source["terminal_condition"]
            == "FINITE_HORIZON_NO_TERMINAL_VALUE_ARRIVALS_OUTSIDE_HORIZON_REJECTED",
            "POLICY_SELECTION_TERMINAL_CONDITION_INVALID",
        )
        for key in (
            "canonical_input_hash",
            "prepared_input_hash",
            "mathematical_input_hash",
            "config_hash",
            "classification_content_hash",
        ):
            hash_value(source[key])
        config = normalize_policy_search(source["search_config"])
        require(
            config["enabled"]
            and canonical_json(config) == canonical_json(source["search_config"])
            and policy_search_hash(config) == source["search_config_hash"]
            and source["selection_rule"] == config["selection_rule"] == SELECTION_RULE,
            "POLICY_SELECTION_SEARCH_CONFIG_MISMATCH",
        )
        baselines = {r["item_id"]: r for r in source["baseline_items"]}
        require(
            bool(baselines)
            and len(baselines) == len(source["baseline_items"])
            and all(r["psi_rows"] for r in baselines.values()),
            "POLICY_SELECTION_BASELINE_INVALID",
        )
        excluded = {r["item_id"]: r["reason_code"] for r in source["excluded_items"]}
        require(
            len(excluded) == len(source["excluded_items"]) and set(excluded) <= set(baselines),
            "POLICY_SELECTION_EXCLUSION_INVALID",
        )
        grouped = {item: [] for item in baselines}
        seen = set()
        for child in source["candidate_evaluations"]:
            candidate = child["candidate"]
            item, candidate_id = candidate["item_id"], candidate["candidate_id"]
            require(
                item in baselines and item not in excluded and candidate_id not in seen,
                "POLICY_SELECTION_CANDIDATE_DUPLICATE_OR_ORPHAN",
            )
            seen.add(candidate_id)
            grouped[item].append(child)
        items = []
        for item in sorted(baselines):
            children = sorted(grouped[item], key=lambda r: r["candidate"]["candidate_id"])
            if children:
                expected = candidate_policies(
                    config, item_id=item, segment_key=children[0]["candidate"]["segment_key"]
                )
                require(
                    canonical_json([c["candidate"] for c in children]) == canonical_json(expected),
                    "POLICY_SELECTION_CANDIDATE_SET_MISMATCH",
                )
            assessments, verified_by_id = [], {}
            for child in children:
                verified = (
                    verify_candidate_evaluation(child, baselines[item], source)
                    if child["status"] == "SUCCEEDED"
                    else None
                )
                if verified is not None:
                    verified["minimum_fill_rate"] = _number(config["minimum_fill_rate"])
                    verified_by_id[child["candidate"]["candidate_id"]] = verified
                assessments.append(assess_candidate(child, verified))
            if verify_only:
                continue
            eligible, ranked_reasons = rank_candidate_assessments(assessments)
            selected = eligible[0] if eligible else None
            if selected:
                reasons = ranked_reasons
            elif item in excluded:
                reasons = ["SEARCH_ITEM_EXCLUDED"]
            elif not children:
                reasons = ["NO_CANDIDATES_EVALUATED"]
            elif all(a["status"] == "FAILED" for a in assessments):
                reasons = ["ALL_CANDIDATES_FAILED"]
            else:
                reasons = sorted({reason for a in assessments for reason in a["reason_codes"]})
            chosen_child = next(
                (
                    c
                    for c in children
                    if selected and c["candidate"]["candidate_id"] == selected["candidate_id"]
                ),
                None,
            )
            items.append(
                {
                    "item_id": item,
                    "status": "SELECTED" if selected else "NOT_SELECTED",
                    "reason_codes": reasons,
                    "search_exclusion_reason": excluded.get(item),
                    "selected_candidate_id": selected["candidate_id"] if selected else None,
                    "selected_candidate_result_hash": selected["candidate_result_hash"]
                    if selected
                    else None,
                    "selected_policy": chosen_child["candidate"] if chosen_child else None,
                    "selected_score": selected["score"] if selected else None,
                    "source_review_required": verified_by_id[selected["candidate_id"]]["review"]
                    if selected
                    else None,
                    "candidate_decisions": assessments,
                }
            )
        if verify_only:
            return {}
        result = {
            "contract_id": "inventory-policy-selection-result",
            "contract_version": "1.0.0",
            "status": "POLICY_SELECTION_COMPUTED_LOCALLY",
            "selection_algorithm": "SERVICE_COST_INVENTORY_ORDER_LEXICOGRAPHIC_V1",
            "selection_rule": SELECTION_RULE,
            "ranking_fields": list(RANK_FIELDS),
            "evaluation_mode": "DEVELOPMENT_SHADOW",
            "optimality_scope": "EVALUATED_CANDIDATE_SET_ONLY",
            "context": source["context"],
            "search_result_reference": "PSR-" + source["content_hash"],
            "search_result_content_hash": source["content_hash"],
            "minimum_fill_rate": config["minimum_fill_rate"],
            "terminal_condition": source["terminal_condition"],
            "item_selections": items,
            "selected_item_count": sum(r["status"] == "SELECTED" for r in items),
            "not_selected_item_count": sum(r["status"] == "NOT_SELECTED" for r in items),
            "automatic_publish_allowed": False,
            "automatic_order_allowed": False,
            "operational_policy_approved": False,
            "database_writes": False,
        }
        for key in (
            "canonical_input_hash",
            "prepared_input_hash",
            "mathematical_input_hash",
            "search_config_hash",
            "config_hash",
            "classification_snapshot_id",
            "classification_content_hash",
        ):
            result[key] = source[key]
        result["content_hash"] = digest(result)
        return verify_policy_selection_result(result)

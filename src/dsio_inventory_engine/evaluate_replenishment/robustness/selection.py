"""Pure empirical risk aggregation and locked development-only policy choices."""

from decimal import Decimal, ROUND_CEILING, localcontext

from dsio_inventory_engine.inventory_contracts.values import (
    canonical_json,
    digest,
    hash_value,
    quantity_text,
    require,
)
from .contracts import (
    MIN_DEMAND,
    NUMBERS,
    PROFILES,
    SPLIT_COUNTS,
    binding,
    clone,
    input_errors,
    number,
    path,
    policy,
    sealed,
    semantic_key,
    shape,
    trace,
)
from .scoring import score_trace


def _bank(document):
    value = clone(document)
    shape(value, ("binding", "expected_paths", "candidates", "traces"))
    binding(value["binding"])
    paths, candidates, traces = (value[k] for k in ("expected_paths", "candidates", "traces"))
    require(type(paths) is list and 1 <= len(paths) <= 16, "ROBUSTNESS_PATH_SET_INVALID")
    for p in paths:
        path(p)
    split = paths[0]["split"]
    require(
        all(p["split"] == split for p in paths) and len(paths) == SPLIT_COUNTS[split],
        "ROBUSTNESS_PATH_COUNT_OR_SPLIT_MISMATCH",
    )
    require(
        len({p["path_id"] for p in paths}) == len(paths)
        and len({p["seed"] for p in paths}) == len(paths),
        "ROBUSTNESS_DUPLICATE_PATH",
    )
    require(
        type(candidates) is list and 1 <= len(candidates) <= 88, "ROBUSTNESS_CANDIDATE_SET_INVALID"
    )
    for c in candidates:
        policy(c)
    require(
        len({c["candidate_id"] for c in candidates}) == len(candidates)
        and len({semantic_key(c) for c in candidates}) == len(candidates),
        "ROBUSTNESS_DUPLICATE_CANDIDATE",
    )
    require(
        type(traces) is list and len(traces) == len(paths) * len(candidates),
        "ROBUSTNESS_TRACE_SET_INCOMPLETE",
    )
    expected = {(p["path_id"], c["candidate_id"]): (p, c) for p in paths for c in candidates}
    seen, initial, gate = set(), None, None
    per_path = {}
    for t in traces:
        parsed = trace(t)
        key = parsed["path"]["path_id"], parsed["candidate"]["candidate_id"]
        require(key in expected and key not in seen, "ROBUSTNESS_TRACE_DUPLICATE_OR_EXTRA")
        seen.add(key)
        require(
            parsed["binding"] == value["binding"]
            and (parsed["path"], parsed["candidate"]) == expected[key],
            "ROBUSTNESS_INPUT_BINDING_MISMATCH",
        )
        # Hash equality alone does not permit different initial stock or Gates.
        if gate is None:
            gate = parsed["gate"]
        require(parsed["gate"] == gate, "ROBUSTNESS_GATE_CHANGED")
        if parsed["status"] == "SUCCEEDED":
            state = {
                k: parsed["rows"][0][k] for k in ("boh_qty", "reserved_qty", "backorder_open_qty")
            }
            common = {"state": state, "receipts": parsed["initial_receipts"]}
            initial = common if initial is None else initial
            require(common == initial, "ROBUSTNESS_INITIAL_STATE_CHANGED")
            demands = [
                {k: row[k] for k in ("confirmed_customer_order_qty", "net_forecast_qty")}
                for row in parsed["rows"]
            ]
            comparison = (parsed["source_result_hash"], demands)
            require(
                per_path.setdefault(key[0], comparison) == comparison,
                "ROBUSTNESS_PATH_SOURCE_CHANGED",
            )
    value["expected_paths"] = sorted(paths, key=lambda p: p["path_id"])
    value["candidates"] = sorted(candidates, key=lambda c: c["candidate_id"])
    value["traces"] = sorted(
        traces, key=lambda t: (t["path"]["path_id"], t["candidate"]["candidate_id"])
    )
    return value


def _aggregate(cards, split):
    failed = [c for c in cards if c["status"] == "FAILED"]
    scores = [c["score"] for c in cards if c["status"] == "SUCCEEDED"]
    demand = [s for s in scores if s["fill_rate"] is not None]
    count = len(demand)
    successes = sum(s["path_service_success"] for s in demand)
    ratio = Decimal(successes) / count if count else None
    tail_count = int((Decimal("0.10") * count).to_integral_value(rounding=ROUND_CEILING))
    fills = sorted(number(s["fill_rate"]) for s in demand)
    loss = (
        sum((1 - fill for fill in fills[:tail_count]), Decimal(0)) / tail_count if count else None
    )
    reasons = []
    if failed:
        reasons.append("TECHNICAL_PATH_FAILURE")
    if any(not s["calculation_gate_allowed"] for s in scores):
        reasons.append("CALCULATION_GATE_BLOCKED")
    if any(
        s["fill_rate"] is None and number(s["ending_customer_backorder_qty"]) > 0 for s in scores
    ):
        reasons.append("UNCLEARED_CUSTOMER_BACKORDER")
    if split in MIN_DEMAND:
        if count < MIN_DEMAND[split]:
            reasons.append("INSUFFICIENT_DEMAND_PATHS")
        if ratio is not None and ratio < Decimal("0.90"):
            reasons.append("PATH_SUCCESS_RATIO_BELOW_MINIMUM")
        if loss is not None and loss > Decimal("0.10"):
            reasons.append("WORST_TAIL_LOSS_ABOVE_MAXIMUM")
    else:
        # Nominal and Stress do not pretend to estimate multi-path operational reliability.
        if count == 0:
            reasons.append("NO_DEMAND_IN_HORIZON")
        if successes != count:
            reasons.append("PATH_SERVICE_REQUIREMENT_FAILED")
    costs = [s["cost"] for s in scores]
    service_risk_eligible = not reasons
    costs_available = not failed and all(c["status"] == "CALCULABLE" for c in costs)
    if not costs_available:
        reasons.append("COST_NOT_CALCULABLE")
    complete = not failed

    def mean(key):
        return quantity_text(sum((number(s[key]) for s in scores), Decimal(0)) / len(scores))

    return {
        "status": "ELIGIBLE"
        if not reasons
        else "TECHNICAL_FAILURE"
        if failed
        else "INSUFFICIENT_DEMAND_PATHS"
        if "INSUFFICIENT_DEMAND_PATHS" in reasons
        else "INELIGIBLE",
        "eligible": not reasons,
        "service_risk_eligible": service_risk_eligible,
        "cost_calculable": costs_available,
        "reason_codes": reasons,
        "path_count": len(cards),
        "demand_path_count": count if complete else None,
        "no_demand_path_count": len(scores) - count if complete else None,
        "failed_path_count": len(failed),
        "completed_path_count": len(scores),
        "successful_demand_path_count": successes if complete else None,
        "empirical_path_success_ratio": quantity_text(ratio)
        if complete and ratio is not None
        else None,
        "worst_tail_path_count": tail_count if complete else None,
        "worst_tail_mean_loss": quantity_text(loss) if complete and loss is not None else None,
        "p10_fill_rate": quantity_text(fills[tail_count - 1]) if complete and count else None,
        "mean_total_cost": quantity_text(
            sum((number(c["total_cost"]) for c in costs), Decimal(0)) / len(cards)
        )
        if costs_available
        else None,
        "mean_cost_per_score_week": quantity_text(
            sum((number(c["total_cost"]) for c in costs), Decimal(0)) / len(cards) / 52
        )
        if costs_available
        else None,
        "mean_average_on_hand_eoh_qty": mean("average_on_hand_eoh_qty") if complete else None,
        "mean_order_count": quantity_text(
            Decimal(sum(s["order_count"] for s in scores)) / len(cards)
        )
        if complete
        else None,
        "source_review_required": any(s["source_review_required"] for s in scores),
    }


def _assess(bank, profile_id):
    require(profile_id in PROFILES, "ROBUSTNESS_COST_PROFILE_INVALID")
    split = bank["expected_paths"][0]["split"]
    result = []
    for candidate in bank["candidates"]:
        cards = [
            score_trace(t, profile_id=profile_id)
            for t in bank["traces"]
            if t["candidate"] == candidate
        ]
        result.append(
            {
                "candidate": candidate,
                "assessment": _aggregate(cards, split),
                "path_scorecards": cards,
            }
        )
    return result


@input_errors
def assess_paths(document, *, profile_id="PRICE_BASE"):
    """Recompute risk/cost from raw PSI, never accept cached fill rates or costs."""
    with localcontext(NUMBERS):
        bank = _bank(document)
        return sealed(
            "inventory-robustness-bank-assessment",
            {
                "bank_hash": digest(bank),
                "binding": bank["binding"],
                "profile_id": profile_id,
                "split": bank["expected_paths"][0]["split"],
                "candidate_assessments": _assess(bank, profile_id),
            },
        )


def _ranking(value):
    score = value["assessment"]
    return (
        number(score["mean_cost_per_score_week"]),
        number(score["mean_average_on_hand_eoh_qty"]),
        number(score["mean_order_count"]),
        *semantic_key(value["candidate"]),
    )


@input_errors
def select_policy(document, *, selector, profile_id="PRICE_BASE"):
    with localcontext(NUMBERS):
        bank = _bank(document)
        require(selector in ("NOMINAL_FORECAST", "MULTIPATH_RISK"), "ROBUSTNESS_SELECTOR_INVALID")
        split = "NOMINAL" if selector == "NOMINAL_FORECAST" else "SELECTION"
        require(bank["expected_paths"][0]["split"] == split, "ROBUSTNESS_RESELECTION_FORBIDDEN")
        assessments = _assess(bank, profile_id)
        eligible = sorted((a for a in assessments if a["assessment"]["eligible"]), key=_ranking)
        winner = eligible[0] if eligible else None
        for entry in assessments:
            score = entry["assessment"]
            entry["selection_status"] = "SELECTED" if entry is winner else "NOT_SELECTED"
            if not score["eligible"]:
                entry["selection_reason"] = "INELIGIBLE"
            elif entry is winner:
                entry["selection_reason"] = "BEST_ELIGIBLE_SCORE"
            else:
                first = next(
                    i
                    for i, (a, b) in enumerate(zip(_ranking(entry), _ranking(winner), strict=True))
                    if a != b
                )
                entry["selection_reason"] = (
                    "HIGHER_MEAN_COST",
                    "HIGHER_MEAN_INVENTORY",
                    "MORE_ORDERS",
                    "SEMANTIC_POLICY_TIE_BREAK",
                    "SEMANTIC_POLICY_TIE_BREAK",
                    "SEMANTIC_POLICY_TIE_BREAK",
                    "SEMANTIC_POLICY_TIE_BREAK",
                    "SEMANTIC_POLICY_TIE_BREAK",
                )[first]
        return sealed(
            "inventory-robustness-policy-selection",
            {
                "selector": selector,
                "profile_id": profile_id,
                "bank_hash": digest(bank),
                "binding": bank["binding"],
                "expected_paths": bank["expected_paths"],
                "status": "SELECTED"
                if winner
                else "NO_NOMINAL_CANDIDATE"
                if split == "NOMINAL"
                else "NO_ROBUST_CANDIDATE",
                "selected_candidate": winner["candidate"] if winner else None,
                "source_review_required": winner["assessment"]["source_review_required"]
                if winner
                else None,
                "candidate_assessments": assessments,
                "optimality_scope": "SUPPLIED_CANDIDATE_SET_ONLY_NOT_OFFICIAL_EXPERIMENT",
            },
        )


@input_errors
def verify_selection(result, document, *, expected_selection_hash):
    hash_value(expected_selection_hash)
    value = clone(result)
    require(
        value.get("content_hash") == expected_selection_hash, "ROBUSTNESS_SELECTION_HASH_MISMATCH"
    )
    rebuilt = select_policy(document, selector=value["selector"], profile_id=value["profile_id"])
    require(
        canonical_json(value) == canonical_json(rebuilt), "ROBUSTNESS_SELECTION_REPLAY_MISMATCH"
    )


@input_errors
def evaluate_locked_policy(result, selection_bank, evaluation_bank, *, expected_selection_hash):
    """Validate the original choice, evaluate ONLY it on fresh paths; never pick a replacement."""
    verify_selection(result, selection_bank, expected_selection_hash=expected_selection_hash)
    with localcontext(NUMBERS):
        bank = _bank(evaluation_bank)
        split = bank["expected_paths"][0]["split"]
        require(split in ("VALIDATION", "HOLDOUT", "STRESS"), "ROBUSTNESS_EVALUATION_SPLIT_INVALID")
        require(bank["binding"] == result["binding"], "ROBUSTNESS_INPUT_BINDING_MISMATCH")
        require(
            result["selected_candidate"] is not None
            and bank["candidates"] == [result["selected_candidate"]],
            "ROBUSTNESS_LOCKED_POLICY_MISMATCH",
        )
        old = result["expected_paths"]
        require(
            not {p["path_id"] for p in old} & {p["path_id"] for p in bank["expected_paths"]}
            and not {p["seed"] for p in old} & {p["seed"] for p in bank["expected_paths"]},
            "ROBUSTNESS_SPLIT_SOURCE_REUSE",
        )
        # Source identity and initial state must be the same, even if their claimed hash is unchanged.
        old_bank = _bank(selection_bank)
        old_success = next((t for t in old_bank["traces"] if t["status"] == "SUCCEEDED"), None)
        for t in bank["traces"]:
            require(t["gate"] == old_bank["traces"][0]["gate"], "ROBUSTNESS_GATE_CHANGED")
            if t["status"] == "SUCCEEDED" and old_success is not None:
                require(
                    t["initial_receipts"] == old_success["initial_receipts"]
                    and all(
                        t["rows"][0][k] == old_success["rows"][0][k]
                        for k in ("boh_qty", "reserved_qty", "backorder_open_qty")
                    ),
                    "ROBUSTNESS_INITIAL_STATE_CHANGED",
                )
        return sealed(
            "inventory-robustness-locked-policy-evaluation",
            {
                "selection_hash": expected_selection_hash,
                "selected_candidate": result["selected_candidate"],
                "binding": bank["binding"],
                "bank_hash": digest(bank),
                "split": split,
                "profile_id": result["profile_id"],
                "candidate_assessment": _assess(bank, result["profile_id"])[0],
                "reselected": False,
            },
        )

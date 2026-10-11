"""Local horizon diagnostics, immutable PSI re-pricing and locked-policy holdout evaluation."""

import copy
from decimal import Decimal, DecimalException, localcontext

from dsio_inventory_engine.inventory_contracts.network import DeploymentScope

from dsio_inventory_engine.inventory_contracts.policy_sensitivity import (
    PolicySensitivityRequest,
    verify_policy_sensitivity_result,
)
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    canonical_json,
    digest,
    require,
)
from .policy_search import NUMBERS, evaluate_rows
from .policy_selection import (
    COORDINATES,
    DEMAND_FIELDS,
    RunPolicySelectionUseCase,
    assess_candidate,
    rank_candidate_assessments,
    verify_candidate_evaluation,
)


def _initial(source: dict) -> dict:
    """Freeze scope, candidate universe, inventory, policy and constraints across experiments."""
    children = sorted(source["candidate_evaluations"], key=lambda c: c["candidate"]["candidate_id"])
    evidence = []
    for child in children:
        if child["status"] != "SUCCEEDED":
            continue
        first = child["decision_evidence"][0]
        obs = first["observation"]
        evidence.append(
            {
                "candidate": child["candidate"],
                "policy": obs["policy"],
                "constraints": {k: v for k, v in obs["control"].items() if k != "order_dates"},
                "quantity_rule": obs["quantity_rule"],
                "capacity_mode": obs["capacity_mode"],
                "allowed_action_types": obs["allowed_action_types"],
                "calculated_policy": first["proposal"]["calculated_policy"],
                "admission": first["effective_policy_admission"],
                "policy_evidence": first["candidate_policy_evidence"],
            }
        )
    return {
        "context": {
            k: v
            for k, v in source["context"].items()
            if k not in ("engine_run_id", "plan_end_date")
        },
        "config_hash": source["config_hash"],
        "classification_content_hash": source["classification_content_hash"],
        "search_config": source["search_config"],
        "excluded_items": source["excluded_items"],
        "initial_inventory": [
            {
                k: b["psi_rows"][0][k]
                for k in ("item_id", "boh_qty", "reserved_qty", "backorder_open_qty")
            }
            for b in sorted(source["baseline_items"], key=lambda b: b["item_id"])
        ],
        "candidates": [c["candidate"] for c in children],
        "evidence": evidence,
    }


def _trace(source: dict) -> dict:
    return {
        b["item_id"]: [{k: r[k] for k in COORDINATES + DEMAND_FIELDS} for r in b["psi_rows"]]
        for b in source["baseline_items"]
    }


def _metrics(child: dict, cost: dict | None, minimum: str, count: int | None = None) -> dict:
    decisions = child["decision_evidence"]
    rows = child["psi_rows"] if count is None else child["psi_rows"][:count]
    boundary = rows[-1]["end_date"]
    orders = [o for o in child["orders"] if o["order_date"] <= boundary]
    metrics = evaluate_rows(
        rows,
        decisions[: len(rows)],
        orders,
        cost,
        minimum,
        Decimal(decisions[0]["candidate_policy_evidence"]["monthly_mean_demand_qty"]),
    )
    total = metrics["total_cost"]
    demand = sum(
        (Decimal(r["confirmed_customer_order_qty"]) + Decimal(r["net_forecast_qty"]) for r in rows),
        Decimal(0),
    )
    metrics.update(
        horizon_weeks=len(rows),
        current_demand_qty=str(demand),
        cost_per_week=None if total is None else format(Decimal(total) / len(rows), "f"),
        cost_per_current_demand_unit=None
        if total is None or demand == 0
        else format(Decimal(total) / demand, "f"),
        open_recommended_qty_at_boundary=str(
            sum(
                (Decimal(o["quantity"]) for o in orders if o["receipt_date"] > boundary), Decimal(0)
            )
        ),
    )
    return metrics


class RunPolicySensitivityUseCase:
    def __init__(self, deployment: DeploymentScope):
        self.deployment = deployment

    def execute(self, request: PolicySensitivityRequest) -> dict:
        require(self.deployment.environment == "DEVELOPMENT", "POLICY_SENSITIVITY_DEVELOPMENT_ONLY")
        try:
            with localcontext(NUMBERS):
                normalized = PolicySensitivityRequest.from_dict(request.to_dict())
                return self._execute(normalized.to_dict(), normalized)
        except InventoryInputError:
            raise
        except (KeyError, TypeError, ValueError, DecimalException):
            raise InventoryInputError("POLICY_SENSITIVITY_SOURCE_INVALID") from None

    def _execute(self, data: dict, request: PolicySensitivityRequest) -> dict:
        cases = {c["case_id"]: c for c in data["cases"]}
        costs = {p["profile_id"]: p["cost_assumptions"] for p in data["cost_profiles"]}
        reference = cases.get(data["reference_case_id"])
        require(
            reference is not None and reference["split"] == "VALIDATION",
            "POLICY_SENSITIVITY_REFERENCE_INVALID",
        )
        require(data["reference_cost_profile_id"] in costs, "POLICY_SENSITIVITY_REFERENCE_INVALID")
        require(
            len({p["currency"] for p in costs.values() if p is not None}) <= 1,
            "POLICY_SENSITIVITY_CURRENCY_MISMATCH",
        )
        verified, initial, traces = {}, None, {}
        require(
            sum(len(c["search_result"]["candidate_evaluations"]) for c in cases.values())
            * len(costs)
            <= 20_000,
            "POLICY_SENSITIVITY_WORKLOAD_LIMIT",
        )
        for case in cases.values():
            source = case["search_result"]
            RunPolicySelectionUseCase(self.deployment).verify_source(source)
            for child in source["candidate_evaluations"]:
                if child["status"] != "SUCCEEDED":
                    continue
                first = child["decision_evidence"][0]
                for decision in child["decision_evidence"]:
                    require(
                        all(
                            decision["observation"][k] == first["observation"][k]
                            for k in (
                                "policy",
                                "control",
                                "quantity_rule",
                                "capacity_mode",
                                "allowed_action_types",
                            )
                        )
                        and decision["candidate_policy_evidence"]
                        == first["candidate_policy_evidence"]
                        and decision["proposal"]["calculated_policy"]
                        == first["proposal"]["calculated_policy"],
                        "POLICY_SENSITIVITY_FROZEN_POLICY_REQUIRED",
                    )
            fingerprint = _initial(source)
            require(
                initial is None or canonical_json(initial) == canonical_json(fingerprint),
                "POLICY_SENSITIVITY_INITIAL_CONDITIONS_MISMATCH",
            )
            initial = fingerprint
            trace = _trace(source)
            require(
                len({len(v) for v in trace.values()}) == 1, "POLICY_SENSITIVITY_HORIZON_MISMATCH"
            )
            traces[case["case_id"]] = trace
            baselines = {b["item_id"]: b for b in source["baseline_items"]}
            verified[case["case_id"]] = {
                c["candidate"]["candidate_id"]: verify_candidate_evaluation(
                    c, baselines[c["candidate"]["item_id"]], source
                )
                for c in source["candidate_evaluations"]
                if c["status"] == "SUCCEEDED"
            }
        # Order calendars may be truncated by horizon, but never silently changed.
        calendars = {}
        for case in cases.values():
            for child in case["search_result"]["candidate_evaluations"]:
                if child["status"] == "SUCCEEDED":
                    cid = child["candidate"]["candidate_id"]
                    first = child["decision_evidence"][0]
                    calendars.setdefault(cid, []).append(
                        (
                            child["psi_rows"][-1]["end_date"],
                            first["observation"]["control"]["order_dates"],
                        )
                    )
        for schedules in calendars.values():
            longest = max(schedules, key=lambda entry: entry[0])[1]
            for boundary, dates in schedules:
                require(
                    [d for d in dates if d <= boundary] == [d for d in longest if d <= boundary],
                    "POLICY_SENSITIVITY_ORDER_CALENDAR_MISMATCH",
                )
        groups = {}
        for case in cases.values():
            groups.setdefault(case["trace_id"], []).append(case)
        for group in groups.values():
            require(
                len({(c["seed"], c["split"]) for c in group}) == 1,
                "POLICY_SENSITIVITY_TRACE_IDENTITY_MISMATCH",
            )
            longest = max(group, key=lambda c: len(next(iter(traces[c["case_id"]].values()))))
            for case in group:
                for item, rows in traces[case["case_id"]].items():
                    require(
                        rows == traces[longest["case_id"]][item][: len(rows)],
                        "POLICY_SENSITIVITY_TRACE_PREFIX_MISMATCH",
                    )
        validation_seeds = {c["seed"] for c in cases.values() if c["split"] == "VALIDATION"}
        validation_paths = {
            digest(traces[c["case_id"]]) for c in cases.values() if c["split"] == "VALIDATION"
        }
        for case in cases.values():
            if case["split"] == "HOLDOUT":
                require(
                    case["seed"] not in validation_seeds
                    and digest(traces[case["case_id"]]) not in validation_paths,
                    "POLICY_SENSITIVITY_HOLDOUT_REUSED",
                )
        matrix = []
        for case in cases.values():
            source = case["search_result"]
            minimum = source["search_config"]["minimum_fill_rate"]
            for profile_id, cost in costs.items():
                for base in sorted(source["baseline_items"], key=lambda b: b["item_id"]):
                    children = sorted(
                        (
                            c
                            for c in source["candidate_evaluations"]
                            if c["candidate"]["item_id"] == base["item_id"]
                        ),
                        key=lambda c: c["candidate"]["candidate_id"],
                    )
                    assessments, metric_rows = [], []
                    for child in children:
                        v = copy.deepcopy(
                            verified[case["case_id"]].get(child["candidate"]["candidate_id"])
                        )
                        metric = None
                        if v is not None:
                            metric = _metrics(child, cost, minimum)
                            v.update(metrics=metric, minimum_fill_rate=Decimal(minimum))
                        assessments.append(assess_candidate(child, v))
                        metric_rows.append(
                            {
                                "candidate_id": child["candidate"]["candidate_id"],
                                "source_candidate_hash": child["content_hash"],
                                "metrics": metric,
                            }
                        )
                    eligible, reasons = (
                        rank_candidate_assessments(assessments)
                        if case["split"] == "VALIDATION"
                        else ([], ["LOCKED_POLICY_ONLY_NO_HOLDOUT_RESELECTION"])
                    )
                    row = {
                        "case_id": case["case_id"],
                        "cost_profile_id": profile_id,
                        "item_id": base["item_id"],
                        "split": case["split"],
                        "horizon_weeks": len(base["psi_rows"]),
                        "selected_candidate_id": eligible[0]["candidate_id"] if eligible else None,
                        "reason_codes": reasons
                        or sorted({r for a in assessments for r in a["reason_codes"]})
                        or ["NO_CANDIDATES_EVALUATED"],
                        "candidate_assessments": assessments,
                        "candidate_metrics": metric_rows,
                    }
                    matrix.append(row)
        reference_rows = [
            r
            for r in matrix
            if r["case_id"] == data["reference_case_id"]
            and r["cost_profile_id"] == data["reference_cost_profile_id"]
        ]
        locked = {r["item_id"]: r["selected_candidate_id"] for r in reference_rows}
        holdout, changes = [], []
        for row in matrix:
            chosen = locked[row["item_id"]]
            if row["split"] == "VALIDATION":
                changes.append(
                    {
                        "case_id": row["case_id"],
                        "cost_profile_id": row["cost_profile_id"],
                        "item_id": row["item_id"],
                        "reference_candidate_id": chosen,
                        "selected_candidate_id": row["selected_candidate_id"],
                        "selection_changed": chosen != row["selected_candidate_id"],
                    }
                )
            else:
                assessment = next(
                    (a for a in row["candidate_assessments"] if a["candidate_id"] == chosen), None
                )
                metric = next(
                    (a["metrics"] for a in row["candidate_metrics"] if a["candidate_id"] == chosen),
                    None,
                )
                holdout.append(
                    {
                        "case_id": row["case_id"],
                        "cost_profile_id": row["cost_profile_id"],
                        "item_id": row["item_id"],
                        "locked_candidate_id": chosen,
                        "status": "EVALUATED" if metric is not None else "NOT_EVALUATED",
                        "reason_codes": (
                            ["REFERENCE_POLICY_NOT_SELECTED"]
                            if chosen is None
                            else assessment["reason_codes"]
                            or ["LOCKED_POLICY_MEETS_SERVICE_AND_COST_REQUIREMENTS"]
                        ),
                        "metrics": metric,
                    }
                )
        terminal = []
        for pair in data["terminal_pairs"]:
            short, extended = cases.get(pair["short_case_id"]), cases.get(pair["extended_case_id"])
            require(
                short is not None
                and extended is not None
                and short["trace_id"] == extended["trace_id"]
                and short["split"] == extended["split"] == "VALIDATION",
                "POLICY_SENSITIVITY_TERMINAL_PAIR_INVALID",
            )
            count = len(next(iter(traces[short["case_id"]].values())))
            require(
                count < len(next(iter(traces[extended["case_id"]].values()))),
                "POLICY_SENSITIVITY_TERMINAL_PAIR_INVALID",
            )
            other = {
                c["candidate"]["candidate_id"]: c
                for c in extended["search_result"]["candidate_evaluations"]
            }
            for child in short["search_result"]["candidate_evaluations"]:
                ext = other[child["candidate"]["candidate_id"]]
                if child["status"] != "SUCCEEDED" or ext["status"] != "SUCCEEDED":
                    terminal.append(
                        {
                            **pair,
                            "candidate_id": child["candidate"]["candidate_id"],
                            "status": "NOT_COMPARABLE",
                            "reason": "CANDIDATE_FAILED",
                        }
                    )
                    continue

                def actions(c: dict) -> list[dict]:
                    return [
                        {
                            "action_type": d["proposal"]["action_type"],
                            "proposed_qty": d["proposal"]["quantity"],
                            **{
                                k: d["validation"][k]
                                for k in ("accepted_order_qty", "due_date", "reason_codes")
                            },
                        }
                        for d in c["decision_evidence"][:count]
                    ]

                changed = [
                    i
                    for i, (a, b) in enumerate(zip(actions(child), actions(ext), strict=True))
                    if a != b
                ]
                cost = costs[data["reference_cost_profile_id"]]
                terminal.append(
                    {
                        **pair,
                        "candidate_id": child["candidate"]["candidate_id"],
                        "status": "COMPARED",
                        "common_window_weeks": count,
                        "changed_action_seq": changed,
                        "horizon_dependent_actions": bool(changed),
                        "short_metrics": _metrics(
                            child,
                            cost,
                            short["search_result"]["search_config"]["minimum_fill_rate"],
                        ),
                        "extended_common_window_metrics": _metrics(
                            ext,
                            cost,
                            extended["search_result"]["search_config"]["minimum_fill_rate"],
                            count,
                        ),
                        "extended_full_metrics": _metrics(
                            ext,
                            cost,
                            extended["search_result"]["search_config"]["minimum_fill_rate"],
                        ),
                    }
                )
        result = {
            "contract_id": "inventory-policy-sensitivity-result",
            "contract_version": "1.0.0",
            "status": "POLICY_SENSITIVITY_EVALUATED_LOCALLY",
            "evaluation_spec": request.specification,
            "input_hash": request.input_hash,
            "comparison_mode": "ACTUAL_HORIZON_SIMULATION_WITH_IMMUTABLE_PSI_REPRICING",
            "holdout_mode": "NEW_SEED_PLANNING_PATH_FIXED_PRE_W0_POLICY_NOT_CHRONOLOGICAL_BACKTEST",
            "terminal_mode": "COMMON_PREFIX_WITH_CONTINUED_POLICY_EXTENSION_NO_SALVAGE_VALUE",
            "matrix": matrix,
            "reference_selection": reference_rows,
            "selection_changes": changes,
            "holdout_evaluations": holdout,
            "terminal_diagnostics": terminal,
            "automatic_publish_allowed": False,
            "automatic_order_allowed": False,
            "operational_policy_approved": False,
            "database_writes": False,
        }
        result["content_hash"] = digest(result)
        return verify_policy_sensitivity_result(result)

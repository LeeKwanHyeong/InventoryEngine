"""Per-item bounded policy search; the common guard and PSI own all stock arithmetic.

This is a development evaluator, not a policy selector or order publisher.
"""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from dataclasses import dataclass
from decimal import Context, Decimal, ROUND_CEILING, ROUND_HALF_EVEN, localcontext

from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.policy_search import (
    candidate_policies,
    normalize_policy_search,
    policy_search_hash,
)
from dsio_inventory_engine.inventory_contracts.replenishment import (
    ReplenishmentObservation,
    ReplenishmentProposal,
)
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    digest,
    quantity_text,
    require,
)
from dsio_inventory_engine.classify_inventory.application import config_hash, SNAPSHOT_NAMESPACE
from dsio_inventory_engine.recommend_replenishment.application.effective_policy import (
    admit_effective_item_policy,
)
from dsio_inventory_engine.recommend_replenishment.application.mathematical import (
    prepare_mathematical_strategy,
)
from dsio_inventory_engine.recommend_replenishment.application.math_policy import calculate_policy
from dsio_inventory_engine.recommend_replenishment.application.run import simulate
from dsio_inventory_engine.simulate_inventory.application.baseline import _roll_forward

NUMBERS = Context(prec=40, rounding=ROUND_HALF_EVEN)
DESCRIPTOR = {
    "strategy_type": "MATHEMATICAL",
    "implementation_id": "monthly-cover-policy-search",
    "version": "1.0.0",
    "model": None,
}


@dataclass(frozen=True)
class CandidateStrategy:
    candidate: dict | None
    stats: dict
    profile: dict
    dates: tuple[str, ...]
    input_hash: str
    input_binding: dict
    admission: dict

    @property
    def descriptor(self):
        return dict(DESCRIPTOR)

    def decide(self, observation: ReplenishmentObservation):
        obs = observation.to_dict()
        require(
            obs["input_content_hash"] == self.input_hash
            and obs.get("strategy_input_binding") == self.input_binding,
            "POLICY_SEARCH_OBSERVATION_MISMATCH",
        )
        zero = {"safety_stock_qty": None, "rop_qty": None, "target_inventory_qty": None}
        if self.candidate is None:
            action, quantity, policy, reason = "HOLD", "0", zero, "BASELINE_NO_NEW_ORDER"
        else:
            candidate = self.candidate
            source = dict(obs["policy"])
            service = max(
                Decimal(candidate["target_service_level"]),
                Decimal(self.admission["search_service_level_floor"]),
            )
            source["approved_service_level"] = quantity_text(service)
            protection = self.admission.get("effective_protection_lead_time_days")
            if protection is not None:
                source["effective_protection_lead_time_days"] = int(
                    Decimal(protection).to_integral_value(rounding=ROUND_CEILING)
                )
            scale = obs["quantity_rule"].get("planning_scale", obs["quantity_rule"].get("scale"))
            policy, _ = calculate_policy(self.stats, source, self.profile, scale)
            monthly = Decimal(self.stats["mean_weekly_demand"]) * Decimal(52) / Decimal(12)
            quantum = Decimal(1).scaleb(-scale)
            rop = max(
                Decimal(policy["rop_qty"]),
                (monthly * Decimal(candidate["rmos"])).quantize(quantum, rounding=ROUND_CEILING),
            )
            lot = (monthly * Decimal(candidate["emos"])).quantize(quantum, rounding=ROUND_CEILING)
            target = rop + lot
            if candidate["policy_kind"] == "PERIODIC_ORDER_UP_TO":
                periodic_source = dict(source)
                periodic_source["effective_protection_lead_time_days"] = (
                    source.get("effective_protection_lead_time_days", source["lead_time_days"])
                    + 7 * candidate["review_cycle_weeks"]
                )
                periodic_policy, _ = calculate_policy(
                    self.stats, periodic_source, self.profile, scale
                )
                target = max(target, Decimal(periodic_policy["rop_qty"]))
                policy["safety_stock_qty"] = periodic_policy["safety_stock_qty"]
            require(target <= Decimal("1000000000000"), "POLICY_SEARCH_QUANTITY_RANGE")
            policy.update(rop_qty=quantity_text(rop), target_inventory_qty=quantity_text(target))
            require(
                Decimal(obs["control"]["min_target_qty"])
                <= target
                <= Decimal(obs["control"]["max_target_qty"]),
                "POLICY_SEARCH_TARGET_OUTSIDE_APPROVED_BOUNDS",
            )
            gate = self.admission.get("no_order_gate", False) or not self.admission.get(
                "recommendation_calculation_allowed", True
            )
            index = self.dates.index(obs["decision_date"])
            eligible_date = (
                index % candidate["review_cycle_weeks"] == 0
                and obs["decision_date"] in obs["control"]["order_dates"]
            )
            position = Decimal(obs["state"]["inventory_position_qty"])
            needed = (
                position <= rop if candidate["policy_kind"] == "ROP_FIXED_Q" else position < target
            )
            if not gate and eligible_date and needed:
                action = "ORDER_QTY" if candidate["policy_kind"] == "ROP_FIXED_Q" else "ORDER_UP_TO"
                quantity = quantity_text(lot if action == "ORDER_QTY" else target)
                reason = "POLICY_SEARCH_CANDIDATE_ORDER"
            else:
                action, quantity = "HOLD", "0"
                reason = (
                    "POLICY_SEARCH_ORDER_GATE"
                    if gate
                    else "POLICY_SEARCH_REVIEW_NOT_DUE"
                    if not eligible_date
                    else "NO_POLICY_REPLENISHMENT"
                )
        return ReplenishmentProposal.from_dict(
            {
                "contract_id": "io-replenishment-decision-v1",
                "decision_id": obs["decision_id"],
                "observation_hash": observation.content_hash,
                "strategy": self.descriptor,
                "action_type": action,
                "quantity": quantity,
                "calculated_policy": policy,
                "reason_codes": [reason],
            }
        )


def evaluate_rows(rows, decisions, orders, cost, minimum_fill_rate, monthly_demand):
    require(bool(rows), "POLICY_SEARCH_EMPTY_SUCCESS")
    demand = sum(
        (
            Decimal(row["confirmed_customer_order_qty"]) + Decimal(row["net_forecast_qty"])
            for row in rows
        ),
        Decimal(0),
    )
    fulfilled = sum(
        (
            Decimal(row["fulfilled_confirmed_customer_order_qty"])
            + Decimal(row["fulfilled_forecast_qty"])
            for row in rows
        ),
        Decimal(0),
    )
    holding = sum((Decimal(row["on_hand_eoh_qty"]) for row in rows), Decimal(0))
    backlog = sum((Decimal(row["backorder_close_qty"]) for row in rows), Decimal(0))
    active = [
        row
        for row in rows
        if Decimal(row["confirmed_customer_order_qty"]) + Decimal(row["net_forecast_qty"]) > 0
    ]
    shortage_free = sum(
        Decimal(row["forecast_shortage_qty"]) == 0 and Decimal(row["backorder_close_qty"]) == 0
        for row in active
    )
    fill = fulfilled / demand if demand else None
    average = holding / len(rows)
    result = {
        "service_status": "AVAILABLE" if demand else "NO_DEMAND_IN_HORIZON",
        "current_demand_qty": quantity_text(demand),
        "current_demand_fulfilled_qty": quantity_text(fulfilled),
        "current_demand_fill_rate": quantity_text(fill) if fill is not None else None,
        "stockout_free_active_bucket_ratio": quantity_text(Decimal(shortage_free) / len(active))
        if active
        else None,
        "average_on_hand_eoh_qty": quantity_text(average),
        "observed_average_on_hand_mos": quantity_text(average / monthly_demand)
        if monthly_demand is not None and monthly_demand > 0
        else None,
        "ending_on_hand_qty": rows[-1]["on_hand_eoh_qty"],
        "ending_backorder_qty": rows[-1]["backorder_close_qty"],
        "backorder_unit_weeks": quantity_text(backlog),
        "forecast_shortage_qty": quantity_text(
            sum((Decimal(row["forecast_shortage_qty"]) for row in rows), Decimal(0))
        ),
        "order_count": len(orders),
        "ordered_qty": quantity_text(sum((Decimal(row["quantity"]) for row in orders), Decimal(0))),
        "guard_rejection_count": sum(
            row["validation"]["status"] == "REJECTED" for row in decisions
        ),
        "service_feasible": fill is not None
        and fill >= Decimal(minimum_fill_rate)
        and Decimal(rows[-1]["backorder_close_qty"]) == 0,
        "cost_status": "AVAILABLE_DEVELOPMENT_ASSUMPTION"
        if cost is not None
        else "COST_PROFILE_NOT_BOUND",
        "cost_components": None,
        "total_cost": None,
        "currency": cost["currency"] if cost else None,
    }
    if cost is not None:
        components = {
            "holding": holding * Decimal(cost["holding_cost_per_unit_week"]),
            "backorder": backlog * Decimal(cost["backorder_cost_per_unit_week"]),
            "ordering_fixed": Decimal(len(orders)) * Decimal(cost["fixed_order_cost"]),
            "ordering_variable": Decimal(result["ordered_qty"])
            * Decimal(cost["variable_order_cost_per_unit"]),
        }
        result["cost_components"] = {key: quantity_text(value) for key, value in components.items()}
        result["total_cost"] = quantity_text(sum(components.values(), Decimal(0)))
    return result


class RunPolicySearchUseCase:
    def __init__(self, deployment: DeploymentScope):
        self.deployment = deployment

    def execute(
        self,
        request: MathematicalPolicyRequest,
        *,
        config_values: dict,
        classification_snapshot: dict,
    ):
        with localcontext(NUMBERS):
            return self._execute(
                request,
                config_values=config_values,
                classification_snapshot=classification_snapshot,
            )

    def _execute(
        self,
        request: MathematicalPolicyRequest,
        *,
        config_values: dict,
        classification_snapshot: dict,
    ):
        request = MathematicalPolicyRequest.from_dict(request.to_dict())
        raw = request.to_dict()
        require(
            raw["recommendation"]["execution"]["execution_mode"] == "LOCAL_SHADOW",
            "POLICY_SEARCH_LOCAL_SHADOW_ONLY",
        )
        values = copy.deepcopy(config_values)
        try:
            search = normalize_policy_search(values["policy_search"])
        except (KeyError, ValueError) as exc:
            raise InventoryInputError("POLICY_SEARCH_CONFIG_INVALID") from exc
        require(search["enabled"], "POLICY_SEARCH_DISABLED")
        require(
            search["lookback_weeks"] == raw["policy_input"]["profile"]["lookback_weeks"],
            "POLICY_SEARCH_LOOKBACK_BINDING_MISMATCH",
        )
        classification = copy.deepcopy(classification_snapshot)
        unsigned = {
            key: value
            for key, value in classification.items()
            if key not in ("content_hash", "classification_snapshot_id")
        }
        actual_hash = hashlib.sha256(
            json.dumps(unsigned, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        require(
            actual_hash == classification.get("content_hash"),
            "POLICY_SEARCH_CLASSIFICATION_HASH_MISMATCH",
        )
        require(
            classification.get("classification_snapshot_id")
            == str(uuid.uuid5(SNAPSHOT_NAMESPACE, actual_hash)),
            "POLICY_SEARCH_CLASSIFICATION_ID_MISMATCH",
        )
        require(
            classification.get("config_hash") == config_hash(values),
            "POLICY_SEARCH_CONFIG_HASH_MISMATCH",
        )
        context = raw["recommendation"]["canonical_input"]["context"]
        require(
            classification.get("config_revision_id") == context["configuration_revision"],
            "POLICY_SEARCH_CONFIG_REVISION_MISMATCH",
        )
        require(
            all(
                classification.get("scope", {}).get(key) == context[key]
                for key in ("company_cd", "subs_cd", "site_cd")
            ),
            "POLICY_SEARCH_SCOPE_MISMATCH",
        )
        require(
            classification.get("window_end_exclusive", "9999") <= context["plan_start_date"],
            "POLICY_SEARCH_CLASSIFICATION_LOOKAHEAD",
        )
        execution = raw["recommendation"]["execution"]
        if execution["contract_version"] == "2.0.0":
            binding = execution["effective_policy_binding"]
            require(
                binding["classification_snapshot_id"]
                == classification["classification_snapshot_id"]
                and binding["classification_config_hash"] == classification["config_hash"],
                "POLICY_SEARCH_EFFECTIVE_POLICY_BINDING_MISMATCH",
            )
        recommendation, prepared, report, _ = prepare_mathematical_strategy(
            request, self.deployment
        )
        stats = {row["item_id"]: row for row in report["history_statistics"]}
        items = classification.get("items", [])
        require(
            isinstance(items, (list, tuple))
            and len({row["item_id"] for row in items}) == len(items),
            "POLICY_SEARCH_CLASSIFICATION_DUPLICATE",
        )
        assignments = {row["item_id"]: row for row in items}
        universe = {row["item_id"] for row in prepared["master"]}
        require(universe <= set(assignments), "POLICY_SEARCH_CLASSIFICATION_COVERAGE")
        require(
            {row["item_id"] for row in search["item_overrides"]} <= universe,
            "POLICY_SEARCH_ORPHAN_OVERRIDE",
        )
        horizon = len(prepared["calendar"])
        require(
            horizon * (horizon + 1) // 2 * len(universe) * search["max_candidates_per_item"]
            <= 200_000,
            "POLICY_SEARCH_WORKLOAD_LIMIT",
        )
        admissions = {
            item: admit_effective_item_policy(
                assignments[item],
                config_hash=classification["config_hash"],
                execution_purpose="SHADOW",
                strategy_descriptor=DESCRIPTOR,
            )
            for item in universe
            if assignments[item].get("classification_status") == "CLASSIFIED"
        }
        # The fixed matrix is a reference policy, not a shadow-search CSL floor.
        # VED business floors remain mandatory; operational promotion is separate.
        for item, admission in admissions.items():
            ved = (
                assignments[item].get("ved_class")
                if values["segmentation"]["ved"]["enabled"]
                else None
            )
            admission["search_service_level_floor"] = (
                str(values["policy_matrix"]["ved_service_level_floor"][ved])
                if ved is not None
                else "0.5"
            )
        if execution["contract_version"] == "2.0.0":
            require(
                all(
                    assignments[row["item_id"]]["effective_policy_hash"]
                    == row["effective_policy_hash"]
                    for row in execution["effective_item_policies"]
                ),
                "POLICY_SEARCH_ITEM_POLICY_BINDING_MISMATCH",
            )
        binding = {
            "contract_id": "inventory-policy-search-config",
            "contract_version": "1.0.0",
            "snapshot_id": "PSC-" + policy_search_hash(search),
            "content_hash": policy_search_hash(search),
        }
        execution = {
            **copy.deepcopy(execution),
            "strategy": dict(DESCRIPTOR),
            "strategy_input_binding": binding,
        }
        base_rows = _roll_forward(prepared)
        outputs, excluded, baselines = [], [], []
        with localcontext(NUMBERS):
            for master in sorted(prepared["master"], key=lambda row: row["item_id"]):
                item = master["item_id"]
                stat = stats[item]
                mean = stat["mean_weekly_demand"]
                monthly = Decimal(mean) * Decimal(52) / Decimal(12) if mean is not None else None
                item_base = [row for row in base_rows if row["item_id"] == item]
                baselines.append(
                    {
                        "item_id": item,
                        "psi_rows": item_base,
                        "metrics": evaluate_rows(
                            item_base,
                            [],
                            [],
                            search["cost_assumptions"],
                            search["minimum_fill_rate"],
                            monthly,
                        ),
                    }
                )
                if stat["missing_weeks"]:
                    excluded.append({"item_id": item, "reason_code": "INSUFFICIENT_DEMAND_HISTORY"})
                    continue
                if Decimal(stat["mean_weekly_demand"]) == 0:
                    excluded.append({"item_id": item, "reason_code": "ZERO_HISTORICAL_MEAN_DEMAND"})
                    continue
                if any(
                    row["item_id"] == item and row["effective_policy_source"] == "APPROVED_OVERRIDE"
                    for row in report["policy_evidence"]
                ):
                    excluded.append({"item_id": item, "reason_code": "APPROVED_OVERRIDE_PINNED"})
                    continue
                assignment = assignments[item]
                try:
                    candidates = candidate_policies(
                        search,
                        item_id=item,
                        segment_key=assignment.get("segment_key")
                        if assignment.get("classification_status") == "CLASSIFIED"
                        else None,
                    )
                except ValueError as exc:
                    excluded.append({"item_id": item, "reason_code": str(exc)})
                    continue
                # Immutable input projection; all mutable state is created afresh by simulate().
                view = {
                    **prepared,
                    "master": [master],
                    **{
                        key: [row for row in prepared[key] if row["item_id"] == item]
                        for key in ("demands", "policies", "receipt_decisions")
                    },
                }
                for candidate in candidates:
                    child_hash = digest(
                        {
                            "source_input_hash": recommendation.input_hash,
                            "candidate": candidate,
                            "search_config_hash": binding["content_hash"],
                        }
                    )
                    strategy = CandidateStrategy(
                        candidate,
                        stat,
                        raw["policy_input"]["profile"],
                        tuple(row["start_date"] for row in prepared["calendar"]),
                        child_hash,
                        binding,
                        admissions.get(item, {}),
                    )
                    try:
                        rows, decisions, orders = simulate(
                            view, execution, child_hash, strategy, admissions=admissions
                        )
                        for decision in decisions:
                            policy = decision["proposal"]["calculated_policy"]
                            rop, target = (
                                Decimal(policy["rop_qty"]),
                                Decimal(policy["target_inventory_qty"]),
                            )
                            source = decision["observation"]["policy"]
                            decision["candidate_policy_evidence"] = {
                                "candidate_id": candidate["candidate_id"],
                                "formula_profile": "MONTHLY_COVER_SEARCH_V1_NOT_LEGACY_EOQ",
                                "monthly_mean_demand_qty": quantity_text(monthly),
                                "requested_rmos": candidate["rmos"],
                                "requested_emos": candidate["emos"],
                                "requested_csl": candidate["target_service_level"],
                                "effective_csl": quantity_text(
                                    max(
                                        Decimal(candidate["target_service_level"]),
                                        Decimal(admissions[item]["search_service_level_floor"]),
                                    )
                                ),
                                "effective_rmos": quantity_text(rop / monthly),
                                "effective_emos": quantity_text((target - rop) / monthly),
                                "policy_max_position_mos": quantity_text(target / monthly),
                                "policy_midpoint_position_mos": quantity_text(
                                    (rop + (target - rop) / 2) / monthly
                                ),
                                "physical_lead_time_days": source["lead_time_days"],
                                "protection_lead_time_days": admissions[item].get(
                                    "effective_protection_lead_time_days"
                                )
                                or str(source["lead_time_days"]),
                                "review_cycle_weeks": candidate["review_cycle_weeks"],
                            }
                        require(
                            len(rows) == len(prepared["calendar"]), "POLICY_SEARCH_EMPTY_SUCCESS"
                        )
                        metrics = evaluate_rows(
                            rows,
                            decisions,
                            orders,
                            search["cost_assumptions"],
                            search["minimum_fill_rate"],
                            monthly,
                        )
                        metrics["policy_selection_eligible"] = (
                            metrics["service_feasible"]
                            and not admissions[item].get("no_order_gate", False)
                            and admissions[item].get("recommendation_calculation_allowed", True)
                        )
                        child = {
                            "candidate": candidate,
                            "status": "SUCCEEDED",
                            "failure_reason": None,
                            "row_count": len(rows),
                            "psi_rows": rows,
                            "decision_evidence": decisions,
                            "orders": orders,
                            "metrics": metrics,
                        }
                    except InventoryInputError as exc:
                        child = {
                            "candidate": candidate,
                            "status": "FAILED",
                            "failure_reason": str(exc),
                            "row_count": None,
                            "psi_rows": None,
                            "decision_evidence": None,
                            "orders": None,
                            "metrics": None,
                        }
                    child.update(
                        canonical_input_hash=prepared["manifest"]["input_content_hash"],
                        prepared_input_hash=prepared["manifest"]["prepared_content_hash"],
                        candidate_execution_hash=child_hash,
                        automatic_publish_allowed=False,
                        automatic_order_allowed=False,
                    )
                    child["content_hash"] = digest(child)
                    outputs.append(child)
        body = {
            "contract_id": "inventory-policy-search-result",
            "contract_version": "1.0.0",
            "status": "POLICY_CANDIDATES_EVALUATED_LOCALLY",
            "context": prepared["context"],
            "search_config": search,
            "search_config_hash": binding["content_hash"],
            "config_hash": classification["config_hash"],
            "classification_snapshot_id": classification["classification_snapshot_id"],
            "classification_content_hash": classification["content_hash"],
            "canonical_input_hash": prepared["manifest"]["input_content_hash"],
            "prepared_input_hash": prepared["manifest"]["prepared_content_hash"],
            "mathematical_input_hash": raw["policy_input"]["content_hash"],
            "baseline_items": baselines,
            "candidate_evaluations": outputs,
            "excluded_items": excluded,
            "selection_rule": search["selection_rule"],
            "selected_policies": None,
            "automatic_publish_allowed": False,
            "automatic_order_allowed": False,
            "terminal_condition": "FINITE_HORIZON_NO_TERMINAL_VALUE_ARRIVALS_OUTSIDE_HORIZON_REJECTED",
            "database_writes": False,
            "durable_artifact_persisted": False,
        }
        body["content_hash"] = digest(body)
        return body

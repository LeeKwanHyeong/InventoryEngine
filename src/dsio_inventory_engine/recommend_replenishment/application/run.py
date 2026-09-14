"""Strategy-neutral, local Recommended PSI with request-local supply ledgers."""

from decimal import Decimal, ROUND_CEILING, localcontext

from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest, SCOPE
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.replenishment import (
    RecommendationRequest,
    ReplenishmentObservation,
    ReplenishmentProposal,
    ReplenishmentStrategy,
    descriptor,
)
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    digest,
    quantity_text,
    require,
)
from dsio_inventory_engine.inventory_contracts.runtime import (
    InventoryRuntimeExecutionRequest,
    validate_canonical_runtime_binding,
    validate_effective_policy_runtime_binding,
)
from dsio_inventory_engine.prepare_inventory.application.inventory_input import (
    PrepareInventoryInputUseCase,
)
from dsio_inventory_engine.simulate_inventory.application.step import InventoryState, advance_bucket
from .effective_policy import admit_effective_item_policy
from .guard import policy_at, validate_action
from .observation import build_observation


class RunRecommendedPsiUseCase:
    """No training, model discovery, DB access, publication, or implicit fallback."""

    def __init__(self, deployment: DeploymentScope):
        self.deployment = deployment

    def execute(
        self,
        request: RecommendationRequest,
        strategy: ReplenishmentStrategy,
        *,
        runtime_request: InventoryRuntimeExecutionRequest | None = None,
    ) -> dict:
        request = RecommendationRequest.from_dict(request.to_dict())
        require(self.deployment.environment == "DEVELOPMENT", "LOCAL_RECOMMENDATION_ONLY")
        data = request.to_dict()
        canonical_input = CanonicalInputRequest.from_dict(data["canonical_input"])
        prepared = PrepareInventoryInputUseCase(self.deployment).execute(canonical_input).to_dict()
        execution = data["execution"]
        require(
            descriptor(strategy.descriptor) == execution["strategy"], "STRATEGY_BINDING_MISMATCH"
        )
        if "strategy_input_binding" in execution:
            require(
                getattr(strategy, "input_binding", None) == execution["strategy_input_binding"],
                "STRATEGY_INPUT_BINDING_MISMATCH",
            )
        admissions = apply_effective_policy_controls(
            prepared,
            execution,
            runtime_request=runtime_request,
            canonical_input=canonical_input,
        )
        with localcontext() as ctx:
            ctx.prec = 40
            validate_controls(prepared, execution)
            rows, decisions, orders = simulate(
                prepared,
                execution,
                request.input_hash,
                strategy,
                admissions=admissions,
            )
        automatic_publish_allowed = all(
            admission["automatic_publish_allowed"] for admission in admissions.values()
        )
        automatic_order_allowed = all(
            admission["automatic_order_allowed"] for admission in admissions.values()
        )
        result = {
            "contract_id": "io-replenishment-result-v1",
            "contract_version": execution["contract_version"],
            "status": "RECOMMENDED_PSI_COMPUTED_LOCALLY",
            "psi_scenario_type": "RECOMMENDED",
            "strategy": execution["strategy"],
            "execution": execution,
            "run_claimed": False,
            "database_writes": False,
            "artifact_sealed": False,
            "evidence_persisted": False,
            "approval_authenticated": False,
            "model_artifact_verified": False,
            "prepared_input": prepared,
            "input_content_hash": request.input_hash,
            "psi_rows": rows,
            "psi_content_hash": digest(rows),
            "decision_evidence": decisions,
            "decision_content_hash": digest(decisions),
            "recommended_orders": orders,
            "orders_content_hash": digest(orders),
        }
        if admissions:
            result.update(
                effective_policy_binding=execution["effective_policy_binding"],
                effective_policy_content_hash=execution["effective_policy_binding"][
                    "effective_policy_content_hash"
                ],
                effective_policy_admission_evidence=list(admissions.values()),
                effective_policy_admission_content_hash=digest(list(admissions.values())),
                automatic_publish_allowed=automatic_publish_allowed,
                automatic_order_allowed=automatic_order_allowed,
                publication_disposition=(
                    "AUTO_PUBLISH_ALLOWED" if automatic_publish_allowed else "REVIEW_REQUIRED"
                ),
            )
        return result


def apply_effective_policy_controls(
    prepared: dict,
    execution: dict,
    *,
    runtime_request: InventoryRuntimeExecutionRequest | None = None,
    canonical_input: CanonicalInputRequest | None = None,
) -> dict[str, dict]:
    """Admit V2 item policies and project their operational controls onto Source policy rows."""

    if execution["contract_version"] != "2.0.0":
        require(runtime_request is None, "RUNTIME_BINDING_UNEXPECTED_FOR_V1")
        return {}
    if execution["execution_mode"] == "PLATFORM_BOUND":
        require(
            runtime_request is not None and canonical_input is not None,
            "RUNTIME_EXECUTION_BINDING_REQUIRED",
        )
        validate_effective_policy_runtime_binding(
            runtime_request,
            execution["effective_policy_binding"],
        )
        validate_canonical_runtime_binding(
            runtime_request,
            canonical_input=canonical_input,
        )
    else:
        require(runtime_request is None, "LOCAL_SHADOW_RUNTIME_BINDING_FORBIDDEN")

    policies = {row["item_id"]: row for row in execution["effective_item_policies"]}
    master_items = {row["item_id"] for row in prepared["master"]}
    require(set(policies) == master_items, "EFFECTIVE_POLICY_UNIVERSE_MISMATCH")
    admissions = {
        item_id: admit_effective_item_policy(
            policy,
            config_hash=execution["effective_policy_binding"]["classification_config_hash"],
            execution_purpose=execution["execution_purpose"],
            strategy_descriptor=execution["strategy"],
            model_approval=execution["model_approval"],
        )
        for item_id, policy in policies.items()
    }
    if runtime_request is not None:
        claim = runtime_request.value["claim"]
        require(
            (
                all(admission["automatic_publish_allowed"] for admission in admissions.values()),
                all(admission["automatic_order_allowed"] for admission in admissions.values()),
            )
            == (
                claim["expected_automatic_publish_allowed"],
                claim["expected_automatic_order_allowed"],
            ),
            "RUNTIME_EFFECTIVE_POLICY_ADMISSION_MISMATCH",
        )

    for source_policy in prepared["policies"]:
        admission = admissions[source_policy["item_id"]]
        source_policy["classification_effective_policy_hash"] = admission["effective_policy_hash"]
        source_policy["classification_config_hash"] = execution["effective_policy_binding"][
            "classification_config_hash"
        ]
        source_policy["source_approved_service_level"] = source_policy["approved_service_level"]
        source_policy["approved_service_level"] = admission["effective_target_service_level"]
        source_policy["effective_review_cycle_weeks"] = admission["effective_review_cycle_weeks"]
        effective_lead_time = admission["effective_protection_lead_time_days"]
        if effective_lead_time is not None:
            rounded_days = int(
                Decimal(effective_lead_time).to_integral_value(rounding=ROUND_CEILING)
            )
            require(0 <= rounded_days <= 3660, "LEAD_TIME_RANGE")
            source_policy["effective_protection_lead_time_days"] = rounded_days
            source_policy["effective_protection_lead_time_basis"] = admission[
                "effective_protection_lead_time_basis"
            ]
    return admissions


def validate_controls(prepared: dict, execution: dict) -> None:
    master = {r["item_id"]: r for r in prepared["master"]}
    controls = execution["item_controls"]
    require({r["item_id"] for r in controls} == set(master), "CONTROL_UNIVERSE_MISMATCH")
    rules = {r["uom"]: r for r in prepared["manifest"]["quantity_rules"]}
    dates = {b["start_date"] for b in prepared["calendar"]}
    count = len(prepared["calendar"])
    require(len(master) * count * (count + 1) // 2 <= 200_000, "RECOMMENDATION_OBSERVATION_LIMIT")
    seq = {b["yyyyww"]: b["seq"] + 1 for b in prepared["calendar"]}
    evidence_rows = (
        2 * len(master) * count * (count + 1)
        + sum(
            seq[r["yyyyww"]]
            for r in prepared["receipt_decisions"]
            if Decimal(r["included_qty"]) > 0
        )
        + count * len(prepared["policies"])
        + count * sum(len(r["order_dates"]) for r in controls)
    )
    require(evidence_rows <= 500_000, "RECOMMENDATION_EVIDENCE_LIMIT")
    for row in controls:
        require(row["uom"] == master[row["item_id"]]["uom"], "CONTROL_UOM_MISMATCH")
        require(set(row["order_dates"]) <= dates, "ORDER_DATE_NOT_BUCKET_START")
        for key in ("max_order_qty", "min_target_qty", "max_target_qty"):
            require(
                Decimal(row[key]).as_tuple().exponent
                >= -rules[row["uom"]].get("planning_scale", rules[row["uom"]].get("scale")),
                "CONTROL_UOM_PRECISION",
            )
    require(all(p["lead_time_days"] <= 3660 for p in prepared["policies"]), "LEAD_TIME_RANGE")


def simulate(
    prepared: dict,
    execution: dict,
    input_hash: str,
    strategy: ReplenishmentStrategy,
    *,
    admissions: dict[str, dict] | None = None,
) -> tuple[list, list, list]:
    rows: list[dict] = []
    decisions: list[dict] = []
    orders: list[dict] = []
    controls = {r["item_id"]: r for r in execution["item_controls"]}
    admissions = admissions or {}
    rules = {r["uom"]: r for r in prepared["manifest"]["quantity_rules"]}
    positions = {r["item_id"]: r for r in prepared["positions"]}
    # Build per-item indexes once; all mutable state remains local to this call.
    demands: dict[str, list] = {r["item_id"]: [] for r in prepared["master"]}
    policies: dict[str, list] = {item: [] for item in demands}
    supplies: dict[str, list] = {item: [] for item in demands}
    for row in prepared["demands"]:
        demands[row["item_id"]].append(row)
    for row in prepared["policies"]:
        policies[row["item_id"]].append(row)
    for row in prepared["receipt_decisions"]:
        if Decimal(row["included_qty"]) > 0:
            supplies[row["item_id"]].append(
                {
                    "supply_id": "CONFIRMED:" + row["receipt_id"],
                    "supply_kind": "CONFIRMED",
                    "due_date": row["due_date"],
                    "receipt_bucket": row["yyyyww"],
                    "quantity": row["included_qty"],
                }
            )
    for item in prepared["master"]:
        item_id = item["item_id"]
        state = InventoryState(
            *(
                Decimal(positions[item_id][k])
                for k in ("available_qty", "reserved_qty", "backorder_qty")
            )
        )
        pending = supplies[item_id]
        demand_by_week = {r["yyyyww"]: r for r in demands[item_id]}
        for index, bucket in enumerate(prepared["calendar"]):
            future = prepared["calendar"][index:]
            policy = policy_at(policies[item_id], bucket["start_date"])
            observation = build_observation(
                context=prepared["context"],
                input_hash=input_hash,
                item=item,
                bucket=bucket,
                calendar=future,
                future_demand=[demand_by_week[b["yyyyww"]] for b in future],
                policy=policy,
                policy_schedule=policies[item_id],
                control=controls[item_id],
                quantity_rule=rules[item["uom"]],
                available_qty=state.available_qty,
                reserved_qty=state.reserved_qty,
                backorder_qty=state.backorder_qty,
                pending_supply=pending,
                execution=execution,
            )
            proposal = invoke(strategy, observation, execution["strategy"])
            validation = validate_action(observation.to_dict(), proposal)
            if Decimal(validation["accepted_order_qty"]) > 0:
                order = {
                    **{k: prepared["context"][k] for k in SCOPE},
                    "item_id": item_id,
                    "uom": item["uom"],
                    "order_id": "REC-" + observation.to_dict()["decision_id"],
                    "decision_id": validation["decision_id"],
                    "order_date": bucket["start_date"],
                    "due_date": validation["due_date"],
                    "receipt_date": validation["receipt_date"],
                    "receipt_bucket": validation["receipt_bucket"],
                    "quantity": validation["accepted_order_qty"],
                    "source_policy_id": policy["policy_id"],
                    "status": "SIMULATED_PENDING",
                }
                if item_id in admissions:
                    admission = admissions[item_id]
                    order.update(
                        effective_policy_hash=admission["effective_policy_hash"],
                        effective_order_action=admission["effective_order_action"],
                        effective_approval_level=admission["effective_approval_level"],
                        automatic_order_allowed=admission["automatic_order_allowed"],
                        execution_disposition=(
                            "AUTO_ALLOWED"
                            if admission["automatic_order_allowed"]
                            else "APPROVAL_REQUIRED"
                        ),
                    )
                orders.append(order)
                pending.append(
                    {
                        "supply_id": order["order_id"],
                        "supply_kind": "RECOMMENDED",
                        "due_date": order["due_date"],
                        "receipt_bucket": order["receipt_bucket"],
                        "quantity": order["quantity"],
                    }
                )
            current = [s for s in pending if s["receipt_bucket"] == bucket["yyyyww"]]
            incoming = {
                kind: sum(
                    (Decimal(s["quantity"]) for s in current if s["supply_kind"] == kind),
                    Decimal(0),
                )
                for kind in ("CONFIRMED", "RECOMMENDED")
            }
            demand = demand_by_week[bucket["yyyyww"]]
            state, quantities = advance_bucket(
                state,
                confirmed_customer_order_qty=Decimal(demand["confirmed_customer_order_qty"]),
                net_forecast_qty=Decimal(demand["net_forecast_qty"]),
                confirmed_supplier_receipt_qty=incoming["CONFIRMED"],
                recommended_receipt_qty=incoming["RECOMMENDED"],
            )
            cap = policy["physical_max_capacity"]
            peak = Decimal(quantities["available_qty"]) + state.reserved_qty
            rows.append(
                {
                    **{k: prepared["context"][k] for k in SCOPE},
                    **bucket,
                    **demand,
                    "psi_scenario_type": "RECOMMENDED",
                    "strategy_type": execution["strategy"]["strategy_type"],
                    **quantities,
                    "physical_capacity_excess_qty": quantity_text(
                        max(Decimal(0), peak - Decimal(cap))
                    )
                    if cap is not None
                    else "0",
                }
            )
            pending[:] = [s for s in pending if s["receipt_bucket"] != bucket["yyyyww"]]
            decision = {
                "observation": observation.to_dict(),
                "observation_hash": observation.content_hash,
                "proposal": proposal,
                "validation": validation,
            }
            if item_id in admissions:
                decision["effective_policy_admission"] = admissions[item_id]
            decisions.append(decision)
    # Every accepted order is within the horizon and has been simulated exactly once.
    for order in orders:
        order["status"] = "SIMULATED_RECEIVED"
    return rows, decisions, orders


def invoke(
    strategy: ReplenishmentStrategy, observation: ReplenishmentObservation, binding: dict
) -> dict:
    try:
        submitted = strategy.decide(observation)
    except Exception:
        raise InventoryInputError(
            "STRATEGY_EXECUTION_FAILED",
            [
                {
                    "decision_id": observation.to_dict()["decision_id"],
                    "strategy": binding,
                }
            ],
        ) from None
    require(isinstance(submitted, ReplenishmentProposal), "INVALID_STRATEGY_RESULT")
    proposal = ReplenishmentProposal.from_dict(submitted.to_dict()).to_dict()
    require(proposal["strategy"] == binding, "STRATEGY_BINDING_MISMATCH")
    require(
        proposal["decision_id"] == observation.to_dict()["decision_id"]
        and proposal["observation_hash"] == observation.content_hash,
        "STALE_STRATEGY_DECISION",
    )
    return proposal

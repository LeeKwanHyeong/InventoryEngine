"""Separate immutable planning observations from current realized environment demand.

An opt-in development runner; existing RunRecommendedPsi and its hashes are unchanged.
Injected strategy code must be trusted: Python objects are not a security sandbox.
"""

from decimal import Decimal

from dsio_inventory_engine.inventory_contracts.canonical import SCOPE
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.replenishment import (
    RecommendationRequest,
    ReplenishmentStrategy,
    descriptor,
)
from dsio_inventory_engine.inventory_contracts.values import (
    canonical_json,
    hash_value,
    quantity_text,
    require,
)
from dsio_inventory_engine.recommend_replenishment.application.guard import (
    policy_at,
    validate_action,
)
from dsio_inventory_engine.recommend_replenishment.application.observation import build_observation
from dsio_inventory_engine.recommend_replenishment.application.run import (
    apply_effective_policy_controls,
    invoke,
    validate_controls,
)
from dsio_inventory_engine.simulate_inventory.application.step import InventoryState, advance_bucket
from .contracts import input_errors, quantity, sealed
from .source_binding import RealizedDemandPath
from .immutable_preparation import ImmutableCausalPreparation


class RunCausalPsiUseCase:
    def __init__(self, deployment: DeploymentScope, *, reuse_preparation: bool = True):
        self.deployment = deployment
        self.preparation = ImmutableCausalPreparation(enabled=reuse_preparation)

    @input_errors
    def execute(
        self,
        request: RecommendationRequest,
        strategy: ReplenishmentStrategy,
        *,
        source: dict,
        source_request: dict,
        expected_source_hash: str,
        realization: RealizedDemandPath,
        expected_realization_hash: str,
    ) -> dict:
        snapshot = self.preparation.prepare(
            self.deployment,
            request,
            source,
            source_request,
            expected_source_hash,
        )
        data, canonical, prepared = snapshot.checkout()
        input_hash = snapshot.input_hash
        execution = data["execution"]
        truth = RealizedDemandPath.from_dict(realization.to_dict())
        hash_value(expected_realization_hash)
        require(truth.content_hash == expected_realization_hash, "CAUSAL_REALIZATION_HASH_MISMATCH")
        world = truth.to_dict()
        require(world["planning_request_hash"] == input_hash, "CAUSAL_PLANNING_BINDING_MISMATCH")
        require(
            len(prepared["calendar"]) == len(world["rows"]), "CAUSAL_REALIZATION_COVERAGE_INVALID"
        )
        require(
            descriptor(strategy.descriptor) == execution["strategy"]
            and getattr(strategy, "input_binding", None) == execution["strategy_input_binding"],
            "CAUSAL_STRATEGY_BINDING_MISMATCH",
        )
        # Actual classification/FSN admission, physical controls and Cut-off are not replaced.
        admissions = apply_effective_policy_controls(prepared, execution, canonical_input=canonical)
        validate_controls(prepared, execution)
        item = prepared["master"][0]
        item_id = item["item_id"]
        admission = admissions[item_id]
        position = prepared["positions"][0]
        policies = prepared["policies"]
        require(len(execution["item_controls"]) == 1, "CAUSAL_CONTROL_UNIVERSE_MISMATCH")
        control = execution["item_controls"][0]
        rules = next(r for r in prepared["manifest"]["quantity_rules"] if r["uom"] == "EA")
        planning = {r["yyyyww"]: r for r in prepared["demands"]}
        for bucket, actual in zip(prepared["calendar"], world["rows"], strict=True):
            require(
                quantity(actual["realized_gross_demand_qty"])
                >= quantity(planning[bucket["yyyyww"]]["confirmed_customer_order_qty"]),
                "CAUSAL_REALIZED_TOTAL_BELOW_KNOWN_ORDER",
            )
        state = InventoryState(
            *(quantity(position[k]) for k in ("available_qty", "reserved_qty", "backorder_qty"))
        )
        pending = [
            {
                "supply_id": "CONFIRMED:" + r["receipt_id"],
                "supply_kind": "CONFIRMED",
                "due_date": r["due_date"],
                "receipt_bucket": r["yyyyww"],
                "quantity": r["included_qty"],
            }
            for r in prepared["receipt_decisions"]
            if quantity(r["included_qty"]) > 0
        ]
        rows, decisions, orders = [], [], []
        for index, bucket in enumerate(prepared["calendar"]):
            remaining = prepared["calendar"][index:]
            policy = policy_at(policies, bucket["start_date"])
            obs = build_observation(
                context=prepared["context"],
                input_hash=input_hash,
                item=item,
                bucket=bucket,
                calendar=remaining,
                # The entire future realization, path ID, Seed and hash stay outside this boundary.
                future_demand=[planning[b["yyyyww"]] for b in remaining],
                policy=policy,
                policy_schedule=policies,
                control=control,
                quantity_rule=rules,
                available_qty=state.available_qty,
                reserved_qty=state.reserved_qty,
                backorder_qty=state.backorder_qty,
                pending_supply=pending,
                execution=execution,
            )
            proposal = invoke(strategy, obs, execution["strategy"])
            require(
                not (
                    admission["no_order_gate"]
                    or not admission["recommendation_calculation_allowed"]
                )
                or proposal["action_type"] == "HOLD",
                "CAUSAL_EFFECTIVE_GATE_BYPASS",
            )
            validation = validate_action(obs.to_dict(), proposal)
            if quantity(validation["accepted_order_qty"]) > 0:
                order = {
                    **{k: prepared["context"][k] for k in SCOPE},
                    "item_id": item_id,
                    "uom": "EA",
                    "order_id": "REC-" + validation["decision_id"],
                    **{
                        k: validation[k]
                        for k in ("decision_id", "due_date", "receipt_date", "receipt_bucket")
                    },
                    "order_date": bucket["start_date"],
                    "quantity": validation["accepted_order_qty"],
                    "effective_policy_hash": admission["effective_policy_hash"],
                    "effective_order_action": admission["effective_order_action"],
                    "automatic_order_allowed": False,
                    "status": "SIMULATED_PENDING",
                }
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

            def arriving(kind: str) -> Decimal:
                return sum(
                    (
                        quantity(s["quantity"])
                        for s in pending
                        if s["supply_kind"] == kind and s["receipt_bucket"] == bucket["yyyyww"]
                    ),
                    Decimal(0),
                )

            # Only after strategy and Guard: reveal this bucket's actual uncertain demand.
            planned = planning[bucket["yyyyww"]]
            confirmed = quantity(planned["confirmed_customer_order_qty"])
            gross = quantity(world["rows"][index]["realized_gross_demand_qty"])
            net = gross - confirmed
            state, quantities = advance_bucket(
                state,
                confirmed_customer_order_qty=confirmed,
                net_forecast_qty=net,
                confirmed_supplier_receipt_qty=arriving("CONFIRMED"),
                recommended_receipt_qty=arriving("RECOMMENDED"),
            )
            rows.append(
                {
                    **{k: prepared["context"][k] for k in SCOPE},
                    **bucket,
                    "item_id": item_id,
                    "uom": "EA",
                    "psi_scenario_type": "RECOMMENDED",
                    "strategy_type": "MATHEMATICAL",
                    "demand_role": "REALIZED_ENVIRONMENT_DEMAND",
                    "planned_gross_forecast_qty": planned["gross_forecast_qty"],
                    "gross_forecast_qty": quantity_text(gross),
                    "forecast_consumed_qty": quantity_text(confirmed),
                    "confirmed_customer_order_qty": quantity_text(confirmed),
                    "net_forecast_qty": quantity_text(net),
                    "realization_path_id": world["path_id"],
                    "realization_content_hash": truth.content_hash,
                    **quantities,
                }
            )
            decisions.append(
                {
                    "observation": obs.to_dict(),
                    "observation_hash": obs.content_hash,
                    "proposal": proposal,
                    "validation": validation,
                    "effective_policy_admission": admission,
                }
            )
            pending = [s for s in pending if s["receipt_bucket"] != bucket["yyyyww"]]
        for order in orders:
            order["status"] = "SIMULATED_RECEIVED"
        return sealed(
            "inventory-robustness-causal-psi",
            {
                "status": "DEVELOPMENT_CAUSAL_PSI_COMPUTED",
                "context": prepared["context"],
                "planning_request_hash": input_hash,
                "planning_prepared_hash": prepared["manifest"]["prepared_content_hash"],
                "development_source_hash": snapshot.source_hash,
                "realization": world,
                "realization_content_hash": truth.content_hash,
                "effective_policy_admission": admission,
                "psi_rows": rows,
                "decision_evidence": decisions,
                "recommended_orders": orders,
                "future_truth_exposed_in_observations": False,
                "official_paths_generated": False,
            },
        )

    @input_errors
    def verify(
        self,
        result: dict,
        request: RecommendationRequest,
        strategy: ReplenishmentStrategy,
        *,
        expected_result_hash: str,
        **bindings,
    ) -> None:
        hash_value(expected_result_hash)
        require(result.get("content_hash") == expected_result_hash, "CAUSAL_RESULT_HASH_MISMATCH")
        require(
            canonical_json(result) == canonical_json(self.execute(request, strategy, **bindings)),
            "CAUSAL_RESULT_REPLAY_MISMATCH",
        )

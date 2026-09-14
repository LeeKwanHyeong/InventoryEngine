"""Runtime-bound Effective Policy V2 replenishment and publication gates."""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from fixtures import reseal
from learned_fixtures import inference_request, model_fixture
from math_fixtures import build_math, reseal_math
from strategy_fixtures import ContractProbe, binding

from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.effective_policy_v2 import (
    EFFECTIVE_POLICY_V2_FIELDS,
    derive_effective_item_policy_v2,
    effective_policy_content_hash,
)
from dsio_inventory_engine.inventory_contracts.mathematical import MathematicalPolicyRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.replenishment import RecommendationRequest
from dsio_inventory_engine.inventory_contracts.runtime import (
    InventoryRuntimeExecutionRequest,
    validate_canonical_runtime_binding,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, digest
from dsio_inventory_engine.learned.application import RunLearnedPsiUseCase
from dsio_inventory_engine.recommend_replenishment.application.mathematical import (
    RunMathematicalReplenishmentUseCase,
)
from dsio_inventory_engine.recommend_replenishment.application.run import (
    RunRecommendedPsiUseCase,
)
from tests.support.runtime_fixtures import (
    CLASSIFICATION_SNAPSHOT_ID,
    CONFIG_REVISION_ID,
    DEMAND_RUN_ID,
    RUN_ID,
    bind_runtime_dispatch_to_canonical,
    reseal_runtime_site_binding,
    runtime_dispatch_v2,
)

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None


CONFIG_HASH = "a" * 64
DEPLOYMENT = DeploymentScope("DSE", "DEVELOPMENT")


def _axis(
    name: str,
    class_code: str | None,
    *,
    mode: str = "OPERATIONAL",
    status: str = "CLASSIFIED",
    evidence: dict | None = None,
) -> dict:
    return {
        "axis": name,
        "status": status,
        "class_code": class_code,
        "application_mode": mode,
        "policy_effective": mode == "OPERATIONAL" and status == "CLASSIFIED",
        "source_contract_key": f"{name}_SOURCE_V1",
        "evidence": evidence or {},
        "reason_code": None if status == "CLASSIFIED" else "SOURCE_NOT_VERIFIED",
    }


def _effective_policy(
    *,
    action: str = "REVIEW",
    approval: str = "HIGH_VALUE",
    p90_days: str = "30",
    strategy: str = "MATHEMATICAL",
) -> dict:
    fsn_class = {"ALLOW": "F", "REVIEW": "N", "BLOCK": "N"}[action]
    plc = _axis(
        "PLC",
        "MATURE",
        mode="SHADOW",
        status="SYNTHETIC",
    )
    axes = [
        _axis("ABC", "A"),
        _axis("XYZ", "X"),
        _axis("VED", "V"),
        _axis("FSN", fsn_class),
        _axis(
            "SDE",
            "S",
            evidence={"p50_lead_time_days": "7", "p90_lead_time_days": p90_days},
        ),
        _axis("HML", {"AUTO": "L", "STANDARD": "M", "HIGH_VALUE": "H"}[approval]),
        plc,
    ]
    base = {
        "item_id": "ITEM-A",
        "classification_status": "CLASSIFIED",
        "segment_key": "AX",
        "unclassified_reason_code": None,
    }
    return {
        **base,
        **derive_effective_item_policy_v2(
            **base,
            classification_config_hash=CONFIG_HASH,
            policy_cell={
                "segment_key": "AX",
                "target_service_level": 0.95,
                "review_cycle_weeks": 2,
                "strategy": strategy,
            },
            ved_service_level_floor={"V": 0.99, "E": 0.97, "D": 0.9},
            axis_results=axes,
            policy_overlays={
                "fsn_order_action": {
                    "F": "ALLOW",
                    "S": "ALLOW",
                    "N": action if action in {"REVIEW", "BLOCK"} else "REVIEW",
                },
                "sde_lead_time_basis": {"S": "P90", "D": "P90", "E": "P50"},
                "hml_approval_level": {"H": "HIGH_VALUE", "M": "STANDARD", "L": "AUTO"},
                "plc_order_action": {
                    "PRE_LAUNCH": "REVIEW",
                    "INTRODUCTION": "ALLOW",
                    "GROWTH": "ALLOW",
                    "MATURE": "ALLOW",
                    "DECLINE": "REVIEW",
                    "SERVICE_ONLY": "REVIEW",
                    "DISCONTINUED": "BLOCK",
                },
            },
        ),
    }


def _requests(
    *,
    action: str = "REVIEW",
    approval: str = "HIGH_VALUE",
    p90_days: str = "30",
) -> tuple[MathematicalPolicyRequest, InventoryRuntimeExecutionRequest]:
    policy = _effective_policy(
        action=action,
        approval=approval,
        p90_days=p90_days,
    )
    policy_hash = effective_policy_content_hash([policy])
    policy_projection = {
        "item_id": policy["item_id"],
        **{key: policy[key] for key in EFFECTIVE_POLICY_V2_FIELDS},
    }
    data = build_math()
    canonical = data["recommendation"]["canonical_input"]
    canonical["context"].update(
        engine_run_id=RUN_ID,
        configuration_revision=CONFIG_REVISION_ID,
        demand_run_id=DEMAND_RUN_ID,
    )
    canonical["snapshots"]["forecast"]["metadata"]["demand_run_id"] = DEMAND_RUN_ID
    canonical = CanonicalInputRequest.from_dict(reseal(canonical))
    data["recommendation"]["canonical_input"] = canonical.to_dict()
    execution = data["recommendation"]["execution"]
    execution.update(
        contract_version="2.0.0",
        execution_mode="PLATFORM_BOUND",
        configuration_revision=CONFIG_REVISION_ID,
        canonical_input_hash=canonical.input_hash,
        execution_purpose="OPERATIONAL",
        model_approval=None,
        effective_policy_binding={
            "contract_id": "inventory-effective-policy-run-binding-v2",
            "contract_version": "2.0.0",
            "classification_snapshot_id": CLASSIFICATION_SNAPSHOT_ID,
            "classification_config_hash": CONFIG_HASH,
            "effective_policy_content_hash": policy_hash,
        },
        effective_item_policies=[policy_projection],
    )
    data["policy_input"]["context"].update(
        canonical_input_hash=canonical.input_hash,
        configuration_revision=CONFIG_REVISION_ID,
    )
    mathematical = MathematicalPolicyRequest.from_dict(reseal_math(data))
    runtime_data = runtime_dispatch_v2(
        config_hash=CONFIG_HASH,
        effective_policy_content_hash=policy_hash,
        expected_automatic_publish_allowed=policy["automatic_publish_allowed"],
        expected_automatic_order_allowed=policy["automatic_order_allowed"],
    )
    runtime_data = bind_runtime_dispatch_to_canonical(
        runtime_data,
        canonical.to_dict(),
    )
    return mathematical, InventoryRuntimeExecutionRequest.from_dict(runtime_data)


def _replace_canonical(
    request: MathematicalPolicyRequest,
    canonical: CanonicalInputRequest,
) -> MathematicalPolicyRequest:
    changed = copy.deepcopy(request.to_dict())
    changed["recommendation"]["canonical_input"] = canonical.to_dict()
    changed["recommendation"]["execution"]["canonical_input_hash"] = canonical.input_hash
    changed["policy_input"]["context"]["canonical_input_hash"] = canonical.input_hash
    return MathematicalPolicyRequest.from_dict(reseal_math(changed))


def _tamper_claim_binding(
    runtime: InventoryRuntimeExecutionRequest,
    *,
    input_type: str,
    field: str,
    replacement: str,
) -> InventoryRuntimeExecutionRequest:
    changed = copy.deepcopy(runtime.to_dict())
    binding = next(
        row for row in changed["claim"]["input_bindings"] if row["input_type"] == input_type
    )
    binding[field] = replacement
    return InventoryRuntimeExecutionRequest.from_dict(reseal_runtime_site_binding(changed))


class RuntimeEffectivePolicyV2Tests(unittest.TestCase):
    @unittest.skipIf(Draft202012Validator is None, "jsonschema is optional")
    def test_v2_execution_matches_published_json_schema(self) -> None:
        request, _ = _requests()
        schema = json.loads(
            (Path(__file__).resolve().parents[2] / "schemas/replenishment.schema.json").read_text()
        )

        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(request.to_dict()["recommendation"]["execution"])

    def test_review_calculates_and_preserves_evidence_but_withholds_automation(self) -> None:
        request, runtime = _requests(p90_days="7")

        result = RunMathematicalReplenishmentUseCase(DEPLOYMENT).execute(
            request,
            runtime_request=runtime,
        )

        self.assertEqual(result["publication_disposition"], "REVIEW_REQUIRED")
        self.assertFalse(result["automatic_publish_allowed"])
        self.assertFalse(result["automatic_order_allowed"])
        self.assertTrue(result["recommended_orders"])
        self.assertEqual(
            result["recommended_orders"][0]["execution_disposition"],
            "APPROVAL_REQUIRED",
        )
        admission = result["effective_policy_admission_evidence"][0]
        self.assertTrue(admission["recommendation_calculation_allowed"])
        self.assertTrue(admission["evidence_storage_allowed"])
        self.assertEqual(admission["effective_order_action"], "REVIEW")

    def test_sde_service_level_and_review_cycle_override_source_policy(self) -> None:
        request, runtime = _requests()

        result = RunMathematicalReplenishmentUseCase(DEPLOYMENT).execute(
            request,
            runtime_request=runtime,
        )

        first = result["mathematical_policy_report"]["policy_evidence"][0]
        trace = first["calculation_trace"]
        self.assertEqual(trace["source_master_lead_time_days"], 7)
        self.assertEqual(trace["physical_due_lead_time_days"], 7)
        self.assertEqual(trace["effective_protection_lead_time_basis"], "P90")
        self.assertEqual(trace["effective_protection_lead_time_days"], 30)
        self.assertEqual(trace["lead_time_weeks"], "5")
        self.assertEqual(trace["source_approved_service_level"], "0.95")
        self.assertEqual(trace["effective_target_service_level"], "0.99")
        self.assertEqual(trace["replenishment_cycle_weeks"], 2)
        source_policy = result["prepared_input"]["policies"][0]
        self.assertEqual(source_policy["lead_time_days"], 7)
        self.assertEqual(source_policy["effective_protection_lead_time_days"], 30)
        self.assertEqual(result["recommended_orders"][0]["due_date"], "2026-10-05")

    def test_hml_auto_allows_automatic_order_when_order_gate_allows(self) -> None:
        request, runtime = _requests(action="ALLOW", approval="AUTO")

        result = RunMathematicalReplenishmentUseCase(DEPLOYMENT).execute(
            request,
            runtime_request=runtime,
        )

        self.assertEqual(result["publication_disposition"], "AUTO_PUBLISH_ALLOWED")
        self.assertTrue(result["automatic_publish_allowed"])
        self.assertTrue(result["automatic_order_allowed"])

    def test_block_fails_before_mathematical_policy_calculation(self) -> None:
        request, runtime = _requests(action="BLOCK")

        with patch(
            "dsio_inventory_engine.recommend_replenishment.application.mathematical.build_policy_report"
        ) as calculate:
            with self.assertRaisesRegex(
                InventoryInputError,
                "ITEM_POLICY_RECOMMENDATION_CALCULATION_BLOCKED",
            ):
                RunMathematicalReplenishmentUseCase(DEPLOYMENT).execute(
                    request,
                    runtime_request=runtime,
                )
        calculate.assert_not_called()

    def test_runtime_binding_drift_is_rejected(self) -> None:
        request, runtime = _requests()
        changed = copy.deepcopy(runtime.to_dict())
        classification = next(
            row
            for row in changed["claim"]["input_bindings"]
            if row["input_type"] == "INVENTORY_CLASSIFICATION"
        )
        classification["source_content_hash"] = "f" * 64
        changed_runtime = InventoryRuntimeExecutionRequest.from_dict(changed)

        with self.assertRaisesRegex(
            InventoryInputError,
            "RUNTIME_CLASSIFICATION_BINDING_MISMATCH",
        ):
            RunMathematicalReplenishmentUseCase(DEPLOYMENT).execute(
                request,
                runtime_request=changed_runtime,
            )

    def test_full_claim_identity_drift_is_rejected_before_policy_calculation(self) -> None:
        request, runtime = _requests()
        mutations = {
            "RUNTIME_CANONICAL_RUN_ID_MISMATCH": lambda value: value.update(
                engine_run_id="10000000-0000-4000-8000-000000000099"
            ),
            "RUNTIME_CANONICAL_CYCLE_MISMATCH": lambda value: value["claim"].update(
                planning_cycle_id="PC-OTHER"
            ),
            "RUNTIME_CANONICAL_SCOPE_MISMATCH": lambda value: value["claim"]["scope"].update(
                plant_cd="V999"
            ),
            "RUNTIME_SITE_BINDING_HASH_MISMATCH": lambda value: value["claim"].update(
                site_binding_hash="f" * 64
            ),
        }
        for code, mutate in mutations.items():
            with self.subTest(code=code):
                changed = copy.deepcopy(runtime.to_dict())
                mutate(changed)
                if code == "RUNTIME_CANONICAL_SCOPE_MISMATCH":
                    changed["claim"]["plan_key_hash"] = digest(
                        {
                            "planning_cycle_revision_id": changed["claim"][
                                "planning_cycle_revision_id"
                            ],
                            "plan_id": changed["claim"]["plan_id"],
                            "scope": changed["claim"]["scope"],
                        }
                    )
                changed_runtime = InventoryRuntimeExecutionRequest.from_dict(changed)
                with patch(
                    "dsio_inventory_engine.recommend_replenishment.application.mathematical.build_policy_report"
                ) as calculate:
                    with self.assertRaisesRegex(InventoryInputError, code):
                        RunMathematicalReplenishmentUseCase(DEPLOYMENT).execute(
                            request,
                            runtime_request=changed_runtime,
                        )
                calculate.assert_not_called()

    def test_plan_id_drift_is_rejected_even_with_recomputed_plan_key(self) -> None:
        request, runtime = _requests()
        changed = copy.deepcopy(runtime.to_dict())
        changed["claim"]["plan_id"] = "TAMPERED-PLAN"
        changed["claim"]["plan_key_hash"] = digest(
            {
                "planning_cycle_revision_id": changed["claim"]["planning_cycle_revision_id"],
                "plan_id": changed["claim"]["plan_id"],
                "scope": changed["claim"]["scope"],
            }
        )
        changed_runtime = InventoryRuntimeExecutionRequest.from_dict(changed)

        with patch(
            "dsio_inventory_engine.recommend_replenishment.application.mathematical.build_policy_report"
        ) as calculate:
            with self.assertRaisesRegex(
                InventoryInputError,
                "RUNTIME_CANONICAL_PLAN_ID_MISMATCH",
            ):
                RunMathematicalReplenishmentUseCase(DEPLOYMENT).execute(
                    request,
                    runtime_request=changed_runtime,
                )
        calculate.assert_not_called()

    def test_demand_run_claim_drift_is_rejected_before_policy_calculation(self) -> None:
        request, runtime = _requests()
        changed = copy.deepcopy(runtime.to_dict())
        changed["claim"]["demand_run_id"] = "50000000-0000-4000-8000-000000000099"
        changed_runtime = InventoryRuntimeExecutionRequest.from_dict(changed)

        with patch(
            "dsio_inventory_engine.recommend_replenishment.application.mathematical.build_policy_report"
        ) as calculate:
            with self.assertRaisesRegex(
                InventoryInputError,
                "RUNTIME_CANONICAL_DEMAND_RUN_MISMATCH",
            ):
                RunMathematicalReplenishmentUseCase(DEPLOYMENT).execute(
                    request,
                    runtime_request=changed_runtime,
                )
        calculate.assert_not_called()

    def test_forecast_provenance_demand_run_drift_is_rejected(self) -> None:
        request, runtime = _requests()
        canonical = request.to_dict()["recommendation"]["canonical_input"]
        provenance = dict(canonical["snapshots"]["forecast"]["metadata"])
        provenance["demand_run_id"] = "50000000-0000-4000-8000-000000000099"

        with self.assertRaisesRegex(
            InventoryInputError,
            "RUNTIME_CANONICAL_DEMAND_RUN_MISMATCH",
        ):
            validate_canonical_runtime_binding(
                runtime,
                context=canonical["context"],
                input_bindings=canonical["input_bindings"],
                forecast_provenance=provenance,
            )

    def test_all_canonical_snapshot_claim_drift_is_rejected(self) -> None:
        request, runtime = _requests()
        for input_type in (
            "DEMAND_FORECAST",
            "INVENTORY_POSITION",
            "REPLENISHMENT_POLICY",
            "SUPPLY_RECEIPTS",
            "CUSTOMER_ORDERS",
            "PRIOR_INVENTORY",
            "CALENDAR",
            "MASTER",
        ):
            for field, replacement in (
                ("source_snapshot_id", "TAMPERED-SNAPSHOT"),
                ("source_content_hash", "f" * 64),
            ):
                with self.subTest(input_type=input_type, field=field):
                    changed_runtime = _tamper_claim_binding(
                        runtime,
                        input_type=input_type,
                        field=field,
                        replacement=replacement,
                    )
                    with self.assertRaisesRegex(
                        InventoryInputError,
                        "RUNTIME_CANONICAL_INPUT_BINDING_MISMATCH",
                    ):
                        RunMathematicalReplenishmentUseCase(DEPLOYMENT).execute(
                            request,
                            runtime_request=changed_runtime,
                        )

    def test_new_binding_matrix_rejects_canonical_and_combined_tampering(self) -> None:
        request, runtime = _requests()
        canonical = request.to_dict()["recommendation"]["canonical_input"]
        mapping = {
            "REPLENISHMENT_POLICY": "policies",
            "SUPPLY_RECEIPTS": "receipts",
            "CUSTOMER_ORDERS": "customer_orders",
            "PRIOR_INVENTORY": "prior_inventory",
        }
        for input_type, kind in mapping.items():
            for claim_field, canonical_field, replacement in (
                ("source_snapshot_id", "snapshot_id", "TAMPERED-SNAPSHOT"),
                ("source_content_hash", "content_hash", "f" * 64),
            ):
                with self.subTest(
                    input_type=input_type,
                    field=claim_field,
                    mode="canonical_only",
                ):
                    canonical_bindings = copy.deepcopy(canonical["input_bindings"])
                    canonical_bindings[kind][canonical_field] = replacement
                    with self.assertRaisesRegex(
                        InventoryInputError,
                        "RUNTIME_CANONICAL_INPUT_BINDING_MISMATCH",
                    ):
                        validate_canonical_runtime_binding(
                            runtime,
                            context=canonical["context"],
                            input_bindings=canonical_bindings,
                            forecast_provenance=canonical["snapshots"]["forecast"]["metadata"],
                        )

                with self.subTest(
                    input_type=input_type,
                    field=claim_field,
                    mode="claim_and_canonical_with_rehashed_site",
                ):
                    changed_canonical = copy.deepcopy(canonical)
                    changed_canonical["input_bindings"][kind][canonical_field] = replacement
                    changed_request = _replace_canonical(
                        request,
                        CanonicalInputRequest.from_dict(changed_canonical),
                    )
                    changed_runtime = _tamper_claim_binding(
                        runtime,
                        input_type=input_type,
                        field=claim_field,
                        replacement=replacement,
                    )
                    with patch(
                        "dsio_inventory_engine.recommend_replenishment.application.mathematical.build_policy_report"
                    ) as calculate:
                        with self.assertRaisesRegex(
                            InventoryInputError,
                            "PINNED_INPUT_MISMATCH",
                        ):
                            RunMathematicalReplenishmentUseCase(DEPLOYMENT).execute(
                                changed_request,
                                runtime_request=changed_runtime,
                            )
                    calculate.assert_not_called()

    def test_same_claim_rejects_changed_physical_policy_due_date(self) -> None:
        request, runtime = _requests()
        changed = request.to_dict()
        canonical = changed["recommendation"]["canonical_input"]
        canonical["snapshots"]["policies"]["rows"][0]["lead_time_days"] = 14
        canonical = CanonicalInputRequest.from_dict(reseal(canonical))
        changed["recommendation"]["canonical_input"] = canonical.to_dict()
        changed["recommendation"]["execution"]["canonical_input_hash"] = canonical.input_hash
        changed["policy_input"]["context"]["canonical_input_hash"] = canonical.input_hash
        changed_request = MathematicalPolicyRequest.from_dict(reseal_math(changed))

        with patch(
            "dsio_inventory_engine.recommend_replenishment.application.mathematical.build_policy_report"
        ) as calculate:
            with self.assertRaisesRegex(
                InventoryInputError,
                "RUNTIME_CANONICAL_INPUT_BINDING_MISMATCH",
            ):
                RunMathematicalReplenishmentUseCase(DEPLOYMENT).execute(
                    changed_request,
                    runtime_request=runtime,
                )
        calculate.assert_not_called()

    def test_same_claim_rejects_resealed_operational_dataset_changes(self) -> None:
        request, runtime = _requests()
        for kind in ("receipts", "customer_orders", "prior_inventory"):
            with self.subTest(kind=kind):
                canonical = copy.deepcopy(request.to_dict()["recommendation"]["canonical_input"])
                context = canonical["context"]
                common = {
                    "company_cd": context["company_cd"],
                    "subs_cd": context["subs_cd"],
                    "site_cd": context["site_cd"],
                    "item_id": "ITEM-A",
                    "uom": "EA",
                }
                if kind == "receipts":
                    canonical["snapshots"][kind]["rows"].append(
                        {
                            **common,
                            "receipt_id": "TAMPERED-RECEIPT",
                            "due_date": context["plan_start_date"],
                            "due_qty": "1",
                            "status": "CONFIRMED",
                            "supply_type": "PURCHASE_ORDER",
                        }
                    )
                elif kind == "customer_orders":
                    canonical["snapshots"][kind]["rows"].append(
                        {
                            **common,
                            "yyyyww": context["plan_yyyyww"],
                            "confirmed_customer_order_qty": "1",
                        }
                    )
                else:
                    prior = canonical["snapshots"][kind]
                    prior["snapshot_id"] += ":TAMPERED"
                    canonical["snapshots"]["inventory"]["metadata"]["prior_snapshot_id"] = prior[
                        "snapshot_id"
                    ]
                changed_canonical = CanonicalInputRequest.from_dict(reseal(canonical))
                changed_request = _replace_canonical(request, changed_canonical)

                with patch(
                    "dsio_inventory_engine.recommend_replenishment.application.mathematical.build_policy_report"
                ) as calculate:
                    with self.assertRaisesRegex(
                        InventoryInputError,
                        "RUNTIME_CANONICAL_INPUT_BINDING_MISMATCH",
                    ):
                        RunMathematicalReplenishmentUseCase(DEPLOYMENT).execute(
                            changed_request,
                            runtime_request=runtime,
                        )
                calculate.assert_not_called()

    def test_approved_learned_strategies_are_admitted_for_local_shadow_only(self) -> None:
        for family in ("PREDICTIVE_ML", "DEEP_RL"):
            with self.subTest(family=family):
                data = build_math()["recommendation"]
                policy = _effective_policy(
                    action="ALLOW",
                    approval="AUTO",
                    p90_days="7",
                    strategy=family,
                )
                policy_hash = effective_policy_content_hash([policy])
                model_descriptor = binding(family)
                execution = data["execution"]
                execution.update(
                    contract_version="2.0.0",
                    execution_mode="LOCAL_SHADOW",
                    execution_purpose="SHADOW",
                    strategy=model_descriptor,
                    model_approval={
                        "approval_reference": f"APPROVED-{family}",
                        "status": "APPROVED",
                        **model_descriptor["model"],
                    },
                    effective_policy_binding={
                        "contract_id": "inventory-effective-policy-run-binding-v2",
                        "contract_version": "2.0.0",
                        "classification_snapshot_id": CLASSIFICATION_SNAPSHOT_ID,
                        "classification_config_hash": CONFIG_HASH,
                        "effective_policy_content_hash": policy_hash,
                    },
                    effective_item_policies=[
                        {
                            "item_id": policy["item_id"],
                            **{key: policy[key] for key in EFFECTIVE_POLICY_V2_FIELDS},
                        }
                    ],
                )
                recommendation = RecommendationRequest.from_dict(data)
                probe = ContractProbe(family)
                probe.input_binding = execution["strategy_input_binding"]

                result = RunRecommendedPsiUseCase(DEPLOYMENT).execute(
                    recommendation,
                    probe,
                )

                self.assertEqual(result["strategy"]["strategy_type"], family)
                self.assertFalse(result["automatic_publish_allowed"])
                self.assertFalse(result["automatic_order_allowed"])
                self.assertEqual(
                    result["effective_policy_admission_evidence"][0]["model_approval_reference"],
                    f"APPROVED-{family}",
                )

    def test_learned_shadow_missing_or_mismatched_model_approval_fails_closed(self) -> None:
        data = build_math()["recommendation"]
        policy = _effective_policy(strategy="PREDICTIVE_ML")
        policy_hash = effective_policy_content_hash([policy])
        model_descriptor = binding("PREDICTIVE_ML")
        execution = data["execution"]
        execution.update(
            contract_version="2.0.0",
            execution_mode="LOCAL_SHADOW",
            execution_purpose="SHADOW",
            strategy=model_descriptor,
            model_approval=None,
            effective_policy_binding={
                "contract_id": "inventory-effective-policy-run-binding-v2",
                "contract_version": "2.0.0",
                "classification_snapshot_id": CLASSIFICATION_SNAPSHOT_ID,
                "classification_config_hash": CONFIG_HASH,
                "effective_policy_content_hash": policy_hash,
            },
            effective_item_policies=[
                {
                    "item_id": policy["item_id"],
                    **{key: policy[key] for key in EFFECTIVE_POLICY_V2_FIELDS},
                }
            ],
        )
        with self.assertRaisesRegex(
            InventoryInputError,
            "REPLENISHMENT_MODEL_APPROVAL_BINDING_MISMATCH",
        ):
            RecommendationRequest.from_dict(data)

    def test_approved_ml_and_ppo_artifacts_execute_with_v2_shadow_policy(self) -> None:
        for family in ("PREDICTIVE_ML", "DEEP_RL"):
            with self.subTest(family=family):
                data = build_math()
                policy = _effective_policy(
                    action="ALLOW",
                    approval="AUTO",
                    p90_days="7",
                    strategy=family,
                )
                policy_hash = effective_policy_content_hash([policy])
                execution = data["recommendation"]["execution"]
                execution.update(
                    contract_version="2.0.0",
                    execution_mode="LOCAL_SHADOW",
                    execution_purpose="SHADOW",
                    model_approval=None,
                    effective_policy_binding={
                        "contract_id": "inventory-effective-policy-run-binding-v2",
                        "contract_version": "2.0.0",
                        "classification_snapshot_id": CLASSIFICATION_SNAPSHOT_ID,
                        "classification_config_hash": CONFIG_HASH,
                        "effective_policy_content_hash": policy_hash,
                    },
                    effective_item_policies=[
                        {
                            "item_id": policy["item_id"],
                            **{key: policy[key] for key in EFFECTIVE_POLICY_V2_FIELDS},
                        }
                    ],
                )
                anchor = MathematicalPolicyRequest.from_dict(reseal_math(data))
                model = model_fixture(anchor.to_dict(), family)
                approval = {
                    "approval_reference": f"APPROVED-{family}",
                    "status": "APPROVED",
                    **model.reference,
                }

                result = RunLearnedPsiUseCase(DEPLOYMENT).execute(
                    inference_request(
                        anchor.to_dict(),
                        model,
                        model_approval=approval,
                    )
                )

                self.assertEqual(result["strategy"]["strategy_type"], family)
                self.assertTrue(result["model_artifact_hash_verified"])
                self.assertFalse(result["automatic_publish_allowed"])
                self.assertFalse(result["automatic_order_allowed"])
                self.assertEqual(
                    result["effective_policy_admission_evidence"][0]["model_approval_reference"],
                    f"APPROVED-{family}",
                )


if __name__ == "__main__":
    unittest.main()

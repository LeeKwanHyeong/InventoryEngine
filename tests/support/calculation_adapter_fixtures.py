"""Packaging only; numerical expectations are not generated here."""

from dsio_inventory_engine.inventory_contracts.calculation_boundary import (
    CalculationBoundaryBinding,
)
from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.values import digest
from dsio_inventory_engine.prepare_inventory.application.inventory_input import (
    PrepareInventoryInputUseCase,
)
from tests.support.fixtures import build_request, reseal

DEPLOYMENT = DeploymentScope("DSE", "DEVELOPMENT")


def case(
    *, items=None, kernel_hash="a" * 64, legacy=True, candidates=1, start="2026-09-28", version=None
):
    raw = build_request(
        {
            "id": "canonical-dll",
            "start_date": start,
            "items": items or [{"id": "ITEM-A", "boh": "10", "forecast": ["10", "10", "10"]}],
        }
    )
    if version is not None:
        raw["contract_id"], raw["contract_version"] = "io-canonical-input-v2", version
        for rule in raw["quantity_rules"]:
            rule.pop("scale")
            rule.update(planning_scale=6, physical_scale=0)
        if version == "2.1.0":
            from dsio_inventory_engine.inventory_contracts.development_universe import (
                seal_development_universe,
            )

            original = {
                k: dict(raw["input_bindings"][k]) for k in ("calendar", "master", "forecast")
            }
            raw["snapshots"]["forecast"]["snapshot_id"] += ".canonical"
            raw = reseal(raw)
            raw["upstream_bindings"] = original
            raw["development_universe"] = seal_development_universe(
                {
                    "contract_id": "io-development-calculation-universe-v1",
                    "environment_scope": "DEVELOPMENT",
                    "demand_run_id": raw["context"]["demand_run_id"],
                    "plan_yyyyww": raw["context"]["plan_yyyyww"],
                    "original_master_content_hash": original["master"]["content_hash"],
                    "original_forecast_content_hash": original["forecast"]["content_hash"],
                    "source_demand_window_file_sha256": "a" * 64,
                    "approval_reference": "TEST-ONLY-NO-APPROVAL",
                    "included_item_ids": [r["item_id"] for r in raw["snapshots"]["master"]["rows"]],
                    "exclusions": [],
                }
            )
    canonical = CanonicalInputRequest.from_dict(raw)
    prepared = PrepareInventoryInputUseCase(DEPLOYMENT).execute(canonical)
    params = {
        "canonical_input_hash": canonical.input_hash,
        "prepared_content_hash": prepared.to_dict()["manifest"]["prepared_content_hash"],
        "source_type": "SYNTHETIC_DEVELOPMENT",
        "rows": [
            {
                "item_id": item["item_id"],
                "candidate_id": "CANDIDATE-" + str(i),
                "stock_policy": "OUTL",
                "internal_lead_time": "201600",
                "lot": {"minimum": "0", "maximum": "1000000", "step": "1"},
                "policies": [
                    {
                        "start": "2020-01-01",
                        "end": "2030-12-31",
                        "rop": "10",
                        "target": "23",
                        "mos": "1",
                        "ma_demand": "10",
                    }
                ],
            }
            for item in prepared.to_dict()["master"]
            for i in range(candidates)
        ],
    }
    reference = "fixture:sealed-legacy-parameters"
    binding = bind(
        canonical, prepared, kernel_hash, legacy=legacy, params=params, reference=reference
    )
    return canonical, prepared, binding, params, reference


def bind(
    canonical,
    prepared,
    kernel_hash,
    *,
    legacy=True,
    params=None,
    reference="fixture:sealed-legacy-parameters",
):
    return CalculationBoundaryBinding.from_sources(
        canonical,
        prepared_content_hash=prepared.to_dict()["manifest"]["prepared_content_hash"],
        psi_simulator={
            "implementation_id": "inventory.psi.legacy_replay"
            if legacy
            else "inventory.psi.target_guarded.csharp",
            "version": "1.0.0",
            "implementation_content_hash": kernel_hash,
        },
        calculation_backend="CSHARP_PYTHONNET",
        semantics_profile="LEGACY_REPLAY" if legacy else "TARGET_GUARDED",
        psi_scenario_type="RECOMMENDED" if legacy else "BASELINE",
        legacy_parameters_binding={"reference": reference, "content_hash": digest(params)}
        if legacy
        else None,
    )

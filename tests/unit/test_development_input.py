"""Literal independent warm-up Golden and fail-closed development cohort."""

import copy
import sys
import unittest
import json
from dataclasses import replace
from decimal import Decimal, localcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))
from fixtures import build_request, reseal

from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.development_universe import seal_development_universe
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from dsio_inventory_engine.prepare_inventory.application.development_input import synthetic_warmup
from dsio_inventory_engine.prepare_inventory.application.inventory_input import (
    PrepareInventoryInputUseCase,
)


def cohort():
    data = build_request(
        {
            "id": "dev-cohort",
            "items": [
                {"id": "ITEM-A", "boh": "10", "forecast": ["1"] * 3},
                {"id": "ITEM-B", "boh": "0", "forecast": ["0"] * 3},
            ],
        }
    )
    data.update(
        contract_id="io-canonical-input-v2",
        quantity_rules=[
            {
                "uom": "EA",
                "planning_scale": 6,
                "physical_scale": 0,
                "tolerance_qty": "0",
                "approval_reference": "USER-DEV-COHORT",
            }
        ],
    )
    for kind in ("forecast", "inventory", "prior_inventory", "policies"):
        data["snapshots"][kind]["rows"] = [
            r for r in data["snapshots"][kind]["rows"] if r["item_id"] == "ITEM-A"
        ]
    raw_forecast_id = data["snapshots"]["forecast"]["snapshot_id"]
    data["snapshots"]["forecast"]["snapshot_id"] += ".canonical"
    data = reseal(data)
    data["contract_version"] = "2.1.0"
    data["upstream_bindings"] = {
        k: copy.deepcopy(data["input_bindings"][k]) for k in ("calendar", "master", "forecast")
    }
    data["upstream_bindings"]["forecast"]["snapshot_id"] = raw_forecast_id
    data["development_universe"] = seal_development_universe(
        {
            "contract_id": "io-development-calculation-universe-v1",
            "environment_scope": "DEVELOPMENT",
            "demand_run_id": data["context"]["demand_run_id"],
            "plan_yyyyww": "202640",
            "original_master_content_hash": data["upstream_bindings"]["master"]["content_hash"],
            "original_forecast_content_hash": data["upstream_bindings"]["forecast"]["content_hash"],
            "source_demand_window_file_sha256": "a" * 64,
            "approval_reference": "USER-DEV-COHORT",
            "included_item_ids": ["ITEM-A"],
            "exclusions": [
                {
                    "item_id": "ITEM-B",
                    "reason": "EXCLUDED_ENDED_DEMAND_WINDOW",
                    "demand_end_yyyyww": "202639",
                }
            ],
        }
    )
    return data


class DevelopmentInputTests(unittest.TestCase):
    def test_v21_runtime_binds_original_forecast_not_canonical_projection(self):
        from test_runtime_effective_policy_v2 import _requests
        from tests.support.runtime_fixtures import (
            bind_runtime_dispatch_to_canonical,
            reseal_runtime_site_binding,
        )
        from dsio_inventory_engine.inventory_contracts.runtime import (
            InventoryRuntimeExecutionRequest,
            validate_canonical_runtime_binding,
        )

        math, runtime = _requests()
        body = math.to_dict()["recommendation"]["canonical_input"]
        raw_bindings = copy.deepcopy(body["input_bindings"])
        body["contract_id"] = "io-canonical-input-v2"
        body["contract_version"] = "2.1.0"
        body["quantity_rules"] = [
            {
                "uom": "EA",
                "planning_scale": 6,
                "physical_scale": 0,
                "tolerance_qty": "0",
                "approval_reference": "DEV-TEST",
            }
        ]
        body["snapshots"]["forecast"]["snapshot_id"] += ".canonical"
        body = reseal(body)
        body["upstream_bindings"] = {k: raw_bindings[k] for k in ("calendar", "master", "forecast")}
        body["development_universe"] = seal_development_universe(
            {
                "contract_id": "io-development-calculation-universe-v1",
                "environment_scope": "DEVELOPMENT",
                "demand_run_id": body["context"]["demand_run_id"],
                "plan_yyyyww": body["context"]["plan_yyyyww"],
                "original_master_content_hash": raw_bindings["master"]["content_hash"],
                "original_forecast_content_hash": raw_bindings["forecast"]["content_hash"],
                "source_demand_window_file_sha256": "a" * 64,
                "approval_reference": "DEV-TEST",
                "included_item_ids": ["ITEM-A"],
                "exclusions": [],
            }
        )
        canonical = CanonicalInputRequest.from_dict(body)
        dispatch = bind_runtime_dispatch_to_canonical(runtime.to_dict(), canonical.to_dict())
        claimed = next(
            b for b in dispatch["claim"]["input_bindings"] if b["input_type"] == "DEMAND_FORECAST"
        )
        claimed.update(
            source_snapshot_id=raw_bindings["forecast"]["snapshot_id"],
            source_content_hash=raw_bindings["forecast"]["content_hash"],
        )
        good = InventoryRuntimeExecutionRequest.from_dict(reseal_runtime_site_binding(dispatch))
        validate_canonical_runtime_binding(good, canonical_input=canonical)
        for key, value in (
            ("source_snapshot_id", body["snapshots"]["forecast"]["snapshot_id"]),
            ("source_content_hash", "f" * 64),
        ):
            changed = copy.deepcopy(dispatch)
            next(
                b
                for b in changed["claim"]["input_bindings"]
                if b["input_type"] == "DEMAND_FORECAST"
            )[key] = value
            with self.assertRaisesRegex(
                InventoryInputError, "RUNTIME_CANONICAL_INPUT_BINDING_MISMATCH"
            ):
                validate_canonical_runtime_binding(
                    InventoryRuntimeExecutionRequest.from_dict(
                        reseal_runtime_site_binding(changed)
                    ),
                    canonical_input=canonical,
                )

    def test_full_v100_recommendation_is_not_silently_above_safety_cap(self):
        from dsio_inventory_engine.recommend_replenishment.application.run import validate_controls

        items = [{"item_id": f"ITEM-{i}", "uom": "EA"} for i in range(3622)]
        prepared = {
            "master": items,
            "calendar": [{"start_date": str(i)} for i in range(26)],
            "manifest": {"quantity_rules": []},
        }
        with self.assertRaisesRegex(InventoryInputError, "RECOMMENDATION_OBSERVATION_LIMIT"):
            validate_controls(prepared, {"item_controls": items})

    def test_first_excluded_week_equal_to_w0_is_ended(self):
        value = cohort()
        scope = value["development_universe"]
        scope.pop("content_hash")
        scope["exclusions"][0]["demand_end_yyyyww"] = "202640"
        value["development_universe"] = seal_development_universe(scope)
        PrepareInventoryInputUseCase(DeploymentScope("DSE", "DEVELOPMENT")).execute(
            CanonicalInputRequest.from_dict(value)
        )

    def test_v21_schema_and_python_reject_unknown_fields(self):
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource
        from referencing.jsonschema import DRAFT202012

        root = Path(__file__).resolve().parents[2] / "schemas"
        schema = json.loads((root / "canonical_input_v21.schema.json").read_text())
        # A file URI keeps relative references local; no network resolver is used.
        schema["$id"] = (root / "canonical_input_v21.schema.json").as_uri()
        base_uri = (root / "canonical_input_v2.schema.json").as_uri()
        registry = Registry().with_resource(
            base_uri,
            Resource.from_contents(
                json.loads((root / "canonical_input_v2.schema.json").read_text()),
                default_specification=DRAFT202012,
            ),
        )
        validator = Draft202012Validator(schema, registry=registry)
        validator.validate(cohort())
        for key in ("upstream_bindings", "development_universe"):
            value = cohort()
            value[key]["force_operational"] = True
            self.assertFalse(validator.is_valid(value))
            with self.assertRaises(InventoryInputError):
                CanonicalInputRequest.from_dict(value)

    def test_pinned_classification_has_no_future_week_or_fsn_promotion(self):
        from test_seven_axis_segmentation import inputs, v2_values, v2_metric
        from dsio_inventory_engine.classify_inventory.application import (
            CONFIG_SCHEMA_V2_ID,
            CONFIG_SCHEMA_V2_VERSION,
            CONFIG_SCHEMA_V2_HASH,
            build_snapshot,
            config_hash,
        )
        from dsio_inventory_engine.infrastructure.postgresql.classification_snapshot import (
            _validate_publishable_snapshot,
        )

        source = inputs()
        config = v2_values()
        changed = replace(
            source,
            metrics=tuple(v2_metric(m, fsn_source_status="UNVERIFIED") for m in source.metrics),
            config_values=config,
            config_hash=config_hash(config),
            config_schema_id=CONFIG_SCHEMA_V2_ID,
            config_schema_version=CONFIG_SCHEMA_V2_VERSION,
            config_schema_hash=CONFIG_SCHEMA_V2_HASH,
            history_source_type="DEVELOPMENT_PINNED",
            source_relation="DEVELOPMENT_PINNED_FIXTURE",
            decision_yyyyww="202602",
        )
        snapshot = build_snapshot(changed)
        _validate_publishable_snapshot(snapshot)
        self.assertEqual(snapshot["window_end_exclusive"], "2026-01-05")
        self.assertTrue(snapshot["source_revision"].startswith("DEVELOPMENT-PINNED-WINDOW-"))
        self.assertTrue(all(not i["operational_io_eligible"] for i in snapshot["items"]))
        for invalid in (
            replace(changed, decision_yyyyww="202601"),
            replace(changed, source_relation="dsdm.tb_dyn_demand_dtl"),
            replace(
                changed,
                metrics=tuple(replace(m, fsn_source_status="VERIFIED") for m in source.metrics),
            ),
        ):
            with self.assertRaises(InventoryInputError):
                build_snapshot(invalid)

    def test_independent_52_week_literal_golden(self):
        boh, bo, pipeline, trace = synthetic_warmup(["10"] * 52)
        self.assertEqual((boh, bo, pipeline), (Decimal(10), Decimal(0), [(52, Decimal(20))]))
        self.assertEqual(
            trace[0],
            {
                "week_index": 0,
                "demand_qty": "10",
                "receipt_qty": "0",
                "sales_qty": "0",
                "eoh_qty": "0",
                "backorder_qty": "10",
                "order_qty": "50",
            },
        )
        for row in trace:
            self.assertGreaterEqual(Decimal(row["eoh_qty"]), 0)
        self.assertEqual(synthetic_warmup(["10"] * 52), synthetic_warmup(["10"] * 52))
        quantities = ["1.123456", "200.765432", "0.000001", "19.010203"] * 13
        expected = synthetic_warmup(quantities)
        with localcontext() as ctx:
            ctx.prec = 8
            self.assertEqual(synthetic_warmup(quantities), expected)

    def test_short_history_and_zero_lead_time_refused(self):
        for values, kwargs in (
            (["10"] * 51, {}),
            (["10"] * 52, {"lead_weeks": 0}),
            (["-0.1"] * 52, {}),
            (["NaN"] * 52, {}),
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(InventoryInputError):
                synthetic_warmup(values, **kwargs)

    def test_master_unchanged_and_no_forecast_fabrication(self):
        value = cohort()
        before = copy.deepcopy(value)
        parsed = CanonicalInputRequest.from_dict(value)
        result = (
            PrepareInventoryInputUseCase(DeploymentScope("DSE", "DEVELOPMENT"))
            .execute(parsed)
            .to_dict()
        )
        self.assertEqual([r["item_id"] for r in result["master"]], ["ITEM-A"])
        self.assertEqual(len(parsed.to_dict()["snapshots"]["master"]["rows"]), 2)
        self.assertEqual(value, before)
        self.assertEqual(len(parsed.to_dict()["snapshots"]["forecast"]["rows"]), 3)

    def test_production_cannot_admit_development_cohort(self):
        with self.assertRaisesRegex(
            InventoryInputError, "DEVELOPMENT_UNIVERSE_PRODUCTION_FORBIDDEN"
        ):
            PrepareInventoryInputUseCase(DeploymentScope("DSE", "PRODUCTION")).execute(
                CanonicalInputRequest.from_dict(cohort())
            )

    def test_universe_hash_binding_coverage_and_end_date_refused(self):
        mutations = [
            lambda v: v["development_universe"].update(content_hash="b" * 64),
            lambda v: v["upstream_bindings"]["forecast"].update(snapshot_id="UNRELATED"),
            lambda v: v["upstream_bindings"]["master"].update(content_hash="b" * 64),
            lambda v: v["development_universe"]["exclusions"][0].update(demand_end_yyyyww="202641"),
        ]
        for mutate in mutations:
            value = cohort()
            mutate(value)
            with self.subTest(mutate=mutate), self.assertRaises(InventoryInputError):
                PrepareInventoryInputUseCase(DeploymentScope("DSE", "DEVELOPMENT")).execute(
                    CanonicalInputRequest.from_dict(value)
                )

    def test_hash_replay_and_unknown_contract_key(self):
        value = cohort()
        first = CanonicalInputRequest.from_dict(value).input_hash
        value["context"]["engine_run_id"] = "RETRY"
        self.assertEqual(CanonicalInputRequest.from_dict(value).input_hash, first)
        value["development_universe"]["force_operational"] = True
        with self.assertRaises(InventoryInputError):
            CanonicalInputRequest.from_dict(value)

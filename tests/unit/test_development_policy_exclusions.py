"""Explicit development exclusions preserve the original classification and Master."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import jsonschema
import pytest
from referencing import Registry, Resource

from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.development_universe import (
    seal_development_universe,
    scoped_effective_policies,
)
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.source import SourceInputRequest, SourceSnapshot
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from dsio_inventory_engine.inventory_evidence.classification_source import (
    validate_classification_source,
)
from dsio_inventory_engine.prepare_inventory.application.inventory_input import (
    PrepareInventoryInputUseCase,
)
from dsio_inventory_engine.prepare_inventory.application.source_input import (
    PrepareSourceInputUseCase,
)
from test_development_input import cohort
from fixtures import reseal
from source_fixtures import wrap, seal


def scoped():
    data = cohort()
    universe = {k: v for k, v in data["development_universe"].items() if k != "content_hash"}
    universe.update(
        contract_id="io-development-calculation-universe-v2",
        classification_snapshot_id="classification-original",
        classification_content_hash="b" * 64,
        exclusions=[
            {
                "item_id": "ITEM-B",
                "reason": "EXCLUDED_POLICY_CALCULATION_BLOCKED",
                "policy_gate_reason_codes": ["MISSING_REVENUE"],
            }
        ],
    )
    data["development_universe"] = seal_development_universe(universe)
    data["snapshots"]["forecast"]["snapshot_id"] = (
        data["upstream_bindings"]["forecast"]["snapshot_id"]
        + ".canonical."
        + data["development_universe"]["content_hash"][:16]
    )
    return reseal(data)


def test_v2_preserves_master_and_old_v1_hash():
    old = CanonicalInputRequest.from_dict(cohort())
    # Captured with the prior installed Wheel, not recomputed by this test's
    # changed implementation.
    assert old.input_hash == "d67aecf1e999895f9b9beacc103ee387e82f64de9ffc58e77958c8865eeb705d"
    request = CanonicalInputRequest.from_dict(scoped())
    assert request.to_dict()["snapshots"]["master"] == old.to_dict()["snapshots"]["master"]
    assert old.input_hash == CanonicalInputRequest.from_dict(cohort()).input_hash
    prepared = (
        PrepareInventoryInputUseCase(DeploymentScope("DSE", "DEVELOPMENT"))
        .execute(request)
        .to_dict()
    )
    assert [r["item_id"] for r in prepared["master"]] == ["ITEM-A"]
    root = Path(__file__).resolve().parents[2] / "schemas"
    schema = json.loads((root / "canonical_input_v21.schema.json").read_text())
    registry = Registry().with_resources(
        (p.name, Resource.from_contents(json.loads(p.read_text())))
        for p in root.glob("canonical_input*.schema.json")
    )
    validator = jsonschema.Draft202012Validator(schema, registry=registry)
    validator.validate(request.to_dict())
    bad = request.to_dict()
    del bad["development_universe"]["classification_content_hash"]
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(bad)


@pytest.mark.parametrize("change", ["empty", "duplicate", "v1"])
def test_exclusions_fail_closed(change):
    scope = scoped()["development_universe"]
    scope.pop("content_hash")
    if change == "empty":
        scope["exclusions"][0]["policy_gate_reason_codes"] = []
    elif change == "duplicate":
        scope["exclusions"][0]["policy_gate_reason_codes"] *= 2
    else:
        scope["contract_id"] = "io-development-calculation-universe-v1"
    with pytest.raises(InventoryInputError):
        seal_development_universe(scope)


@pytest.mark.parametrize("change", [None, "eligible", "reason", "extra", "binding"])
def test_calculation_scope_does_not_replace_full_policy_hash(change):
    data = scoped()
    rows = [
        {"item_id": "ITEM-A"},
        {
            "item_id": "ITEM-B",
            "recommendation_calculation_allowed": False,
            "policy_gate_reason_codes": ["MISSING_REVENUE"],
        },
    ]
    binding = {
        "classification_snapshot_id": "classification-original",
        "effective_policy_content_hash": "d" * 64,
    }
    if change == "eligible":
        rows[1]["recommendation_calculation_allowed"] = True
    elif change == "reason":
        rows[1]["policy_gate_reason_codes"] = ["OTHER"]
    elif change == "extra":
        rows.append({"item_id": "EXTRA"})
    elif change == "binding":
        binding["classification_snapshot_id"] = "another"
    before = copy.deepcopy(rows)
    if change:
        with pytest.raises(InventoryInputError):
            scoped_effective_policies(data, rows, binding)
    else:
        assert scoped_effective_policies(data, rows, binding) == [rows[0]]
        assert rows == before and binding["effective_policy_content_hash"] == "d" * 64


@pytest.mark.parametrize("change", [None, "eligible", "reason", "binding"])
def test_full_classification_must_prove_each_exclusion(change):
    canonical = CanonicalInputRequest.from_dict(scoped())
    snapshot = {
        "classification_snapshot_id": "classification-original",
        "content_hash": "b" * 64,
        "tenant_id": "default",
        "project_id": "project",
        "config_id": "config",
        "config_revision_id": "revision",
        "config_hash": "c" * 64,
        "scope": {"company_cd": "DSE", "subs_cd": "C100", "plant_cd": "V100", "site_cd": "V100"},
        "effective_policy_contract_version": "2.0.0",
        "effective_policy_content_hash": "d" * 64,
        "items": [
            {"item_id": "ITEM-A"},
            {
                "item_id": "ITEM-B",
                "recommendation_calculation_allowed": False,
                "policy_gate_reason_codes": ["MISSING_REVENUE"],
            },
        ],
    }
    claim = {
        k: snapshot[k]
        for k in (
            "tenant_id",
            "project_id",
            "config_id",
            "config_revision_id",
            "config_hash",
            "scope",
        )
    }
    request = SimpleNamespace(
        classification_binding={
            "source_snapshot_id": "classification-original",
            "source_content_hash": "d" * 64,
        },
        to_dict=lambda: {"claim": claim},
        strategy_execution_plan={},
    )
    if change == "eligible":
        snapshot["items"][1]["recommendation_calculation_allowed"] = True
    elif change == "reason":
        snapshot["items"][1]["policy_gate_reason_codes"] = ["OTHER_REASON"]
    elif change == "binding":
        snapshot["content_hash"] = "e" * 64
    with (
        patch(
            "dsio_inventory_engine.inventory_evidence.classification_source.normalized_payload",
            return_value=(snapshot, None),
        ),
        patch(
            "dsio_inventory_engine.inventory_evidence.classification_source.effective_policy_content_hash",
            return_value="d" * 64,
        ),
    ):
        if change:
            with pytest.raises(InventoryInputError, match="CLASSIFICATION_EXCLUSION"):
                validate_classification_source(
                    request=request, snapshot=snapshot, canonical=canonical
                )
        else:
            assert (
                validate_classification_source(
                    request=request, snapshot=snapshot, canonical=canonical
                )
                == snapshot
            )


@pytest.mark.asyncio
async def test_source_v21_roundtrip_preserves_canonical_and_mapping():
    data = scoped()
    raw, sources = wrap(copy.deepcopy(data))
    # wrap pins the test Demand run; retain the actual canonical identity instead.
    raw["context"] = data["context"]
    sources["forecast"]["metadata"]["demand_run_id"] = data["context"]["demand_run_id"]
    raw.update(
        contract_id="io-source-input-v2",
        contract_version="2.1.0",
        quantity_rules=data["quantity_rules"],
        development_universe=data["development_universe"],
        upstream_bindings=data["upstream_bindings"],
    )
    raw = seal(raw, sources)

    class Reader:
        async def read_snapshot(self, kind, snapshot_id):
            assert sources[kind]["snapshot_id"] == snapshot_id
            return SourceSnapshot.from_dict(sources[kind])

    result = await PrepareSourceInputUseCase(
        Reader(), DeploymentScope("DSE", "DEVELOPMENT")
    ).execute(SourceInputRequest.from_dict(raw))
    assert result["canonical_input_hash"] == CanonicalInputRequest.from_dict(data).input_hash
    assert len(result["source_mapping_evidence"]) == 8

"""Closed calculation-only execution, atomic partitions and offline recovery."""

import copy
import hashlib
import json
import uuid
from unittest.mock import patch

import pytest

from dsio_inventory_engine.classify_inventory.application import SNAPSHOT_NAMESPACE
from dsio_inventory_engine.infrastructure.runtime_build import installed_runtime_build
from dsio_inventory_engine.infrastructure.sqlite import SqliteInventoryEvidenceUnitOfWork
from dsio_inventory_engine.inventory_contracts.canonical import CanonicalInputRequest
from dsio_inventory_engine.inventory_contracts.development_calculation import (
    seal_command,
    root_reference,
)
from dsio_inventory_engine.inventory_contracts.effective_policy_v2 import EFFECTIVE_POLICY_V2_FIELDS
from dsio_inventory_engine.run_inventory.development_calculation import (
    DevelopmentCalculationExecutor,
    seal_source_bundle,
)
from tests.support.fixtures import reseal
from tests.support.math_fixtures import reseal_math
from tests.support.policy_search_fixtures import multi_item_bundle, bundle
from tests.unit.test_runtime_effective_policy_v2 import DEPLOYMENT


def inputs(*, blocked=False, part_size=1):
    raw, _, _ = multi_item_bundle()
    canonical = raw["recommendation"]["canonical_input"]
    ctx = canonical["context"]
    ctx["demand_run_id"] = "11111111-1111-4111-8111-111111111111"
    ctx["configuration_revision"] = "22222222-2222-4222-8222-222222222222"
    canonical["snapshots"]["forecast"]["metadata"]["demand_run_id"] = ctx["demand_run_id"]
    canonical = CanonicalInputRequest.from_dict(reseal(canonical)).to_dict()
    raw["recommendation"]["canonical_input"] = canonical
    parent_hash = CanonicalInputRequest.from_dict(canonical).input_hash
    raw["recommendation"]["execution"]["canonical_input_hash"] = parent_hash
    raw["recommendation"]["execution"]["configuration_revision"] = ctx["configuration_revision"]
    raw["policy_input"]["context"].update(
        configuration_revision=ctx["configuration_revision"], canonical_input_hash=parent_hash
    )
    raw = reseal_math(raw)
    raw, config, snap = bundle(
        raw, metric_changes={"fsn_source_status": "UNVERIFIED"} if blocked else {}
    )
    snap["as_of_yyyyww"] = ctx["plan_yyyyww"]
    snap["content_hash"] = hashlib.sha256(
        json.dumps(
            {
                k: v
                for k, v in snap.items()
                if k not in {"content_hash", "classification_snapshot_id"}
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    snap["classification_snapshot_id"] = str(uuid.uuid5(SNAPSHOT_NAMESPACE, snap["content_hash"]))
    execution = raw["recommendation"]["execution"]
    execution.update(
        contract_version="2.0.0",
        execution_mode="LOCAL_SHADOW",
        execution_purpose="SHADOW",
        model_approval=None,
        effective_policy_binding={
            "contract_id": "inventory-effective-policy-run-binding-v2",
            "contract_version": "2.0.0",
            "classification_snapshot_id": snap["classification_snapshot_id"],
            "classification_config_hash": snap["config_hash"],
            "effective_policy_content_hash": snap["effective_policy_content_hash"],
        },
        effective_item_policies=[
            {"item_id": r["item_id"], **{k: r[k] for k in EFFECTIVE_POLICY_V2_FIELDS}}
            for r in snap["items"]
        ],
    )
    source = seal_source_bundle(
        mathematical_request=raw, config_values=config, classification_snapshot=snap
    )
    build = installed_runtime_build()["implementations"]
    command = seal_command(
        {
            "contract_id": "inventory-development-calculation",
            "contract_version": "1.0.0",
            "environment": "DEVELOPMENT",
            "execution_purpose": "CALCULATION_ONLY",
            "calculation_id": "33333333-3333-4333-8333-333333333333",
            "attempt_no": 1,
            "tenant_id": snap["tenant_id"],
            "project_id": snap["project_id"],
            "scope": {k: ctx[k] for k in ("company_cd", "subs_cd", "site_cd")},
            "demand_run_id": ctx["demand_run_id"],
            "plan_yyyyww": ctx["plan_yyyyww"],
            "source_binding": {"bundle_id": "SOURCE-ONE", "content_hash": source["content_hash"]},
            "canonical_input_hash": parent_hash,
            "config_revision_id": ctx["configuration_revision"],
            "config_hash": snap["config_hash"],
            "classification_snapshot_id": snap["classification_snapshot_id"],
            "classification_content_hash": snap["content_hash"],
            "effective_policy_content_hash": snap["effective_policy_content_hash"],
            "mathematical_implementation_hash": build["mathematical"][
                "implementation_content_hash"
            ],
            "psi_implementation_hash": build["psi_simulator"]["implementation_content_hash"],
            "client_request_hash": "f" * 64,
            "max_items_per_part": part_size,
            "automatic_publish_allowed": False,
            "automatic_order_allowed": False,
            "engine_run_claimed": False,
        }
    )
    return command, source


@pytest.mark.parametrize("blocked", [False, True])
def test_partitions_real_psi_and_original_gates(tmp_path, blocked):
    command, source = inputs(blocked=blocked)
    before = copy.deepcopy(source)
    repo = SqliteInventoryEvidenceUnitOfWork(tmp_path / "evidence.sqlite3")
    executor = DevelopmentCalculationExecutor(
        deployment=DEPLOYMENT, repository=repo, source_provider=lambda _: source
    )
    result = executor.execute(command)
    assert result["item_count"] == 2 and result["row_count"] == 6
    assert result["blocked_item_count"] == (2 if blocked else 0)
    assert not result["automatic_publish_allowed"] and not result["engine_run_claimed"]
    assert source == before
    orders = []
    for ref in result["parts"]:
        part = repo.read_artifact(ref["reference"]).document()
        assert len(part["baseline_rows"]) == 3
        orders.extend(part["simulated_orders"])
        if blocked:
            assert part["gate_evidence"][0]["reason_codes"]
            assert part["recommended_rows"][0]["recommended_receipt_qty"] == "0"
    assert bool(orders) != blocked
    assert list(repo.list_pending()) == []
    with patch(
        "dsio_inventory_engine.run_inventory.development_calculation._calculate_part",
        side_effect=AssertionError("must not rerun"),
    ):
        executor.source_provider = lambda _: pytest.fail("must not read Source")
        assert executor.execute(command) == result
        assert executor.read(command) == result


def test_interrupted_part_is_atomic_and_reused(tmp_path):
    command, source = inputs(blocked=True)
    repo = SqliteInventoryEvidenceUnitOfWork(tmp_path / "evidence.sqlite3")
    executor = DevelopmentCalculationExecutor(
        deployment=DEPLOYMENT, repository=repo, source_provider=lambda _: source
    )
    original = repo.commit_artifacts
    count = 0

    def interrupted(artifacts):
        nonlocal count
        count += 1
        if count == 3:
            raise RuntimeError("interrupted before second part commit")
        return original(artifacts)

    with (
        patch.object(repo, "commit_artifacts", side_effect=interrupted),
        pytest.raises(RuntimeError),
    ):
        executor.execute(command)
    assert repo.read_artifact(root_reference(command) + ":part:000000")
    from dsio_inventory_engine.run_inventory.development_calculation import _calculate_part

    with patch(
        "dsio_inventory_engine.run_inventory.development_calculation._calculate_part",
        wraps=_calculate_part,
    ) as calc:
        executor.source_provider = lambda _: pytest.fail("saved input required")
        result = executor.execute(command)
        assert calc.call_count == 1
        assert result["item_count"] == 2


@pytest.mark.parametrize("mutation", ["config", "source", "implementation", "scope", "policy"])
def test_tampered_binding_never_creates_artifacts(tmp_path, mutation):
    command, source = inputs()
    command = copy.deepcopy(command)
    if mutation == "scope":
        command["scope"]["site_cd"] = "OTHER"
    elif mutation == "source":
        command["source_binding"]["content_hash"] = "a" * 64
    else:
        command[
            {
                "config": "config_hash",
                "implementation": "mathematical_implementation_hash",
                "policy": "effective_policy_content_hash",
            }[mutation]
        ] = "a" * 64
    command = seal_command(command)
    repo = SqliteInventoryEvidenceUnitOfWork(tmp_path / "evidence.sqlite3")
    with pytest.raises(ValueError):
        DevelopmentCalculationExecutor(
            deployment=DEPLOYMENT, repository=repo, source_provider=lambda _: source
        ).execute(command)


def test_part_size_does_not_change_psi_metrics(tmp_path):
    command, source = inputs()
    first = DevelopmentCalculationExecutor(
        deployment=DEPLOYMENT,
        repository=SqliteInventoryEvidenceUnitOfWork(tmp_path / "a.sqlite3"),
        source_provider=lambda _: source,
    ).execute(command)
    command = seal_command({**command, "max_items_per_part": 2})
    second = DevelopmentCalculationExecutor(
        deployment=DEPLOYMENT,
        repository=SqliteInventoryEvidenceUnitOfWork(tmp_path / "b.sqlite3"),
        source_provider=lambda _: source,
    ).execute(command)
    assert first["metrics"] == second["metrics"]


@pytest.mark.parametrize("mutation", ["flag", "version", "extra", "bool_size", "hash"])
def test_closed_command_python_schema_parity(mutation):
    from pathlib import Path
    from jsonschema import Draft202012Validator
    from dsio_inventory_engine.inventory_contracts.development_calculation import validate_command

    command, _ = inputs()
    schema = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "schemas/inventory-development-calculation-command.schema.json"
        ).read_text()
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(command)
    if mutation == "flag":
        command["automatic_publish_allowed"] = True
    elif mutation == "version":
        command["contract_version"] = "999.0.0"
    elif mutation == "extra":
        command["operational_io_eligible"] = True
    elif mutation == "bool_size":
        command["max_items_per_part"] = True
    else:
        command["content_hash"] = "a" * 64
    with pytest.raises(ValueError):
        validate_command(command)
    if mutation != "hash":
        with pytest.raises(Exception):
            Draft202012Validator(schema).validate(command)


@pytest.mark.parametrize("kind", ["gate", "blocked_psi", "horizon", "fields"])
def test_resealed_partial_part_cannot_bypass_original_source(tmp_path, kind):
    from dsio_inventory_engine.run_inventory.development_calculation import _artifact, PART_KEY

    command, source = inputs(blocked=True)
    repo = SqliteInventoryEvidenceUnitOfWork(tmp_path / "evidence.sqlite3")
    executor = DevelopmentCalculationExecutor(
        deployment=DEPLOYMENT, repository=repo, source_provider=lambda _: source
    )
    result = executor.execute(command)
    part = repo.read_artifact(result["parts"][0]["reference"])
    document = part.document()
    if kind == "gate":
        document["gate_evidence"][0]["disposition"] = "SIMULATION_ONLY"
    elif kind == "blocked_psi":
        document["recommended_rows"][0]["gross_forecast_qty"] = "11"
    elif kind == "horizon":
        document["baseline_rows"] = document["baseline_rows"][:-1]
    else:
        document["publication_override"] = True
    document.pop("content_hash")
    forged = _artifact(part.reference, PART_KEY, document)
    original = repo.read_artifact

    def read(reference):
        if reference == result["artifact_reference"]:
            from dsio_inventory_engine.inventory_contracts.values import InventoryInputError

            raise InventoryInputError("ARTIFACT_NOT_FOUND")
        if reference == part.reference:
            return forged
        return original(reference)

    with patch.object(repo, "read_artifact", side_effect=read), pytest.raises(ValueError):
        executor.execute(command)


def test_manifest_coverage_metrics_and_operational_publication_are_closed(tmp_path):
    from pathlib import Path
    from jsonschema import Draft202012Validator
    from dsio_inventory_engine.inventory_contracts.development_calculation import (
        validate_manifest,
        content_hash,
    )

    command, source = inputs(blocked=True)
    repo = SqliteInventoryEvidenceUnitOfWork(tmp_path / "evidence.sqlite3")
    executor = DevelopmentCalculationExecutor(
        deployment=DEPLOYMENT, repository=repo, source_provider=lambda _: source
    )
    result = executor.execute(command)
    schema = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "schemas/inventory-development-calculation-manifest.schema.json"
        ).read_text()
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(result)
    for field in ("item_count", "row_count", "blocked_item_count"):
        bad = {**result, field: True}
        with pytest.raises(ValueError):
            validate_manifest(bad)
    bad = copy.deepcopy(result)
    bad["metrics"]["baseline"]["projected_service_level"] = "1.000000"
    bad["content_hash"] = content_hash({k: v for k, v in bad.items() if k != "content_hash"})
    with pytest.raises(ValueError):
        validate_manifest(bad)


def test_append_only_conflict_and_source_id_lookup(tmp_path):
    from dsio_inventory_engine.run_inventory.development_calculation import (
        PinnedDevelopmentCalculationSourceProvider,
    )

    command, source = inputs()
    provider = PinnedDevelopmentCalculationSourceProvider([(command, source)])
    assert provider(command) == source
    forged = seal_command(
        {**command, "source_binding": {**command["source_binding"], "bundle_id": "OTHER"}}
    )
    with pytest.raises(ValueError):
        provider(forged)
    repo = SqliteInventoryEvidenceUnitOfWork(tmp_path / "evidence.sqlite3")
    executor = DevelopmentCalculationExecutor(
        deployment=DEPLOYMENT, repository=repo, source_provider=provider
    )
    result = executor.execute(command)
    assert executor.execute(command) == result
    with pytest.raises(ValueError):
        executor.execute(seal_command({**command, "client_request_hash": "e" * 64}))


def test_decimal_caller_does_not_change_hash(tmp_path):
    from decimal import localcontext, ROUND_UP

    command, source = inputs()

    def run(name):
        return DevelopmentCalculationExecutor(
            deployment=DEPLOYMENT,
            repository=SqliteInventoryEvidenceUnitOfWork(tmp_path / name),
            source_provider=lambda _: source,
        ).execute(command)

    first = run("a.sqlite3")
    with localcontext() as ctx:
        ctx.prec = 8
        ctx.rounding = ROUND_UP
        second = run("b.sqlite3")
    assert first == second


def test_opaque_item_numbers_are_not_normalized(tmp_path):
    from pathlib import Path
    from jsonschema import Draft202012Validator
    from dsio_inventory_engine.inventory_contracts.development_calculation import (
        content_hash,
        validate_manifest,
    )

    command, source = inputs(blocked=True)
    result = DevelopmentCalculationExecutor(
        deployment=DEPLOYMENT,
        repository=SqliteInventoryEvidenceUnitOfWork(tmp_path / "evidence.sqlite3"),
        source_provider=lambda _: source,
    ).execute(command)
    result["parts"][1]["item_ids"] = ["SKU@서울/01"]
    result["content_hash"] = content_hash({k: v for k, v in result.items() if k != "content_hash"})
    validate_manifest(result)
    schema = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "schemas/inventory-development-calculation-manifest.schema.json"
        ).read_text()
    )
    Draft202012Validator(schema).validate(result)


def test_completed_recovery_requires_every_retained_part(tmp_path):
    from dsio_inventory_engine.inventory_contracts.values import InventoryInputError

    command, source = inputs(blocked=True)
    repo = SqliteInventoryEvidenceUnitOfWork(tmp_path / "evidence.sqlite3")
    executor = DevelopmentCalculationExecutor(
        deployment=DEPLOYMENT, repository=repo, source_provider=lambda _: source
    )
    result = executor.execute(command)
    original = repo.read_artifact

    def missing(ref):
        if ref == result["parts"][1]["reference"]:
            raise InventoryInputError("ARTIFACT_NOT_FOUND")
        return original(ref)

    executor.source_provider = lambda _: pytest.fail("corruption must not restart from Source")
    with (
        patch.object(repo, "read_artifact", side_effect=missing),
        pytest.raises(InventoryInputError, match="ARTIFACT_NOT_FOUND"),
    ):
        executor.execute(command)


def test_chunk_limit_is_not_relaxed(tmp_path):
    command, source = inputs()
    repo = SqliteInventoryEvidenceUnitOfWork(tmp_path / "evidence.sqlite3")
    executor = DevelopmentCalculationExecutor(
        deployment=DEPLOYMENT, repository=repo, source_provider=lambda _: source
    )
    with (
        patch("dsio_inventory_engine.run_inventory.development_calculation.MAX_PART_BYTES", 16),
        pytest.raises(ValueError, match="PART_SIZE_LIMIT"),
    ):
        executor.execute(command)
    from dsio_inventory_engine.inventory_contracts.values import InventoryInputError

    with pytest.raises(InventoryInputError, match="ARTIFACT_NOT_FOUND"):
        repo.read_artifact(root_reference(command) + ":manifest")

import copy
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from dsio_inventory_engine.infrastructure.target_pipeline_artifacts import (
    LocalTargetPipelineArtifactStore,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from dsio_inventory_engine.recommend_replenishment.application.target_durable import (
    RunDurableTargetPolicyPipelineUseCase,
)
from tests.support.calculation_adapter_fixtures import DEPLOYMENT
from tests.unit.test_target_execution import ReferenceKernel, pipeline_case


def case(tmp_path, mode="ABC_V1", parts=None):
    old, args, runner, kernel, binding, sources, result = pipeline_case(mode, parts)
    repo = LocalTargetPipelineArtifactStore(tmp_path)
    durable = RunDurableTargetPolicyPipelineUseCase(DEPLOYMENT, kernel, repo)
    sources = {**sources, "producer": old.producer}
    run_id = binding.to_dict()["engine_run_id"]
    return durable, repo, args["request"], binding, sources, result, run_id


@pytest.mark.parametrize(
    "mode,count",
    [("ABC_V1", 2), ("ABC_XYZ_SHARED_BUDGET", 32), ("LEGACY_STYLE", 32), ("WIDE_REFERENCE", 88)],
)
def test_every_candidate_and_existing_hash_survive_storage_and_replay(tmp_path, mode, count):
    durable, repo, request, binding, sources, result, run_id = case(tmp_path, mode)
    actual = durable.persist(result, request, binding, **sources)
    assert actual["result"] == result
    assert actual["counts"]["candidate_count"] == count
    before = len(durable.kernel.inputs)
    assert durable.read_integrity(run_id)["verification_status"] == "INTEGRITY_VERIFIED"
    assert len(durable.kernel.inputs) == before  # no stock transition on integrity read
    fresh = RunDurableTargetPolicyPipelineUseCase(
        DEPLOYMENT, ReferenceKernel(), LocalTargetPipelineArtifactStore(tmp_path)
    )
    assert fresh.read_verified(run_id) == actual
    assert fresh.recover(run_id) == actual
    files = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert durable.execute(request, binding, **sources) == actual
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == files
    assert repo.lookup(run_id, "INPUT") == actual["input_reference"]
    assert not actual["automatic_publish_allowed"] and not actual["database_writes"]


@pytest.mark.parametrize("point", ["INPUT", "PART", "MANIFEST", "COMPLETE"])
def test_interruption_at_each_publication_boundary_resumes_from_local_input(tmp_path, point):
    durable, repo, request, binding, sources, result, run_id = case(tmp_path)
    put, bind = repo.put, repo.bind
    hit = []

    def interrupted_put(kind, value):
        ref = put(kind, value)
        if kind == point and point != "INPUT":
            hit.append(kind)
            raise OSError("injected interruption")
        return ref

    def interrupted_bind(run, phase, ref):
        bind(run, phase, ref)
        if phase == point:
            hit.append(phase)
            raise OSError("injected interruption")

    repo.put, repo.bind = interrupted_put, interrupted_bind
    with pytest.raises(OSError, match="interruption"):
        durable.execute(request, binding, **sources)
    assert hit
    fresh = RunDurableTargetPolicyPipelineUseCase(
        DEPLOYMENT, ReferenceKernel(), LocalTargetPipelineArtifactStore(tmp_path)
    )
    if point != "COMPLETE":
        with pytest.raises(InventoryInputError, match="INCOMPLETE"):
            fresh.read_integrity(run_id)
    assert fresh.recover(run_id)["result"] == result


def test_input_is_durable_before_first_numerical_call_and_failed_call_is_not_complete(tmp_path):
    durable, repo, request, binding, sources, result, run_id = case(tmp_path)

    def stop(given):
        assert repo.lookup(run_id, "INPUT") is not None
        assert repo.lookup(run_id, "COMPLETE") is None
        raise RuntimeError("worker failed")

    durable.kernel.advance_target = stop
    with pytest.raises(InventoryInputError, match="TARGET_TRANSITION_FAILED"):
        durable.execute(request, binding, **sources)
    fresh = RunDurableTargetPolicyPipelineUseCase(DEPLOYMENT, ReferenceKernel(), repo)
    assert fresh.recover(run_id)["result"] == result


@pytest.mark.parametrize("mutation", ["candidate", "selection", "order", "permission", "binding"])
def test_resealed_or_changed_external_result_is_rejected_before_publication(tmp_path, mutation):
    durable, repo, request, binding, sources, result, run_id = case(tmp_path)
    value = copy.deepcopy(result)
    if mutation == "candidate":
        value["search_result"]["candidate_evaluations"].pop()
    elif mutation == "selection":
        value["selection_result"]["item_selections"][0]["status"] = "SELECTED"
        value["selection_result"]["selected_item_count"] += 1
    elif mutation == "order":
        value["order_plan"]["orders_content_hash"] = "b" * 64
    elif mutation == "permission":
        value["automatic_order_allowed"] = True
    else:
        value["calculation_binding"]["psi_simulator"]["implementation_content_hash"] = "b" * 64
    with pytest.raises(InventoryInputError, match="TARGET_PIPELINE_REPLAY_MISMATCH"):
        durable.persist(value, request, binding, **sources)
    assert not list(tmp_path.iterdir())
    assert repo.lookup(run_id, "COMPLETE") is None


def test_different_input_under_same_run_is_exclusive_conflict(tmp_path):
    durable, repo, request, binding, sources, result, run_id = case(tmp_path)
    original = durable.persist(result, request, binding, **sources)
    changed = copy.deepcopy(sources)
    changed["config_values"]["development_note"] = "different source"
    with pytest.raises(InventoryInputError, match="DURABLE_ARTIFACT_CONFLICT"):
        durable.execute(request, binding, **changed)
    assert durable.read_verified(run_id) == original


def test_expected_source_reference_and_installed_build_are_pinned_on_recovery(tmp_path):
    durable, repo, request, binding, sources, result, run_id = case(tmp_path, "LEGACY_STYLE")
    actual = durable.persist(result, request, binding, **sources)
    wrong = {**actual["input_reference"], "content_hash": "b" * 64}
    with pytest.raises(InventoryInputError, match="EXPECTED_INPUT_MISMATCH"):
        durable.recover(run_id, expected_input_reference=wrong)
    durable.kernel.dll_sha256 = "b" * 64
    with pytest.raises(InventoryInputError):
        durable.recover(run_id)


@pytest.mark.parametrize("corruption", ["missing", "truncated", "modified", "symlink"])
def test_missing_or_corrupt_part_is_not_healed_or_silently_overwritten(tmp_path, corruption):
    durable, repo, request, binding, sources, result, run_id = case(tmp_path)
    durable.persist(result, request, binding, **sources)
    manifest = repo.read(repo.lookup(run_id, "COMPLETE"))
    ref = next(
        e["reference"]
        for e in manifest["parts"]
        if e["role"] == "search_result.candidate_evaluations"
    )
    path = tmp_path / ("target-object-" + ref["content_hash"] + ".json")
    payload = path.read_bytes()
    if corruption == "missing":
        path.unlink()
    elif corruption == "truncated":
        path.write_bytes(payload[:50])
    elif corruption == "modified":
        path.write_bytes(payload.replace(b"SUCCEEDED", b"CORRUPTED", 1))
    else:
        target = tmp_path / "outside"
        target.write_bytes(payload)
        path.unlink()
        path.symlink_to(target)
    for method in (durable.read_integrity, durable.read_verified, durable.recover):
        with pytest.raises(InventoryInputError):
            method(run_id)


@pytest.mark.parametrize("corruption", ["missing", "duplicate", "reordered", "count", "extra"])
def test_manifest_coverage_and_counts_are_checked_without_numerical_calls(tmp_path, corruption):
    durable, repo, request, binding, sources, result, run_id = case(tmp_path)
    durable.persist(result, request, binding, **sources)
    manifest = repo.read(repo.lookup(run_id, "COMPLETE"))
    if corruption == "missing":
        manifest["parts"].pop()
    elif corruption == "duplicate":
        manifest["parts"].append(manifest["parts"][-1])
    elif corruption == "reordered":
        manifest["parts"].reverse()
    elif corruption == "count":
        manifest["counts"]["candidate_count"] += 1
    else:
        manifest["parts"][0]["extra"] = True
    fake = repo.put("MANIFEST", manifest)
    # Deliberate pointer attack, bypassing exclusive publisher. This is not normal recovery.
    slot = repo.put("SLOT", {"engine_run_id": run_id, "phase": "COMPLETE", "reference": fake})
    from dsio_inventory_engine.inventory_contracts.values import canonical_json

    (tmp_path / repo._target_slot(run_id, "COMPLETE")).write_text(canonical_json(slot) + "\n")
    before = len(durable.kernel.inputs)
    with pytest.raises(InventoryInputError):
        durable.read_integrity(run_id)
    assert len(durable.kernel.inputs) == before


def test_concurrent_identical_publication_and_symlink_root(tmp_path):
    repo = LocalTargetPipelineArtifactStore(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        refs = list(pool.map(lambda _: repo.put("PART", {"same": True}), range(8)))
    assert all(r == refs[0] for r in refs)
    assert repo.read(refs[0]) == {"same": True}
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(InventoryInputError, match="ROOT_INVALID"):
        LocalTargetPipelineArtifactStore(alias)
    assert not any(p.name.startswith(".robustness-") for p in tmp_path.iterdir())


def test_private_root_replacement_is_rejected(tmp_path):
    root = tmp_path / "private"
    root.mkdir()
    repo = LocalTargetPipelineArtifactStore(root)
    os.rename(root, tmp_path / "old")
    root.mkdir()
    with pytest.raises(InventoryInputError, match="ROOT_CHANGED"):
        repo.put("PART", {})


def test_reference_cannot_admit_wrong_kind_size_or_noncanonical_bytes(tmp_path):
    repo = LocalTargetPipelineArtifactStore(tmp_path)
    ref = repo.put("PART", {"same": True})
    for wrong in (
        {**ref, "kind": "INPUT"},
        {**ref, "byte_count": ref["byte_count"] + 1},
        {**ref, "content_hash": "../escape"},
        {**ref, "byte_count": True},
    ):
        with pytest.raises(InventoryInputError):
            repo.read(wrong)
    path = tmp_path / ("target-object-" + ref["content_hash"] + ".json")
    path.write_bytes(path.read_bytes().replace(b'"value":', b'"value": ', 1))
    with pytest.raises(InventoryInputError):
        repo.read({**ref, "byte_count": ref["byte_count"] + 1})


def test_integrity_is_not_a_claim_of_numerical_replay(tmp_path):
    from dsio_inventory_engine.inventory_contracts.values import canonical_json

    durable, repo, request, binding, sources, result, run_id = case(tmp_path)
    durable.persist(result, request, binding, **sources)
    manifest = repo.read(repo.lookup(run_id, "COMPLETE"))
    entry = manifest["parts"][0]
    assert entry["role"] == "evidence"
    part = repo.read(entry["reference"])
    part["value"]["completed_transition_calls"]["POLICY_SELECTION"] += 1
    entry["reference"] = repo.put("PART", part)
    ref = repo.put("MANIFEST", manifest)
    slot = repo.put("SLOT", {"engine_run_id": run_id, "phase": "COMPLETE", "reference": ref})
    # Re-checksummed metadata attack; not the public append-only write path.
    (tmp_path / repo._target_slot(run_id, "COMPLETE")).write_text(canonical_json(slot) + "\n")
    assert durable.read_integrity(run_id)["verification_status"] == "INTEGRITY_VERIFIED"
    for method in (durable.read_verified, durable.recover):
        with pytest.raises(InventoryInputError, match="TARGET_PIPELINE_REPLAY_MISMATCH"):
            method(run_id)


def test_split_storage_does_not_bypass_total_raw_budget(tmp_path, monkeypatch):
    from dsio_inventory_engine.recommend_replenishment.application import target_durable

    durable, repo, request, binding, sources, _, run_id = case(tmp_path)
    monkeypatch.setattr(target_durable, "MAX_RESULT_BYTES", 1)
    with pytest.raises(InventoryInputError, match="TARGET_STORAGE_RESULT_SIZE_LIMIT"):
        durable.execute(request, binding, **sources)
    assert repo.lookup(run_id, "INPUT") is not None
    assert repo.lookup(run_id, "COMPLETE") is None
    assert not [p for p in tmp_path.iterdir() if b'"kind":"PART"' in p.read_bytes()]


def test_aggregate_may_exceed_individual_cap_without_changing_results(tmp_path, monkeypatch):
    from dsio_inventory_engine.inventory_contracts import target_result_parts
    from dsio_inventory_engine.inventory_contracts.values import canonical_json
    from dsio_inventory_engine.recommend_replenishment.application.target_durable import _parts

    durable, repo, request, binding, sources, result, run_id = case(tmp_path, "LEGACY_STYLE")
    cap = (
        max(
            len(canonical_json({"role": role, "index": index, "value": value}).encode())
            for role, value, index in _parts(result)
        )
        + 1
    )
    assert len(canonical_json(result).encode()) > cap
    monkeypatch.setattr(target_result_parts, "MAX_ARTIFACT_BYTES", cap)
    stored = durable.persist(result, request, binding, **sources)
    assert stored["result"] == result
    assert durable.read_verified(run_id) == stored
    assert durable.recover(run_id) == stored
    assert stored["counts"]["candidate_count"] == 32


def test_individual_part_limit_still_blocks_before_publication(tmp_path, monkeypatch):
    from dsio_inventory_engine.infrastructure import target_pipeline_artifacts

    durable, repo, request, binding, sources, result, run_id = case(tmp_path)
    input_ref = durable.seal_input(request, binding, **sources)
    inputs = repo.read(input_ref)
    monkeypatch.setattr(target_pipeline_artifacts, "MAX_BYTES", 1)
    with pytest.raises(InventoryInputError, match="TARGET_STORAGE_SIZE_LIMIT"):
        durable._publish(inputs, input_ref, result)
    assert repo.lookup(run_id, "COMPLETE") is None


def test_total_budget_checked_before_part_reads(tmp_path, monkeypatch):
    from dsio_inventory_engine.recommend_replenishment.application import target_durable

    durable, repo, request, binding, sources, result, run_id = case(tmp_path)
    durable.persist(result, request, binding, **sources)
    manifest = repo.read(repo.lookup(run_id, "COMPLETE"))
    monkeypatch.setattr(target_durable, "MAX_RESULT_BYTES", 1)
    monkeypatch.setattr(repo, "read", lambda ref: pytest.fail("must reject before reading parts"))
    with pytest.raises(InventoryInputError, match="TARGET_STORAGE_RESULT_SIZE_LIMIT"):
        durable._read_manifest(manifest)


def test_replay_part_and_total_limits_are_independent(tmp_path, monkeypatch):
    from dsio_inventory_engine.inventory_contracts import target_result_parts

    *_, result, _ = case(tmp_path)
    monkeypatch.setattr(target_result_parts, "MAX_ARTIFACT_BYTES", 1)
    with pytest.raises(InventoryInputError, match="TARGET_STORAGE_SIZE_LIMIT"):
        target_result_parts.verify_partitioned_result(result, result)
    monkeypatch.setattr(target_result_parts, "MAX_ARTIFACT_BYTES", 128_000_000)
    monkeypatch.setattr(target_result_parts, "MAX_RAW_RESULT_BYTES", 1)
    with pytest.raises(InventoryInputError, match="TARGET_STORAGE_RESULT_SIZE_LIMIT"):
        target_result_parts.verify_partitioned_result(result, result)

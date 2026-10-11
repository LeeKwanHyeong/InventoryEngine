"""Exact-ID, append-only publication after Source/Cut-off admission."""

import json

import pytest

from dsio_inventory_engine.infrastructure.canonical_snapshot_publisher import (
    KINDS,
    LocalCanonicalSnapshotPublisher,
    SourceToCanonicalSnapshotProducer,
)
from dsio_inventory_engine.entrypoints.source_input import seal_site_sources
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.source import SourceInputRequest
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError
from support.source_fixtures import MemoryReader, wrap


DEV = DeploymentScope("DSE", "DEVELOPMENT")


@pytest.mark.asyncio
async def test_source_producer_seals_five_inventory_snapshots_idempotently(tmp_path):
    request, sources = wrap()
    producer = SourceToCanonicalSnapshotProducer(
        reader=MemoryReader(sources),
        publisher=LocalCanonicalSnapshotPublisher(tmp_path),
        deployment=DEV,
    )
    first = await producer.produce(SourceInputRequest.from_dict(request))
    second = await producer.produce(SourceInputRequest.from_dict(request))

    assert first == second
    assert set(first["snapshot_bindings"]) == set(KINDS)
    assert first["database_writes"] is False
    assert len(list(tmp_path.glob("*.json"))) == 5
    for kind, reference in first["snapshot_bindings"].items():
        document = json.loads((tmp_path / f"{reference['snapshot_id']}.json").read_text())
        assert document["content_hash"] == reference["content_hash"]
        assert document["status"] == "SEALED"
        assert document["row_count"] == len(document["rows"])


@pytest.mark.asyncio
async def test_source_producer_rejects_unsealed_and_missing_coverage_before_any_file(tmp_path):
    request, sources = wrap()
    sources["inventory"]["status"] = "COLLECTING"
    producer = SourceToCanonicalSnapshotProducer(
        reader=MemoryReader(sources),
        publisher=LocalCanonicalSnapshotPublisher(tmp_path),
        deployment=DEV,
    )
    with pytest.raises(InventoryInputError):
        await producer.produce(SourceInputRequest.from_dict(request))
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_source_producer_rejects_changed_pinned_source_hash(tmp_path):
    request, sources = wrap()
    sources["policies"]["rows"][0]["moq"] = "1000"
    producer = SourceToCanonicalSnapshotProducer(
        reader=MemoryReader(sources),
        publisher=LocalCanonicalSnapshotPublisher(tmp_path),
        deployment=DEV,
    )
    with pytest.raises(InventoryInputError):
        await producer.produce(SourceInputRequest.from_dict(request))
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_local_publisher_cannot_seal_in_production(tmp_path):
    request, sources = wrap()
    producer = SourceToCanonicalSnapshotProducer(
        reader=MemoryReader(sources),
        publisher=LocalCanonicalSnapshotPublisher(tmp_path),
        deployment=DeploymentScope("DSE", "PRODUCTION"),
    )
    with pytest.raises(InventoryInputError):
        await producer.produce(SourceInputRequest.from_dict(request))
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_source_producer_never_overwrites_conflicting_snapshot_id(tmp_path):
    request, sources = wrap()
    producer = SourceToCanonicalSnapshotProducer(
        reader=MemoryReader(sources),
        publisher=LocalCanonicalSnapshotPublisher(tmp_path),
        deployment=DEV,
    )
    first = await producer.produce(SourceInputRequest.from_dict(request))
    target = tmp_path / f"{first['snapshot_bindings']['inventory']['snapshot_id']}.json"
    target.write_text("tampered", encoding="utf-8")
    with pytest.raises(InventoryInputError, match="SNAPSHOT_ID_CONFLICT"):
        await producer.produce(SourceInputRequest.from_dict(request))
    assert target.read_text(encoding="utf-8") == "tampered"


@pytest.mark.asyncio
async def test_seal_entrypoint_reads_exact_source_files_and_writes_separate_catalog(tmp_path):
    request, sources = wrap()
    source_root = tmp_path / "source"
    canonical_root = tmp_path / "canonical"
    source_root.mkdir()
    canonical_root.mkdir()
    for source in sources.values():
        (source_root / f"{source['snapshot_id']}.json").write_text(
            json.dumps(source), encoding="utf-8"
        )

    receipt = await seal_site_sources(
        SourceInputRequest.from_dict(request), DEV, source_root, canonical_root
    )

    assert receipt["status"] == "CANONICAL_SITE_SNAPSHOTS_SEALED"
    assert receipt["forecast_database_corroborated"] is False
    assert len(list(canonical_root.glob("*.json"))) == 5
    assert len(list(source_root.glob("*.json"))) == 8

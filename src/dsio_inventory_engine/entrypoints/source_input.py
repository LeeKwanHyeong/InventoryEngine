"""Offline file admission, with an explicit opt-in for Forecast DB corroboration."""

from pathlib import Path

from dsio_inventory_engine.infrastructure.source_files import (
    FileSourceSnapshotReader,
)
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.source import SourceInputRequest, SourceSnapshot
from dsio_inventory_engine.inventory_contracts.values import require
from dsio_inventory_engine.prepare_inventory.application.source_input import (
    PrepareSourceInputUseCase,
)


async def prepare_source(
    request: SourceInputRequest,
    deployment: DeploymentScope,
    root: Path,
    *,
    read_postgres: bool = False,
    dsn: str = "",
) -> dict:
    require(
        request.to_dict()["context"]["company_cd"] == deployment.company_cd,
        "DEPLOYMENT_COMPANY_MISMATCH",
    )
    files = FileSourceSnapshotReader(root)
    if read_postgres:
        require(bool(dsn.strip()), "IO_POSTGRES_DSN_REQUIRED")
    # Admit every pinned input, including Cut-off reconciliation, before network I/O.
    prepared = await PrepareSourceInputUseCase(files, deployment).execute(request)
    if not read_postgres:
        return prepared
    forecast = SourceSnapshot.from_dict(prepared["source_snapshots"]["forecast"])
    require(
        forecast.to_dict()["adapter"] == "DSDM_FORECAST_V1", "POSTGRES_FORECAST_ADAPTER_REQUIRED"
    )
    import asyncpg
    from dsio_inventory_engine.infrastructure.postgresql.source_reader import (
        PostgresInventorySourceReader,
    )

    connection = await asyncpg.connect(
        dsn,
        timeout=10,
        command_timeout=15,
        server_settings={
            "default_transaction_read_only": "on",
            "application_name": "io_source_readonly",
        },
    )
    try:
        # Corroborate the detached, admitted snapshot; never reread files or replace its seal.
        await PostgresInventorySourceReader(connection).verify_snapshot(forecast)
        return prepared
    finally:
        await connection.close()

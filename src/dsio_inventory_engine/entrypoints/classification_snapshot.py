"""Run one Inventory classification lifecycle; no write unless --apply is present."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any

from dsio_inventory_engine.classify_inventory.application import (
    InventoryClassificationLifecycleUseCase,
    InventoryScope,
)
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, require
from dsio_inventory_engine.run_inventory.classification_stage import (
    InventoryClassificationStageUseCase,
    InventoryRunContext,
)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    try:
        import asyncpg
    except ImportError as exc:
        raise InventoryInputError("CLASSIFICATION_ASYNCPG_REQUIRED") from exc

    connection = await asyncpg.connect(args.dsn, timeout=10, command_timeout=120)
    try:
        from dsio_inventory_engine.infrastructure.postgresql.classification_snapshot import (
            PostgresClassificationPublisher,
            PostgresClassificationSource,
        )

        lifecycle = InventoryClassificationLifecycleUseCase(
            PostgresClassificationSource(connection),
            PostgresClassificationPublisher(connection),
        )
        scope = InventoryScope(
            project_id=args.project_id,
            company_cd=args.company_cd,
            subs_cd=args.subs_cd,
            plant_cd=args.plant_cd,
            site_cd=args.site_cd,
        )
        if args.engine_run_id:
            from dsio_inventory_engine.infrastructure.postgresql.run_events import (
                PostgresInventoryRunEventRecorder,
            )

            return await InventoryClassificationStageUseCase(
                lifecycle,
                PostgresInventoryRunEventRecorder(connection),
            ).execute(
                InventoryRunContext(
                    engine_run_id=args.engine_run_id,
                    tenant_id=args.tenant_id,
                    project_id=args.project_id,
                    scope=scope,
                ),
                approved_by=args.approved_by,
            )
        return await lifecycle.execute(
            scope,
            approved_by=args.approved_by,
            publish=args.apply,
        )
    finally:
        await connection.close()


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--dsn", default=os.getenv("IO_POSTGRES_DSN"))
    value.add_argument("--tenant-id", default=os.getenv("IO_TENANT_ID", "default"))
    value.add_argument("--engine-run-id")
    value.add_argument("--project-id", required=True)
    value.add_argument("--company-cd", required=True)
    value.add_argument("--subs-cd", required=True)
    value.add_argument("--plant-cd", required=True)
    value.add_argument("--site-cd", required=True)
    value.add_argument("--approved-by", default=os.getenv("IO_APPROVED_BY", "admin"))
    value.add_argument("--apply", action="store_true")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        validate_args(args)
        receipt = asyncio.run(run(args))
    except InventoryInputError as exc:
        print(
            json.dumps(
                {
                    "status": "REJECTED",
                    "error_code": str(exc),
                    "evidence": json.loads(exc.evidence_json),
                },
                ensure_ascii=False,
            )
        )
        return 2
    except Exception:
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "error_code": "CLASSIFICATION_LIFECYCLE_FAILED",
                }
            )
        )
        return 1
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


def validate_args(args: argparse.Namespace) -> None:
    require(bool(args.dsn and args.dsn.strip()), "IO_POSTGRES_DSN_REQUIRED")
    if args.engine_run_id:
        require(args.apply, "INVENTORY_RUN_CLASSIFICATION_APPLY_REQUIRED")


if __name__ == "__main__":
    raise SystemExit(main())

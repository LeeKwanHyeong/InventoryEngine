"""Optional FastAPI ingress for Platform-claimed Inventory executions."""

from __future__ import annotations

import hmac

from fastapi import FastAPI, HTTPException, Request, status

from dsio_inventory_engine.inventory_contracts.runtime import CONTRACT_ID
from dsio_inventory_engine.inventory_contracts.values import InventoryInputError, require
from dsio_inventory_engine.run_inventory.runtime_execution import (
    InventoryRuntimeExecutionUseCase,
    InventoryRuntimeSubmissionConflict,
    InventoryRuntimeSubmissionPort,
    InventoryRuntimeSubmissionUnavailable,
)


def create_inventory_runtime_app(
    submission: InventoryRuntimeSubmissionPort,
    *,
    bearer_token: str | None = None,
    environment: str = "development",
) -> FastAPI:
    if environment.strip().lower() in {"production", "prod"}:
        require(bool(bearer_token), "RUNTIME_API_AUTH_REQUIRED")
    use_case = InventoryRuntimeExecutionUseCase(submission)
    app = FastAPI(title="InventoryEngine Runtime", version="1.0.0")

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {"status": "ready", "capabilities": [CONTRACT_ID]}

    @app.post("/api/v1/executions", status_code=status.HTTP_202_ACCEPTED)
    async def execute(request: Request) -> dict[str, object]:
        _authenticate(request, bearer_token)
        try:
            body = await request.body()
            document = body.decode("utf-8", errors="strict")
            return (await use_case.accept(document)).to_dict()
        except InventoryRuntimeSubmissionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except InventoryRuntimeSubmissionUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        except (InventoryInputError, UnicodeDecodeError):
            raise HTTPException(
                status_code=422,
                detail="INVENTORY_RUNTIME_REQUEST_INVALID",
            ) from None

    return app


def _authenticate(request: Request, bearer_token: str | None) -> None:
    if bearer_token is None:
        return
    authorization = request.headers.get("authorization") or ""
    expected = f"Bearer {bearer_token}"
    if not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="INVENTORY_RUNTIME_UNAUTHORIZED")


__all__ = ["create_inventory_runtime_app"]

"""Authenticated, development-only ingress separate from operational Run submission."""

import asyncio
import hmac

from fastapi import FastAPI, Header, HTTPException

from dsio_inventory_engine.inventory_contracts.development_calculation import (
    check,
    validate_command,
)


def create_development_calculation_app(*, executor, bearer_token, environment):
    check(
        environment == "development" and executor.deployment.environment == "DEVELOPMENT",
        "CALCULATION_DEVELOPMENT_ONLY",
    )
    check(
        type(bearer_token) is str and len(bearer_token) >= 16, "CALCULATION_RUNTIME_AUTH_REQUIRED"
    )
    app = FastAPI(title="Inventory development calculation only")

    async def call(command, authorization, verify):
        if not isinstance(authorization, str) or not hmac.compare_digest(
            authorization.encode("utf-8"), ("Bearer " + bearer_token).encode("utf-8")
        ):
            raise HTTPException(status_code=401, detail="inventory_development_auth_required")
        try:
            command = validate_command(command)
            return await asyncio.to_thread(executor.read if verify else executor.execute, command)
        except ValueError:
            raise HTTPException(
                status_code=422, detail="inventory_development_calculation_invalid"
            ) from None
        except Exception:
            raise HTTPException(
                status_code=503, detail="inventory_development_calculation_unavailable"
            ) from None

    @app.post("/api/v1/development-calculations/execute")
    async def execute(command: dict, authorization: str | None = Header(default=None)):
        return await call(command, authorization, False)

    @app.post("/api/v1/development-calculations/verify")
    async def verify(command: dict, authorization: str | None = Header(default=None)):
        return await call(command, authorization, True)

    return app

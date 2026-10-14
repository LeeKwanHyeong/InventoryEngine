"""One-shot development Runtime for an exact Platform-claimed V100 execution.

No latest-source lookup, fixture rewriting, local approval, or fake callback.
The worker reads approved inputs and returns events/publication through Platform.
No HTTP queue is implied: this bounded command waits for the one execution.
"""

import argparse
import asyncio
import os
from pathlib import Path
from urllib.parse import urlparse

from dsio_inventory_engine.infrastructure.http.platform_lifecycle import (
    PlatformInventoryLifecycleHttpClient,
    PlatformLifecycleHttpSettings,
)
from dsio_inventory_engine.infrastructure.http.runtime_inputs import PlatformRuntimeInputHttpClient
from dsio_inventory_engine.infrastructure.runtime_build import (
    installed_runtime_build,
    registered_installed_implementations,
)
from dsio_inventory_engine.infrastructure.sqlite import SqliteInventoryEvidenceUnitOfWork
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.inventory_contracts.runtime import InventoryRuntimeExecutionRequest
from dsio_inventory_engine.inventory_contracts.values import (
    InventoryInputError,
    canonical_json,
    read_json,
    require,
)
from dsio_inventory_engine.run_inventory.runtime_composition import (
    build_approved_inventory_runtime_worker,
)


def validate_v100_request(request):
    require(
        request.tenant_id == "default" and request.project_id == "9ed62f41",
        "LOCAL_RUNTIME_PROJECT_MISMATCH",
    )
    scope = request.value["claim"]["scope"]
    require(
        scope == {"company_cd": "DSE", "subs_cd": "C100", "plant_cd": "V100", "site_cd": "V100"},
        "LOCAL_RUNTIME_SITE_MISMATCH",
    )
    plan = request.strategy_execution_plan
    require(
        plan is not None and plan["contract_version"] == "1.4.0", "LOCAL_RUNTIME_PLAN_14_REQUIRED"
    )
    require(
        plan["operational_strategy"]["strategy_type"] == "MATHEMATICAL"
        and not plan["shadow_challenger_bindings"]
        and not plan["stress_scenario_bindings"]
        and plan["cost_profile_binding"] is None
        and plan.get("landed_cost_binding") is None,
        "LOCAL_RUNTIME_MATHEMATICAL_ONLY",
    )


def read_document(path: Path, max_bytes=8_000_000):
    require(
        path.is_absolute() and path.is_file() and not path.is_symlink(),
        "LOCAL_RUNTIME_DOCUMENT_INVALID",
    )
    require(path.stat().st_size <= max_bytes, "LOCAL_RUNTIME_DOCUMENT_LIMIT")
    return read_json(path.read_text(encoding="utf-8"), max_bytes=max_bytes)


async def execute_local(*, request, expected_build, evidence_path, settings):
    validate_v100_request(request)
    implementations = registered_installed_implementations(expected_build)
    settings.validate(environment="development")
    parsed = urlparse(settings.base_url)
    require(
        parsed.hostname in {"127.0.0.1", "localhost", "::1"},
        "LOCAL_RUNTIME_PLATFORM_LOOPBACK_REQUIRED",
    )
    require(bool(settings.bearer_token), "LOCAL_RUNTIME_PLATFORM_AUTH_REQUIRED")
    require(
        evidence_path.is_absolute()
        and evidence_path.parent.is_dir()
        and not evidence_path.is_symlink(),
        "LOCAL_RUNTIME_EVIDENCE_PATH_INVALID",
    )
    worker = build_approved_inventory_runtime_worker(
        deployment=DeploymentScope("DSE", "DEVELOPMENT"),
        input_provider=PlatformRuntimeInputHttpClient(settings),
        implementations=implementations,
        evidence_repository=SqliteInventoryEvidenceUnitOfWork(evidence_path),
        platform=PlatformInventoryLifecycleHttpClient(settings),
    )
    return await worker.execute(request)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect-build", action="store_true")
    parser.add_argument("--request", type=Path)
    parser.add_argument("--installed-build", type=Path)
    parser.add_argument("--evidence-path", type=Path)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Allow the approved development execution/callback writes.",
    )
    args = parser.parse_args(argv)
    try:
        if args.inspect_build:
            require(not args.execute and args.request is None, "LOCAL_RUNTIME_ARGUMENT_CONFLICT")
            print(canonical_json(installed_runtime_build()))
            return 0
        require(
            args.request is not None
            and args.installed_build is not None
            and args.evidence_path is not None,
            "LOCAL_RUNTIME_ARGUMENTS_REQUIRED",
        )
        request = InventoryRuntimeExecutionRequest.from_dict(read_document(args.request))
        validate_v100_request(request)
        registered_installed_implementations(read_document(args.installed_build))
        if not args.execute:
            print(
                canonical_json(
                    {
                        "status": "request_validated",
                        "database_writes": False,
                        "engine_run_id": request.engine_run_id,
                    }
                )
            )
            return 0
        result = asyncio.run(
            execute_local(
                request=request,
                expected_build=read_document(args.installed_build),
                evidence_path=args.evidence_path,
                settings=PlatformLifecycleHttpSettings.from_env(os.environ),
            )
        )
        print(canonical_json(result))
        return 0
    except InventoryInputError as error:
        print(canonical_json({"status": "REJECTED", "error_code": str(error)}))
        return 2
    except Exception as error:
        print(canonical_json({"status": "FAILED", "error_type": type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

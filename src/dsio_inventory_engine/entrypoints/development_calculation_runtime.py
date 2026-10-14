"""Local calculation-only Runtime factory; no Claim, callback or publication."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from dsio_inventory_engine.infrastructure.runtime_build import installed_runtime_build
from dsio_inventory_engine.infrastructure.sqlite import SqliteInventoryEvidenceUnitOfWork
from dsio_inventory_engine.inventory_contracts.development_calculation import (
    check,
    content_hash,
    validate_command,
)
from dsio_inventory_engine.inventory_contracts.network import DeploymentScope
from dsio_inventory_engine.run_inventory.development_api import create_development_calculation_app
from dsio_inventory_engine.run_inventory.development_calculation import (
    DevelopmentCalculationExecutor,
    PinnedDevelopmentCalculationSourceProvider,
    seal_source_bundle,
)


def load_runtime_spec(path):
    path = Path(path)
    check(
        path.is_absolute()
        and path.is_file()
        and not path.is_symlink()
        and path.stat().st_size <= 1_000_000,
        "LOCAL_CALCULATION_SPEC_INVALID",
    )
    spec = json.loads(path.read_text())
    check(
        set(spec)
        == {
            "contract_id",
            "contract_version",
            "environment",
            "backend_url",
            "runtime_url",
            "evidence_path",
            "command",
            "runtime_build",
            "source_files",
            "content_hash",
        },
        "LOCAL_CALCULATION_SPEC_FIELDS_INVALID",
    )
    check(
        spec["contract_id"] == "inventory-local-calculation-deployment"
        and spec["contract_version"] == "1.0.0"
        and spec["environment"] == "DEVELOPMENT"
        and content_hash({k: v for k, v in spec.items() if k != "content_hash"})
        == spec["content_hash"],
        "LOCAL_CALCULATION_SPEC_HASH_MISMATCH",
    )
    validate_command(spec["command"])
    from urllib.parse import urlsplit

    for name in ("backend_url", "runtime_url"):
        parsed = urlsplit(spec[name])
        check(
            parsed.scheme == "http"
            and parsed.hostname == "127.0.0.1"
            and parsed.port is not None
            and 1024 <= parsed.port <= 65535
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and parsed.path in ("", "/"),
            "LOCAL_CALCULATION_LOOPBACK_REQUIRED",
        )
    evidence = Path(spec["evidence_path"])
    check(
        evidence.is_absolute()
        and evidence.parent.resolve() == path.parent.resolve()
        and not evidence.is_symlink(),
        "LOCAL_CALCULATION_EVIDENCE_PATH_INVALID",
    )
    check(installed_runtime_build() == spec["runtime_build"], "LOCAL_CALCULATION_BUILD_MISMATCH")
    build = spec["runtime_build"]["implementations"]
    check(
        spec["command"]["mathematical_implementation_hash"]
        == build["mathematical"]["implementation_content_hash"]
        and spec["command"]["psi_implementation_hash"]
        == build["psi_simulator"]["implementation_content_hash"],
        "LOCAL_CALCULATION_BUILD_BINDING_MISMATCH",
    )
    return spec


def load_pinned_source(spec):
    files = spec["source_files"]
    check(
        set(files) == {"mathematical_request", "config_values", "classification_snapshot"},
        "LOCAL_CALCULATION_SOURCE_FILES_INVALID",
    )
    values = {}
    for kind, record in files.items():
        path = Path(record["path"])
        check(
            path.is_absolute()
            and path.is_file()
            and not path.is_symlink()
            and path.stat().st_size <= 80_000_000,
            "LOCAL_CALCULATION_SOURCE_FILE_INVALID",
        )
        payload = path.read_bytes()
        check(
            hashlib.sha256(payload).hexdigest() == record["sha256"],
            "LOCAL_CALCULATION_SOURCE_FILE_HASH_MISMATCH",
        )
        value = json.loads(payload)
        if kind == "config_values":
            value = value["config_values"]
            if isinstance(value, str):
                value = json.loads(value)
        values[kind] = value
    bundle = seal_source_bundle(**values)
    check(
        bundle["content_hash"] == spec["command"]["source_binding"]["content_hash"],
        "LOCAL_CALCULATION_SOURCE_BINDING_MISMATCH",
    )
    return bundle


def create_runtime_app():
    check(
        os.environ.get("DSAI_ENV") == "development"
        and os.environ.get("DSAI_PRODUCTION", "false").lower() not in {"1", "true", "yes"},
        "LOCAL_CALCULATION_DEVELOPMENT_ONLY",
    )
    spec = load_runtime_spec(os.environ["DSAI_INVENTORY_LOCAL_SPEC"])
    recovery = os.environ.get("DSAI_INVENTORY_LOCAL_RECOVERY_ONLY") == "1"
    provider = (
        None
        if recovery
        else PinnedDevelopmentCalculationSourceProvider(
            [(spec["command"], load_pinned_source(spec))]
        )
    )
    repository = SqliteInventoryEvidenceUnitOfWork(Path(spec["evidence_path"]))
    # Opt-in fault injection is local-only and stops AFTER a committed partition.
    after = int(os.environ.get("DSAI_INVENTORY_LOCAL_INTERRUPT_AFTER_PARTS", "0"))
    check(after >= 0 and not (recovery and after), "LOCAL_CALCULATION_FAULT_SETTING_INVALID")
    if after:
        original = repository.commit_artifacts
        count = 0

        def commit(artifacts):
            nonlocal count
            result = original(artifacts)
            count += sum(
                a.contract_key == "inventory.development_calculation_part" for a in artifacts
            )
            if count >= after:
                print(
                    json.dumps(
                        {"event": "local_calculation_interrupted", "committed_parts": count}
                    ),
                    flush=True,
                )
                os._exit(75)
            return result

        repository.commit_artifacts = commit
    executor = DevelopmentCalculationExecutor(
        deployment=DeploymentScope(spec["command"]["scope"]["company_cd"], "DEVELOPMENT"),
        repository=repository,
        source_provider=provider,
    )
    app = create_development_calculation_app(
        executor=executor,
        bearer_token=os.environ.get("DSAI_INVENTORY_LOCAL_RUNTIME_TOKEN"),
        environment="development",
    )

    @app.get("/health")
    async def health():
        return {
            "status": "ready",
            "role": "CALCULATION_ONLY",
            "source_provider_enabled": not recovery,
            "automatic_publish_allowed": False,
            "automatic_order_allowed": False,
            "deployment_content_hash": spec["content_hash"],
            "runtime_build_content_hash": spec["runtime_build"]["content_hash"],
        }

    return app

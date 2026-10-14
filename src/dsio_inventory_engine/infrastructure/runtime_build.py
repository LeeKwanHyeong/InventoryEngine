"""Identify the installed Python implementation, independently of Run requests.

Recipe v1 hashes the complete package source closure plus algorithm identity.
It is deliberately conservative: any package code change requires new bindings.
Wheel SHA-256 and dependency versions are separate build evidence, not input hashes.
"""

import hashlib
from pathlib import Path

import dsio_inventory_engine
from dsio_inventory_engine.inventory_contracts.values import digest, require
from dsio_inventory_engine.run_inventory.approved_inputs import RegisteredRuntimeImplementations


def package_source_rows(root: Path) -> list[dict[str, str]]:
    require(
        root.is_absolute() and root.is_dir() and not root.is_symlink(),
        "RUNTIME_BUILD_PACKAGE_ROOT_INVALID",
    )
    rows = []
    for path in sorted(root.rglob("*.py")):
        require(
            not path.is_symlink() and path.resolve().is_relative_to(root.resolve()),
            "RUNTIME_BUILD_SOURCE_SYMLINK",
        )
        rows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    require(bool(rows), "RUNTIME_BUILD_SOURCE_EMPTY")
    return rows


def installed_runtime_build() -> dict:
    root = Path(dsio_inventory_engine.__file__).resolve().parent
    tree = digest(package_source_rows(root))
    base = {
        "recipe": "inventory-python-source-closure-v1",
        "package_version": dsio_inventory_engine.__version__,
        "package_source_content_hash": tree,
    }
    implementations = {}
    for name, key in (
        ("mathematical", "historical-normal-r-s"),
        ("psi_simulator", "inventory.psi.weekly_roll_forward"),
    ):
        implementations[name] = {
            "implementation_id": key,
            "implementation_version": "1.0.0",
            "implementation_content_hash": digest(
                {**base, "implementation_id": key, "implementation_version": "1.0.0"}
            ),
        }
    body = {
        "contract_id": "inventory-installed-runtime-build",
        "contract_version": "1.0.0",
        **base,
        "implementations": implementations,
        "ppo_implementations": [],
    }
    return {**body, "content_hash": digest(body)}


def registered_installed_implementations(expected_build: dict):
    actual = installed_runtime_build()
    require(expected_build == actual, "RUNTIME_INSTALLED_BUILD_MISMATCH")
    return RegisteredRuntimeImplementations(
        mathematical_content_hash=actual["implementations"]["mathematical"][
            "implementation_content_hash"
        ],
        psi_simulator_content_hash=actual["implementations"]["psi_simulator"][
            "implementation_content_hash"
        ],
        ppo_implementations={},
    )

"""Arithmetic-only samples, never reported as actual measurements."""

import json
from pathlib import Path

from dsio_inventory_engine.evaluate_replenishment.robustness.contracts import sealed
from dsio_inventory_engine.evaluate_replenishment.robustness.split_preflight import MODES
from dsio_inventory_engine.inventory_contracts.values import digest

ROOT = Path(__file__).resolve().parents[2]


def protocol():
    return json.loads(
        (ROOT / "docs/architecture/data/catalog-robustness-protocol-v1.json").read_text()
    )


def sample(mode):
    count = MODES[mode]
    return sealed(
        "inventory-robustness-hand-resource-sample",
        {
            "mode": mode,
            "w0": "2055-01-04",
            "candidate_count": count,
            "psi_row_count": count * 65,
            **{
                key: digest({"manual_only": mode, "key": key})
                for key in (
                    "result_hash",
                    "input_hash",
                    "assembly_hash",
                    "realization_hash",
                    "catalog_hash",
                    "candidate_ids_hash",
                )
            },
            "pool_id": "f" * 64,
            "storage_version": "3.0.0",
            "raw_bytes_added": count * 1000,
            "physical_bytes_added": count * 10,
            "files_added": count,
            "phase_wall_ns": {"run": 100, "inspect": 20, "verify_recalculation": 200},
            "total_wall_ns": 360,
            "sample_kind": "HAND_CASE_MEASURED_NOT_OFFICIAL_BOUND",
            "integrity_status": "VERIFIED",
            "psi_recalculation_status": "VERIFIED",
            "official_paths_generated": False,
        },
    )


def samples():
    return [sample(mode) for mode in MODES]


def reseal(value):
    value["content_hash"] = digest({k: v for k, v in value.items() if k != "content_hash"})
    return value

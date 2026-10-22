"""Local resource policy, separate from immutable calculation/approval contracts."""

from dataclasses import dataclass
import math

from .robustness_storage import MAX_EXPERIMENT_RAW_BYTES, MAX_INVOCATION_SECONDS
from .values import InventoryInputError, require

RESOURCE_VERSION = "1.0.0"
QUARANTINE_RETENTION_SECONDS = 86_400
MAX_RESOURCE_FILES = 100_000


class ResourceControlError(InventoryInputError):
    """A resource stop is not a failed policy candidate or an approved result."""


@dataclass(frozen=True)
class ResourceLimits:
    raw_bytes: int = MAX_EXPERIMENT_RAW_BYTES

    def __post_init__(self):
        require(
            type(self.raw_bytes) is int and 0 < self.raw_bytes <= MAX_EXPERIMENT_RAW_BYTES,
            "RESOURCE_RAW_LIMIT_INVALID",
        )

    def policy(self) -> dict:
        return {
            "contract_id": "inventory-robustness-local-resource-policy",
            "contract_version": RESOURCE_VERSION,
            "raw_limit_bytes": self.raw_bytes,
            "max_invocation_seconds": MAX_INVOCATION_SECONDS,
            "max_cpu_workers": 1,
            "quarantine_retention_seconds": QUARANTINE_RETENTION_SECONDS,
            "max_files": MAX_RESOURCE_FILES,
            "automatic_publish_allowed": False,
            "automatic_order_allowed": False,
            "database_writes": False,
        }


def invocation_seconds(value: float) -> float:
    require(
        type(value) in (int, float)
        and math.isfinite(value)
        and 0 < value <= MAX_INVOCATION_SECONDS,
        "RESOURCE_DEADLINE_INVALID",
    )
    return float(value)

"""Original classification serialization: preserve decimal and ASCII hash projection."""

import hashlib
import json
from decimal import Decimal
from typing import Any, Mapping

from dsio_inventory_engine.classify_inventory.models import VedAssignment


def _snapshot_hash(value: Any) -> str:
    document = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(document.encode("utf-8")).hexdigest()


def _decimal_text(value: Decimal, *, places: int | None = None) -> str:
    normalized = value.quantize(Decimal(1).scaleb(-places)) if places is not None else value
    return format(normalized, "f")


def _optional_decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else _decimal_text(value)


def _axis_count(value: int | None, fallback: int) -> int:
    return fallback if value is None else value


def _combined_axis_reason(abc_reason: str | None, xyz_reason: str | None) -> str:
    if abc_reason == "INVALID_ABC_SOURCE_RECORD" and xyz_reason == "INVALID_XYZ_SOURCE_RECORD":
        return "INVALID_SOURCE_RECORD"
    if abc_reason == "INSUFFICIENT_ABC_HISTORY" and xyz_reason == "INSUFFICIENT_XYZ_HISTORY":
        return "INSUFFICIENT_DEMAND_HISTORY"
    return abc_reason or xyz_reason or "CLASSIFICATION_SOURCE_INVALID"


def _ved_result(
    item_id: str,
    *,
    enabled: bool,
    default_class: str,
    assignments: Mapping[str, VedAssignment],
) -> tuple[str | None, str, str | None]:
    if not enabled:
        return None, "DISABLED", None
    assignment = assignments.get(item_id)
    if assignment is None:
        return default_class, "DEFAULT_CLASS", None
    return assignment.ved_class, "ASSIGNMENT_SNAPSHOT", assignment.reason

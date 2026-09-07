"""Strict JSON value rules for deterministic, local inventory contracts."""

import hashlib
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Callable


class InventoryInputError(ValueError):
    def __init__(self, code: str, evidence: list[dict] | None = None):
        super().__init__(code)
        self.evidence_json = canonical_json(evidence or [])


def require(condition: bool, code: str, evidence: list[dict] | None = None) -> None:
    if not condition:
        raise InventoryInputError(code, evidence)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def read_json(document: str, *, max_bytes: int = 8_000_000) -> dict:
    def unique_pairs(pairs: list) -> dict:
        result = {}
        for key, value in pairs:
            require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    try:
        require(len(document.encode("utf-8")) <= max_bytes, "INPUT_SIZE_LIMIT")
        value = json.loads(document, object_pairs_hook=unique_pairs)
        require(type(value) is dict, "JSON_OBJECT_REQUIRED")
        return value
    except InventoryInputError:
        raise
    except (ValueError, TypeError, RecursionError):
        raise InventoryInputError("INVALID_JSON") from None


def text(value: Any) -> str:
    require(isinstance(value, str) and bool(value.strip()) and len(value) <= 512, "INVALID_TEXT")
    return value


def identifier(value: Any) -> str:
    require(
        isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value) is not None,
        "INVALID_IDENTIFIER",
    )
    return value


def hash_value(value: Any) -> str:
    require(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None, "INVALID_HASH"
    )
    return value


def yyyyww(value: Any) -> str:
    require(
        isinstance(value, str)
        and re.fullmatch(r"[1-9][0-9]{3}(0[1-9]|[1-4][0-9]|5[0-3])", value) is not None,
        "INVALID_YYYYWW",
    )
    return value


def day(value: Any) -> str:
    try:
        require(
            isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is not None,
            "INVALID_DATE",
        )
        date.fromisoformat(value)
        return value
    except ValueError:
        raise InventoryInputError("INVALID_DATE") from None


def timestamp(value: Any) -> str:
    try:
        text(value)
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(stamp.tzinfo is not None, "TIMEZONE_REQUIRED")
        return stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except InventoryInputError:
        raise
    except ValueError:
        raise InventoryInputError("INVALID_TIMESTAMP") from None


def decimal_string(value: Any, signed: bool = False) -> str:
    require(isinstance(value, str) and len(value) <= 40, "DECIMAL_STRING_REQUIRED")
    try:
        number = Decimal(value)
        require(number.is_finite(), "NON_FINITE_QUANTITY")
        require(number.copy_abs() <= Decimal("1000000000000"), "QUANTITY_RANGE")
        require(signed or number >= 0, "NEGATIVE_QUANTITY")
        exponent = number.as_tuple().exponent
        require(isinstance(exponent, int) and exponent >= -6, "QUANTITY_PRECISION")
        require(signed or not value.startswith("-"), "DECIMAL_FORMAT")
        require(
            re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]{1,6})?", value) is not None,
            "DECIMAL_FORMAT",
        )
        return quantity_text(number)
    except InvalidOperation:
        raise InventoryInputError("INVALID_DECIMAL") from None


def quantity_text(value: Decimal) -> str:
    with localcontext() as ctx:
        ctx.prec = 40
        return "0" if value == 0 else format(value.normalize(), "f")


def integer(value: Any) -> int:
    require(type(value) is int and 0 <= value <= 1_000_000, "INVALID_INTEGER")
    return value


def boolean(value: Any) -> bool:
    require(type(value) is bool, "INVALID_BOOLEAN")
    return value


def optional(rule: Callable) -> Callable:
    return lambda value: None if value is None else rule(value)


def choice(*values: str) -> Callable:
    def parse(value: Any) -> str:
        require(type(value) is str and value in values, "INVALID_CODE")
        return value

    return parse


def shape(value: Any, fields: dict[str, Callable]) -> dict:
    require(type(value) is dict and set(value) == set(fields), "CONTRACT_FIELDS")
    return {key: rule(value[key]) for key, rule in fields.items()}


def records(value: Any, fields: dict[str, Callable], limit: int = 100_000) -> list[dict]:
    require(type(value) is list and len(value) <= limit, "ROW_LIMIT")
    return [shape(row, fields) for row in value]

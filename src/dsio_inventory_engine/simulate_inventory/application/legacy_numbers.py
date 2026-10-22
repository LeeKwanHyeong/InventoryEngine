"""Small .NET binary64 helpers for the isolated Legacy reference, not target PSI.

Math.Round(value, digits) scales the binary64 before rounding, unlike Python's
round(value, digits). Lot.GetSize also casts its binary64 ratio to .NET decimal
before Ceiling. Keep these two intermediate rounding steps explicit.
"""

import math


def dotnet_round(value: float, digits: int = 2) -> float:
    if type(digits) is not int or not 0 <= digits <= 15:
        raise ValueError("LEGACY_ROUND_DIGITS")
    if not math.isfinite(value) or abs(value) >= 1e16:
        return value
    scale = float(10**digits)
    rounded = float(round(value * scale)) / scale
    return math.copysign(rounded, value) if rounded == 0 else rounded


def decimal_ceiling_of_double(value: float) -> float:
    """Model VarDecFromR8's 15-digit conversion, then decimal Ceiling.

    The scale is selected from the binary exponent, capped at 28 decimal
    places. Use integer arithmetic after the rounded binary64 mantissa so
    the caller's Python Decimal context cannot change the result.
    """
    if not math.isfinite(value):
        raise ValueError("LEGACY_DECIMAL_OVERFLOW")
    exponent = math.frexp(value)[1]
    if value == 0 or exponent < -94:
        return 0.0
    if exponent > 96:
        raise ValueError("LEGACY_DECIMAL_OVERFLOW")
    magnitude = abs(value)
    power = 14 - ((exponent * 19728) >> 16)
    if power >= 0:
        power = min(power, 28)
        magnitude *= float(10**power)
    elif power != -1 or magnitude >= 1e15:
        magnitude /= float(10 ** (-power))
    else:
        power = 0
    if magnitude < 1e14 and power < 28:
        magnitude *= 10.0
        power += 1
    mantissa = round(magnitude)
    if mantissa == 0:
        return 0.0
    if value < 0:
        mantissa = -mantissa
    if power < 0:
        integer = mantissa * 10 ** (-power)
        if abs(integer) >= 2**96:
            raise ValueError("LEGACY_DECIMAL_OVERFLOW")
        return float(integer)
    integer = -(-mantissa // 10**power)
    return math.copysign(0.0, value) if integer == 0 else float(integer)

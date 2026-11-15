"""Small JSON helpers: safe numbers (NaN and infinity mapped to None) and UTC timestamps.

Rounding is left to the caller. Only the standard library is used, so the
module can be imported without numpy or OpenCV, including from
`vision_server.detection.apriltag`.
"""

import math
from datetime import datetime, timezone


def finite_or_none(value, digits: int | None = None) -> float | None:
    """Return NaN and infinity as ``None``, otherwise the number rounded to ``digits``.

    `json.dumps` writes NaN as a bare ``NaN``, which is not valid JSON.
    `vision_server.payload._dumps` serializes with `allow_nan=False` and raises
    on it. A single outlier would then fail the whole job as INTERNAL, not just
    one attribute. ``None`` is the honest answer here: the value is not available.
    ``None`` stays ``None``.

    Rounding changes the file format, so the caller decides it. Without
    ``digits`` the value is not rounded. The detection payload rounds to 4
    digits, the hand-eye file to 6.
    """
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number if digits is None else round(number, digits)


def utc_now_z() -> str:
    """Return the current UTC time, to the second, e.g. `2026-10-31T12:00:00Z` (`createdAt`)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_stamp() -> str:
    """Return the current UTC time for file and folder names, e.g. `20261031T120000Z`."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


__all__ = ["finite_or_none", "utc_now_z", "utc_stamp"]

"""State and status of the live preview during tag referencing (overlay mode `tagreference`).

Plain data objects and helpers, no threads: the annotator
(`stream_overlay.AprilTagStreamAnnotator`) holds them. All objects are immutable
and are only swapped by attribute assignment. That lets the event loop (OPC UA
method) set them and the overlay thread read them without a lock.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

PREVIEW_SCHEMA = "wsc.vision.tagreference.preview/1"
#: A preview expires if it has not been renewed for this long.
PREVIEW_TTL_S = 120.0
#: At most 3 evaluations per second.
PREVIEW_INTERVAL_S = 1.0 / 3.0
#: How long a preview evaluation stays drawn on new frames.
PREVIEW_HOLD_S = 1.0
#: Debug image of a running measurement: held longer, because the measurement is slower.
MEASURE_HOLD_S = 5.0


@dataclass(frozen=True)
class ReferencePreview:
    """The active preview: request (`TagReferenceRequest`) and expiry time (`time.monotonic`)."""

    request: Any
    expires_at: float


@dataclass(frozen=True)
class ReferenceView:
    """A finished evaluation ready to draw; pixels are in an image of size `image_size`."""

    debug: tuple
    corners_px: Any
    image_size: tuple
    title: str
    layers: dict
    taken_at: float
    hold_s: float


def build_status(
    *, active: bool, tag_id: int | None, tag_seen: bool, debug=(), layers: dict | None = None
) -> dict:
    """Content of `TagReferencePreviewStatus` (`wsc.vision.tagreference.preview/1`)."""
    lines = [
        {
            "id": entry["id"],
            "found": bool(entry.get("found", False)),
            "offsetMm": entry.get("offsetMm"),
            "angleDeg": entry.get("angleDeg"),
            "ambiguous": bool(entry.get("ambiguous", False)),
            "reason": entry.get("reason"),
            "supportPx": entry.get("supportPx"),
            "contrast": entry.get("contrast"),
        }
        for entry in debug
        if entry.get("id") != "_tag"
    ]
    return {
        "schema": PREVIEW_SCHEMA,
        "active": bool(active),
        "tagId": tag_id,
        "tagSeen": bool(tag_seen),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "overlay": dict(layers) if layers is not None else {},
        "lines": lines,
    }

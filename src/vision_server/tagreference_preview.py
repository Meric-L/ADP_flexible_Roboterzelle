"""Zustand und Status der Live-Vorschau beim Tag-Einmessen (Overlay-Modus `tagreference`).

Reine Datenobjekte und Hilfen, ohne Threads: der Annotator
(`stream_overlay.AprilTagStreamAnnotator`) haelt sie. Alle Objekte sind
unveraenderlich und werden nur per Attributzuweisung ausgetauscht -- so kann der
Event-Loop (OPC-UA-Methode) sie setzen und der Overlay-Thread lesen, ohne Sperre.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

PREVIEW_SCHEMA = "wsc.vision.tagreference.preview/1"
#: Eine Vorschau laeuft ab, wenn sie so lange nicht erneuert wurde.
PREVIEW_TTL_S = 120.0
#: Hoechstens 3 Auswertungen je Sekunde.
PREVIEW_INTERVAL_S = 1.0 / 3.0
#: So lange wird eine Vorschau-Auswertung auf neue Frames gezeichnet.
PREVIEW_HOLD_S = 1.0
#: Debug-Bild einer laufenden Messung: laenger gehalten, die Messung ist langsamer.
MEASURE_HOLD_S = 5.0


@dataclass(frozen=True)
class ReferencePreview:
    """Die aktive Vorschau: Anfrage (`TagReferenceRequest`) und Ablaufzeit (`time.monotonic`)."""

    request: Any
    expires_at: float


@dataclass(frozen=True)
class ReferenceView:
    """Eine fertige Auswertung zum Zeichnen; Pixel im Bild der Groesse `image_size`."""

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
    """Inhalt von `TagReferencePreviewStatus` (`wsc.vision.tagreference.preview/1`)."""
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

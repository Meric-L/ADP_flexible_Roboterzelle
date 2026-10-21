"""Zeichnet die Zwischenergebnisse der Kantensuche (`measure_reference_lines(debug=...)`).

Nur Zeichnen, nichts rechnen: die Debug-Dicts kommen fertig aus
`tagloc.reference_edges` (Pixel im Rohbild). Farben (BGR):

- Startwert-Kante: gelb gestrichelt, Suchband: duenne gelbe Raender
- Kandidatenpunkte (Gradientenmaxima): cyan
- verworfene RANSAC-Linien (Inlier): orange
- gewaehlte Linie: gruen, bei mehrdeutiger Kante rot
- Tag-Maske (Tag plus Rand): grau, Tag-Rahmen: gruen

Strichstaerken sind auf 640 px Bildbreite abgestimmt und wachsen mit der
Bildbreite (`overlay._scale_for`). Die Schrift ist ASCII (OpenCV-Hershey-Font
kennt keine Umlaute).
"""

from collections.abc import Mapping, Sequence

import numpy as np

from .overlay import (
    COLOR_AMBIGUOUS,
    COLOR_OK,
    COLOR_TEXT,
    _put_text,
    _scale_for,
)

COLOR_GUESS = (0, 230, 255)
COLOR_CANDIDATE_POINT = (255, 255, 0)
COLOR_REJECTED = (0, 140, 255)
COLOR_CHOSEN = (60, 220, 60)
COLOR_BAD = (60, 60, 255)
COLOR_MASK = (150, 150, 150)

#: Schaltbare Ebenen; fehlende Schluessel gelten als an.
LAYER_KEYS = ("guess", "band", "candidates", "rejected", "chosen", "mask", "labels", "tagBox")


def effective_layers(layers: Mapping | None) -> dict[str, bool]:
    """Wirksame Ebenen: fehlende Schluessel = True, unbekannte werden ignoriert."""
    layers = layers or {}
    return {key: bool(layers.get(key, True)) for key in LAYER_KEYS}


def _int_points(points) -> np.ndarray:
    return np.round(np.asarray(points, dtype=np.float64).reshape(-1, 2)).astype(np.int32)


def _dashed(image, points, color, thickness) -> None:
    import cv2

    pts = _int_points(points)
    for index in range(0, len(pts) - 1, 2):
        cv2.line(image, tuple(pts[index]), tuple(pts[index + 1]), color, thickness)


def _band_edges(debug_line: dict) -> list[np.ndarray]:
    positions = np.asarray(debug_line.get("profilesPx") or [], dtype=np.float64).reshape(-1, 2)
    normals = np.asarray(debug_line.get("normalsPx") or [], dtype=np.float64).reshape(-1, 2)
    band = float(debug_line.get("bandPx") or 0.0)
    if len(positions) < 2 or len(positions) != len(normals) or band <= 0:
        return []
    return [positions + band * normals, positions - band * normals]


def _label(debug_line: dict) -> str:
    name = str(debug_line.get("id", "?"))
    reason = debug_line.get("reason")
    if reason:
        return f"{name}  {reason}"
    if debug_line.get("ambiguous"):
        return f"{name}  mehrdeutig"
    offset, angle = debug_line.get("offsetMm"), debug_line.get("angleDeg")
    if offset is None or angle is None:
        return name
    return f"{name}  {offset:+.1f} mm  {angle:.1f} deg".replace(".", ",")


def _label_anchor(debug_line: dict, width: int, height: int):
    for key in ("measuredPx", "guessPx"):
        points = debug_line.get(key)
        if points:
            pts = np.asarray(points, dtype=np.float64).reshape(-1, 2)
            middle = pts[len(pts) // 2] if key == "guessPx" else pts.mean(axis=0)
            return (
                int(min(max(middle[0] + 6, 4), max(width - 120, 4))),
                int(min(max(middle[1] - 8, 16), height - 4)),
            )
    return None


def draw_reference_debug(
    image,
    debug: Sequence[dict],
    *,
    tag_corners_px=None,
    title: str = "",
    layers: Mapping[str, bool] | None = None,
) -> np.ndarray:
    """Zeichnet die Kantensuche auf eine Kopie von `image` (BGR oder Grau) und gibt sie zurueck.

    `layers`: Ebenen ein/aus (`LAYER_KEYS`), `None` = alles an. Der Titel bleibt immer.
    """
    import cv2

    on = effective_layers(layers)

    canvas = np.asarray(image)
    if canvas.ndim == 2:
        canvas = cv2.cvtColor(canvas, cv2.COLOR_GRAY2BGR)
    else:
        canvas = canvas.copy()
    height, width = canvas.shape[:2]
    scale = _scale_for(canvas)
    thin = max(1, round(scale))
    thick = max(2, round(2 * scale))
    dot = max(1, round(1.5 * scale))

    for entry in debug:
        outline = entry.get("maskOutlinePx")
        if outline and on["mask"]:
            cv2.polylines(
                canvas, [_int_points(outline).reshape(-1, 1, 2)], True, COLOR_MASK, thin
            )
    if tag_corners_px is not None and on["tagBox"]:
        corners = _int_points(tag_corners_px).reshape(-1, 1, 2)
        cv2.polylines(canvas, [corners], True, COLOR_OK, thick)

    for entry in debug:
        if entry.get("id") == "_tag":
            continue
        for edge in _band_edges(entry) if on["band"] else []:
            cv2.polylines(canvas, [_int_points(edge).reshape(-1, 1, 2)], False, COLOR_GUESS, thin)
        guess = entry.get("guessPx")
        if guess and on["guess"]:
            _dashed(canvas, guess, COLOR_GUESS, thick)
        for u, v in _int_points(entry.get("edgePointsPx") or []) if on["candidates"] else []:
            cv2.circle(canvas, (int(u), int(v)), dot, COLOR_CANDIDATE_POINT, -1)
        for candidate in (entry.get("candidates") or []) if on["rejected"] else []:
            if candidate.get("chosen"):
                continue
            for u, v in _int_points(candidate.get("inliersPx") or []):
                cv2.circle(canvas, (int(u), int(v)), dot + 1, COLOR_REJECTED, -1)
        measured = entry.get("measuredPx")
        if measured and on["chosen"]:
            poly = entry.get("measuredPolyPx") or measured
            colour = COLOR_BAD if entry.get("ambiguous") else COLOR_CHOSEN
            cv2.polylines(canvas, [_int_points(poly).reshape(-1, 1, 2)], False, colour, thick)
        anchor = _label_anchor(entry, width, height)
        if anchor is not None and on["labels"]:
            colour = COLOR_BAD if entry.get("ambiguous") else (
                COLOR_CHOSEN if measured else COLOR_AMBIGUOUS
            )
            _put_text(canvas, _label(entry), anchor, colour)

    if title:
        _put_text(canvas, title, (round(8 * scale), round(22 * scale)), COLOR_TEXT)
    return canvas


_POINT_KEYS = ("guessPx", "profilesPx", "edgePointsPx", "measuredPx", "measuredPolyPx", "maskOutlinePx")


def scale_debug(debug: Sequence[dict], factor_x: float, factor_y: float) -> list[dict]:
    """Rechnet die Pixel der Debug-Dicts auf ein anderes Bild um (z. B. den verkleinerten Stream).

    Die Normalen bleiben unveraendert (Richtungen), `bandPx` folgt dem x-Faktor.
    """
    factor = np.array([factor_x, factor_y], dtype=np.float64)
    scaled = []
    for entry in debug:
        copy = dict(entry)
        for key in _POINT_KEYS:
            if copy.get(key):
                copy[key] = (np.asarray(copy[key], dtype=np.float64).reshape(-1, 2) * factor).tolist()
        if copy.get("candidates"):
            copy["candidates"] = [
                {
                    **candidate,
                    "inliersPx": (
                        np.asarray(candidate["inliersPx"], dtype=np.float64).reshape(-1, 2) * factor
                    ).tolist(),
                }
                for candidate in copy["candidates"]
            ]
        if copy.get("bandPx"):
            copy["bandPx"] = float(copy["bandPx"]) * factor_x
        scaled.append(copy)
    return scaled

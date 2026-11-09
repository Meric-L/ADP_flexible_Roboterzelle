"""Helligkeitstest: wie gut die Tag-Erkennung bei abnehmendem Licht noch ist.

Aufbau (Frontend, Fenster "Helligkeitstest"): Tag und Kamera fest montiert,
Belichtung, Gain und Weissabgleich eingefroren (`SetExposureLock`), das Licht
in Stufen gedimmt. Je Stufe nimmt `RecordLightLevel` viele Bilder auf (Standard
300) und wertet jedes einzeln aus:

* `cv2.aruco` -- der Detektor des Betriebs: erkannt ja/nein, Pose im
  Kamera-KS (fuer den Pose-Jitter), Reprojektionsfehler;
* `pupil_apriltags` -- nur fuer diesen Test: erkannt ja/nein, Decision Margin
  und Hamming-Distanz (die liefert `cv2.aruco` nicht);
* Michelson-Kontrast des Tags: mittlere Helligkeit des schwarzen Rands gegen
  die des weissen Rands direkt ausserhalb, im entzerrten Tag-Ausschnitt.

Gespeichert wird je Stufe eine JSON mit allen Einzelbildern und der Zusammen-
fassung, dazu eine Stichprobe von Vollbildern:
`data/Helligkeitstest/reihe_NNN/stufe_NNN.json` und `stufe_NNN_bild_NN.png`.

Hier steckt kein asyncua und keine Kamera: Bilder und Detektionen kommen als
Daten herein. Der Ablauf mit Kamera steht in `runner/light_test.py`.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from tagloc.jsonio import finite_or_none

REQUEST_SCHEMA = "wsc.vision.lighttest.request/1"
RESULT_SCHEMA = "wsc.vision.lighttest.level/1"
STATUS_SCHEMA = "wsc.vision.lighttest.status/1"
EXPOSURE_SCHEMA = "wsc.vision.exposure/1"

DEFAULT_FRAMES = 300
MAX_FRAMES = 2000
DEFAULT_SAMPLE_IMAGES = 10
MAX_SAMPLE_IMAGES = 50

#: Ablage, relativ zur Wurzel des Repos (`profiles._REPO_ROOT`).
DATA_FOLDER = ("data", "Helligkeitstest")
SERIES_FOLDER = "reihe"
LEVEL_STEM = "stufe"

#: tag36h11: das schwarze Quadrat (`sizeM`) hat 8 x 8 Zellen, die aeusserste
#: Zellreihe ist der schwarze Rand.
TAG_CELLS = 8
#: Pixel je Zelle im entzerrten Ausschnitt.
CELL_PX = 12
#: Der weisse Rand wird nur bis so weit ausserhalb gemessen (Zellen) -- er ist
#: bei unseren Tags schmaler als eine Zelle.
WHITE_RING_CELLS = (0.15, 0.55)
#: Vom schwarzen Rand nur die Mitte der Zellreihe, weg von den Kanten.
BLACK_RING_CELLS = (0.2, 0.8)


@dataclass(frozen=True)
class LightLevelRequest:
    tag_id: int
    frames: int = DEFAULT_FRAMES
    sample_images: int = DEFAULT_SAMPLE_IMAGES
    #: 0 legt eine neue Reihe an, >= 1 haengt die Stufe an diese.
    series: int = 0
    #: Kantenlaenge des schwarzen Quadrats; `None` = Tag-Map bzw. Standard.
    size_m: float | None = None
    #: Was das Frontend zur Stufe mitgibt (Helligkeit in %, Lux, Bezeichnung ...).
    level: dict = field(default_factory=dict)
    client: dict = field(default_factory=dict)


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def parse_request(text: str) -> LightLevelRequest:
    """`wsc.vision.lighttest.request/1` lesen; ValueError mit Klartext."""
    try:
        data = json.loads(text) if text and text.strip() else {}
    except json.JSONDecodeError as error:
        raise ValueError(f"Kein JSON: {error}") from None
    if not isinstance(data, dict) or data.get("schema", REQUEST_SCHEMA) != REQUEST_SCHEMA:
        raise ValueError(f"Erwartet schema „{REQUEST_SCHEMA}“.")
    tag_id = data.get("tagId")
    if not _is_int(tag_id) or tag_id < 0:
        raise ValueError("tagId muss eine nicht-negative Ganzzahl sein.")
    frames = data.get("frames", DEFAULT_FRAMES)
    if not _is_int(frames) or not 1 <= frames <= MAX_FRAMES:
        raise ValueError(f"frames muss eine Ganzzahl von 1 bis {MAX_FRAMES} sein.")
    samples = data.get("sampleImages", DEFAULT_SAMPLE_IMAGES)
    if not _is_int(samples) or not 0 <= samples <= MAX_SAMPLE_IMAGES:
        raise ValueError(f"sampleImages muss eine Ganzzahl von 0 bis {MAX_SAMPLE_IMAGES} sein.")
    series = data.get("series", 0)
    if not _is_int(series) or series < 0:
        raise ValueError("series muss eine Ganzzahl >= 0 sein.")
    size_m = data.get("sizeM")
    if size_m is not None and (isinstance(size_m, bool) or not isinstance(size_m, (int, float)) or not size_m > 0):
        raise ValueError("sizeM muss eine positive Zahl oder null sein.")
    level = data.get("level")
    level = {} if level is None else level
    client = data.get("metadata")
    client = {} if client is None else client
    if not isinstance(level, dict) or not isinstance(client, dict):
        raise ValueError("level und metadata muessen Objekte sein.")
    return LightLevelRequest(
        int(tag_id), int(frames), int(samples), int(series),
        float(size_m) if size_m is not None else None, level, client,
    )


def sample_indices(frames: int, count: int) -> list[int]:
    """`count` Bildnummern, gleichmaessig ueber `frames` Bilder verteilt (erste und letzte dabei)."""
    count = min(count, frames)
    if count <= 0:
        return []
    if count == 1:
        return [0]
    return sorted({round(index * (frames - 1) / (count - 1)) for index in range(count)})


def michelson_contrast(gray: Any, corners) -> dict | None:
    """Kontrast des Tags: schwarzer Rand gegen weissen Rand direkt ausserhalb.

    `corners`: die vier Aussenecken des schwarzen Quadrats, im Umlauf (die
    Reihenfolge des Detektors). Der Tag wird auf ein Raster von Zellen
    entzerrt; gemessen werden der Ring der aeussersten schwarzen Zellreihe und
    ein schmaler Ring ausserhalb. `None`, wenn der Ausschnitt nicht ins Bild
    passt. Michelson: (I_weiss - I_schwarz) / (I_weiss + I_schwarz).
    """
    import cv2

    if corners is None or len(corners) != 4:
        return None
    border = 1.0
    size = int(round((TAG_CELLS + 2 * border) * CELL_PX))
    offset = border * CELL_PX
    span = TAG_CELLS * CELL_PX
    target = np.array(
        [[offset, offset], [offset + span, offset], [offset + span, offset + span], [offset, offset + span]],
        dtype=np.float32,
    )
    source = np.asarray(corners, dtype=np.float32).reshape(4, 2)
    height, width = gray.shape[:2]
    # Der Ausschnitt muss ganz im Bild liegen, sonst misst man Schwarz ausserhalb des Bilds.
    inverse = cv2.getPerspectiveTransform(target, source)
    frame_corners = np.array([[[0, 0], [size, 0], [size, size], [0, size]]], dtype=np.float32)
    outline = cv2.perspectiveTransform(frame_corners, inverse).reshape(4, 2)
    if outline[:, 0].min() < 0 or outline[:, 1].min() < 0 or outline[:, 0].max() > width - 1 or outline[:, 1].max() > height - 1:
        return None
    warp = cv2.warpPerspective(
        gray, cv2.getPerspectiveTransform(source, target), (size, size), flags=cv2.INTER_LINEAR
    ).astype(np.float64)

    # Abstand jedes Pixels vom Quadrat in Zellen: < 0 innen, > 0 aussen.
    coords = (np.arange(size) + 0.5 - offset) / CELL_PX
    x, y = np.meshgrid(coords, coords)
    inside_depth = np.minimum.reduce([x, y, TAG_CELLS - x, TAG_CELLS - y])
    outside = -inside_depth
    black = (inside_depth >= BLACK_RING_CELLS[0]) & (inside_depth <= BLACK_RING_CELLS[1])
    white = (outside >= WHITE_RING_CELLS[0]) & (outside <= WHITE_RING_CELLS[1])
    i_black = float(warp[black].mean())
    i_white = float(warp[white].mean())
    total = i_white + i_black
    return {
        "michelson": (i_white - i_black) / total if total > 0 else None,
        "black": i_black,
        "white": i_white,
    }


def frame_record(
    index: int,
    captured_at: str,
    image_mean: float,
    *,
    aruco: dict,
    apriltag: dict | None,
    contrast: dict | None,
) -> dict:
    """Ein Bild der Stufe, so wie es in die JSON kommt."""
    return {
        "index": index,
        "capturedAt": captured_at,
        "imageMean": finite_or_none(image_mean),
        "aruco": aruco,
        "apriltag": apriltag,
        "contrast": contrast,
    }


def _stats(values: list[float]) -> dict | None:
    values = [float(v) for v in values if v is not None and math.isfinite(v)]
    if not values:
        return None
    array = np.asarray(values)
    return {
        "mean": float(array.mean()),
        "sd": float(array.std(ddof=1)) if len(values) > 1 else None,
        "min": float(array.min()),
        "max": float(array.max()),
        "count": len(values),
    }


def _pose_jitter(poses: list[dict]) -> dict | None:
    """Streuung der Pose ueber die Bilder: je Achse (mm), radial (mm), Drehung (Grad)."""
    if len(poses) < 2:
        return None
    positions = np.asarray([pose["position"] for pose in poses], dtype=np.float64)
    mean = positions.mean(axis=0)
    radial = np.linalg.norm(positions - mean, axis=1)
    quats = np.asarray([pose["orientation"] for pose in poses], dtype=np.float64)
    # Vorzeichen an der ersten ausrichten (q und -q sind dieselbe Drehung), dann mitteln.
    quats[np.einsum("ij,j->i", quats, quats[0]) < 0] *= -1
    mean_q = quats.mean(axis=0)
    mean_q /= np.linalg.norm(mean_q)
    angles = 2 * np.degrees(np.arccos(np.clip(np.abs(quats @ mean_q), -1.0, 1.0)))
    return {
        "positionSdMm": [float(v) for v in positions.std(axis=0, ddof=1) * 1000],
        "radialRmsMm": float(math.sqrt(float(np.mean(radial ** 2))) * 1000),
        "radialMaxMm": float(radial.max() * 1000),
        "rotationRmsDeg": float(math.sqrt(float(np.mean(angles ** 2)))),
        "meanPosition": [float(v) for v in mean],
        "meanDistanceM": float(np.linalg.norm(mean)),
        "count": len(poses),
    }


def summarize(records: list[dict], frames_requested: int) -> dict:
    """Die Kennwerte einer Stufe aus ihren Einzelbildern."""
    total = len(records)
    aruco_seen = [r for r in records if r["aruco"].get("detected")]
    poses = [r["aruco"]["poseInCamera"] for r in aruco_seen if r["aruco"].get("poseInCamera")]
    apriltag_records = [r for r in records if r.get("apriltag") is not None]
    apriltag_seen = [r for r in apriltag_records if r["apriltag"].get("detected")]
    hamming: dict[str, int] = {}
    for r in apriltag_seen:
        value = r["apriltag"].get("hamming")
        if value is not None:
            hamming[str(value)] = hamming.get(str(value), 0) + 1
    contrasts = [r["contrast"]["michelson"] for r in records if r.get("contrast") and r["contrast"].get("michelson") is not None]
    return {
        "framesRequested": frames_requested,
        "framesUsed": total,
        "aruco": {
            "detectionRate": len(aruco_seen) / total if total else None,
            "detected": len(aruco_seen),
            "posed": len(poses),
            "reprojectionErrorPx": _stats([r["aruco"].get("reprojectionErrorPx") for r in aruco_seen]),
        },
        "apriltag": (
            {
                "detectionRate": len(apriltag_seen) / len(apriltag_records) if apriltag_records else None,
                "detected": len(apriltag_seen),
                "decisionMargin": _stats([r["apriltag"].get("decisionMargin") for r in apriltag_seen]),
                "hamming": _stats([r["apriltag"].get("hamming") for r in apriltag_seen]),
                "hammingCounts": hamming,
            }
            if apriltag_records
            else None
        ),
        "poseJitter": _pose_jitter(poses),
        "contrast": _stats(contrasts),
        "imageMean": _stats([r.get("imageMean") for r in records]),
    }


def next_number(directory: Path, pattern: str) -> int:
    """Hoechste Nummer `<pattern>_NNN` in `directory` (Dateien oder Ordner) plus 1."""
    regex = re.compile(rf"^{re.escape(pattern)}_(\d+)(?:\.json)?$")
    highest = 0
    if directory.is_dir():
        for entry in directory.iterdir():
            match = regex.match(entry.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return highest + 1


def level_paths(base_dir: Path, series: int) -> tuple[int, Path, int]:
    """`(Reihe, Ordner, Stufe)`: Reihe 0 nimmt die naechste freie, die Stufe ist die naechste im Ordner."""
    if series <= 0:
        series = next_number(base_dir, SERIES_FOLDER)
    folder = base_dir / f"{SERIES_FOLDER}_{series:03d}"
    folder.mkdir(parents=True, exist_ok=True)
    return series, folder, next_number(folder, LEVEL_STEM)


def write_level(folder: Path, level: int, document: dict) -> Path:
    """Die JSON der Stufe, exklusiv angelegt (nie ueberschreibend)."""
    path = folder / f"{LEVEL_STEM}_{level:03d}.json"
    with open(path, "x", encoding="utf-8") as handle:
        json.dump(document, handle, indent=1, ensure_ascii=False, default=str)
        handle.write("\n")
    return path


def image_name(level: int, sample: int) -> str:
    return f"{LEVEL_STEM}_{level:03d}_bild_{sample:02d}.png"


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)

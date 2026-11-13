"""MeasureFloorTags: wo Tags auf dem Boden liegen -- einmal entzerrt, einmal im Rohbild.

Fuer den Lineal-Test der Deckenkamera (Frontend, Fenster "Lineal-Test"):
Tags liegen entlang eines Lineals auf dem Boden. Je Bild und je Variante

* `entzerrt`: mit der Kalibrierung (Kameramatrix und Verzeichnung), wie im Betrieb;
* `roh`: dieselbe Kameramatrix, Verzeichnung null -- das Bild, wie es aus dem Sensor kommt;

wird die Kamerapose aus den Welttags bestimmt (gemeinsames PnP, wie Layer 1)
und jede Tag-Ecke als Sehstrahl mit der Bodenebene z = `heightM`
geschnitten. Die Mitte der vier Bodenpunkte ist die Tag-Position, ihre
Kantenlaenge die auf dem Boden gemessene Tag-Groesse. Beides haengt nicht von
der eingetragenen Tag-Groesse ab, nur von Kamerapose und Linsenmodell.

Die Lineal-Marker koennen AprilTags (wie die Welttags) oder ArUco-Marker sein
(`markers: "aruco"`, eigenes Woerterbuch, auf Wunsch automatisch gewaehlt).
Die Kamerapose kommt immer aus den Welttags (AprilTag). Gleiche IDs werden
nach ihrer Lage getrennt: Messungen einer ID, die weiter als
`CLUSTER_RADIUS_M` auseinanderliegen, sind verschiedene Marker ("7", "7b").

Bezug `camera` (ohne Welttags): Die Ebene der Marker (z. B. ein Tisch)
kommt aus den Markern selbst -- jeder Marker per PnP mit der angegebenen
Groesse, eine Ebene durch alle ihre Ecken --, die Ecken werden als
Sehstrahl mit ihr geschnitten. Koordinaten in dieser Ebene, Ursprung dort,
wo die optische Achse sie trifft: x/y die Bildachsen, in die Ebene gelegt.
Der Abstand eines Markers vom Ursprung ist sein Abstand von der Bildmitte;
der Massstab haengt an der angegebenen Marker-Groesse.

Hier steckt kein asyncua und keine Kamera: Bilder, Detektionen und die
Kamerapose kommen als Daten herein (`runner/measure_floor_tags.py`).
"""

from __future__ import annotations

import dataclasses
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from tagloc.jsonio import finite_or_none

REQUEST_SCHEMA = "wsc.vision.floortags.request/1"
RESULT_SCHEMA = "wsc.vision.floortags.result/1"

DEFAULT_FRAMES = 5
MAX_FRAMES = 30
#: Reihenfolge der Varianten im Ergebnis.
VARIANTS = ("entzerrt", "roh")
MARKERS = ("apriltag", "aruco")
#: Die ArUco-Woerterbuecher, unter denen "auto" waehlt: je Bitgroesse das groesste --
#: die kleineren (4X4_50 ...) sind seine Anfaenge, die IDs stimmen also ueberein.
ARUCO_CANDIDATES = (
    "DICT_4X4_1000",
    "DICT_5X5_1000",
    "DICT_6X6_1000",
    "DICT_7X7_1000",
    "DICT_ARUCO_ORIGINAL",
    "DICT_ARUCO_MIP_36h12",
)
#: Messungen einer ID, die so nah beieinander liegen, sind derselbe Marker (Meter, waagrecht).
CLUSTER_RADIUS_M = 0.05
#: Ablage je Messung, relativ zur Wurzel des Repos: `data/Linealtest/messung_NNN/`
#: mit `bild_NN.png` (die ersten `saveImages` Bilder, Vollbild wie aufgenommen) und
#: `messung.json` (Anfrage und Ergebnis) -- zum spaeteren Nachrechnen.
DATA_FOLDER = ("data", "Linealtest")
DEFAULT_SAVE_IMAGES = 1


@dataclass(frozen=True)
class FloorRequest:
    frames: int = DEFAULT_FRAMES
    #: Hoehe der Tag-Oberflaeche ueber dem Welt-Ursprung (Boden: 0).
    height_m: float = 0.0
    #: `None` = alle Tags im Bild.
    tag_ids: frozenset[int] | None = None
    #: Art der Lineal-Marker: "apriltag" (Familie der Welttags) oder "aruco".
    markers: str = "apriltag"
    #: ArUco-Woerterbuch (`DICT_...`) oder "auto".
    aruco_dictionary: str = "auto"
    #: So viele der Bilder werden mit dem Ergebnis abgelegt (0 = keine Ablage).
    save_images: int = DEFAULT_SAVE_IMAGES
    #: "world": Kamerapose aus den Welttags, Ebene z = `height_m`. "camera": ohne Welttags,
    #: Ebene aus den Markern, Koordinaten in ihr ab der optischen Achse.
    reference: str = "world"
    #: Kantenlaenge der Marker (schwarzes Quadrat) fuer den Bezug "camera"; `None` = Tag-Map bzw. Standard.
    tag_size_m: float | None = None


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def parse_request(text: str) -> FloorRequest:
    """`wsc.vision.floortags.request/1` lesen; ValueError mit Klartext. Leer: Standardwerte."""
    try:
        data = json.loads(text) if text and text.strip() else {}
    except json.JSONDecodeError as error:
        raise ValueError(f"Kein JSON: {error}") from None
    if not isinstance(data, dict) or data.get("schema", REQUEST_SCHEMA) != REQUEST_SCHEMA:
        raise ValueError(f"Erwartet schema „{REQUEST_SCHEMA}“.")
    frames = data.get("frames", DEFAULT_FRAMES)
    if not _is_int(frames) or not 1 <= frames <= MAX_FRAMES:
        raise ValueError(f"frames muss eine Ganzzahl von 1 bis {MAX_FRAMES} sein.")
    height = data.get("heightM", 0.0)
    if isinstance(height, bool) or not isinstance(height, (int, float)) or not math.isfinite(height):
        raise ValueError("heightM muss eine Zahl sein.")
    raw_ids = data.get("tagIds")
    tag_ids = None
    if raw_ids is not None:
        if not isinstance(raw_ids, list) or not all(_is_int(v) and v >= 0 for v in raw_ids):
            raise ValueError("tagIds muss eine Liste nicht-negativer Ganzzahlen oder null sein.")
        tag_ids = frozenset(raw_ids) or None
    markers = data.get("markers", "apriltag")
    if markers not in MARKERS:
        raise ValueError(f"markers muss eines von {', '.join(MARKERS)} sein.")
    dictionary = data.get("arucoDictionary", "auto")
    if not isinstance(dictionary, str) or not (dictionary == "auto" or dictionary.startswith("DICT_")):
        raise ValueError("arucoDictionary muss \"auto\" oder ein cv2.aruco-Name DICT_... sein.")
    save_images = data.get("saveImages", DEFAULT_SAVE_IMAGES)
    if not _is_int(save_images) or not 0 <= save_images <= MAX_FRAMES:
        raise ValueError(f"saveImages muss eine Ganzzahl von 0 bis {MAX_FRAMES} sein.")
    reference = data.get("reference", "world")
    if reference not in ("world", "camera"):
        raise ValueError("reference muss \"world\" oder \"camera\" sein.")
    size = data.get("tagSizeM")
    if size is not None and (isinstance(size, bool) or not isinstance(size, (int, float)) or not size > 0):
        raise ValueError("tagSizeM muss eine positive Zahl oder null sein.")
    return FloorRequest(
        int(frames), float(height), tag_ids, markers, dictionary, int(save_images), reference,
        float(size) if size is not None else None,
    )


def marker_plane(corner_sets: list[np.ndarray]) -> tuple[np.ndarray, float] | None:
    """Ebene `n . x = d` (Kamera-KS) durch alle Marker-Ecken; `n` zeigt zur Kamera (n_z < 0). `None` bei < 2 Markern."""
    if len(corner_sets) < 2:
        return None
    points = np.vstack(corner_sets)
    centre = points.mean(axis=0)
    _, _, vt = np.linalg.svd(points - centre)
    normal = vt[2]
    if normal[2] > 0:
        normal = -normal
    return normal, float(normal @ centre)


def plane_frame(normal: np.ndarray, d: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """`(Ursprung, u, v)`: wo die optische Achse die Ebene trifft, Bild-x und -y in die Ebene gelegt."""
    origin = np.array([0.0, 0.0, d / normal[2]])
    u = np.array([1.0, 0.0, 0.0]) - normal[0] * normal
    u /= np.linalg.norm(u)
    v = np.cross(normal, u)
    if v[1] < 0:  # Bild-y nach unten bleibt +v
        v = -v
    return origin, u, v


def measure_frame_camera(
    observations,
    calibrations: dict[str, Any],
    estimate: Callable[[Any, list], list],
    tag_ids: frozenset[int] | None,
) -> dict[str, dict]:
    """Wie `measure_frame`, aber ohne Welttags: Ebene aus den Markern, Koordinaten in ihr ab der optischen Achse.

    `estimate(calibration, observations)` liefert die Marker-Posen (`TagPose`) mit genau dieser Kalibrierung.
    """
    from tagloc.pose import tag_object_points

    chosen = [o for o in observations if tag_ids is None or int(o.tag_id) in tag_ids]
    # Die Ebene EINMAL, aus der ersten Variante (entzerrt) -- die beste Schaetzung der echten Lage.
    # Bestimmte jede Variante ihre eigene, glichen die Marker-Groessen im Rohbild die Verzeichnung
    # fast ganz aus (Marker und Abstaende sind aussen gleich gestaucht); so zeigt "roh", wie weit
    # die unkorrigierten Sehstrahlen auf dem echten Tisch daneben liegen.
    first = next(iter(calibrations.values()))
    corner_sets = []
    scale_tag = None
    matrix_k = np.asarray(first.camera_matrix, dtype=np.float64)
    principal = matrix_k[:2, 2]
    for pose in estimate(first, chosen):
        matrix = np.asarray(pose.pose_cam_tag, dtype=np.float64)
        size = float(getattr(pose, "size_m", 0) or 0)
        if size <= 0:
            continue
        points = tag_object_points(size)
        corners = (matrix[:3, :3] @ points.T).T + matrix[:3, 3]
        corner_sets.append(corners)
        # Der Marker am naechsten an der Bildmitte: dort ist die Verzeichnung am kleinsten.
        offset = float(np.linalg.norm(np.mean(np.asarray(pose.corners_px, dtype=np.float64), axis=0) - principal))
        if scale_tag is None or offset < scale_tag[0]:
            scale_tag = (offset, int(pose.tag_id), corners.mean(axis=0))
    plane = marker_plane(corner_sets)
    if plane is not None and scale_tag is not None:
        # Neigung aus allen Markern, Abstand (Massstab) allein aus dem mittleren.
        plane = (plane[0], float(plane[0] @ scale_tag[2]))
    result: dict[str, dict] = {}
    for name, calibration in calibrations.items():
        entry: dict = {
            "camera": {
                "solver": "plane",
                "worldTagIds": [],
                "markersForPlane": len(corner_sets),
                "scaleTagId": scale_tag[1] if scale_tag else None,
            },
            "tags": [],
        }
        if plane is not None:
            normal, d = plane
            origin, u, v = plane_frame(normal, d)
            entry["camera"].update(
                {
                    # Die Kamera ueber dem Ursprung, im Ebenen-KS: von hier zaehlt "nach aussen".
                    "positionInWorld": np.array([0.0, 0.0, float(np.linalg.norm(origin))]),
                    "planeDistanceM": float(abs(d)),
                    "tiltDeg": float(np.degrees(np.arccos(min(1.0, abs(normal[2]))))),
                }
            )
            from tagloc.lens import undistort_points

            inverse = np.linalg.inv(np.asarray(calibration.camera_matrix, dtype=np.float64))
            for observation in chosen:
                pixels = undistort_points(np.asarray(observation.corners, dtype=np.float64), calibration)
                rays = (inverse @ np.c_[pixels, np.ones(len(pixels))].T).T
                t = d / (rays @ normal)
                if np.any(t <= 0):
                    continue
                points = rays * t[:, None] - origin
                in_plane = np.c_[points @ u, points @ v, np.zeros(len(points))]
                entry["tags"].append({"tagId": int(observation.tag_id), **tag_on_floor(in_plane), "world": False})
        result[name] = entry
    return result


def save_measurement(base_dir, images: list, document: dict) -> tuple[str, list[str]]:
    """Legt `messung_NNN/` mit den Bildern (PNG) und `messung.json` an; `(Ordner, Bildnamen)`."""
    import cv2

    from .lighttest import next_number

    base_dir.mkdir(parents=True, exist_ok=True)
    number = next_number(base_dir, "messung")
    while True:
        folder = base_dir / f"messung_{number:03d}"
        try:
            folder.mkdir()
            break
        except FileExistsError:
            number += 1
    names = []
    for index, image in enumerate(images, start=1):
        name = f"bild_{index:02d}.png"
        if cv2.imwrite(str(folder / name), image):
            names.append(name)
    with open(folder / "messung.json", "x", encoding="utf-8") as handle:
        json.dump({**document, "bilder": names}, handle, indent=1, ensure_ascii=False, default=str)
        handle.write("\n")
    return str(folder), names


def raw_calibration(calibration):
    """Dieselbe Kameramatrix ohne Verzeichnung: das Rohbild, als waere die Linse ideal."""
    from tagloc.calibration import PINHOLE

    return dataclasses.replace(
        calibration,
        distortion=np.zeros(5, dtype=np.float64),
        model=PINHOLE,
        calibration_id=f"{calibration.calibration_id}-ohne-verzeichnung",
    )


def corners_on_plane(corners_px, calibration, pose_world_cam, height_m: float) -> np.ndarray | None:
    """Die Bildpunkte als Sehstrahlen, geschnitten mit z = `height_m` (Welt-KS); `None` bei flachem Strahl.

    Mit `calibration` entzerrt (bei Verzeichnung null: unveraendert), dann
    ueber die Kameramatrix in Strahlen im Kamera-KS, mit `pose_world_cam`
    (4x4) in die Welt.
    """
    from tagloc.lens import undistort_points

    pixels = undistort_points(np.asarray(corners_px, dtype=np.float64), calibration)
    matrix = np.asarray(calibration.camera_matrix, dtype=np.float64)
    rays = np.linalg.inv(matrix) @ np.c_[pixels, np.ones(len(pixels))].T
    pose = np.asarray(pose_world_cam, dtype=np.float64)
    directions = (pose[:3, :3] @ rays).T
    origin = pose[:3, 3]
    if np.any(np.abs(directions[:, 2]) < 1e-9):
        return None
    t = (height_m - origin[2]) / directions[:, 2]
    if np.any(t <= 0):
        return None
    return origin + directions * t[:, None]


def tag_on_floor(corners_world: np.ndarray) -> dict:
    """Mitte und mittlere Kantenlaenge der vier Bodenpunkte."""
    edges = [float(np.linalg.norm(corners_world[i] - corners_world[(i + 1) % 4])) for i in range(4)]
    return {"position": corners_world.mean(axis=0), "edgeM": float(np.mean(edges))}


def measure_frame(
    observations,
    calibrations: dict[str, Any],
    localize: Callable[[Any], tuple[Any, dict]],
    height_m: float,
    tag_ids: frozenset[int] | None,
    reference_ids: set[int],
) -> dict[str, dict]:
    """Ein Bild, je Variante: `{"camera": {...}, "tags": [{tagId, position, edgeM, world}, ...]}`.

    `observations` sind die Lineal-Marker (bei AprilTags auch die Welttags,
    `reference_ids`), gleiche IDs bleiben einzeln. `localize(calibration)`
    liefert `(T_world_cam oder None, Info)` -- die Kamerapose aus den
    Welttags, mit genau dieser Kalibrierung geloest.
    """
    result: dict[str, dict] = {}
    for name, calibration in calibrations.items():
        pose, info = localize(calibration)
        entry: dict = {"camera": dict(info), "tags": []}
        if pose is not None:
            entry["camera"]["positionInWorld"] = np.asarray(pose, dtype=np.float64)[:3, 3]
            for observation in observations:
                tag_id = int(observation.tag_id)
                if tag_ids is not None and tag_id not in tag_ids:
                    continue
                corners = corners_on_plane(observation.corners, calibration, pose, height_m)
                if corners is None:
                    continue
                entry["tags"].append({"tagId": tag_id, **tag_on_floor(corners), "world": tag_id in reference_ids})
        result[name] = entry
    return result


def cluster_by_place(found: list[dict], radius_m: float = CLUSTER_RADIUS_M) -> list[list[dict]]:
    """Messungen nach ID und Lage gruppiert: dieselbe ID weiter als `radius_m` vom Mittel ist ein anderer Marker."""
    clusters: list[tuple[int, list[dict]]] = []
    for tag in found:
        best = None
        for tag_id, members in clusters:
            if tag_id != tag["tagId"]:
                continue
            centre = np.mean([m["position"][:2] for m in members], axis=0)
            distance = float(np.linalg.norm(np.asarray(tag["position"][:2]) - centre))
            if distance <= radius_m and (best is None or distance < best[0]):
                best = (distance, members)
        if best is None:
            clusters.append((tag["tagId"], [tag]))
        else:
            best[1].append(tag)
    return [members for _, members in clusters]


def _label(tag_id: int, instance: int) -> str:
    """"7", "7b", "7c" ..."""
    return str(tag_id) if instance == 1 else f"{tag_id}{chr(ord('a') + instance - 1)}"


def _round(values) -> list:
    return [finite_or_none(float(v), 6) for v in values]


def summarize(frames: list[dict[str, dict]], frames_requested: int) -> dict:
    """Je Variante: Kamera (gemittelt) und je Tag Mittel, Streuung, Kantenlaenge ueber die Bilder."""
    variants = {}
    for name in VARIANTS:
        entries = [frame[name] for frame in frames if name in frame]
        cameras = [e["camera"] for e in entries if e["camera"].get("positionInWorld") is not None]
        errors = [c.get("reprojectionErrorPx") for c in cameras if c.get("reprojectionErrorPx") is not None]
        errors = [e for e in errors if math.isfinite(e)]
        clusters = cluster_by_place([tag for e in entries for tag in e["tags"]])
        # Gleiche IDs der Reihe nach (x, dann y) benannt -- "7", "7b"; das Frontend
        # ordnet die Varianten nach der Lage einander zu, nicht nach dem Namen.
        clusters.sort(key=lambda seen: (seen[0]["tagId"], *np.round(np.mean([t["position"][:2] for t in seen], axis=0), 2)))
        tags = []
        instances: dict[int, int] = {}
        for seen in clusters:
            tag_id = int(seen[0]["tagId"])
            instances[tag_id] = instances.get(tag_id, 0) + 1
            positions = np.asarray([t["position"] for t in seen], dtype=np.float64)
            mean = positions.mean(axis=0)
            tags.append(
                {
                    "tagId": tag_id,
                    "label": _label(tag_id, instances[tag_id]),
                    "world": bool(seen[0]["world"]),
                    "position": _round(mean),
                    "sdMm": _round(positions.std(axis=0, ddof=1) * 1000) if len(seen) > 1 else None,
                    "spreadM": finite_or_none(float(np.max(np.linalg.norm(positions[:, :2] - mean[:2], axis=1))), 6),
                    "edgeM": finite_or_none(float(np.mean([t["edgeM"] for t in seen])), 6),
                    "count": len(seen),
                }
            )
        planes = [c for c in cameras if c.get("planeDistanceM") is not None]
        variants[name] = {
            "camera": {
                "frames": len(cameras),
                "planeDistanceM": finite_or_none(float(np.mean([c["planeDistanceM"] for c in planes])), 6) if planes else None,
                "tiltDeg": finite_or_none(float(np.mean([c["tiltDeg"] for c in planes])), 4) if planes else None,
                "scaleTagId": next((c.get("scaleTagId") for c in planes if c.get("scaleTagId") is not None), None),
                "positionInWorld": _round(np.mean([c["positionInWorld"] for c in cameras], axis=0)) if cameras else None,
                "reprojectionErrorPx": finite_or_none(float(np.mean(errors)), 4) if errors else None,
                "solvers": sorted({str(c.get("solver")) for c in cameras}),
                "worldTagIds": sorted({int(i) for c in cameras for i in c.get("worldTagIds", [])}),
            },
            "tags": tags,
        }
    return {"framesRequested": frames_requested, "framesUsed": len(frames), "variants": variants}


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)

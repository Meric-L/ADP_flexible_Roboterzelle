"""AprilTag-Ziel fuer die Wiederholgenauigkeit: Detektor, Auswahl, Kennzahlen je Bild.

Gemeinsam genutzt von der Live-Messung des Pi (`live.py`) und der
Offline-Auswertung (`session.py`), damit beide dieselbe Rechnung benutzen.
Der Detektor wird aus den Einstellungen der AprilTag-Quelle des Pi
(`AprilTagProfileConfig`, in der Bild-JSON unter `pi.apriltag`) gebaut.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from .charuco import _to_gray
from .metrics import homography_metrics_points

#: Mindestzahl Punkte der Homographie bei einem Tag (nur 4 Ecken).
MIN_TAG_CORNERS = 4


@dataclass(frozen=True)
class AprilTagSpec:
    """Ziel `{"type": "apriltag", "tagId", "tagSizeM"}`; `tag_id` None = einziger erkannter Tag."""

    tag_id: int | None
    tag_size_m: float

    def as_dict(self) -> dict[str, Any]:
        return {"type": "apriltag", "tagId": self.tag_id, "tagSizeM": self.tag_size_m}


def target_type(client: Any) -> str:
    """`"apriltag"` oder `"charuco"` (Standard, auch ohne `target`) nach dem Client-Record."""
    target = client.get("target") if isinstance(client, dict) else None
    if isinstance(target, dict) and target.get("type") == "apriltag":
        return "apriltag"
    return "charuco"


def apriltag_spec_from_dict(d: Any) -> AprilTagSpec:
    """`AprilTagSpec` aus dem Client-Format; `ValueError` bei Unsinn."""
    if not isinstance(d, dict) or d.get("type") != "apriltag":
        raise ValueError("Ziel-Angabe ist kein AprilTag-Objekt")
    try:
        tag_id = None if d.get("tagId") is None else int(d["tagId"])
        size = float(d["tagSizeM"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"AprilTag-Angabe unvollstaendig oder ungueltig: {error}") from error
    if not (size > 0 and math.isfinite(size)):
        raise ValueError("tagSizeM muss > 0 sein")
    if tag_id is not None and tag_id < 0:
        raise ValueError("tagId muss >= 0 sein")
    return AprilTagSpec(tag_id, size)


def build_apriltag_detector(config: Any):
    """Baut den Tag-Detektor wie die AprilTag-Quelle des Pi.

    `config`: `AprilTagProfileConfig` oder ein Mapping mit denselben Feldnamen
    (`dataclasses.asdict`, also `pi.apriltag` aus der Bild-JSON). Fehlende Felder
    nehmen die Standardwerte der Quelle.
    """
    from tagloc.detector import build_detector

    def get(name: str, default: Any) -> Any:
        value = config.get(name, default) if isinstance(config, Mapping) else getattr(config, name, default)
        return default if value is None else value

    return build_detector(
        get("tag_family", "tag36h11"),
        get("detector_backend", "aruco"),
        subpixel_corner_refinement=bool(get("subpixel_corner_refinement", False)),
        corner_refinement_method=get("corner_refinement_method", "subpix"),
        apriltag_quad_decimate=float(get("apriltag_quad_decimate", 0.0)),
        detector_parameters=dict(get("detector_parameters", {})),
    )


def detect_observations(image: Any, detector: Any) -> list:
    """Alle Tags des Bildes (`TagObservation`, sortiert nach ID)."""
    return list(detector.detect(_to_gray(np.asarray(image))))


def select_observation(observations: list, tag_id: int | None) -> tuple[Any, str]:
    """`(Beobachtung, "")` oder `(None, Grund)`.

    Mit `tag_id`: genau dieser Tag, genau einmal. Ohne: es muss genau ein Tag erkannt sein.
    """
    if tag_id is not None:
        found = [o for o in observations if o.tag_id == tag_id]
        if not found:
            return None, f"Tag {tag_id} nicht erkannt"
        if len(found) > 1:
            return None, f"Tag {tag_id} mehrfach erkannt"
        return found[0], ""
    if not observations:
        return None, "kein Tag erkannt"
    if len(observations) > 1:
        ids = ", ".join(str(o.tag_id) for o in observations)
        return None, f"mehrere Tags erkannt ({ids}) und keine tagId angegeben"
    return observations[0], ""


def pose_from_client(d: Any):
    """Pose `{"position", "orientation" [x, y, z, w]}` als 4x4-Matrix; `None` bei fehlenden/ungueltigen Daten."""
    from tagloc.geometry import pose_from_dict

    try:
        pose = pose_from_dict(d)
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    return pose if np.all(np.isfinite(pose)) else None


def _calibration_for(calibration: Any, image_size: tuple[int, int]):
    """Intrinsik passend zur Bildgroesse (Seitenverhaeltnis-treu skaliert); `None` sonst."""
    from tagloc.calibration import scale_to_resolution

    if calibration is None:
        return None
    if tuple(int(v) for v in calibration.image_size) == tuple(int(v) for v in image_size):
        return calibration
    try:
        return scale_to_resolution(calibration, image_size)
    except ValueError:
        return None


def measure_observation(
    observation: Any,
    spec: AprilTagSpec,
    image_size: tuple[int, int],
    calibration: Any = None,
    pose_flange_cam: Any = None,
    flange_in_base: Any = None,
) -> dict[str, Any]:
    """Alle Kennzahlen eines Tag-Bildes (ohne `ok`/`target`-Huelle).

    Kalibrierfrei (`centerOnBoardMm` ...) wie beim Brett, mit den 4 Tag-Ecken als
    Objektpunkten (mm, Tag-Ebene, Achsen wie `tagloc.pose.tag_object_points`).
    `tagInCam*`/`reprojectionErrorPx` brauchen `calibration`, `tagInBaseM` zusaetzlich
    `pose_flange_cam` (T_flange_cam) und `flange_in_base` (T_base_flange), beide 4x4.
    Fehlendes ergibt `None`; `ValueError` nur, wenn die Homographie scheitert.
    """
    from tagloc.geometry import to_position_quaternion
    from tagloc.pose import estimate_tag_pose, tag_object_points

    corners = np.asarray(observation.corners, dtype=np.float64).reshape(4, 2)
    object_mm = tag_object_points(spec.tag_size_m)[:, :2] * 1000.0
    result = homography_metrics_points(corners, object_mm, image_size, MIN_TAG_CORNERS)
    result.update({
        "target": "apriltag",
        "tagId": int(observation.tag_id),
        "nMarkers": 1,
        "cornersPx": corners.tolist(),
        "tagInCamM": None,
        "tagInCamQuat": None,
        "reprojectionErrorPx": None,
        "tagInBaseM": None,
    })
    calib = _calibration_for(calibration, image_size)
    if calib is None:
        return result
    try:
        tag_pose = estimate_tag_pose(observation, spec.tag_size_m, calib)
    except Exception:
        return result
    cam_tag = np.asarray(tag_pose.pose_cam_tag, dtype=np.float64)
    if not np.all(np.isfinite(cam_tag)):
        return result
    position, quat = to_position_quaternion(cam_tag)
    result["tagInCamM"] = [float(v) for v in position]
    result["tagInCamQuat"] = [float(v) for v in quat]
    error = tag_pose.reprojection_error_px
    result["reprojectionErrorPx"] = float(error) if math.isfinite(error) else None
    if pose_flange_cam is not None and flange_in_base is not None:
        base_tag = (
            np.asarray(flange_in_base, dtype=np.float64)
            @ np.asarray(pose_flange_cam, dtype=np.float64)
            @ cam_tag
        )
        if np.all(np.isfinite(base_tag)):
            result["tagInBaseM"] = [float(v) for v in base_tag[:3, 3]]
    return result

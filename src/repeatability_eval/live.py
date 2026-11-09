"""Live-Messung beim Speichern: wirft nie."""

import json
from dataclasses import dataclass
from typing import Any
from collections.abc import Callable


@dataclass
class AprilTagLive:
    """Zustand der AprilTag-Live-Messung auf dem Pi (einmal beim Installieren gebaut).

    `detector`: Tag-Detektor (`build_apriltag_detector`); `calibration`/`pose_flange_cam`
    liefern bei jedem Aufruf die aktuelle Intrinsik (`CameraCalibration` oder None) bzw.
    T_flange_cam (4x4 oder None), damit eine neu aufgenommene Kalibrierung wirkt.
    """

    detector: Any
    calibration: Callable[[], Any] = lambda: None
    pose_flange_cam: Callable[[], Any] = lambda: None


def measure_for_save(image: Any, client_meta: Any, apriltag: AprilTagLive | None = None) -> dict:
    """Kennwerte des Bildes; `{"ok": False, "grund": ...}` bei jedem Problem.

    `client_meta`: JSON-Text oder dict. Ziel `target` (`type` `charuco` oder `apriltag`);
    ohne `target` gilt das Board unter `board` (ChArUco). Fuer AprilTag wird `apriltag`
    (Detektor und Pi-Daten) gebraucht.
    """
    try:
        meta = client_meta
        if isinstance(meta, (str, bytes)):
            try:
                meta = json.loads(meta) if meta else None
            except ValueError:
                return {"ok": False, "grund": "Client-Metadaten sind kein gueltiges JSON"}
        from .apriltag import target_type

        if target_type(meta) == "apriltag":
            return _measure_apriltag(image, meta, apriltag)
        board = meta.get("board") if isinstance(meta, dict) else None
        if not board and isinstance(meta, dict) and isinstance(meta.get("target"), dict) \
                and meta["target"].get("type") == "charuco":
            board = meta["target"]
        if not board:
            return {"ok": False, "grund": "kein Board in den Metadaten"}
        from .charuco import _detect, spec_from_dict
        from .metrics import homography_metrics

        spec = spec_from_dict(board)
        ids, corners, n_markers = _detect(image, spec)
        height, width = image.shape[:2]
        result = homography_metrics(ids, corners, spec, (width, height))
        result["nMarkers"] = n_markers
        return {"ok": True, **result}
    except Exception as error:
        return {"ok": False, "grund": f"{type(error).__name__}: {error}"}


def _measure_apriltag(image: Any, meta: dict, live: AprilTagLive | None) -> dict:
    from .apriltag import (
        apriltag_spec_from_dict,
        detect_observations,
        measure_observation,
        pose_from_client,
        select_observation,
    )

    if live is None or live.detector is None:
        return {"ok": False, "grund": "AprilTag-Detektor auf dem Pi nicht verfuegbar"}
    spec = apriltag_spec_from_dict(meta.get("target"))
    observation, grund = select_observation(detect_observations(image, live.detector), spec.tag_id)
    if observation is None:
        return {"ok": False, "grund": grund}
    actual = meta.get("actual")
    flange = pose_from_client(actual.get("flangeInBase")) if isinstance(actual, dict) else None
    height, width = image.shape[:2]
    result = measure_observation(
        observation, spec, (width, height), live.calibration(), live.pose_flange_cam(), flange
    )
    return {"ok": True, **result}

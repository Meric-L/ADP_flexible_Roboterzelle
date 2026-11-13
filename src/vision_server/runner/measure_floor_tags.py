"""MeasureFloorTags: Tags auf dem Boden, entzerrt und im Rohbild (Lineal-Test der Deckenkamera)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from asyncua import Node

from ..address_space import VisionAddressSpace
from ..config import VisionServerConfig
from ..errors import VisionErrorCode, VisionJobError
from ..state_machine import VisionStateMachines
from .uamethods import add_vision_method, json_method, text_reply

_log = logging.getLogger(__package__)


class ArucoFloorDetector:
    """ArUco-Marker eines Woerterbuchs, Ecken subpixelgenau, in der Eckenreihenfolge von `TagObservation`."""

    def __init__(self, dictionary_name: str) -> None:
        import cv2

        from tagloc.detector import predefined_dictionary

        self.dictionary = dictionary_name
        parameters = cv2.aruco.DetectorParameters()
        parameters.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
        self._detector = cv2.aruco.ArucoDetector(predefined_dictionary(dictionary_name), parameters)

    def detect(self, gray: Any) -> list:
        import numpy as np

        from tagloc.detector import TagObservation

        corners, ids, _ = self._detector.detectMarkers(gray)
        if ids is None:
            return []
        return [
            TagObservation(tag_id=int(tag_id), corners=tuple(map(tuple, np.asarray(quad, dtype=np.float64).reshape(4, 2))))
            for tag_id, quad in zip(np.asarray(ids).reshape(-1), corners)
        ]


def outside(observation, others) -> bool:
    """Ob die Mitte des Markers ausserhalb aller `others` (der AprilTags) liegt -- kein Fehlgriff in einen Welttag."""
    import cv2
    import numpy as np

    centre = tuple(float(v) for v in np.mean(np.asarray(observation.corners, dtype=np.float32), axis=0))
    return all(cv2.pointPolygonTest(np.asarray(o.corners, dtype=np.float32), centre, False) < 0 for o in others)


def choose_aruco(gray: Any, candidates) -> "ArucoFloorDetector | None":
    """Das Woerterbuch mit den meisten Markern in diesem Bild; `None`, wenn keines welche findet."""
    best = None
    for name in candidates:
        try:
            detector = ArucoFloorDetector(name)
        except Exception:
            continue
        count = len(detector.detect(gray))
        if count and (best is None or count > best[0]):
            best = (count, detector)
    return best[1] if best else None


def floor_frame(source: Any, image: Any, request, aruco: "ArucoFloorDetector | None" = None) -> dict:
    """Ein Bild auswerten, beide Varianten. Worker-Thread."""
    from tagloc import frames as frame_tools
    from tagloc.localize import localize_camera
    from tagloc.pose import estimate_tag_poses

    from ..floortags import measure_frame, raw_calibration

    gray = frame_tools.to_gray(image)
    observations = source._detector.detect(gray)
    calibrated = source._calibration_for(image)
    calibrations = {"entzerrt": calibrated, "roh": raw_calibration(calibrated)}
    tag_map = source._tag_map
    config = source._config

    def localize(calibration):
        # Die Tag-Posen (fuer die Welttags) mit genau dieser Kalibrierung; kein Verwerfen
        # nach Reprojektionsfehler -- im Rohbild ist er gross, das ist hier die Messgroesse.
        tag_poses = estimate_tag_poses(
            observations, calibration, tag_map=tag_map, default_size_m=config.tag_size_m
        )
        localization = localize_camera(
            tag_poses, tag_map, calibration=calibration, max_reprojection_error_px=config.max_reproj_error_px
        )
        if localization is None:
            return None, {"solver": None, "worldTagIds": []}
        return localization.pose_world_cam, {
            "solver": localization.solver,
            "reprojectionErrorPx": float(localization.reprojection_error_px),
            "worldTagIds": [int(i) for i in localization.world_tag_ids],
        }

    reference_ids = set(tag_map.reference_poses()) if tag_map is not None else set()
    if request.markers == "aruco":
        # Eigene Familie: ihre IDs haben mit den Welttags nichts zu tun.
        floor_markers = [m for m in aruco.detect(gray) if outside(m, observations)] if aruco else []
        return measure_frame(floor_markers, calibrations, localize, request.height_m, request.tag_ids, set())
    return measure_frame(observations, calibrations, localize, request.height_m, request.tag_ids, reference_ids)


def _pick_aruco(image: Any, names) -> "ArucoFloorDetector | None":
    from tagloc import frames as frame_tools

    return choose_aruco(frame_tools.to_gray(image), names)


async def install_measure_floor_tags(
    space: VisionAddressSpace,
    config: VisionServerConfig,
    source: Any,
    methods: dict[str, Node],
    states: VisionStateMachines,
    busy: Callable[[], bool],
) -> None:
    """Legt `MeasureFloorTags(RequestJson) -> (ResultJson, Error)` an (beide Pis, gedacht fuer die Decke)."""
    from ..floortags import ARUCO_CANDIDATES, DATA_FOLDER, RESULT_SCHEMA, dumps, parse_request, save_measurement, summarize
    from ..profiles import _REPO_ROOT
    from .uamethods import repo_relative

    def refusal(code: VisionErrorCode, message: str):
        return text_reply(dumps({"schema": RESULT_SCHEMA, "error": f"{code.name}: {message}"}), code)

    async def measure(request, _text: str):
        source.measure_calls += 1
        frames: list[dict] = []
        error = None
        image_size = None
        aruco = None
        kept: list = []
        try:
            seen = source._latest_timestamp()
            for _ in range(request.frames):
                try:
                    frame = await source._next_frame(seen)
                except VisionJobError as job_error:
                    error = job_error.message
                    break
                seen = frame.timestamp
                if request.markers == "aruco" and aruco is None:
                    names = ARUCO_CANDIDATES if request.aruco_dictionary == "auto" else (request.aruco_dictionary,)
                    aruco = await source.run_blocking(_pick_aruco, frame.image, names)
                frames.append(await source.run_blocking(floor_frame, source, frame.image, request, aruco))
                if len(kept) < request.save_images:
                    kept.append(frame.image)
                image_size = list(frame.image.shape[1::-1])
        except Exception as failure:
            _log.exception("MeasureFloorTags fehlgeschlagen")
            return refusal(VisionErrorCode.INTERNAL, str(failure))
        finally:
            source.measure_calls -= 1
        result = {
            "schema": RESULT_SCHEMA,
            "frameId": source._config.frame_id,
            "heightM": request.height_m,
            "calibrationId": getattr(source._calibration, "calibration_id", ""),
            "intrinsicsSource": getattr(source, "intrinsics_source", None),
            "imageSize": image_size,
            "markers": request.markers,
            "arucoDictionary": aruco.dictionary if aruco is not None else None,
            **summarize(frames, request.frames),
            "error": error,
        }
        if kept:
            # Bild und Ergebnis zum spaeteren Nachrechnen; ein Fehler beim Schreiben kostet nur die Ablage.
            try:
                folder, names = await source.run_blocking(
                    save_measurement,
                    _REPO_ROOT.joinpath(*DATA_FOLDER),
                    kept,
                    {**result, "request": {"frames": request.frames, "heightM": request.height_m,
                                           "tagIds": sorted(request.tag_ids) if request.tag_ids else None,
                                           "markers": request.markers, "arucoDictionary": request.aruco_dictionary}},
                )
                from pathlib import Path

                result["savedFolder"] = repo_relative(Path(folder))
                result["savedImages"] = names
            except Exception:
                _log.exception("MeasureFloorTags: Ablage fehlgeschlagen")
                result["saveError"] = "Bild und Ergebnis konnten nicht gespeichert werden."
        code = VisionErrorCode.OK if frames else VisionErrorCode.DETECTION_FAILED
        return text_reply(dumps(result), code)

    handler = json_method(
        "MeasureFloorTags",
        states,
        parse=parse_request,
        refuse=refusal,
        run=measure,
        busy=busy,
        busy_message="Job oder Kalibrierung laeuft",
    )
    from asyncua import ua

    await add_vision_method(
        space, config, methods, "MeasureFloorTags", handler,
        [ua.VariantType.String], [ua.VariantType.String, ua.VariantType.Int32],
    )

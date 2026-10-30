"""Von den Bildecken zur Pose: `T_cam_tag` samt Guetemassen.

Der Prototyp (`src/apriltag/detect_apriltags.py`, am 2026-10-30 entfernt) macht
zwei Dinge schlechter:

1. Keine Entzerrung. `D` ging dort nur an `drawFrameAxes`; die Pose wurde am
   verzerrten Bild gerechnet (doc/projektdoku/altlasten.md, "Was nicht hier
   steht"). Hier werden die Ecken zuerst entzerrt und dann mit einem idealen
   Pinhole-Modell geloest.
2. Keine Angabe der Mehrdeutigkeit. Ein quadratischer Marker, unter flachem
   Winkel gesehen, hat zwei fast gleich gute Loesungen; `solvePnPGeneric`
   liefert beide, und das Verhaeltnis ihrer Restfehler ist die ehrlichste
   Warnung, die es gibt.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from .calibration import CameraCalibration
from .geometry import Pose, compose, from_rvec_tvec, rvec_from_rotation
from .observations import TagObservation, TagPose

_log = logging.getLogger(__name__)


def tag_object_points(size_m: float) -> np.ndarray:
    """Liefert die vier Tag-Ecken im Tag-KS, in Metern.

    Die Reihenfolge passt zu `TagObservation.corners` (im Uhrzeigersinn, oben
    links zuerst) und zu dem, was `SOLVEPNP_IPPE_SQUARE` erwartet. Der
    Tag-Ursprung liegt in der Mitte, +Z zeigt aus dem Tag heraus.
    """
    half = float(size_m) / 2.0
    return np.array(
        [
            [-half, half, 0.0],
            [half, half, 0.0],
            [half, -half, 0.0],
            [-half, -half, 0.0],
        ],
        dtype=np.float64,
    )


def undistort_corners(corners, calibration: CameraCalibration) -> np.ndarray:
    """Entzerrt Bildecken auf das ideale Pinhole-Modell.

    `P=K` bringt das Ergebnis zurueck in Pixel, sodass danach mit
    Verzeichnung null geloest werden kann -- unabhaengig vom Linsenmodell der
    Kalibrierung (Pinhole oder Fisheye, siehe `tagloc.lens`).
    """
    from . import lens

    return lens.undistort_points(corners, calibration)


def _reprojection_error_px(object_points, image_points, rvec, tvec, camera_matrix) -> float:
    """Liefert den RMS-Abstand zwischen gemessenen und reprojizierten Ecken."""
    import cv2

    projected, _ = cv2.projectPoints(
        object_points, rvec, tvec, camera_matrix, np.zeros(5, dtype=np.float64)
    )
    difference = projected.reshape(-1, 2) - np.asarray(image_points).reshape(-1, 2)
    return float(np.sqrt(np.mean(np.sum(difference**2, axis=1))))


def _solve_tag_pose(
    observation: TagObservation,
    object_points: np.ndarray,
    image_points: np.ndarray,
    camera_matrix: np.ndarray,
    no_distortion: np.ndarray,
) -> TagPose:
    """Löst eine Tag-Pose aus bereits vorbereiteten Punkten und Kameradaten."""
    import cv2

    flags = getattr(cv2, "SOLVEPNP_IPPE_SQUARE", None)
    if flags is not None and hasattr(cv2, "solvePnPGeneric"):
        found, rvecs, tvecs, errors = cv2.solvePnPGeneric(
            object_points, image_points, camera_matrix, no_distortion, flags=flags
        )
        if not found:
            raise ValueError(f"Keine Pose fuer Tag {observation.tag_id} loesbar")
        # IPPE liefert beide Kandidaten ohne Nachschaerfung. Beide mit
        # Levenberg-Marquardt verfeinern und den mit der kleineren
        # Reprojektion nehmen -- wie `boards.estimate_board_pose`.
        candidates = []
        for candidate_rvec, candidate_tvec in zip(rvecs, tvecs):
            candidate_rvec = np.array(candidate_rvec, dtype=np.float64)
            candidate_tvec = np.array(candidate_tvec, dtype=np.float64)
            if hasattr(cv2, "solvePnPRefineLM"):
                candidate_rvec, candidate_tvec = cv2.solvePnPRefineLM(
                    object_points,
                    image_points,
                    camera_matrix,
                    no_distortion,
                    candidate_rvec,
                    candidate_tvec,
                )
            candidates.append(
                (
                    _reprojection_error_px(
                        object_points, image_points, candidate_rvec, candidate_tvec, camera_matrix
                    ),
                    candidate_rvec,
                    candidate_tvec,
                )
            )
        _, rvec, tvec = min(candidates, key=lambda candidate: candidate[0])
        ambiguity = 0.0
        if errors is not None and len(errors) >= 2:
            # Das Verhaeltnis kommt bewusst aus den **rohen** IPPE-Fehlern, nicht
            # aus den verfeinerten: Nach der Verfeinerung erklaeren beide Loesungen
            # ein Tag mit nur vier Ecken fast gleich gut, das Verhaeltnis stiege
            # ueber `AMBIGUITY_THRESHOLD` (0,6), und `measure_tag` verwuerfe
            # eindeutige Tags. Die Schwelle ist am rohen Verhaeltnis gemessen.
            best = float(np.asarray(errors).reshape(-1)[0])
            second = float(np.asarray(errors).reshape(-1)[1])
            # In (0, 1]: nahe 1 heisst, beide Loesungen erklaeren das Bild
            # gleich gut. Ein zweiter Fehler von 0 kommt nur vor, wenn auch der
            # erste 0 ist -- das waere eine entartete Anordnung.
            ambiguity = best / second if second > 1e-12 else 1.0
    else:  # pragma: no cover - sehr alte OpenCV-Version
        _log.debug("SOLVEPNP_IPPE_SQUARE nicht verfuegbar, weiche auf ITERATIVE aus")
        ok, rvec, tvec = cv2.solvePnP(
            object_points, image_points, camera_matrix, no_distortion
        )
        if not ok:
            raise ValueError(f"Keine Pose fuer Tag {observation.tag_id} loesbar")
        # Dieser Pfad liefert nur eine Loesung, die Mehrdeutigkeit ist also
        # unbekannt. Als sicherer Standard gilt "mehrdeutig" statt "eindeutig":
        # ein faelschlich markierter Tag kostet eine Pruefung, ein faelschlich
        # eindeutiger eine falsche Fahrt.
        ambiguity = 1.0

    error_px = _reprojection_error_px(
        object_points, image_points, rvec, tvec, camera_matrix
    )
    return TagPose(
        tag_id=observation.tag_id,
        pose_cam_tag=from_rvec_tvec(rvec, tvec),
        reprojection_error_px=error_px,
        ambiguity_ratio=ambiguity,
        observation=observation,
        attributes={"sideLengthPx": observation.side_length_px()},
    )


def estimate_tag_pose(
    observation: TagObservation, size_m: float, calibration: CameraCalibration
) -> TagPose:
    """Schaetzt `T_cam_tag` fuer einen einzelnen Tag."""
    return _solve_tag_pose(
        observation,
        tag_object_points(size_m),
        undistort_corners(observation.corners, calibration),
        np.asarray(calibration.camera_matrix, dtype=np.float64),
        np.zeros(5, dtype=np.float64),
    )


def estimate_tag_poses(
    observations: Sequence[TagObservation],
    calibration: CameraCalibration,
    *,
    tag_map: Any = None,
    default_size_m: float = 0.05,
    max_reprojection_error_px: float | None = None,
) -> list[TagPose]:
    """Schaetzt die Posen aller gefundenen Tags.

    Die Tag-Groesse kommt **je Tag aus der Karte**, nicht als globale
    Konstante: Welttafel- und Modul-Tags sind verschieden gross, und eine
    falsche Groesse skaliert den Abstand linear, ohne sonst aufzufallen.

    Tags ueber `max_reprojection_error_px` werden verworfen und protokolliert,
    nicht stumm durchgereicht.
    """
    import cv2

    from . import lens

    expected_errors = _solver_exceptions()
    if not observations:
        return []
    camera_matrix = np.asarray(calibration.camera_matrix, dtype=np.float64)
    no_distortion = np.zeros(5, dtype=np.float64)
    object_points_by_size: dict[float, np.ndarray] = {}
    prepared: list[tuple[TagObservation, np.ndarray, np.ndarray]] = []
    for observation in observations:
        size_m = (
            tag_map.size_for(observation.tag_id, default_size_m)
            if tag_map is not None
            else default_size_m
        )
        try:
            corners = np.asarray(observation.corners, dtype=np.float64).reshape(4, 1, 2)
        except expected_errors:
            _log.info("Pose fuer Tag %d nicht loesbar", observation.tag_id, exc_info=True)
            continue
        object_points = object_points_by_size.get(size_m)
        if object_points is None:
            object_points = tag_object_points(size_m)
            object_points_by_size[size_m] = object_points
        prepared.append((observation, object_points, corners))
    if not prepared:
        return []

    # OpenCV kann alle Ecken eines Bildes in einem Aufruf entzerren. Bei einem
    # defekten Punkt bleibt der bisherige Einzel-Tag-Fehlerpfad erhalten.
    try:
        points = np.concatenate([item[2] for item in prepared])
        undistorted = lens.undistort_points(points, calibration).reshape(-1, 4, 2)
    except cv2.error:
        undistorted = None

    poses: list[TagPose] = []
    for index, (observation, object_points, corners) in enumerate(prepared):
        try:
            image_points = (
                undistorted[index]
                if undistorted is not None
                else lens.undistort_points(corners, calibration).reshape(4, 2)
            )
            tag_pose = _solve_tag_pose(
                observation, object_points, image_points, camera_matrix, no_distortion
            )
        except expected_errors:
            _log.info("Pose fuer Tag %d nicht loesbar", observation.tag_id, exc_info=True)
            continue
        if not tag_pose.is_usable(max_reprojection_error_px):
            _log.info(
                "Tag %d verworfen: Reprojektionsfehler %s px (Schranke %s px)",
                tag_pose.tag_id,
                f"{tag_pose.reprojection_error_px:.2f}",
                max_reprojection_error_px,
            )
            continue
        poses.append(tag_pose)
    return poses


def _solver_exceptions() -> tuple[type[BaseException], ...]:
    """Liefert die Ausnahmen, die eine einzelne Beobachtung plausibel ausloesen kann.

    Bewusst eng: ein `AttributeError` oder `TypeError` waere ein Fehler im
    Code und kaehme fuer jeden Tag in jedem Bild wieder -- eine wiederholte
    Logzeile statt eines klaren Absturzes. Solche Fehler sollen durchlaufen.
    """
    try:
        import cv2

        return (ValueError, cv2.error)
    except Exception:
        return (ValueError,)


@dataclass(frozen=True)
class JointCameraPose:
    """`T_cam_world` aus einem PnP ueber alle Ecken mehrerer Welttags."""

    pose_cam_world: Pose
    #: RMS ueber alle Ecken aller beteiligten Tags, Pixel.
    reprojection_error_px: float
    #: Je Tag der RMS seiner vier Ecken unter der gemeinsamen Pose.
    tag_errors_px: dict[int, float]
    tag_ids: tuple[int, ...]


def solve_camera_from_world_tags(
    tag_poses: Sequence[TagPose],
    world_poses: dict[int, Pose],
    sizes_m: dict[int, float],
    calibration: CameraCalibration,
    initial_pose_cam_world: Pose | None = None,
) -> JointCameraPose | None:
    """Kamerapose aus **allen** Ecken aller Welttags in einem PnP.

    Je Tag ein eigenes PnP aus vier Ecken auf 20 cm Basis ist aus 2-3 m
    Abstand in der Neigung schlecht bestimmt -- 1 Grad Kippfehler sind am
    Boden 5 cm. Das Mittel solcher Einzelschaetzungen erbt das. Ein PnP ueber
    die Ecken aller Welttags hat dagegen die ganze Zelle als Basis.

    Die Ecken werden wie beim Einzel-Tag entzerrt (Linsenmodell aus
    `tagloc.lens`) und dann mit idealem Pinhole geloest. Startwerte: die
    uebergebene Schaetzung (typisch das Mittel der Einzel-Tags) und
    `SOLVEPNP_SQPNP` bzw. `IPPE`; jede wird mit Levenberg-Marquardt
    verfeinert, die mit dem kleineren Fehler gewinnt.

    Mehrdeutige Tags zaehlen **mit**: die Mehrdeutigkeit gehoert zur
    Einzel-Tag-Loesung aus vier Ecken (zwei Kippstellungen erklaeren sie
    gleich gut), nicht zu den Ecken selbst. Gerade ein frontal von oben
    gesehener Tag ist oft "mehrdeutig" -- im gemeinsamen PnP ueber alle Ecken
    verschwindet das. Tags ohne Weltpose bleiben aussen vor. Unter zwei Tags
    `None` -- dann gibt es nichts zu verbinden.
    """
    import cv2

    used = [
        tag_pose
        for tag_pose in sorted(tag_poses, key=lambda item: item.tag_id)
        if tag_pose.tag_id in world_poses and tag_pose.observation is not None
    ]
    if len(used) < 2:
        return None

    object_points, image_points = _world_correspondences(used, world_poses, sizes_m, calibration)
    objects = np.concatenate(object_points).reshape(-1, 1, 3)
    images = np.concatenate(image_points).reshape(-1, 1, 2)
    camera_matrix = np.asarray(calibration.camera_matrix, dtype=np.float64)
    no_distortion = np.zeros(5, dtype=np.float64)

    starts = _pnp_starts(objects, images, camera_matrix, no_distortion, initial_pose_cam_world)
    if not starts:
        return None

    best = None
    for rvec, tvec in starts:
        rvec, tvec = cv2.solvePnPRefineLM(
            objects, images, camera_matrix, no_distortion, rvec.copy(), tvec.copy()
        )
        error = _reprojection_error_px(objects, images, rvec, tvec, camera_matrix)
        if best is None or error < best[0]:
            best = (error, rvec, tvec)
    error, rvec, tvec = best

    tag_errors = {}
    for index, tag_pose in enumerate(used):
        tag_errors[int(tag_pose.tag_id)] = _reprojection_error_px(
            object_points[index], image_points[index], rvec, tvec, camera_matrix
        )
    _log.debug("Kamerapose gemeinsam aus %d Welttags, RMS %.2f px", len(used), error)
    return JointCameraPose(
        pose_cam_world=from_rvec_tvec(rvec, tvec),
        reprojection_error_px=error,
        tag_errors_px=tag_errors,
        tag_ids=tuple(int(tag_pose.tag_id) for tag_pose in used),
    )


def _world_correspondences(
    used: Sequence[TagPose],
    world_poses: dict[int, Pose],
    sizes_m: dict[int, float],
    calibration: CameraCalibration,
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Je Tag die vier Ecken im Welt-KS und die entzerrten Bildecken, in gleicher Reihenfolge."""
    object_points = []
    image_points = []
    for tag_pose in used:
        corners_tag = tag_object_points(sizes_m[tag_pose.tag_id])
        corners_world = [
            compose(world_poses[tag_pose.tag_id], _translation(corner))[:3, 3]
            for corner in corners_tag
        ]
        object_points.append(np.asarray(corners_world, dtype=np.float64))
        image_points.append(undistort_corners(tag_pose.observation.corners, calibration))
    return object_points, image_points


def _pnp_starts(
    objects: np.ndarray,
    images: np.ndarray,
    camera_matrix: np.ndarray,
    no_distortion: np.ndarray,
    initial_pose_cam_world: Pose | None,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Startwerte `(rvec, tvec)` fuer das gemeinsame PnP.

    Zuerst die uebergebene Schaetzung, dann die erste Loesung von `SQPNP` bzw.
    `IPPE`, die OpenCV liefert. Leer, wenn keine zustande kommt.
    """
    import cv2

    starts = []
    if initial_pose_cam_world is not None:
        start = np.asarray(initial_pose_cam_world, dtype=np.float64)
        starts.append((rvec_from_rotation(start[:3, :3]).reshape(3, 1), start[:3, 3].reshape(3, 1)))
    for name in ("SOLVEPNP_SQPNP", "SOLVEPNP_IPPE"):
        flag = getattr(cv2, name, None)
        if flag is None:
            continue
        try:
            found, rvec, tvec = cv2.solvePnP(objects, images, camera_matrix, no_distortion, flags=flag)
        except cv2.error:
            continue
        if found:
            starts.append((rvec, tvec))
            break
    return starts


def _translation(point) -> Pose:
    pose = np.eye(4, dtype=np.float64)
    pose[:3, 3] = point
    return pose


def poses_by_tag(tag_poses: Sequence[TagPose]) -> dict[int, Pose]:
    """Liefert `{tag_id: T_cam_tag}`, das Eingabeformat von `tagmap.place_tags`."""
    return {tag_pose.tag_id: tag_pose.pose_cam_tag for tag_pose in tag_poses}


__all__ = [
    "JointCameraPose",
    "TagPose",
    "estimate_tag_pose",
    "estimate_tag_poses",
    "poses_by_tag",
    "solve_camera_from_world_tags",
    "tag_object_points",
    "undistort_corners",
]

"""AprilTag detection over the camera shared with the livestream.

One source for both cameras. The ceiling camera and the eye-in-hand camera
use the same class and code; they differ only in the `AprilTagProfileConfig`
that `vision_server/server.py` sets per Pi, and in what they get to see:

* if world tags from the map are in the image, the camera pose is derived
  from them, and every module pose is reported in the world frame together
  with the world tag it sits closest to
* otherwise everything stays in the camera frame, and `frameConvention`
  tells the reader where +Z points

**Only the world tags are fixed** -- four of them on the border of the cell.
The robot is a module like any other: it carries its own tags and gets
measured, not read from a file. The ceiling camera is the only one that sees
the robot and the world tags at the same time, so it is the one that answers
which world tag the robot stands closest to. The eye-in-hand camera then
localises itself against that tag optically -- no hand-eye calibration
needed for that step.

The actual computation lives in `tagloc`; this file is the OPC-UA adapter.
"""

import asyncio
import logging
import math
import time
from typing import Any

from ..camera import SharedCamera
from ..errors import VisionErrorCode, VisionJobError
from ..profiles import AprilTagProfileConfig, CameraStreamConfig
from .base import Detection, DetectionRequest, DetectionSource

_log = logging.getLogger(__name__)

POLL_INTERVAL_S = 0.02

#: Frame id -> which of the two cameras this is. Mirrors the frames that
#: `vision_server.server.PI_IDENTITIES` assigns per Pi. Deliberately plain
#: strings and not `tagloc.localize.SOURCE_*`: this module must stay
#: importable without numpy, so `tagloc` is only imported inside methods.
#: An unknown frame yields an empty source, which `merge_locations` then
#: ranks below both known cameras instead of guessing.
SOURCE_BY_FRAME = {"cam_ceiling": "ceiling", "cam_flange": "flange"}


def robot_pose_from_parameters(parameters) -> Any | None:
    """Return `T_base_flansch` from the job parameters, or `None`.

    Sieben Floats: `(x, y, z, qx, qy, qz, qw)` -- Meter und Quaternion xyzw wie
    ueberall im Projekt. Der Roboter steht waehrend der Aufnahmen still, also
    gilt eine Pose fuer den ganzen Job; ein Zeitstempel-Abgleich mit dem
    einzelnen Frame eruebrigt sich damit.

    Fehlt der Parameter oder ist er unbrauchbar, kommt `None` zurueck und der
    Anker bleibt ungenutzt. Es wird nicht geraten -- eine falsche Roboterpose
    verschiebt jede Modulpose, ohne dass man es dem Ergebnis ansieht.
    """
    if not parameters or len(parameters) < 7:
        return None
    try:
        values = [float(value) for value in parameters[:7]]
    except (TypeError, ValueError):
        _log.warning("Roboterpose im Job ist nicht numerisch: %r", parameters[:7])
        return None
    if not all(math.isfinite(value) for value in values):
        _log.warning("Roboterpose im Job enthaelt NaN/Inf: %r", values)
        return None

    from tagloc.geometry import from_position_quaternion

    norm = math.sqrt(sum(value * value for value in values[3:]))
    if norm < 1e-9:
        _log.warning("Roboterpose im Job hat ein Quaternion der Laenge 0")
        return None
    return from_position_quaternion(values[:3], values[3:])


def _finite_or_none(value: float | None) -> float | None:
    """Map NaN and infinity to `None` before they reach the payload.

    `payload._dumps` serialises with `allow_nan=False` and raises on NaN --
    so a single outlier wouldn't just drop the attribute, it would end the
    whole job as INTERNAL. `None` is the honest answer here: "this value
    isn't available".
    """
    if value is None:
        return None
    number = float(value)
    return None if math.isnan(number) or math.isinf(number) else round(number, 4)


class AprilTagDetectionSource(DetectionSource):
    """Locate modules by their AprilTags."""

    profile_id = "apriltag"
    is_simulated = False

    def __init__(
        self,
        config: AprilTagProfileConfig,
        camera_config: CameraStreamConfig | None = None,
        *,
        camera: SharedCamera | None = None,
        detector: Any = None,
        calibration: Any = None,
        tag_map: Any = None,
    ) -> None:
        self._config = config
        self._camera_config = camera_config or CameraStreamConfig()
        #: Passed in from outside when `camera_stream.py` needs the same
        #: handle (see `runner.py`).
        self.camera = camera if camera is not None else SharedCamera(self._camera_config)
        #: Injectable so tests can run without OpenCV and without files.
        self._detector = detector
        self._calibration = calibration
        self._tag_map = tag_map
        #: Camera pose of the last evaluated image, set by `_locate`. `None`
        #: until the first image, and again whenever no world tag was visible
        #: and no anchor could carry it.
        self._localization = None
        #: Hand-Auge, einmal kalibriert -- die Kamera sitzt starr am Roboter.
        self._hand_eye = None
        #: Wo die Roboterbasis im Welt-KS steht. Wird beim ersten Welttag im
        #: Bild gesetzt und traegt danach ueber alle Module hinweg, auch wenn
        #: kein Welttag mehr zu sehen ist. Lebt bewusst ueber Jobs hinweg:
        #: geankert wird einmal je Lokalisierungsvorgang, nicht je Modul.
        self._anchor = None
        #: Letzte gemessene Drift gegen den Anker, `None` solange ungeprueft.
        self._drift = None
        self.frame_id = config.frame_id
        self.frame_convention = config.frame_convention
        self.configuration_id = self._build_configuration_id()

    # -- Resources -----------------------------------------------------------

    def _build_configuration_id(self) -> str:
        """Combine tag family, calibration and map into one string.

        Later answers which calibration and map were active behind a bad
        pose (doc/altlasten.md C4). Deliberately needs no OpenCV and doesn't
        read the files.
        """
        from tagloc.identity import calibration_identity, tag_map_identity

        return (
            f"{self._config.tag_family}@{calibration_identity(self._config.calibration_path)}"
            f"+{tag_map_identity(self._config.tag_map_path)}"
        )

    async def open(self) -> None:
        """Load calibration and map, build the detector, open the camera.

        If calibration is missing, the source doesn't open and the server
        stays in Preoperational -- intentional, since a vision server
        without calibration would report meaningless numbers. The one
        exception is `allow_placeholder_calibration`: a deliberate, temporary
        bypass to run detection before the real calibration run exists.
        """
        from tagloc.calibration import default_calibration, load_calibration
        from tagloc.detector import build_detector
        from tagloc.tagmap import empty_tag_map, load_tag_map, validate_tag_map

        if self._calibration is None:
            try:
                self._calibration = await self.run_blocking(
                    load_calibration, self._config.calibration_path
                )
            except FileNotFoundError:
                if not self._config.allow_placeholder_calibration:
                    raise
                _log.warning(
                    "Keine Kalibrierung unter %s -- verwende Platzhalter-Intrinsik "
                    "(allow_placeholder_calibration=True). Posen sind NICHT masshaltig.",
                    self._config.calibration_path,
                )
                self._calibration = default_calibration(
                    self._config.resolution, frame_id=self._config.frame_id
                )
        if self._tag_map is None:
            path = self._config.tag_map_path
            if path is not None and path.is_file():
                self._tag_map = await self.run_blocking(load_tag_map, path)
                # Reported, not enforced: a cell still being built must be
                # allowed to measure. Silence here would mean a missing world
                # tag only ever shows up as quietly worse accuracy.
                for problem in validate_tag_map(self._tag_map):
                    _log.warning("Tag-Map %s: %s", path, problem)
            else:
                _log.warning(
                    "Keine Tag-Map unter %s -- Posen bleiben im Kamera-KS und "
                    "Tags werden ohne Modulzuordnung gemeldet",
                    path,
                )
                self._tag_map = empty_tag_map()
        if self._hand_eye is None and self._config.hand_eye_path is not None:
            from tagloc.handeye import load_hand_eye

            try:
                self._hand_eye = await self.run_blocking(
                    load_hand_eye, self._config.hand_eye_path
                )
                _log.info(
                    "Hand-Auge geladen: %s (Streuung %.2f mm / %.3f deg ueber %d Posen)",
                    self._config.hand_eye_path,
                    (self._hand_eye.rms_position_m or 0.0) * 1000.0,
                    self._hand_eye.rms_rotation_deg or 0.0,
                    self._hand_eye.sample_count,
                )
            except FileNotFoundError:
                # Kein Fehler: die Deckenkamera hat keine, und der Hand-Pi darf
                # auch ohne messen -- dann eben nur mit Welttag im Bild.
                _log.warning(
                    "Keine Hand-Auge-Kalibrierung unter %s -- ohne Welttag im Bild "
                    "bleiben die Posen im Kamera-KS",
                    self._config.hand_eye_path,
                )

        if self._detector is None:
            self._detector = await self.run_blocking(
                build_detector, self._config.tag_family, self._config.detector_backend
            )
        self.configuration_id = self._build_configuration_id()
        await self.camera.open()

    async def close(self) -> None:
        await self.camera.close()
        await self.shutdown_executor()

    # -- Capture ---------------------------------------------------------------

    async def _next_frame(self, seen_timestamp: float | None):
        """Wait for a frame newer than the last one processed.

        Its own timeout, because the runner's job timeout can't kill a
        stuck worker thread (see `base.run_blocking`).
        """
        deadline = time.monotonic() + self._config.capture_timeout_s
        while time.monotonic() < deadline:
            frame = self.camera.latest_frame
            if frame is not None and frame.timestamp != seen_timestamp:
                return frame
            await asyncio.sleep(POLL_INTERVAL_S)
        raise VisionJobError(
            VisionErrorCode.DETECTION_FAILED,
            f"Kein Kamerabild innerhalb von {self._config.capture_timeout_s:.1f} s",
        )

    def _locate(self, image, pose_base_flange=None) -> list:
        """Evaluate one image. Runs in the worker thread, never on the loop."""
        from tagloc import frames as frame_tools
        from tagloc.calibration import check_resolution, scale_to_resolution
        from tagloc.localize import (
            anchor_drift,
            anchor_from_localization,
            locate_modules,
            localize_camera,
            localize_camera_from_anchor,
        )
        from tagloc.pose import estimate_tag_poses

        size = frame_tools.image_size(image)
        calibration = self._calibration
        if tuple(calibration.image_size) != tuple(size):
            if not self._config.allow_resolution_mismatch:
                check_resolution(calibration, size)
            calibration = scale_to_resolution(calibration, size)

        tag_poses = estimate_tag_poses(
            self._detector.detect(frame_tools.to_gray(image)),
            calibration,
            tag_map=self._tag_map,
            default_size_m=self._config.tag_size_m,
            max_reprojection_error_px=self._config.max_reproj_error_px,
        )
        localization = localize_camera(
            tag_poses,
            self._tag_map,
            max_reprojection_error_px=self._config.max_reproj_error_px,
        )

        # Der optische Wert gewinnt immer, wenn ein Welttag im Bild liegt --
        # er ist die Messung, alles andere ist Fortschreibung. Liegt keiner
        # im Bild, traegt der Anker: genau der Fall zwischen zwei Modulen.
        if self._hand_eye is not None and pose_base_flange is not None:
            if localization is not None:
                if self._anchor is not None:
                    self._drift = anchor_drift(
                        self._anchor, localization, pose_base_flange, self._hand_eye
                    )
                self._anchor = anchor_from_localization(
                    localization, pose_base_flange, self._hand_eye
                )
            elif self._anchor is not None:
                localization = localize_camera_from_anchor(
                    self._anchor, pose_base_flange, self._hand_eye
                )

        # Kept for `acquire_and_detect`, which reports the world tags and
        # their spread alongside the poses. Only ever written from the single
        # worker thread, so no lock is needed.
        self._localization = localization
        frame_id = self._tag_map.frame_id if localization is not None else self._config.frame_id
        return locate_modules(
            tag_poses,
            self._tag_map,
            localization=localization,
            frame_id=frame_id,
            source=SOURCE_BY_FRAME.get(self._config.frame_id, ""),
            max_reprojection_error_px=self._config.max_reproj_error_px,
        )

    async def acquire_and_detect(self, request: DetectionRequest) -> list[Detection]:
        """Capture several images, average the poses, and report the modules."""
        from tagloc.geometry import to_position_quaternion
        from tagloc.localize import expected_but_missing, merge_by_module, merge_samples

        pose_base_flange = robot_pose_from_parameters(request.parameters)
        self._drift = None

        samples: list[list] = []
        seen_timestamp: float | None = None
        for _ in range(max(1, self._config.samples_per_job)):
            frame = await self._next_frame(seen_timestamp)
            seen_timestamp = frame.timestamp
            samples.append(
                await self.run_blocking(self._locate, frame.image, pose_base_flange)
            )

        located = merge_samples(samples)
        if not located:
            raise VisionJobError(
                VisionErrorCode.DETECTION_FAILED,
                f"Kein AprilTag der Familie '{self._config.tag_family}' gefunden",
            )

        # Before merging by module: `expected_but_missing` compares tag ids,
        # and merging several robot tags into one result would make the tags
        # it swallowed look missing.
        missing = expected_but_missing(self._tag_map, located)
        if missing:
            _log.info("Erwartet, aber nicht gefunden: %s", ", ".join(missing))

        # Several tags on the robot measure one and the same base pose.
        located = merge_by_module(located, tag_map=self._tag_map)

        # The frame follows what was actually computed: with reference tags
        # the world frame, without them the camera frame.
        self.frame_id = located[0].frame_id or self._config.frame_id

        localization = self._localization
        detections: list[Detection] = []
        for location in located:
            position, orientation = to_position_quaternion(location.pose)
            attributes = {
                "tagId": location.tag_id,
                "reprojErrorPx": _finite_or_none(location.reprojection_error_px),
                "ambiguous": location.ambiguous,
                "sampleCount": location.attributes.get("sampleCount", len(samples)),
                "frameTimestamp": seen_timestamp,
                "recipeId": request.recipe_id,
                "source": location.source,
                "role": location.role,
            }
            if location.attributes.get("tagCount"):
                # Several tags measured this module -- say which, so a wrong
                # CAD offset on one of them can be tracked down.
                attributes["tagIds"] = location.attributes["tagIds"]
                attributes["tagCount"] = location.attributes["tagCount"]
            if location.reference_tag_id >= 0:
                attributes["referenceTagId"] = location.reference_tag_id
            if location.pose_in_reference_tag is not None:
                reference_position, reference_orientation = to_position_quaternion(
                    location.pose_in_reference_tag
                )
                attributes["referencePosition"] = reference_position
                attributes["referenceOrientation"] = reference_orientation
                attributes["referenceDistanceM"] = _finite_or_none(
                    math.dist(reference_position, (0.0, 0.0, 0.0))
                )
            if localization is not None:
                attributes["worldTagIds"] = list(localization.world_tag_ids)
                attributes["cameraSpreadM"] = _finite_or_none(localization.spread_m)
                attributes["cameraSpreadDeg"] = _finite_or_none(
                    math.degrees(localization.spread_rad)
                )
                # Ob die Kamerapose gemessen oder fortgeschrieben wurde, ist
                # kein Detail: die beiden haben nicht dieselbe Genauigkeit.
                attributes["cameraPoseOrigin"] = localization.origin
            if self._anchor is not None:
                attributes["anchorWorldTagId"] = self._anchor.world_tag_id
            if self._drift is not None:
                drift_m, drift_rad = self._drift
                attributes["anchorDriftM"] = _finite_or_none(drift_m)
                attributes["anchorDriftDeg"] = _finite_or_none(math.degrees(drift_rad))
            if missing:
                attributes["missingModules"] = ", ".join(missing)
            detections.append(
                Detection(
                    module_id=location.module_id,
                    instance_id=location.instance_id,
                    position=position,
                    orientation=orientation,
                    confidence=_finite_or_none(location.confidence) or 0.0,
                    attributes=attributes,
                )
            )
        return detections

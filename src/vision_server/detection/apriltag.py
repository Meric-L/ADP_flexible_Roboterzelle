"""AprilTag detection over the camera shared with the livestream.

One source for both layers. Layer 1 (ceiling camera) and Layer 2 (flange
camera) use the same class and code; they differ only in the
`AprilTagProfileConfig` that `OPCUA/server.py` sets per Pi, and in where
`T_world_cam` comes from:

* if reference tags from the map are in the image (world board, robot
  table), the camera pose is derived from them and the module pose is
  reported in the world frame
* otherwise everything stays in the camera frame, and `frameConvention`
  tells the reader where +Z points. The backend then does the chaining
  with the robot pose -- the vision server doesn't know the robot and
  shouldn't guess it.

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
        without calibration would report meaningless numbers.
        """
        from tagloc.calibration import load_calibration
        from tagloc.detector import build_detector
        from tagloc.tagmap import empty_tag_map, load_tag_map

        if self._calibration is None:
            self._calibration = await self.run_blocking(
                load_calibration, self._config.calibration_path
            )
        if self._tag_map is None:
            path = self._config.tag_map_path
            if path is not None and path.is_file():
                self._tag_map = await self.run_blocking(load_tag_map, path)
            else:
                _log.warning(
                    "Keine Tag-Map unter %s -- Posen bleiben im Kamera-KS und "
                    "Tags werden ohne Modulzuordnung gemeldet",
                    path,
                )
                self._tag_map = empty_tag_map()
        if self._detector is None:
            self._detector = await self.run_blocking(
                build_detector, self._config.tag_family, self._config.detector_backend
            )
        self.configuration_id = self._build_configuration_id()
        await self.camera.open()

    async def close(self) -> None:
        await self.camera.close()
        await self.shutdown_executor()

    # -- Calibration ------------------------------------------------------------

    @property
    def calibration_path(self):
        """Where this camera's calibration lives -- the remote calibration writes here."""
        return self._config.calibration_path

    @property
    def calibration_frame_id(self) -> str:
        """The camera's own frame, recorded in the calibration file."""
        return self._config.frame_id

    @property
    def calibration(self) -> Any:
        """The calibration in use, or `None` before the source opened."""
        return self._calibration

    async def reload_calibration(self) -> None:
        """Swap in the calibration file as it is now on disk.

        For a source that is already open (after a recalibration). The new
        object replaces the old one in one assignment; a job running right
        now keeps the reference it started with.
        """
        from tagloc.calibration import load_calibration

        self._calibration = await self.run_blocking(
            load_calibration, self._config.calibration_path
        )
        self.configuration_id = self._build_configuration_id()

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

    def _locate(self, image) -> list:
        """Evaluate one image. Runs in the worker thread, never on the loop."""
        from tagloc import frames as frame_tools
        from tagloc.calibration import check_resolution, scale_to_resolution
        from tagloc.localize import camera_pose_from_reference_tags, locate_modules
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
        pose_world_cam = camera_pose_from_reference_tags(tag_poses, self._tag_map)
        frame_id = self._tag_map.frame_id if pose_world_cam is not None else self._config.frame_id
        return locate_modules(
            tag_poses,
            self._tag_map,
            pose_world_cam=pose_world_cam,
            frame_id=frame_id,
            max_reprojection_error_px=self._config.max_reproj_error_px,
        )

    async def acquire_and_detect(self, request: DetectionRequest) -> list[Detection]:
        """Capture several images, average the poses, and report the modules."""
        from tagloc.geometry import to_position_quaternion
        from tagloc.localize import expected_but_missing, merge_samples

        samples: list[list] = []
        seen_timestamp: float | None = None
        for _ in range(max(1, self._config.samples_per_job)):
            frame = await self._next_frame(seen_timestamp)
            seen_timestamp = frame.timestamp
            samples.append(await self.run_blocking(self._locate, frame.image))

        located = merge_samples(samples)
        if not located:
            raise VisionJobError(
                VisionErrorCode.DETECTION_FAILED,
                f"Kein AprilTag der Familie '{self._config.tag_family}' gefunden",
            )

        missing = expected_but_missing(self._tag_map, located)
        if missing:
            _log.info("Erwartet, aber nicht gefunden: %s", ", ".join(missing))

        # The frame follows what was actually computed: with reference tags
        # the world frame, without them the camera frame.
        self.frame_id = located[0].frame_id or self._config.frame_id

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
            }
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

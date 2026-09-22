"""Tests for the AprilTag detection source (level 3: server without a camera).

The source is never `open()`ed; detector, calibration and tag map are set
directly. So no test needs hardware, OpenCV or a calibration file.
"""

import tempfile
import unittest
from collections import deque
from dataclasses import replace
from pathlib import Path

from vision_server.camera import CameraFrame
from vision_server.detection.apriltag import AprilTagDetectionSource
from vision_server.detection.base import DetectionRequest
from vision_server.errors import VisionErrorCode, VisionJobError
from vision_server.profiles import AprilTagProfileConfig

try:
    import numpy as np

    from tagloc import geometry, tagmap
    from tagloc.observations import TagObservation, TagPose
except Exception:  # numpy broken in this environment
    np = None
    geometry = None
    tagmap = None
    TagObservation = None
    TagPose = None

SQUARE = ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0))


class FakeCamera:
    """Provide prepared images via `latest_frame`, without hardware.

    Each call returns the next queued image with a new timestamp -- exactly
    what `_next_frame` waits for. Once nothing is left, the last frame stays
    and the source runs into its capture timeout.
    """

    def __init__(self) -> None:
        self._pending: deque = deque()
        self._latest: CameraFrame | None = None
        self.opened = 0
        self.closed = 0
        self.consumed: list = []

    async def open(self) -> None:
        self.opened += 1

    async def close(self) -> None:
        self.closed += 1

    def push_frame(self, image) -> None:
        self._pending.append(image)

    @property
    def latest_frame(self) -> CameraFrame | None:
        if self._pending:
            image = self._pending.popleft()
            self.consumed.append(image)
            self._latest = CameraFrame(image=image, timestamp=float(len(self.consumed)))
        return self._latest


class FakeDetector:
    """Provide a fixed list of observations with their associated poses.

    Runs in `run_blocking`'s worker thread; both methods just return
    pre-determined values and need no locking.
    """

    def __init__(self, tag_poses=()) -> None:
        self._tag_poses = list(tag_poses)
        self.images: list = []

    def detect(self, image):
        self.images.append(image)
        return [tag_pose.observation for tag_pose in self._tag_poses]

    def estimate(self, observations):
        return list(self._tag_poses)


class FakeSolverSource(AprilTagDetectionSource):
    """Replace, in `_locate`, only the step that needs OpenCV.

    `tagloc.pose.estimate_tag_pose` solves the pose via `cv2.solvePnPGeneric`;
    the fake detector already knows it instead. Everything after -- camera
    pose from reference tags, module chaining, frame selection -- is still
    production code.
    """

    def _locate(self, image):
        from tagloc.localize import camera_pose_from_reference_tags, locate_modules

        tag_poses = self._detector.estimate(self._detector.detect(image))
        pose_world_cam = camera_pose_from_reference_tags(tag_poses, self._tag_map)
        frame_id = self._tag_map.frame_id if pose_world_cam is not None else self._config.frame_id
        return locate_modules(
            tag_poses,
            self._tag_map,
            pose_world_cam=pose_world_cam,
            frame_id=frame_id,
            max_reprojection_error_px=self._config.max_reproj_error_px,
        )


def profile_config(folder: Path, **overrides) -> AprilTagProfileConfig:
    config = AprilTagProfileConfig(
        calibration_path=folder / "camera.json",
        tag_map_path=folder / "tagmap.json",
        capture_timeout_s=0.05,
        samples_per_job=3,
    )
    return replace(config, **overrides) if overrides else config


def tag_pose(tag_id: int, translation, *, error_px: float = 0.5, ambiguity: float = 0.0):
    return TagPose(
        tag_id=tag_id,
        pose_cam_tag=geometry.from_rvec_tvec((0.0, 0.0, 0.0), translation),
        reprojection_error_px=error_px,
        ambiguity_ratio=ambiguity,
        observation=TagObservation(tag_id=tag_id, corners=SQUARE),
    )


def make_source(folder: Path, *, tag_poses=(), tag_map=None, **overrides):
    """Build a source with a fake camera and fake detector; never `open()`ed."""
    camera = FakeCamera()
    detector = FakeDetector(tag_poses)
    source = FakeSolverSource(profile_config(folder, **overrides), camera=camera)
    source._detector = detector
    source._calibration = object()  # untouched without the cv2 path
    source._tag_map = tag_map if tag_map is not None else tagmap.empty_tag_map()
    return source, camera, detector


class ConfigurationIdTest(unittest.TestCase):
    """The `configurationId` must be set before anything is loaded."""

    def test_names_family_calibration_and_tag_map_before_open(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "camera.json").write_text("{}", encoding="utf-8")
            source = AprilTagDetectionSource(
                profile_config(path), camera=FakeCamera()
            )

            configuration_id = source.configuration_id

        self.assertTrue(configuration_id.startswith("tag36h11@"))
        self.assertIn("camera#", configuration_id)
        self.assertIn("+tagmap#", configuration_id)

    def test_marks_a_missing_calibration_and_tag_map(self):
        with tempfile.TemporaryDirectory() as folder:
            source = AprilTagDetectionSource(
                profile_config(Path(folder)), camera=FakeCamera()
            )

        self.assertIn("camera#fehlt", source.configuration_id)
        self.assertIn("tagmap#fehlt", source.configuration_id)

    def test_reports_operation_without_a_tag_map(self):
        with tempfile.TemporaryDirectory() as folder:
            source = AprilTagDetectionSource(
                profile_config(Path(folder), tag_map_path=None), camera=FakeCamera()
            )

        self.assertTrue(source.configuration_id.endswith("+tagmap#keine"))


class ApplyCalibrationTest(unittest.TestCase):
    """`apply_calibration` -- der Hot-Reload-Pfad nach einer interaktiven
    Kalibrierung (siehe `CalibrationSession`/`runner.py`), ohne Server-Neustart."""

    def test_replaces_the_calibration_object(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "camera.json").write_text("{}", encoding="utf-8")
            source = AprilTagDetectionSource(profile_config(path), camera=FakeCamera())
            new_calibration = object()

            source.apply_calibration(new_calibration)

        self.assertIs(source._calibration, new_calibration)

    def test_rebuilds_the_configuration_id_from_the_current_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "camera.json").write_text("{}", encoding="utf-8")
            source = AprilTagDetectionSource(profile_config(path), camera=FakeCamera())

            source.apply_calibration(object())

        self.assertIn("camera#", source.configuration_id)


class ProfileSettingsTest(unittest.TestCase):
    def test_takes_frame_id_and_convention_from_the_profile_config(self):
        with tempfile.TemporaryDirectory() as folder:
            config = profile_config(
                Path(folder), frame_id="cam_flansch", frame_convention="z_up_x_forward"
            )
            source = AprilTagDetectionSource(config, camera=FakeCamera())

        self.assertEqual(source.frame_id, "cam_flansch")
        self.assertEqual(source.frame_convention, "z_up_x_forward")

    def test_is_a_real_camera_source(self):
        with tempfile.TemporaryDirectory() as folder:
            source = AprilTagDetectionSource(profile_config(Path(folder)), camera=FakeCamera())

        self.assertFalse(source.is_simulated)
        self.assertEqual(source.profile_id, "apriltag")


class CaptureTimeoutTest(unittest.IsolatedAsyncioTestCase):
    async def test_fails_with_detection_failed_without_any_frame(self):
        with tempfile.TemporaryDirectory() as folder:
            source = AprilTagDetectionSource(
                profile_config(Path(folder)), camera=FakeCamera()
            )

            with self.assertRaises(VisionJobError) as caught:
                await source._next_frame(None)
            await source.close()

        self.assertEqual(caught.exception.code, VisionErrorCode.DETECTION_FAILED)
        self.assertIn("Kein Kamerabild", caught.exception.message)

    async def test_fails_when_the_frame_does_not_change(self):
        with tempfile.TemporaryDirectory() as folder:
            camera = FakeCamera()
            camera.push_frame("bild-1")
            source = AprilTagDetectionSource(profile_config(Path(folder)), camera=camera)

            frame = await source._next_frame(None)
            with self.assertRaises(VisionJobError) as caught:
                await source._next_frame(frame.timestamp)
            await source.close()

        self.assertEqual(caught.exception.code, VisionErrorCode.DETECTION_FAILED)


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class AcquireAndDetectTest(unittest.IsolatedAsyncioTestCase):
    async def test_fails_with_detection_failed_when_no_tag_is_found(self):
        with tempfile.TemporaryDirectory() as folder:
            source, camera, _ = make_source(Path(folder))
            for index in range(3):
                camera.push_frame(f"bild-{index}")

            with self.assertRaises(VisionJobError) as caught:
                await source.acquire_and_detect(DetectionRequest(job_id="job-000001"))
            await source.close()

        self.assertEqual(caught.exception.code, VisionErrorCode.DETECTION_FAILED)
        self.assertIn("tag36h11", caught.exception.message)

    async def test_fails_with_detection_failed_when_the_camera_stalls(self):
        with tempfile.TemporaryDirectory() as folder:
            source, camera, _ = make_source(
                Path(folder), tag_poses=[tag_pose(5, (0.1, 0.2, 1.0))]
            )
            camera.push_frame("bild-0")  # too few images for samples_per_job=3

            with self.assertRaises(VisionJobError) as caught:
                await source.acquire_and_detect(DetectionRequest(job_id="job-000001"))
            await source.close()

        self.assertEqual(caught.exception.code, VisionErrorCode.DETECTION_FAILED)

    async def test_consumes_one_frame_per_sample(self):
        with tempfile.TemporaryDirectory() as folder:
            source, camera, detector = make_source(
                Path(folder), tag_poses=[tag_pose(5, (0.1, 0.2, 1.0))], samples_per_job=2
            )
            for index in range(4):
                camera.push_frame(f"bild-{index}")

            await source.acquire_and_detect(DetectionRequest(job_id="job-000001"))
            await source.close()

        self.assertEqual(camera.consumed, ["bild-0", "bild-1"])
        self.assertEqual(detector.images, ["bild-0", "bild-1"])

    async def test_fills_the_payload_attributes(self):
        with tempfile.TemporaryDirectory() as folder:
            source, camera, _ = make_source(
                Path(folder),
                tag_poses=[tag_pose(5, (0.1, 0.2, 1.0), error_px=0.75, ambiguity=0.9)],
            )
            for index in range(3):
                camera.push_frame(f"bild-{index}")

            [detection] = await source.acquire_and_detect(
                DetectionRequest(job_id="job-000001", recipe_id="apriltag")
            )
            await source.close()

        self.assertEqual(detection.attributes["tagId"], 5)
        self.assertAlmostEqual(detection.attributes["reprojErrorPx"], 0.75)
        self.assertTrue(detection.attributes["ambiguous"])
        self.assertEqual(detection.attributes["sampleCount"], 3)
        self.assertEqual(detection.attributes["recipeId"], "apriltag")
        self.assertEqual(detection.module_id, "TAG-5")

    async def test_counts_the_samples_the_pose_rests_on(self):
        with tempfile.TemporaryDirectory() as folder:
            source, camera, _ = make_source(
                Path(folder), tag_poses=[tag_pose(5, (0.1, 0.2, 1.0))], samples_per_job=4
            )
            for index in range(4):
                camera.push_frame(f"bild-{index}")

            [detection] = await source.acquire_and_detect(DetectionRequest(job_id="job-1"))
            await source.close()

        self.assertEqual(detection.attributes["sampleCount"], 4)

    async def test_reports_the_position_it_measured(self):
        with tempfile.TemporaryDirectory() as folder:
            source, camera, _ = make_source(
                Path(folder), tag_poses=[tag_pose(5, (0.1, -0.2, 1.5))]
            )
            for index in range(3):
                camera.push_frame(f"bild-{index}")

            [detection] = await source.acquire_and_detect(DetectionRequest(job_id="job-1"))
            await source.close()

        np.testing.assert_allclose(detection.position, (0.1, -0.2, 1.5), atol=1e-12)
        np.testing.assert_allclose(detection.orientation, (0.0, 0.0, 0.0, 1.0), atol=1e-12)


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class FrameOfReferenceTest(unittest.IsolatedAsyncioTestCase):
    def _world_map(self):
        return tagmap.TagMap(
            frame_id="world",
            entries={
                0: tagmap.TagEntry(
                    tag_id=0, role="world", size_m=0.10, pose_in_world=geometry.identity()
                ),
                5: tagmap.TagEntry(tag_id=5, role="module", size_m=0.04, module_id="MOD-A"),
            },
        )

    async def test_switches_to_the_world_frame_with_a_reference_tag(self):
        with tempfile.TemporaryDirectory() as folder:
            source, camera, _ = make_source(
                Path(folder),
                tag_poses=[tag_pose(0, (0.0, 0.0, 2.0)), tag_pose(5, (0.3, 0.0, 2.0))],
                tag_map=self._world_map(),
            )
            for index in range(3):
                camera.push_frame(f"bild-{index}")

            [detection] = await source.acquire_and_detect(DetectionRequest(job_id="job-1"))
            await source.close()

        self.assertEqual(source.frame_id, "world")
        self.assertEqual(detection.module_id, "MOD-A")
        # T_world_cam comes from tag 0; tag 5 sits 0.3 m to the side.
        np.testing.assert_allclose(detection.position, (0.3, 0.0, 0.0), atol=1e-12)

    async def test_stays_in_the_camera_frame_without_a_reference_tag(self):
        with tempfile.TemporaryDirectory() as folder:
            source, camera, _ = make_source(
                Path(folder),
                tag_poses=[tag_pose(5, (0.3, 0.0, 2.0))],
                tag_map=self._world_map(),
                frame_id="cam_ceiling",
            )
            for index in range(3):
                camera.push_frame(f"bild-{index}")

            [detection] = await source.acquire_and_detect(DetectionRequest(job_id="job-1"))
            await source.close()

        self.assertEqual(source.frame_id, "cam_ceiling")
        np.testing.assert_allclose(detection.position, (0.3, 0.0, 2.0), atol=1e-12)


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class OpenWithoutCalibrationTest(unittest.IsolatedAsyncioTestCase):
    """`open()` selbst, mit echtem `build_detector` (braucht cv2) aber einer
    `FakeCamera` statt echter Hardware -- die Kalibrierdatei existiert bei
    beiden Tests nicht."""

    def _config(self, *, allow_placeholder: bool) -> AprilTagProfileConfig:
        missing = Path(tempfile.mkdtemp()) / "does-not-exist.json"
        return AprilTagProfileConfig(
            calibration_path=missing,
            tag_map_path=None,
            frame_id="cam_ceiling",
            resolution=(640, 480),
            allow_placeholder_calibration=allow_placeholder,
        )

    async def test_fails_without_the_bypass(self):
        camera = FakeCamera()
        source = AprilTagDetectionSource(self._config(allow_placeholder=False), camera=camera)

        with self.assertRaises(FileNotFoundError):
            await source.open()

        self.assertEqual(camera.opened, 0, "Kamera darf bei fehlgeschlagenem open() nicht laufen")

    async def test_falls_back_to_a_placeholder_when_allowed(self):
        from tagloc.calibration import PLACEHOLDER_CALIBRATION_ID

        camera = FakeCamera()
        source = AprilTagDetectionSource(self._config(allow_placeholder=True), camera=camera)

        await source.open()

        self.assertEqual(source._calibration.calibration_id, PLACEHOLDER_CALIBRATION_ID)
        self.assertEqual(source._calibration.image_size, (640, 480))
        self.assertEqual(camera.opened, 1)
        await source.close()


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class CloseTest(unittest.IsolatedAsyncioTestCase):
    async def test_closes_the_camera_and_shuts_the_executor_down(self):
        with tempfile.TemporaryDirectory() as folder:
            source, camera, _ = make_source(
                Path(folder), tag_poses=[tag_pose(5, (0.0, 0.0, 1.0))], samples_per_job=1
            )
            camera.push_frame("bild-0")
            await source.acquire_and_detect(DetectionRequest(job_id="job-1"))
            self.assertIsNotNone(source._executor)

            await source.close()

        self.assertEqual(camera.closed, 1)
        self.assertIsNone(source._executor)

    async def test_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            source, camera, _ = make_source(Path(folder))

            await source.close()
            await source.close()

        self.assertEqual(camera.closed, 2)


if __name__ == "__main__":
    unittest.main()

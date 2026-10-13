"""Tests for the AprilTag detection source (level 3: server without a camera).

The source is never `open()`ed; detector, calibration and tag map are set
directly. So no test needs hardware, OpenCV or a calibration file.
"""

import json
import math
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
    pose from the world tags, module chaining, frame selection -- is still
    production code.
    """

    def _locate(self, image, pose_base_flange=None):
        from tagloc.localize import locate_modules, localize_camera

        from vision_server.detection.apriltag import SOURCE_BY_FRAME

        tag_poses = self._detector.estimate(self._detector.detect(image))
        localization = localize_camera(
            tag_poses,
            self._tag_map,
            max_reprojection_error_px=self._config.max_reproj_error_px,
        )
        self._localization = localization
        from vision_server.detection.apriltag import _observed_world_tags

        self._world_tag_sample = _observed_world_tags(tag_poses, self._tag_map, localization)
        frame_id = self._tag_map.frame_id if localization is not None else self._config.frame_id
        return locate_modules(
            tag_poses,
            self._tag_map,
            localization=localization,
            frame_id=frame_id,
            source=SOURCE_BY_FRAME.get(self._config.frame_id, ""),
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
        # Ohne Welt-KS keine Welttags und keine Kamerapose im Payload.
        self.assertEqual(source.world_tags, ())
        self.assertIsNone(source.camera_localization)


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class WorldTagsTest(unittest.IsolatedAsyncioTestCase):
    """`worldTags`: wo das Bild die Welttags sieht -- gegen die Karte pruefbar."""

    def _map(self):
        return tagmap.TagMap(
            frame_id="world",
            entries={
                0: tagmap.TagEntry(tag_id=0, role="world", size_m=0.1, pose_in_world=geometry.identity()),
                1: tagmap.TagEntry(
                    tag_id=1,
                    role="world",
                    size_m=0.1,
                    pose_in_world=geometry.from_rvec_tvec((0, 0, 0), (1.0, 0.0, 0.0)),
                ),
                5: tagmap.TagEntry(tag_id=5, role="module", size_m=0.04, module_id="MOD-A"),
            },
        )

    async def run_job(self, folder, tag_poses):
        source, camera, _ = make_source(Path(folder), tag_poses=tag_poses, tag_map=self._map())
        for index in range(3):
            camera.push_frame(f"bild-{index}")
        try:
            return source, await source.acquire_and_detect(DetectionRequest(job_id="job-1"))
        except VisionJobError as error:
            return source, error

    async def test_the_observed_world_poses_are_reported_beside_the_modules(self):
        with tempfile.TemporaryDirectory() as folder:
            # Die Kamera sieht Tag 1 zwei Zentimeter weiter weg als die Karte sagt.
            source, detections = await self.run_job(
                folder,
                [tag_pose(0, (0, 0, 2)), tag_pose(1, (1.02, 0, 2)), tag_pose(5, (0.3, 0, 2))],
            )
        self.assertEqual([d.module_id for d in detections], ["MOD-A"])  # unveraendert
        world = {tag["tagId"]: tag for tag in source.world_tags}
        self.assertEqual(sorted(world), [0, 1])
        distance = math.dist(world[0]["position"], world[1]["position"])
        self.assertAlmostEqual(distance, 1.02, places=9)  # gesehen, nicht eingemessen
        self.assertEqual(world[0]["sampleCount"], 3)
        self.assertFalse(world[0]["ambiguous"])
        self.assertAlmostEqual(world[0]["reprojectionErrorPx"], 0.5)
        json.dumps(source.world_tags, allow_nan=False)

    async def test_an_image_with_only_world_tags_fails_but_still_reports_them(self):
        with tempfile.TemporaryDirectory() as folder:
            source, error = await self.run_job(folder, [tag_pose(0, (0, 0, 2)), tag_pose(1, (1, 0, 2))])
        self.assertIsInstance(error, VisionJobError)
        self.assertEqual(error.code, VisionErrorCode.DETECTION_FAILED)
        self.assertEqual([tag["tagId"] for tag in source.world_tags], [0, 1])
        # Auch ohne Detektion: wie die Kamerapose zustande kam.
        summary = source.camera_localization
        self.assertEqual(summary["worldTagIds"], [0, 1])
        self.assertEqual(summary["solver"], "average")  # Fake ohne Kalibrierung
        json.dumps(summary, allow_nan=False)

    def test_an_anchored_pose_is_reported_as_anchor_without_an_error(self):
        from tagloc.localize import ORIGIN_ROBOT_POSE, CameraLocalization

        from vision_server.detection.apriltag import _localization_summary

        self.assertIsNone(_localization_summary(None))
        summary = _localization_summary(
            CameraLocalization(
                pose_world_cam=geometry.identity(),
                world_tag_ids=(),
                primary_tag_id=583,
                spread_m=0.002,
                origin=ORIGIN_ROBOT_POSE,
            )
        )
        self.assertEqual(summary["solver"], "anchor")
        self.assertIsNone(summary["reprojectionErrorPx"])
        self.assertEqual(summary["spreadM"], 0.002)
        self.assertEqual(summary["worldTagIds"], [])

    def test_the_joint_corner_error_is_averaged_per_tag(self):
        from vision_server.detection.apriltag import _merge_world_tags

        pose = geometry.identity()
        merged = _merge_world_tags(
            [[(583, pose, 0.4, False, 0.6)], [(583, pose, 0.6, False, 1.0)], [(584, pose, 0.5, False, None)]]
        )
        by_id = {tag["tagId"]: tag for tag in merged}
        self.assertAlmostEqual(by_id[583]["jointReprojectionErrorPx"], 0.8)
        self.assertAlmostEqual(by_id[583]["reprojectionErrorPx"], 0.5)
        # Ohne gemeinsame Pose (ein Welttag allein) fehlt das Feld.
        self.assertNotIn("jointReprojectionErrorPx", by_id[584])

    async def test_a_new_job_does_not_report_the_old_world_tags(self):
        with tempfile.TemporaryDirectory() as folder:
            source, _ = await self.run_job(folder, [tag_pose(0, (0, 0, 2)), tag_pose(5, (0.3, 0, 2))])
            self.assertEqual(len(source.world_tags), 1)
            # Kein Bild mehr: der Job scheitert an der Kamera.
            with self.assertRaises(VisionJobError):
                await source.acquire_and_detect(DetectionRequest(job_id="job-2"))
        self.assertEqual(source.world_tags, ())


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class OpenMitKaputterTagMapTest(unittest.IsolatedAsyncioTestCase):
    """Eine unbrauchbare Tag-Map darf die Erkennung nicht stilllegen.

    Vorher warf `load_tag_map` bei einer Karte im alten Schema, `open()` liess
    die Ausnahme durch, und der ganze Vision-Server blieb Preoperational --
    wegen einer Textdatei. Jetzt laeuft er ohne Karte weiter und meldet die
    Tags als `TAG-<id>`, damit das Frontend seine Platzhalter bekommt.
    """

    async def _open_with(self, text: str):
        folder = Path(tempfile.mkdtemp())
        tag_map_path = folder / "tagmap.json"
        tag_map_path.write_text(text, encoding="utf-8")
        config = replace(
            profile_config(folder),
            tag_map_path=tag_map_path,
            frame_id="cam_ceiling",
            resolution=(640, 480),
            allow_placeholder_calibration=True,
        )
        camera = FakeCamera()
        source = AprilTagDetectionSource(config, camera=camera)
        source._detector = FakeDetector([])
        await source.open()
        return source

    async def test_survives_an_unreadable_tag_map(self):
        source = await self._open_with("{ kein gueltiges json")
        try:
            self.assertEqual(source._tag_map.entries, {})
        finally:
            await source.close()

    async def test_survives_an_unknown_schema(self):
        source = await self._open_with(json.dumps({"schema": "falsch/9", "tags": []}))
        try:
            self.assertEqual(source._tag_map.entries, {})
        finally:
            await source.close()

    async def test_says_why_it_continues_without_a_map(self):
        with self.assertLogs("vision_server.detection.apriltag", level="ERROR") as logs:
            source = await self._open_with("{ kaputt")
            await source.close()

        zusammen = "\n".join(logs.output)
        self.assertIn("TAG-", zusammen)


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


class FakeMeasureSource(AprilTagDetectionSource):
    """Wie `FakeSolverSource`, nur fuer `measure_tag`: die Pose kennt der
    Fake-Detektor, alles andere ist Produktionscode."""

    def _measure_tag(self, image, tag_id):
        tag_poses = self._detector.estimate(self._detector.detect(image))
        return next((pose for pose in tag_poses if pose.tag_id == tag_id), None)


@unittest.skipUnless(np is not None, "numpy fehlt")
class MeasureTagTest(unittest.IsolatedAsyncioTestCase):
    """`measure_tag` fuer die Hand-Auge-Kalibrierung: Kamera-KS, ohne Karte."""

    def source(self, folder, tag_poses):
        camera = FakeCamera()
        source = FakeMeasureSource(profile_config(Path(folder)), camera=camera)
        source._detector = FakeDetector(tag_poses)
        source._tag_map = tagmap.empty_tag_map()
        for _ in range(3):
            camera.push_frame(object())
        return source, camera

    async def test_the_tag_pose_is_averaged_over_the_job_samples(self):
        with tempfile.TemporaryDirectory() as folder:
            source, camera = self.source(folder, [tag_pose(12, (0.1, 0.0, 0.5)), tag_pose(3, (0, 0, 1))])
            pose, reason = await source.measure_tag(12)
        self.assertEqual(reason, "")
        np.testing.assert_allclose(pose[:3, 3], [0.1, 0.0, 0.5])
        self.assertEqual(len(camera.consumed), 3)

    async def test_missing_and_ambiguous_tags_give_a_reason(self):
        with tempfile.TemporaryDirectory() as folder:
            source, _ = self.source(folder, [tag_pose(3, (0, 0, 1))])
            pose, reason = await source.measure_tag(12)
        self.assertIsNone(pose)
        self.assertIn("nicht gesehen", reason)

        with tempfile.TemporaryDirectory() as folder:
            source, _ = self.source(folder, [tag_pose(12, (0, 0, 1), ambiguity=0.95)])
            pose, reason = await source.measure_tag(12)
        self.assertIsNone(pose)
        self.assertIn("mehrdeutig", reason)

    async def test_no_camera_frame_is_a_reason_not_a_crash(self):
        with tempfile.TemporaryDirectory() as folder:
            source = FakeMeasureSource(profile_config(Path(folder)), camera=FakeCamera())
            source._detector = FakeDetector()
            pose, reason = await source.measure_tag(12)
        self.assertIsNone(pose)
        self.assertIn("Kamerabild", reason)

    def test_a_new_hand_eye_drops_the_anchor(self):
        with tempfile.TemporaryDirectory() as folder:
            source, _, _ = make_source(Path(folder))
        source._anchor = object()
        source.apply_hand_eye("neu")
        self.assertEqual(source._hand_eye, "neu")
        self.assertIsNone(source._anchor)


if __name__ == "__main__":
    unittest.main()

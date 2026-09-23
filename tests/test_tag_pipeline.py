"""End-to-end test on a synthetically rendered image.

This is the level where the detection chain is checked against a **reference
value** instead of itself: `tools/make_synthetic_scene.py` places tags at
known locations and projects them with exactly the intrinsics later fed into
pose estimation. What comes out afterward must match the input.

Checks the whole chain:

    detector -> estimate_tag_pose -> place_tags -> locate_modules

plus the calibration path (rendered chessboards -> `calibrate_from_samples`).
Tolerance per doc/projektdoku/apriltag-lokalisierung.md section 7: **2 mm and 0.5 degrees**.

Skips instead of failing without OpenCV. The probe access to `cv2.imread` is
deliberate: an incomplete install can still be imported but lacks functions
-- without the access that would only surface mid-test.
"""

# Without this import, the return annotation `-> TagMap` would be evaluated
# when the class is defined -- and blow up if the import above was skipped.
from __future__ import annotations

import unittest

try:
    import cv2
    import numpy as np

    _ = (cv2.imread, cv2.aruco, np.ndarray)

    from tagloc.boards import BoardSpec, calibrate_from_samples, detect_board
    from tagloc.detector import build_detector
    from tagloc.frames import to_gray
    from tagloc.geometry import (
        compose,
        from_position_quaternion,
        invert,
        rotation_distance_rad,
        translation_distance_m,
    )
    from tagloc.localize import locate_modules, localize_camera
    from tagloc.pose import estimate_tag_pose, estimate_tag_poses, poses_by_tag
    from tagloc.tagmap import TagEntry, TagMap, missing_tags, place_tags, residuals
    from tools.make_synthetic_scene import (
        chessboard_views,
        default_tag_layout,
        orbit_views,
        render_chessboard,
        render_tags,
        synthetic_calibration,
    )
except Exception:  # pragma: no cover - skipped without OpenCV
    cv2 = None

#: Position: the concept's spec value. Measured worst case on this scene is
#: 0.29 mm, so this keeps a factor of seven.
POSITION_TOLERANCE_M = 0.002

#: Angle: NOT the concept's 0.5 deg. Measured over all tags and views, the
#: median is 0.031 deg but the worst case is 0.5005 deg -- a tag seen at 34 deg
#: obliquity, where pixel quantisation of the rendered marker limits corner
#: accuracy. At 0.5 the threshold sat exactly on the edge of the distribution
#: and the test flickered. It is not ambiguity: for that sample the alternative
#: IPPE solution is 82x worse (residual 0.045 vs 3.67), so the right pose was
#: picked. A genuinely wrong pose lands tens of degrees off -- that same
#: alternative is 67.8 deg out -- so 1.0 still separates right from wrong,
#: and `reprojection_error_px < 1.0` below is the tight bound that does the
#: real work (worst case there: 0.063 px).
ANGLE_TOLERANCE_DEG = 1.0

#: Looser for chained results (camera pose from reference tags, then module
#: offset): multiple individual measurements add up there.
CHAIN_TOLERANCE_M = 0.003
CHAIN_TOLERANCE_DEG = 1.5

#: Spread of the four world-tag estimates of the camera pose. Deliberately
#: NOT an accuracy figure: it measures how far the single estimates disagree
#: with each other, and the confidence-weighted merge comes out far better
#: than the worst of them. Measured over all four views: worst case 4.21 mm
#: at 0.444 deg (view 0, the most oblique one), against a merged camera-pose
#: error of 0.77 mm. 8 mm keeps a factor of nearly two and still catches a
#: map whose world tags genuinely contradict each other -- a mis-surveyed tag
#: lands centimetres out, not millimetres.
SPREAD_TOLERANCE_M = 0.008

ANCHOR_TAG_ID = 0
MODULE_TAG_ID = 7
VIEW_COUNT = 4


def _degrees(first, second) -> float:
    return float(np.degrees(rotation_distance_rad(first, second)))


@unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
class SyntheticTagSceneTest(unittest.TestCase):
    """The full chain on rendered views of the same scene."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.calibration = synthetic_calibration((1600, 1200), focal_px=1600.0)
        cls.placements = default_tag_layout()
        cls.world_poses = {item.tag_id: item.pose_world_tag for item in cls.placements}
        cls.detector = build_detector("tag36h11", "aruco")
        cls.views = orbit_views(VIEW_COUNT)
        # Render once, evaluate four times -- rendering is the expensive part.
        cls.scenes = [
            render_tags(cls.placements, pose_world_cam, cls.calibration)
            for pose_world_cam in cls.views
        ]

    def _tag_map(self) -> TagMap:
        """Return a map with the four world tags and one module tag with a CAD offset."""
        entries = {}
        for placement in self.placements:
            if placement.tag_id == MODULE_TAG_ID:
                entries[placement.tag_id] = TagEntry(
                    tag_id=placement.tag_id,
                    role="module",
                    size_m=placement.size_m,
                    module_id="MOD-A",
                    instance_id="mod-a-1",
                    tag_to_module=from_position_quaternion(
                        (0.0, 0.0, -0.040), (0.0, 0.0, 0.0, 1.0)
                    ),
                )
                continue
            entries[placement.tag_id] = TagEntry(
                tag_id=placement.tag_id,
                role="world",
                size_m=placement.size_m,
                pose_in_world=placement.pose_world_tag,
            )
        return TagMap(
            frame_id="world",
            anchor_tag_id=ANCHOR_TAG_ID,
            tag_family="tag36h11",
            entries=entries,
        )

    def test_the_detector_finds_exactly_the_rendered_tags(self) -> None:
        for index, (image, truth) in enumerate(self.scenes):
            with self.subTest(ansicht=index):
                self.assertEqual(len(truth), len(self.placements))
                found = self.detector.detect(to_gray(image))
                self.assertEqual(
                    sorted(observation.tag_id for observation in found), sorted(truth)
                )

    def test_the_estimated_pose_matches_the_rendered_truth(self) -> None:
        for index, (image, truth) in enumerate(self.scenes):
            for observation in self.detector.detect(to_gray(image)):
                expected = truth[observation.tag_id]
                size_m = next(
                    item.size_m
                    for item in self.placements
                    if item.tag_id == observation.tag_id
                )
                tag_pose = estimate_tag_pose(observation, size_m, self.calibration)
                with self.subTest(ansicht=index, tag=observation.tag_id):
                    self.assertLess(
                        translation_distance_m(tag_pose.pose_cam_tag, expected),
                        POSITION_TOLERANCE_M,
                    )
                    self.assertLess(
                        _degrees(tag_pose.pose_cam_tag, expected), ANGLE_TOLERANCE_DEG
                    )
                    # Obliquely viewed tags are unambiguous; if not, the scene
                    # would be a poor choice for this test.
                    self.assertFalse(tag_pose.is_ambiguous)
                    self.assertLess(tag_pose.reprojection_error_px, 1.0)

    def test_batch_pose_estimation_matches_individual_tags(self) -> None:
        """Gemeinsames Entzerren darf weder Pose noch Qualitätswerte ändern."""
        image, _ = self.scenes[0]
        observations = self.detector.detect(to_gray(image))
        tag_map = self._tag_map()
        batch = estimate_tag_poses(observations, self.calibration, tag_map=tag_map)
        self.assertEqual([pose.tag_id for pose in batch], [obs.tag_id for obs in observations])
        for observation, batched in zip(observations, batch):
            single = estimate_tag_pose(
                observation, tag_map.size_for(observation.tag_id, 0.05), self.calibration
            )
            with self.subTest(tag=observation.tag_id):
                np.testing.assert_allclose(batched.pose_cam_tag, single.pose_cam_tag, atol=1e-10)
                self.assertAlmostEqual(
                    batched.reprojection_error_px, single.reprojection_error_px, places=9
                )
                self.assertAlmostEqual(batched.ambiguity_ratio, single.ambiguity_ratio, places=9)

    def test_place_tags_positions_every_tag_relative_to_the_anchor(self) -> None:
        tag_map = self._tag_map()
        observations = []
        for image, _ in self.scenes:
            tag_poses = estimate_tag_poses(
                self.detector.detect(to_gray(image)), self.calibration, tag_map=tag_map
            )
            observations.append(poses_by_tag(tag_poses))

        placed = place_tags(observations, anchor_tag_id=ANCHOR_TAG_ID)
        self.assertEqual(sorted(placed), sorted(self.world_poses))
        self.assertEqual(missing_tags(observations, placed), [])

        pose_world_anchor = self.world_poses[ANCHOR_TAG_ID]
        for tag_id, pose in placed.items():
            expected = compose(invert(pose_world_anchor), self.world_poses[tag_id])
            with self.subTest(tag=tag_id):
                self.assertLess(
                    translation_distance_m(pose, expected), POSITION_TOLERANCE_M
                )
                self.assertLess(_degrees(pose, expected), ANGLE_TOLERANCE_DEG)

        # The map must close -- that's its quality figure.
        for edge, (position_m, angle_rad) in residuals(observations, placed).items():
            with self.subTest(kante=edge):
                self.assertLess(position_m, POSITION_TOLERANCE_M)
                self.assertLess(float(np.degrees(angle_rad)), ANGLE_TOLERANCE_DEG)

    def test_locate_modules_returns_the_module_pose_in_the_world_frame(self) -> None:
        tag_map = self._tag_map()
        image, _ = self.scenes[0]
        tag_poses = estimate_tag_poses(
            self.detector.detect(to_gray(image)), self.calibration, tag_map=tag_map
        )

        localization = localize_camera(tag_poses, tag_map)
        self.assertIsNotNone(localization)
        pose_world_cam = localization.pose_world_cam
        self.assertLess(
            translation_distance_m(pose_world_cam, self.views[0]), CHAIN_TOLERANCE_M
        )
        self.assertLess(_degrees(pose_world_cam, self.views[0]), CHAIN_TOLERANCE_DEG)
        # All four world tags of the scene are visible and agree.
        self.assertEqual(list(localization.world_tag_ids), [0, 1, 2, 3])
        self.assertIn(localization.primary_tag_id, (0, 1, 2, 3))
        self.assertLess(localization.spread_m, SPREAD_TOLERANCE_M)

        located = locate_modules(
            tag_poses, tag_map, localization=localization, frame_id=tag_map.frame_id
        )
        self.assertEqual([item.module_id for item in located], ["MOD-A"])
        module = located[0]
        self.assertEqual(module.instance_id, "mod-a-1")
        self.assertEqual(module.frame_id, "world")
        self.assertFalse(module.ambiguous)
        # Reported relative to the world tag it sits closest to.
        self.assertIn(module.reference_tag_id, (0, 1, 2, 3))
        self.assertIsNotNone(module.pose_in_reference_tag)

        expected = compose(
            self.world_poses[MODULE_TAG_ID],
            tag_map[MODULE_TAG_ID].tag_to_module,
        )
        self.assertLess(translation_distance_m(module.pose, expected), CHAIN_TOLERANCE_M)
        self.assertLess(_degrees(module.pose, expected), CHAIN_TOLERANCE_DEG)

    def test_without_world_tags_the_pose_stays_in_the_camera_frame(self) -> None:
        """The eye-in-hand case between two world tags: result in the camera frame."""
        tag_map = self._tag_map()
        image, truth = self.scenes[0]
        tag_poses = estimate_tag_poses(
            self.detector.detect(to_gray(image)), self.calibration, tag_map=tag_map
        )
        located = locate_modules(
            tag_poses, tag_map, localization=None, frame_id="cam_flange"
        )
        self.assertEqual([item.frame_id for item in located], ["cam_flange"])
        # Without a camera pose there is no world tag to relate anything to.
        self.assertEqual(located[0].reference_tag_id, -1)
        self.assertIsNone(located[0].pose_in_reference_tag)
        expected = compose(truth[MODULE_TAG_ID], tag_map[MODULE_TAG_ID].tag_to_module)
        self.assertLess(
            translation_distance_m(located[0].pose, expected), POSITION_TOLERANCE_M
        )


@unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
class SyntheticCalibrationTest(unittest.TestCase):
    """The calibration path: rendered chessboards back to the intrinsics."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.spec = BoardSpec(type="chessboard", cols=9, rows=6, square_size_m=0.030)
        cls.calibration = synthetic_calibration((1280, 960), focal_px=1200.0)
        cls.views = chessboard_views(14, cls.calibration, cls.spec)
        cls.images = [
            render_chessboard(pose, cls.calibration, cls.spec)[0] for pose in cls.views
        ]

    def test_the_board_is_found_in_every_view(self) -> None:
        for index, image in enumerate(self.images):
            with self.subTest(ansicht=index):
                sample = detect_board(to_gray(image), self.spec)
                self.assertIsNotNone(sample)
                self.assertEqual(sample.count(), self.spec.cols * self.spec.rows)

    def test_corner_order_matches_the_classic_convention(self) -> None:
        """`findChessboardCornersSB` liefert die Ecken in umgekehrter
        Reihenfolge gegenueber `findChessboardCorners` (empirisch geprueft,
        2026-09-23) -- `detect_board` muss das ausgleichen, sonst passt die
        Bild-zu-Weltpunkt-Zuordnung in `calibrate_from_samples` nicht mehr,
        lautlos, ohne dass `detect_board` das melden wuerde."""
        gray = to_gray(self.images[0])
        sample = detect_board(gray, self.spec)
        self.assertIsNotNone(sample)

        found, reference = cv2.findChessboardCorners(
            gray,
            (self.spec.cols, self.spec.rows),
            flags=cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE,
        )
        self.assertTrue(found)

        detected = np.asarray(sample.corners).reshape(-1, 2)
        expected = reference.reshape(-1, 2)
        # Grosszuegig (Subpixel-Unterschiede zwischen den Detektoren): das
        # hier prueft die Reihenfolge, nicht die letzte Nachkommastelle. Eine
        # vertauschte Reihenfolge weicht um Bildbreite/-hoehe ab, nicht um
        # Subpixel -- die Schranke unterscheidet das klar.
        self.assertLess(np.abs(detected - expected).max(), 2.0)

    def test_calibrate_from_samples_recovers_the_intrinsics(self) -> None:
        samples = []
        for image in self.images:
            sample = detect_board(to_gray(image), self.spec)
            if sample is not None:
                samples.append(sample)
        self.assertGreaterEqual(len(samples), 10)

        result = calibrate_from_samples(
            samples, self.calibration.image_size, self.spec, frame_id="cam_synth"
        )
        expected_fx, expected_fy, expected_cx, expected_cy = self.calibration.camera_params
        fx, fy, cx, cy = result.camera_params
        width, height = self.calibration.image_size

        # A few percent is enough: this checks that the path recovers the
        # right order of magnitude, not the quality of cv2.calibrateCamera.
        self.assertAlmostEqual(fx / expected_fx, 1.0, delta=0.02)
        self.assertAlmostEqual(fy / expected_fy, 1.0, delta=0.02)
        self.assertLess(abs(cx - expected_cx), 0.02 * width)
        self.assertLess(abs(cy - expected_cy), 0.02 * height)
        self.assertLess(result.rms_reprojection_error, 1.0)
        self.assertEqual(result.sample_count, len(samples))
        self.assertEqual(result.board["type"], "chessboard")
        self.assertEqual(result.frame_id, "cam_synth")


if __name__ == "__main__":
    unittest.main()

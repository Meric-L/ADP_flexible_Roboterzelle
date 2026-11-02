"""Kamerapose gemeinsam aus allen Welttag-Ecken statt Mittel der Einzel-Tags.

Synthetische Szene wie in der Zelle: vier Welttags (19,8 cm) an den Ecken von
etwa 1,6 x 1,6 m, die Kamera 2,5 m darueber, leicht schraeg. Die Ecken
werden mit dem Linsenmodell projiziert und verrauscht; danach laeuft die
echte Kette `estimate_tag_poses` -> `localize_camera`.
"""

import math
import unittest

try:
    import cv2
    import numpy as np

    from tagloc import calibration as calib
    from tagloc import geometry, lens
    from tagloc.localize import SOLVER_AVERAGE, SOLVER_JOINT, SOLVER_SINGLE, localize_camera
    from tagloc.observations import TagObservation
    from tagloc.pose import estimate_tag_poses, tag_object_points
    from tagloc.tagmap import TagEntry, TagMap
except Exception:  # cv2/numpy nicht verfuegbar
    cv2 = None

TAG_SIZE_M = 0.2
#: Die Welttags der Zelle (config/tagmap.json).
WORLD_TAGS = {583: (0.0, 0.0), 584: (1.593, -0.052), 585: (1.593, 1.618), 586: (0.0, 1.618)}
IMAGE_SIZE = (2028, 1520)
NOISE_PX = 0.5


def _calibration(model):
    distortion = (
        np.array([0.05, -0.01, 0.002, -0.0003]) if model == calib.FISHEYE else np.array([-0.2, 0.05, 0.0, 0.0, 0.0])
    )
    return calib.CameraCalibration(
        camera_matrix=np.array([[1100.0, 0.0, 1014.0], [0.0, 1100.0, 760.0], [0.0, 0.0, 1.0]]),
        distortion=distortion,
        image_size=IMAGE_SIZE,
        model=model,
    )


def _tag_map():
    return TagMap(
        frame_id="world",
        anchor_tag_id=583,
        entries={
            tag_id: TagEntry(
                tag_id,
                "world",
                TAG_SIZE_M,
                pose_in_world=geometry.from_rotation_translation(np.eye(3), (x, y, 0.0)),
            )
            for tag_id, (x, y) in WORLD_TAGS.items()
        },
    )


def _true_world_cam():
    """Ueber der Zellmitte, 2,5 m hoch, nach unten schauend, 4 Grad gekippt."""
    down = geometry.from_rvec_tvec((math.pi, 0.0, 0.0), (0.0, 0.0, 0.0))[:3, :3]
    tilt = geometry.from_rvec_tvec((math.radians(4.0), math.radians(-3.0), 0.2), (0, 0, 0))[:3, :3]
    return geometry.from_rotation_translation(down @ tilt, (0.8, 0.78, 2.5))


def _observations(pose_world_cam, calibration, rng):
    """Ecken aller Welttags, projiziert und verrauscht."""
    pose_cam_world = geometry.invert(pose_world_cam)
    observations = []
    for tag_id, entry in _tag_map().entries.items():
        pose_cam_tag = geometry.compose(pose_cam_world, entry.pose_in_world)
        rvec = geometry.rvec_from_rotation(pose_cam_tag[:3, :3])
        corners = lens.project_points(
            tag_object_points(TAG_SIZE_M), rvec, np.ascontiguousarray(pose_cam_tag[:3, 3]), calibration
        )
        corners = corners + rng.normal(0.0, NOISE_PX, corners.shape)
        observations.append(TagObservation(tag_id, tuple(map(tuple, corners))))
    return observations


@unittest.skipUnless(cv2 is not None, "cv2/numpy nicht verfuegbar")
class JointCameraPoseTest(unittest.TestCase):
    def _errors(self, model, trials=25):
        """Mittlerer Positions- und Winkelfehler: (gemeinsam, Mittel der Einzel-Tags)."""
        calibration = _calibration(model)
        tag_map = _tag_map()
        truth = _true_world_cam()
        rng = np.random.default_rng(7)
        joint_errors, average_errors = [], []
        for _ in range(trials):
            tag_poses = estimate_tag_poses(
                _observations(truth, calibration, rng), calibration, tag_map=tag_map
            )
            joint = localize_camera(tag_poses, tag_map, calibration=calibration)
            average = localize_camera(tag_poses, tag_map)
            self.assertEqual(joint.solver, SOLVER_JOINT)
            self.assertEqual(average.solver, SOLVER_AVERAGE)
            for result, bucket in ((joint, joint_errors), (average, average_errors)):
                bucket.append(
                    (
                        geometry.translation_distance_m(truth, result.pose_world_cam),
                        math.degrees(geometry.rotation_distance_rad(truth, result.pose_world_cam)),
                    )
                )
        return np.mean(joint_errors, axis=0), np.mean(average_errors, axis=0)

    def test_joint_is_clearly_more_accurate_than_the_average_pinhole(self):
        (joint_m, joint_deg), (average_m, average_deg) = self._errors(calib.PINHOLE)
        self.assertLess(joint_m, 0.5 * average_m)
        self.assertLess(joint_deg, 0.5 * average_deg)
        self.assertLess(joint_m, 0.01)

    def test_joint_is_clearly_more_accurate_than_the_average_fisheye(self):
        (joint_m, joint_deg), (average_m, average_deg) = self._errors(calib.FISHEYE)
        self.assertLess(joint_m, 0.5 * average_m)
        self.assertLess(joint_deg, 0.5 * average_deg)

    def test_quality_figures(self):
        calibration = _calibration(calib.PINHOLE)
        tag_map = _tag_map()
        tag_poses = estimate_tag_poses(
            _observations(_true_world_cam(), calibration, np.random.default_rng(1)),
            calibration,
            tag_map=tag_map,
        )
        localization = localize_camera(tag_poses, tag_map, calibration=calibration)
        self.assertEqual(sorted(localization.tag_errors_px), sorted(WORLD_TAGS))
        # Verrauscht mit 0,5 px: der RMS liegt in dieser Groessenordnung.
        self.assertLess(localization.reprojection_error_px, 1.5)
        self.assertGreater(localization.reprojection_error_px, 0.1)

    def test_a_single_world_tag_keeps_the_single_tag_path(self):
        calibration = _calibration(calib.PINHOLE)
        tag_map = _tag_map()
        tag_poses = estimate_tag_poses(
            _observations(_true_world_cam(), calibration, np.random.default_rng(1))[:1],
            calibration,
            tag_map=tag_map,
        )
        localization = localize_camera(tag_poses, tag_map, calibration=calibration)
        self.assertEqual(localization.solver, SOLVER_SINGLE)
        self.assertEqual(localization.tag_errors_px, {})
        self.assertAlmostEqual(
            localization.reprojection_error_px, tag_poses[0].reprojection_error_px
        )

    def test_a_shifted_world_tag_shows_up_in_its_own_error(self):
        """Steht ein Welttag 5 cm anders als die Karte sagt, faellt er unter
        der gemeinsamen Pose mit dem groessten Eckenfehler auf."""
        calibration = _calibration(calib.PINHOLE)
        tag_map = _tag_map()
        observations = _observations(_true_world_cam(), calibration, np.random.default_rng(3))
        shifted = TagMap(
            frame_id="world",
            entries={
                **tag_map.entries,
                585: TagEntry(
                    585,
                    "world",
                    TAG_SIZE_M,
                    pose_in_world=geometry.from_rotation_translation(np.eye(3), (1.643, 1.618, 0.0)),
                ),
            },
        )
        tag_poses = estimate_tag_poses(observations, calibration, tag_map=shifted)
        localization = localize_camera(tag_poses, shifted, calibration=calibration)
        worst = max(localization.tag_errors_px, key=localization.tag_errors_px.get)
        self.assertEqual(worst, 585)


if __name__ == "__main__":
    unittest.main()

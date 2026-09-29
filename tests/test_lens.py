"""Tests fuer `tagloc.lens`: Pinhole (Brown-Conrady) und Fisheye (Kannala-Brandt).

Alles synthetisch, ohne Kamera und ohne Bilddateien: Punkte werden mit einem
bekannten Modell ins Bild projiziert und muessen danach wiedergefunden werden
-- beim Entzerren, bei der Posenschaetzung und bei der Kalibrierung selbst.
"""

import unittest
from dataclasses import replace

try:
    import cv2
    import numpy as np

    from tagloc import calibration as calib
    from tagloc import lens
    from tagloc.boards import BoardSample, BoardSpec, calibrate_from_samples
    from tagloc.observations import TagObservation
    from tagloc.pose import estimate_tag_pose, estimate_tag_poses
except Exception:  # cv2/numpy nicht verfuegbar
    cv2 = None

#: Grob wie die HQ-Kamera an der Decke, auf ein handliches Format verkleinert.
IMAGE_SIZE = (1014, 760)
FISHEYE_D = np.array([0.08, -0.02, 0.004, -0.0006]) if cv2 is not None else None
PINHOLE_D = np.array([-0.21, 0.05, 0.001, -0.002, 0.01]) if cv2 is not None else None


def _camera_matrix():
    return np.array([[620.0, 0.0, 507.0], [0.0, 622.0, 380.0], [0.0, 0.0, 1.0]])


def _calibration(model):
    return calib.CameraCalibration(
        camera_matrix=_camera_matrix(),
        distortion=FISHEYE_D if model == calib.FISHEYE else PINHOLE_D,
        image_size=IMAGE_SIZE,
        model=model,
    )


def _grid_points():
    """Punkte vor der Kamera, bis weit an den Bildrand -- dort unterscheiden
    sich die Modelle am staerksten."""
    xs, ys = np.meshgrid(np.linspace(-0.6, 0.6, 7), np.linspace(-0.45, 0.45, 5))
    return np.stack([xs.ravel(), ys.ravel(), np.full(xs.size, 1.0)], axis=1)


@unittest.skipUnless(cv2 is not None, "cv2/numpy nicht verfuegbar")
class UndistortProjectTest(unittest.TestCase):
    def _assert_round_trip(self, model):
        calibration = _calibration(model)
        points = _grid_points()
        zero = np.zeros(3)
        distorted = lens.project_points(points, zero, zero, calibration)

        undistorted = lens.undistort_points(distorted, calibration)

        ideal, _ = cv2.projectPoints(points, zero, zero, _camera_matrix(), np.zeros(5))
        # 0,02 px: `cv2.undistortPoints` (Pinhole) iteriert standardmaessig nur
        # 5-mal und bleibt in den Bildecken bis ~0,01 px daneben -- das war
        # schon vor dem Fisheye-Modell so.
        np.testing.assert_allclose(undistorted, ideal.reshape(-1, 2), atol=0.02)

    def test_fisheye_round_trip_lands_on_the_ideal_pinhole_image(self):
        self._assert_round_trip(calib.FISHEYE)

    def test_pinhole_round_trip_lands_on_the_ideal_pinhole_image(self):
        self._assert_round_trip(calib.PINHOLE)

    def test_the_two_models_really_differ(self):
        """Sonst pruefte der Round-Trip oben nichts: gleiche Koeffizienten
        mit dem falschen Modell muessen sichtbar andere Punkte ergeben."""
        calibration = _calibration(calib.FISHEYE)
        points = _grid_points()
        zero = np.zeros(3)
        fisheye = lens.project_points(points, zero, zero, calibration)
        as_pinhole = lens.project_points(
            points,
            zero,
            zero,
            replace(calibration, model=calib.PINHOLE, distortion=np.append(FISHEYE_D, 0.0)),
        )
        self.assertGreater(np.abs(fisheye - as_pinhole).max(), 1.0)

    def test_fisheye_with_five_coefficients_is_rejected(self):
        calibration = replace(_calibration(calib.FISHEYE), distortion=PINHOLE_D)
        with self.assertRaises(ValueError):
            lens.undistort_points([[500.0, 380.0]], calibration)

    def test_unknown_model_is_rejected(self):
        with self.assertRaises(ValueError):
            lens.undistort_points([[500.0, 380.0]], replace(_calibration(calib.PINHOLE), model="x"))


@unittest.skipUnless(cv2 is not None, "cv2/numpy nicht verfuegbar")
class FisheyePoseTest(unittest.TestCase):
    """Eine Tag-Pose aus einem Fisheye-Bild: nur mit dem richtigen Modell
    stimmt die Entfernung."""

    TAG_SIZE_M = 0.10

    def _observation(self, calibration, tvec):
        half = self.TAG_SIZE_M / 2
        corners_3d = np.array(
            [[-half, half, 0.0], [half, half, 0.0], [half, -half, 0.0], [-half, -half, 0.0]]
        )
        # Leicht gekippt. Keine Drehung um pi: die kehrt den Umlaufsinn der
        # Ecken um, und IPPE_SQUARE loest dann eine gespiegelte Pose.
        rvec = np.array([0.2, -0.3, 0.05])
        corners = lens.project_points(corners_3d, rvec, tvec, calibration)
        return TagObservation(tag_id=7, corners=tuple(map(tuple, corners)))

    def test_recovers_the_tag_position_near_the_image_edge(self):
        calibration = _calibration(calib.FISHEYE)
        tvec = np.array([0.55, 0.35, 1.2])
        observation = self._observation(calibration, tvec)

        pose = estimate_tag_pose(observation, self.TAG_SIZE_M, calibration)

        np.testing.assert_allclose(np.asarray(pose.pose_cam_tag)[:3, 3], tvec, atol=2e-3)
        self.assertLess(pose.reprojection_error_px, 0.05)

    def test_batch_path_matches_the_single_tag_path(self):
        calibration = _calibration(calib.FISHEYE)
        tvec = np.array([-0.4, 0.2, 1.0])
        observation = self._observation(calibration, tvec)

        (pose,) = estimate_tag_poses(
            [observation], calibration, default_size_m=self.TAG_SIZE_M
        )

        np.testing.assert_allclose(np.asarray(pose.pose_cam_tag)[:3, 3], tvec, atol=2e-3)


@unittest.skipUnless(cv2 is not None, "cv2/numpy nicht verfuegbar")
class CalibrateFromSamplesTest(unittest.TestCase):
    """Die Kalibrierung findet ein bekanntes Modell aus synthetischen
    Schachbrett-Aufnahmen wieder."""

    SPEC = BoardSpec(type="chessboard", cols=7, rows=9, square_size_m=0.022)

    def _samples(self, calibration):
        grid = np.zeros((self.SPEC.cols * self.SPEC.rows, 3))
        grid[:, :2] = np.mgrid[0 : self.SPEC.cols, 0 : self.SPEC.rows].T.reshape(-1, 2)
        grid *= self.SPEC.square_size_m
        grid -= grid.mean(axis=0)
        rng = np.random.default_rng(29)
        samples = []
        while len(samples) < 20:
            rvec = rng.uniform(-0.5, 0.5, 3)
            tvec = np.array([rng.uniform(-0.12, 0.12), rng.uniform(-0.09, 0.09), 0.3])
            corners = lens.project_points(grid, rvec, tvec, calibration)
            inside = (
                (corners[:, 0] > 0)
                & (corners[:, 0] < IMAGE_SIZE[0])
                & (corners[:, 1] > 0)
                & (corners[:, 1] < IMAGE_SIZE[1])
            )
            if inside.all():
                samples.append(BoardSample(corners=corners.reshape(-1, 1, 2).astype(np.float32)))
        return samples

    def test_fisheye_recovers_intrinsics_and_coefficients(self):
        truth = _calibration(calib.FISHEYE)

        result = calibrate_from_samples(
            self._samples(truth), IMAGE_SIZE, self.SPEC, model=calib.FISHEYE, frame_id="cam_ceiling"
        )

        self.assertEqual(result.model, calib.FISHEYE)
        self.assertEqual(result.distortion.shape, (4,))
        np.testing.assert_allclose(result.camera_matrix, truth.camera_matrix, rtol=5e-3, atol=1.0)
        np.testing.assert_allclose(result.distortion, FISHEYE_D, atol=5e-3)
        self.assertLess(result.rms_reprojection_error, 0.05)

    def test_pinhole_has_exactly_five_coefficients(self):
        truth = _calibration(calib.PINHOLE)

        result = calibrate_from_samples(self._samples(truth), IMAGE_SIZE, self.SPEC)

        self.assertEqual(result.model, calib.PINHOLE)
        self.assertEqual(result.distortion.shape, (5,))
        np.testing.assert_allclose(result.camera_matrix, truth.camera_matrix, rtol=5e-3, atol=1.0)
        self.assertLess(result.rms_reprojection_error, 0.05)


@unittest.skipUnless(cv2 is not None, "cv2/numpy nicht verfuegbar")
class ScaleTest(unittest.TestCase):
    def test_scaling_keeps_the_fisheye_model(self):
        scaled = calib.scale_to_resolution(_calibration(calib.FISHEYE), (507, 380))

        self.assertEqual(scaled.model, calib.FISHEYE)
        np.testing.assert_allclose(scaled.distortion, FISHEYE_D)


@unittest.skipUnless(cv2 is not None, "cv2/numpy nicht verfuegbar")
class OverlayTest(unittest.TestCase):
    def test_draws_fisheye_axes_without_error(self):
        from tagloc.geometry import from_rvec_tvec
        from tagloc.observations import TagPose
        from tagloc.overlay import draw_tag_axes

        image = np.zeros((IMAGE_SIZE[1], IMAGE_SIZE[0], 3), dtype=np.uint8)
        pose = TagPose(
            tag_id=1, pose_cam_tag=from_rvec_tvec(np.array([np.pi, 0, 0]), np.array([0, 0, 1.0]))
        )

        draw_tag_axes(image, pose, _calibration(calib.FISHEYE), 0.05)

        self.assertGreater(int(image.sum()), 0)


if __name__ == "__main__":
    unittest.main()

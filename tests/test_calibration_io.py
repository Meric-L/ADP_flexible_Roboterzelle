"""Tests for calibration files, resolution checks and configuration identity.

Files are created in a tempdir, never in the repo.
"""

import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tagloc.identity import calibration_identity, file_identity, tag_map_identity

try:
    import numpy as np

    from tagloc import calibration as calib
except Exception:  # numpy broken in this environment
    np = None
    calib = None

BOARD = {
    "type": "charuco",
    "cols": 9,
    "rows": 6,
    "squareSizeM": 0.03,
    "markerSizeM": 0.022,
    "dictionary": "DICT_4X4_50",
}


def sample_calibration(width: int = 640, height: int = 480):
    return calib.CameraCalibration(
        camera_matrix=np.array(
            [[800.0, 0.0, 320.0], [0.0, 810.0, 240.0], [0.0, 0.0, 1.0]], dtype=np.float64
        ),
        distortion=np.array([-0.21, 0.05, 0.001, -0.002, 0.0], dtype=np.float64),
        image_size=(width, height),
        frame_id="cam_ceiling",
        calibration_id="cam_ceiling@2026-01-01",
        rms_reprojection_error=0.3125,
        sample_count=17,
        board=dict(BOARD),
    )


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class CalibrationFileTest(unittest.TestCase):
    def test_round_trips_through_a_file(self):
        original = sample_calibration()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sub" / "camera.json"
            calib.save_calibration(path, original)
            loaded = calib.load_calibration(path)

        np.testing.assert_allclose(loaded.camera_matrix, original.camera_matrix, atol=1e-12)
        np.testing.assert_allclose(loaded.distortion, original.distortion, atol=1e-12)
        self.assertEqual(loaded.image_size, (640, 480))
        self.assertEqual(loaded.frame_id, "cam_ceiling")
        self.assertEqual(loaded.calibration_id, "cam_ceiling@2026-01-01")
        self.assertAlmostEqual(loaded.rms_reprojection_error, 0.3125)
        self.assertEqual(loaded.sample_count, 17)

    def test_returns_the_matrices_as_numpy_arrays(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "camera.json"
            calib.save_calibration(path, sample_calibration())
            loaded = calib.load_calibration(path)

        self.assertIsInstance(loaded.camera_matrix, np.ndarray)
        self.assertIsInstance(loaded.distortion, np.ndarray)
        self.assertEqual(loaded.camera_matrix.shape, (3, 3))
        self.assertEqual(loaded.distortion.shape, (5,))

    def test_keeps_the_board_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "camera.json"
            calib.save_calibration(path, sample_calibration())
            loaded = calib.load_calibration(path)

        self.assertEqual(loaded.board, BOARD)

    def test_writes_the_schema_and_a_creation_timestamp(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "camera.json"
            calib.save_calibration(path, sample_calibration())
            data = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(data["schema"], calib.SCHEMA)
        self.assertEqual(data["imageSize"], [640, 480])
        self.assertIn("createdAt", data)

    def test_rejects_an_unknown_schema(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "camera.json"
            path.write_text(json.dumps({"schema": "falsch/9"}), encoding="utf-8")

            with self.assertRaises(ValueError) as caught:
                calib.load_calibration(path)

        self.assertIn("falsch/9", str(caught.exception))
        self.assertIn(calib.SCHEMA, str(caught.exception))

    def test_rejects_a_missing_file_and_names_the_path(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fehlt.json"

            with self.assertRaises(FileNotFoundError) as caught:
                calib.load_calibration(path)

        self.assertIn(str(path), str(caught.exception))


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class CameraParamsTest(unittest.TestCase):
    def test_returns_fx_fy_cx_cy_in_that_order(self):
        self.assertEqual(sample_calibration().camera_params, (800.0, 810.0, 320.0, 240.0))


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class DefaultCalibrationTest(unittest.TestCase):
    def test_matches_the_requested_resolution(self):
        result = calib.default_calibration((1280, 720))

        self.assertEqual(result.image_size, (1280, 720))
        self.assertEqual(result.camera_matrix.shape, (3, 3))
        self.assertEqual(result.distortion.shape, (5,))

    def test_centers_the_principal_point(self):
        result = calib.default_calibration((1280, 720))

        _, _, cx, cy = result.camera_params
        self.assertEqual((cx, cy), (640.0, 360.0))

    def test_is_usable_by_check_resolution(self):
        """Must be a drop-in for a real calibration, not a special case."""
        calib.check_resolution(calib.default_calibration((640, 480)), (640, 480))

    def test_marks_itself_as_a_placeholder(self):
        self.assertEqual(
            calib.default_calibration((640, 480)).calibration_id,
            calib.PLACEHOLDER_CALIBRATION_ID,
        )

    def test_carries_no_real_measurement(self):
        result = calib.default_calibration((640, 480))

        self.assertTrue(math.isnan(result.rms_reprojection_error))
        self.assertEqual(result.sample_count, 0)

    def test_passes_the_frame_id_through(self):
        self.assertEqual(
            calib.default_calibration((640, 480), frame_id="cam_flange").frame_id,
            "cam_flange",
        )


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class CheckResolutionTest(unittest.TestCase):
    def test_accepts_the_matching_size(self):
        calib.check_resolution(sample_calibration(), (640, 480))

    def test_rejects_a_different_size(self):
        with self.assertRaises(ValueError) as caught:
            calib.check_resolution(sample_calibration(), (2028, 1520))

        self.assertIn("640x480", str(caught.exception))
        self.assertIn("2028x1520", str(caught.exception))


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class ScaleToResolutionTest(unittest.TestCase):
    def test_scales_the_intrinsics_linearly(self):
        scaled = calib.scale_to_resolution(sample_calibration(), (1280, 960))

        fx, fy, cx, cy = scaled.camera_params
        self.assertAlmostEqual(fx, 1600.0)
        self.assertAlmostEqual(fy, 1620.0)
        self.assertAlmostEqual(cx, 640.0)
        self.assertAlmostEqual(cy, 480.0)
        self.assertEqual(scaled.image_size, (1280, 960))

    def test_leaves_the_distortion_untouched(self):
        original = sample_calibration()

        scaled = calib.scale_to_resolution(original, (320, 240))

        np.testing.assert_allclose(scaled.distortion, original.distortion, atol=1e-15)

    def test_does_not_modify_the_original(self):
        original = sample_calibration()

        calib.scale_to_resolution(original, (1280, 960))

        self.assertAlmostEqual(original.camera_params[0], 800.0)

    def test_marks_the_scaled_calibration_in_its_id(self):
        scaled = calib.scale_to_resolution(sample_calibration(), (1280, 960))

        self.assertTrue(scaled.calibration_id.endswith("@1280x960"))
        self.assertEqual(scaled.frame_id, "cam_ceiling")
        self.assertEqual(scaled.board, BOARD)

    def test_rejects_a_changed_aspect_ratio(self):
        with self.assertRaises(ValueError) as caught:
            calib.scale_to_resolution(sample_calibration(), (640, 360))

        self.assertIn("Seitenverhaeltnis", str(caught.exception))


class IdentityTest(unittest.TestCase):
    """`tagloc.identity` must work without numpy and without cv2."""

    def test_is_importable_without_numpy(self):
        source_root = Path(__file__).resolve().parent.parent / "src"
        environment = dict(os.environ, PYTHONPATH=str(source_root))

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, tagloc.identity; print('numpy' in sys.modules, 'cv2' in sys.modules)",
            ],
            capture_output=True,
            text=True,
            env=environment,
            check=True,
        )

        self.assertEqual(result.stdout.strip(), "False False")

    def test_combines_the_file_stem_and_its_modification_time(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "camera.json"
            path.write_text("{}", encoding="utf-8")
            os.utime(path, (1_700_000_000, 1_700_000_000))

            self.assertEqual(calibration_identity(path), "camera#1700000000")

    def test_changes_when_the_file_is_rewritten(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "camera.json"
            path.write_text("{}", encoding="utf-8")
            os.utime(path, (1_700_000_000, 1_700_000_000))
            before = calibration_identity(path)

            path.write_text('{"neu": true}', encoding="utf-8")
            os.utime(path, (1_700_000_060, 1_700_000_060))

            self.assertNotEqual(calibration_identity(path), before)

    def test_reports_a_missing_file_instead_of_raising(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(
                calibration_identity(Path(folder) / "camera.json"), "camera#fehlt"
            )
            self.assertEqual(tag_map_identity(Path(folder) / "tagmap.json"), "tagmap#fehlt")

    def test_reports_the_absent_tag_map_as_a_normal_case(self):
        self.assertEqual(tag_map_identity(None), "tagmap#keine")

    def test_accepts_a_custom_marker_for_a_missing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            identity = file_identity(Path(folder) / "camera.json", missing="unbekannt")

        self.assertEqual(identity, "camera#unbekannt")


if __name__ == "__main__":
    unittest.main()

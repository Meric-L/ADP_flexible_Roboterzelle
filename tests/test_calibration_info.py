"""Tests fuer `runner._calibration_info_payload` -- die Kurzfassung der
gerade aktiven Kalibrierung, die `ActiveCalibrationInfo` fuellt.

Reiner Stdlib-Test: `CameraCalibration` wird nicht importiert, ein
`SimpleNamespace` mit denselben Feldern reicht als Fake.
"""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    from tagloc.calibration import PLACEHOLDER_CALIBRATION_ID
except Exception:  # numpy/tagloc nicht verfuegbar
    PLACEHOLDER_CALIBRATION_ID = None

from vision_server.runner import _calibration_info_payload


def _fake_calibration(**overrides):
    fields = dict(
        frame_id="cam_flange",
        calibration_id="cam_flange@2026-09-22T10:00:00+00:00",
        rms_reprojection_error=0.2945,
        sample_count=21,
        board={"type": "chessboard", "cols": 7, "rows": 9},
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


@unittest.skipUnless(PLACEHOLDER_CALIBRATION_ID is not None, "tagloc nicht verfuegbar")
class CalibrationInfoPayloadTest(unittest.TestCase):
    def test_reads_calibration_id_and_created_at_from_the_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cam_flange.json"
            path.write_text(
                json.dumps(
                    {
                        "calibrationId": "cam_flange@2026-09-22T10:00:00+00:00",
                        "createdAt": "2026-09-22T10:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )

            info = _calibration_info_payload(_fake_calibration(), path)

        self.assertFalse(info["placeholder"])
        self.assertEqual(info["calibrationId"], "cam_flange@2026-09-22T10:00:00+00:00")
        self.assertEqual(info["createdAt"], "2026-09-22T10:00:00+00:00")
        self.assertEqual(info["rms"], 0.2945)
        self.assertEqual(info["samples"], 21)
        self.assertEqual(info["board"]["cols"], 7)

    def test_falls_back_gracefully_when_the_file_is_missing(self):
        missing_path = Path(tempfile.gettempdir()) / "does-not-exist-cam.json"

        info = _calibration_info_payload(_fake_calibration(), missing_path)

        self.assertFalse(info["placeholder"])
        self.assertEqual(info["calibrationId"], "cam_flange@2026-09-22T10:00:00+00:00")
        self.assertIsNone(info["createdAt"])

    def test_marks_the_placeholder_calibration_without_reading_a_file(self):
        calibration = _fake_calibration(
            calibration_id=PLACEHOLDER_CALIBRATION_ID,
            rms_reprojection_error=float("nan"),
            sample_count=0,
            board={},
        )
        missing_path = Path(tempfile.gettempdir()) / "does-not-exist-cam.json"

        info = _calibration_info_payload(calibration, missing_path)

        self.assertTrue(info["placeholder"])
        self.assertIsNone(info["rms"])
        self.assertIsNone(info["createdAt"])

    def test_result_is_json_serialisable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cam_flange.json"
            path.write_text(json.dumps({"calibrationId": "x", "createdAt": "y"}), encoding="utf-8")

            info = _calibration_info_payload(_fake_calibration(), path)

            json.dumps(info)  # darf nicht werfen


if __name__ == "__main__":
    unittest.main()

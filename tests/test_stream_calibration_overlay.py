"""Datumsformat der Kalibrier-Beschriftung (`tagloc.overlay`).

Der Stream-Modus "calibration" ist seit 2026-11-17 entfernt; die Hilfsfunktionen
in `tagloc.overlay` bleiben und werden hier weiter geprueft."""

import unittest

try:
    import cv2
except Exception:  # OpenCV nicht installiert
    cv2 = None


class CalibrationStampTest(unittest.TestCase):
    def fmt(self, calibration_id):
        from tagloc.overlay import format_calibration_stamp

        return format_calibration_stamp(calibration_id)

    def test_summer_time_is_utc_plus_two(self):
        self.assertEqual(
            self.fmt("cam_flange@2026-09-23T15:25:06.698737+00:00"), "23.09.2026, 17:25 Uhr"
        )

    def test_winter_time_is_utc_plus_one(self):
        self.assertEqual(self.fmt("cam@2026-01-15T15:25:06+00:00"), "15.01.2026, 16:25 Uhr")

    def test_z_suffix_and_day_rollover(self):
        # Handeye schreibt "Z"; 23:30 UTC im Sommer ist am Folgetag 01:30 Berlin.
        self.assertEqual(self.fmt("cam@2026-06-30T23:30:00Z"), "01.07.2026, 01:30 Uhr")

    def test_other_offset_is_converted(self):
        self.assertEqual(self.fmt("cam@2026-09-23T10:00:00-05:00"), "23.09.2026, 17:00 Uhr")

    def test_resolution_suffix_after_timestamp_is_skipped(self):
        self.assertEqual(
            self.fmt("cam_flange@2026-09-23T15:25:06+00:00@1280x720"), "23.09.2026, 17:25 Uhr"
        )

    def test_unusable_input_gives_dash_not_an_error(self):
        for value in (None, "", "cam_flange", "cam@", "cam@kaputt", "cam@2026-09-23T15:25:06"):
            with self.subTest(value=value):
                self.assertEqual(self.fmt(value), "\u2013")

    def test_missing_calibration_caption(self):
        if cv2 is None:
            self.skipTest("OpenCV nicht verfuegbar")
        from tagloc.overlay import CALIBRATION_CAPTION_PREFIX

        self.assertEqual(
            CALIBRATION_CAPTION_PREFIX + self.fmt("unkalibriert"),
            "Kamera-Kalibrierung \u00b7 Letzte Kalibrierung: \u2013",
        )


if __name__ == "__main__":
    unittest.main()

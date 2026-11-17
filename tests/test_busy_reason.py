"""BusyReason: why the Pi refuses new work, published in VisionProgram/Status.

Replaces the frontend's client-side Pi activity lock (piActivity.ts); the Pi is the one
place that knows what it is doing.
"""

import unittest
from types import SimpleNamespace

from vision_server.runner.part10 import _busy_reason


class BusyReasonTest(unittest.TestCase):
    def setUp(self):
        self.flags = {"calibration": False, "layer2": False, "hand_eye": False}
        self.wiring = SimpleNamespace(
            jobs=SimpleNamespace(busy=False),
            calibration_busy=lambda: self.flags["calibration"],
            layer2_active=lambda: self.flags["layer2"],
            hand_eye_active=lambda: self.flags["hand_eye"],
        )

    def test_is_empty_when_the_pi_is_free(self):
        self.assertEqual(_busy_reason(self.wiring), "")

    def test_names_each_activity(self):
        cases = (
            ("job", "Job laeuft"),
            ("calibration", "Kamera-Kalibrierung laeuft"),
            ("layer2", "Layer-2-Lauf laeuft"),
            ("hand_eye", "Hand-Auge-Kalibrierung laeuft"),
        )
        for activity, text in cases:
            with self.subTest(activity=activity):
                self.setUp()
                if activity == "job":
                    self.wiring.jobs.busy = True
                else:
                    self.flags[activity] = True
                self.assertEqual(_busy_reason(self.wiring), text)

    def test_a_running_job_takes_precedence(self):
        self.wiring.jobs.busy = True
        self.flags["layer2"] = True
        self.assertEqual(_busy_reason(self.wiring), "Job laeuft")

    def test_frees_the_pi_once_the_activity_ends(self):
        self.flags["hand_eye"] = True
        self.assertTrue(_busy_reason(self.wiring))
        self.flags["hand_eye"] = False
        self.assertEqual(_busy_reason(self.wiring), "")


if __name__ == "__main__":
    unittest.main()

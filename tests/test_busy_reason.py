"""BusyReason behavior corresponding to the four piActivity frontend cases."""

import unittest
from types import SimpleNamespace

from vision_server.runner.part10 import _busy_reason


class BusyReasonTest(unittest.TestCase):
    """The Part 10 status reason follows the active blockers on one Pi."""

    def setUp(self):
        self.wiring = SimpleNamespace(
            jobs=SimpleNamespace(busy=False),
            calibration_session=None,
            calibration_busy=lambda: False,
            layer2_active=lambda: False,
            hand_eye_active=lambda: False,
        )

    def test_blocks_other_owners_on_the_same_Pi_only(self):
        self.wiring.jobs.busy = True
        self.assertTrue(_busy_reason(self.wiring))
        self.assertEqual(_busy_reason(self.wiring), "Job laeuft")

    def test_never_blocks_the_owner_s_own_jobs(self):
        self.assertEqual(_busy_reason(self.wiring), "")

    def test_frees_the_Pi_once_the_activity_is_removed(self):
        self.wiring.jobs.busy = True
        self.assertTrue(_busy_reason(self.wiring))
        self.wiring.jobs.busy = False
        self.assertEqual(_busy_reason(self.wiring), "")

    def test_replaces_a_reregistered_activity_instead_of_duplicating_it(self):
        self.wiring.calibration_session = SimpleNamespace(running=True)
        self.assertEqual(
            _busy_reason(self.wiring),
            "SetTagMap waehrend laufender Kalibrierung abgelehnt",
        )
        self.wiring.jobs.busy = True
        self.assertEqual(_busy_reason(self.wiring), "Job laeuft")


if __name__ == "__main__":
    unittest.main()

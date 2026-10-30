"""`tagloc.jsonio`: `finite_or_none` (NaN/Unendlich auf `None`, Rundung je Aufrufer) und die UTC-Zeitstempel."""

import math
import re
import unittest

from tagloc.jsonio import finite_or_none, utc_now_z, utc_stamp


class FiniteOrNoneTest(unittest.TestCase):
    def test_none_stays_none(self):
        self.assertIsNone(finite_or_none(None))
        self.assertIsNone(finite_or_none(None, 4))

    def test_nan_and_infinity_become_none(self):
        for value in (math.nan, math.inf, -math.inf, float("nan")):
            with self.subTest(value=value):
                self.assertIsNone(finite_or_none(value))
                self.assertIsNone(finite_or_none(value, 4))

    def test_without_digits_the_number_is_not_rounded(self):
        self.assertEqual(finite_or_none(0.123456789), 0.123456789)
        self.assertEqual(finite_or_none(3), 3.0)
        self.assertIsInstance(finite_or_none(3), float)

    def test_digits_round_like_the_callers_before(self):
        self.assertEqual(finite_or_none(0.123456789, 4), 0.1235)
        self.assertEqual(finite_or_none(0.123456789, 6), 0.123457)

    def test_accepts_numeric_strings_and_numpy_scalars_like_float(self):
        self.assertEqual(finite_or_none("1.25", 1), 1.2)  # round-half-even wie bisher
        try:
            import numpy as np
        except ImportError:
            return
        self.assertEqual(finite_or_none(np.float32(0.5), 4), 0.5)
        self.assertIsNone(finite_or_none(np.float64("nan"), 4))


class UtcStampTest(unittest.TestCase):
    def test_formats_of_the_files(self):
        self.assertRegex(utc_now_z(), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertRegex(utc_stamp(), r"^\d{8}T\d{6}Z$")
        self.assertIsNone(re.search(r"\.", utc_now_z()))  # keine Bruchteile


if __name__ == "__main__":
    unittest.main()

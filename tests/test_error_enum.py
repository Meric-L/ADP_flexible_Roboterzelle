"""Operator-visible descriptions for the VisionErrorCode OPC UA enumeration."""

import unittest

from vision_server.errors import ENUM_VALUES


class VisionErrorEnumTest(unittest.TestCase):
    """The enum values stay stable and carry the agreed German descriptions."""

    def test_names_the_known_VisionErrorCodes_in_German(self):
        self.assertEqual(
            ENUM_VALUES,
            (
                ("OK", "OK"),
                ("INVALID_STATE", "Im aktuellen Zustand nicht möglich (z. B. noch kein Kamerabild)."),
                ("INVALID_ARGUMENT", "Ungültiger Parameter."),
                ("BUSY", "Der Pi ist beschäftigt – danach erneut versuchen."),
                ("UNKNOWN_RECIPE", "Unbekanntes Rezept."),
                ("DETECTION_FAILED", "Erkennung fehlgeschlagen."),
                ("INTERNAL", "Interner Fehler auf dem Pi."),
                ("CANCELLED", "Abgebrochen."),
            ),
        )
        self.assertIn("Kamerabild", ENUM_VALUES[1][1])
        self.assertIn("beschäftigt", ENUM_VALUES[3][1])
        self.assertIn("fehlgeschlagen", ENUM_VALUES[5][1])


if __name__ == "__main__":
    unittest.main()

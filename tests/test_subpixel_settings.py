"""Subpixel-Ecken: Preset der Handkamera und Uebersteuerung per Umgebung."""

import os
import unittest
from unittest import mock

from vision_server.server import PI_APRILTAG_PRESETS, subpixel_corner_settings

ENV = "VISION_SUBPIXEL_CORNER_REFINEMENT"


def settings(value=None, frame="cam_flange"):
    env = {k: v for k, v in os.environ.items() if k != ENV}
    if value is not None:
        env[ENV] = value
    with mock.patch.dict(os.environ, env, clear=True):
        return subpixel_corner_settings(dict(PI_APRILTAG_PRESETS[frame]))


class SubpixelSettingsTest(unittest.TestCase):
    def test_both_cameras_refine_with_subpix(self):
        expected = {"subpixel_corner_refinement": True, "corner_refinement_method": "subpix"}
        self.assertEqual(settings(), expected)
        self.assertEqual(settings(frame="cam_ceiling"), expected)

    def test_preset_without_entry_stays_off(self):
        with mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != ENV}, clear=True):
            self.assertFalse(subpixel_corner_settings({})["subpixel_corner_refinement"])

    def test_ceiling_can_be_switched_off(self):
        self.assertFalse(settings("0", frame="cam_ceiling")["subpixel_corner_refinement"])

    def test_environment_overrides(self):
        for off in ("0", "none", "false"):
            self.assertFalse(settings(off)["subpixel_corner_refinement"])
        self.assertEqual(settings("1")["corner_refinement_method"], "subpix")
        self.assertEqual(
            settings("apriltag"), {"subpixel_corner_refinement": True, "corner_refinement_method": "apriltag"}
        )
        self.assertTrue(settings("subpix", frame="cam_ceiling")["subpixel_corner_refinement"])

    def test_unknown_value_stops_the_start(self):
        with self.assertRaises(ValueError):
            settings("ja")


if __name__ == "__main__":
    unittest.main()

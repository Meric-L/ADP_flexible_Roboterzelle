"""Tests fuer Zulassung und Routing der RecipeIds."""

import unittest
from dataclasses import replace

from vision_server.config import VisionServerConfig
from vision_server.detection import build_detection_sources
from vision_server.errors import VisionErrorCode, VisionJobError
from vision_server.job import build_job_request

#: Die Namen, die das Frontend senden kann (useVisionJob.ts), mit ihrem Profil.
FRONTEND_RECIPES = {
    "": "hello_world",
    "hello-world": "hello_world",
    "calibration": "calibration",
    "image-recognition": "image_recognition",
}


def request(recipe_id, config=None):
    return build_job_request(config or VisionServerConfig(), None, None, recipe_id, None, [])


class AdmissionTest(unittest.TestCase):
    def test_accepts_every_recipe_the_frontend_sends(self):
        for recipe_id in FRONTEND_RECIPES:
            with self.subTest(recipe_id=recipe_id):
                self.assertEqual(request(recipe_id).recipe_id, recipe_id or None)

    def test_rejects_unknown_recipe(self):
        with self.assertRaises(VisionJobError) as caught:
            request("gibts-nicht")
        self.assertEqual(caught.exception.code, VisionErrorCode.UNKNOWN_RECIPE)

    def test_error_text_lists_the_known_recipes(self):
        with self.assertRaises(VisionJobError) as caught:
            request("gibts-nicht")
        self.assertIn("image-recognition", caught.exception.message)


class RoutingTest(unittest.TestCase):
    def test_resolves_the_expected_profile_for_every_recipe(self):
        config = VisionServerConfig()
        for recipe_id, profile in FRONTEND_RECIPES.items():
            with self.subTest(recipe_id=recipe_id):
                self.assertEqual(request(recipe_id, config).profile_id, profile)

    def test_routes_to_distinct_profiles(self):
        config = replace(
            VisionServerConfig(),
            recipe_profiles=(("", "apriltag"), ("hello-world", "hello_world")),
        )
        self.assertEqual(config.profile_for(""), "apriltag")
        self.assertEqual(config.profile_for("hello-world"), "hello_world")

    def test_known_recipe_ids_follows_the_mapping(self):
        config = replace(VisionServerConfig(), recipe_profiles=(("solo", "hello_world"),))
        self.assertEqual(config.known_recipe_ids, frozenset({"solo"}))

    def test_config_stays_hashable(self):
        hash(VisionServerConfig())


class SourceBuildingTest(unittest.TestCase):
    def test_builds_one_instance_per_profile(self):
        """Zwei Rezepte auf dasselbe Profil teilen sich eine Instanz."""
        config = replace(
            VisionServerConfig(),
            recipe_profiles=(("", "hello_world"), ("hello-world", "hello_world")),
        )
        sources = build_detection_sources(config)
        self.assertEqual(sorted(sources), ["hello_world"])

    def test_builds_the_script_profiles(self):
        sources = build_detection_sources(VisionServerConfig())
        self.assertEqual(sorted(sources), ["calibration", "hello_world", "image_recognition"])

    def test_unknown_profile_fails_at_install_time(self):
        config = replace(VisionServerConfig(), recipe_profiles=(("", "appriltag"),))
        with self.assertRaises(ValueError) as caught:
            build_detection_sources(config)
        self.assertIn("appriltag", str(caught.exception))

    def test_every_admitted_recipe_has_a_source(self):
        """Zulassung und Routing duerfen nicht auseinanderlaufen."""
        config = VisionServerConfig()
        sources = build_detection_sources(config)
        for recipe_id in config.known_recipe_ids:
            self.assertIn(config.profile_for(recipe_id), sources)


if __name__ == "__main__":
    unittest.main()

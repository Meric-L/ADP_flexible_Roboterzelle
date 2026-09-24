"""Ankern am Welttag, dann messen ohne Welttag im Bild.

Der Kern des Konzepts: die Handkamera sieht den Welttag **einmal** zu Beginn
eines Lokalisierungsvorgangs. Danach steht sie dicht vor den Modulen, der
naechste Welttag liegt laengst ausserhalb des Bildfelds -- und die Modulposen
muessen trotzdem im Welt-KS herauskommen. Die Bruecke ist die Kinematik plus
das einmal kalibrierte `T_flansch_cam`.

Braucht nur numpy -- auch der Solver, weil `cv2.calibrateHandEye` in OpenCV
5.0 nicht mehr nach Python exportiert ist. Alle Ansichten werden aus bekannten
Posen gerechnet, nicht gerendert: `T_cam_tag = invert(T_world_cam) @ T_world_tag`.
"""

import json
import math
import tempfile
import unittest
from pathlib import Path

try:
    import numpy as np

    from tagloc import geometry, handeye, tagmap
    from tagloc.localize import (
        ORIGIN_ROBOT_POSE,
        ORIGIN_WORLD_TAGS,
        SOURCE_FLANGE,
        anchor_drift,
        anchor_from_localization,
        locate_modules,
        localize_camera,
        localize_camera_from_anchor,
    )
    from tagloc.observations import TagPose
except Exception:  # pragma: no cover - numpy broken in this environment
    np = None
    geometry = None
    handeye = None

WORLD_TAG_ID = 2
MODULE_TAG_ID = 7

#: Die Zelle: ein Welttag in der Ecke, ein Modul weit davon entfernt --
#: absichtlich so weit, dass beide nie in ein Bild passen.
WORLD_TAG_POSITION = (2.4, 1.8, 0.0)
MODULE_TAG_POSITION = (0.6, 0.4, 0.95)
MODULE_ORIGIN_OFFSET = (0.0, 0.0, -0.04)

#: Roboterbasis irgendwo in der Zelle, leicht verdreht -- eine Basis in der
#: Identitaet wuerde einen Vorzeichenfehler in der Ankerrechnung verdecken.
ROBOT_BASE_POSITION = (1.5, 1.2, 0.0)
ROBOT_BASE_YAW_RAD = math.radians(35.0)

#: Der konstante Versatz Flansch -> Kameraoptik. Starr, einmal kalibriert.
FLANGE_CAM_POSITION = (0.041, -0.012, 0.087)
FLANGE_CAM_RVEC = (0.0, math.radians(12.0), 0.0)

#: Rauschgrenze der Winkelstreuung bei rechnerisch exakten Eingaben. Nicht 0:
#: `average_poses` mittelt ueber eine Eigenwertzerlegung, die bei fuenf Posen
#: rund 2e-08 rad (1,2e-06 Grad) Rundungsrest laesst. Ein echter Fehler liegt
#: Groessenordnungen darueber -- `test_the_spread_exposes_a_wrong_transform`
#: zeigt das an einem um 10 mm verschobenen Versatz.
NUMERIC_NOISE_RAD = 1e-6


def pose(translation, *, rvec=(0.0, 0.0, 0.0)):
    return geometry.from_rvec_tvec(rvec, translation)


def pose_world_base():
    return pose(ROBOT_BASE_POSITION, rvec=(0.0, 0.0, ROBOT_BASE_YAW_RAD))


def pose_flange_cam():
    return pose(FLANGE_CAM_POSITION, rvec=FLANGE_CAM_RVEC)


def hand_eye() -> "handeye.HandEye":
    return handeye.HandEye(
        pose_flange_cam=pose_flange_cam(),
        frame_id="cam_flange",
        rms_position_m=0.0012,
        rms_rotation_deg=0.08,
        sample_count=14,
    )


def flange_pose(translation, *, rvec=(0.0, 0.0, 0.0)):
    """Eine Armstellung, ausgedrueckt als `T_base_flansch`."""
    return pose(translation, rvec=rvec)


def camera_in_world(pose_base_flange):
    """Wo die Kamera steht, wenn der Arm so steht -- die Wahrheit im Test."""
    return geometry.compose(pose_world_base(), pose_base_flange, pose_flange_cam())


def seen(pose_world_cam, pose_world_tag, tag_id, *, error_px=0.2) -> "TagPose":
    return TagPose(
        tag_id=tag_id,
        pose_cam_tag=geometry.compose(geometry.invert(pose_world_cam), pose_world_tag),
        reprojection_error_px=error_px,
    )


def cell_map() -> "tagmap.TagMap":
    """Ein Welttag (fest) und ein Modul (beweglich), weit auseinander."""
    return tagmap.TagMap(
        frame_id="world",
        anchor_tag_id=WORLD_TAG_ID,
        entries={
            WORLD_TAG_ID: tagmap.TagEntry(
                tag_id=WORLD_TAG_ID,
                role="world",
                size_m=0.10,
                pose_in_world=pose(WORLD_TAG_POSITION),
            ),
            MODULE_TAG_ID: tagmap.TagEntry(
                tag_id=MODULE_TAG_ID,
                role="module",
                size_m=0.05,
                module_id="MOD-A",
                instance_id="mod-a-1",
                tag_to_module=pose(MODULE_ORIGIN_OFFSET),
            ),
        },
    )


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class AnkerRechnungTest(unittest.TestCase):
    def test_recovers_the_robot_base_from_one_world_tag_sighting(self):
        arm = flange_pose((0.30, 0.10, 0.60), rvec=(0.0, math.radians(20.0), 0.0))

        recovered = handeye.anchor_world_base(
            camera_in_world(arm), arm, pose_flange_cam()
        )

        np.testing.assert_allclose(recovered, pose_world_base(), atol=1e-12)

    def test_carries_the_camera_pose_to_another_arm_position(self):
        """Der eigentliche Zweck: Kamerapose ohne Welttag im Bild."""
        other = flange_pose((-0.20, 0.45, 0.35), rvec=(math.radians(15.0), 0.0, 0.0))

        carried = handeye.camera_pose_from_robot(
            pose_world_base(), other, pose_flange_cam()
        )

        np.testing.assert_allclose(carried, camera_in_world(other), atol=1e-12)

    def test_anchor_and_carry_are_inverse_of_each_other(self):
        first = flange_pose((0.30, 0.10, 0.60), rvec=(0.0, math.radians(20.0), 0.0))
        second = flange_pose((0.05, -0.30, 0.50), rvec=(0.0, 0.0, math.radians(70.0)))

        base = handeye.anchor_world_base(camera_in_world(first), first, pose_flange_cam())
        carried = handeye.camera_pose_from_robot(base, second, pose_flange_cam())

        np.testing.assert_allclose(carried, camera_in_world(second), atol=1e-12)


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class EinmalAnkernDannMessenTest(unittest.TestCase):
    """Einmal am Welttag ankern, danach jedes Modul in Reichweite messen."""

    def setUp(self):
        self.tag_map = cell_map()
        self.hand_eye = hand_eye()
        # Stellung 1: der Arm zeigt zum Welttag.
        self.anchor_arm = flange_pose(
            (0.35, 0.20, 0.55), rvec=(0.0, math.radians(18.0), 0.0)
        )
        # Stellung 2: der Arm steht dicht vor dem Modul, ganz woanders.
        self.measure_arm = flange_pose(
            (-0.40, -0.25, 0.30), rvec=(0.0, 0.0, math.radians(-55.0))
        )

    def _anchor(self):
        tag_poses = [
            seen(camera_in_world(self.anchor_arm), pose(WORLD_TAG_POSITION), WORLD_TAG_ID)
        ]
        localization = localize_camera(tag_poses, self.tag_map)
        self.assertIsNotNone(localization)
        return anchor_from_localization(localization, self.anchor_arm, self.hand_eye)

    def _module_only(self):
        return [
            seen(camera_in_world(self.measure_arm), pose(MODULE_TAG_POSITION), MODULE_TAG_ID)
        ]

    def test_the_anchor_puts_the_robot_base_in_the_world_frame(self):
        anchor = self._anchor()

        np.testing.assert_allclose(anchor.pose_world_base, pose_world_base(), atol=1e-9)
        self.assertEqual(anchor.world_tag_id, WORLD_TAG_ID)

    def test_without_a_world_tag_there_is_no_optical_localization(self):
        """Vorbedingung des Tests: am Modul ist wirklich kein Welttag zu sehen."""
        self.assertIsNone(localize_camera(self._module_only(), self.tag_map))

    def test_the_module_lands_in_the_world_frame_without_a_world_tag(self):
        """Der Kern: gemessen wird ohne Welttag, das Ergebnis ist trotzdem Welt-KS."""
        anchor = self._anchor()

        localization = localize_camera_from_anchor(anchor, self.measure_arm, self.hand_eye)
        located = locate_modules(
            self._module_only(),
            self.tag_map,
            localization=localization,
            frame_id=self.tag_map.frame_id,
            source=SOURCE_FLANGE,
        )

        self.assertEqual(len(located), 1)
        expected = np.add(MODULE_TAG_POSITION, MODULE_ORIGIN_OFFSET)
        np.testing.assert_allclose(located[0].pose[:3, 3], expected, atol=1e-9)
        self.assertEqual(located[0].frame_id, "world")

    def test_the_module_is_still_related_to_its_nearest_world_tag(self):
        anchor = self._anchor()

        located = locate_modules(
            self._module_only(),
            self.tag_map,
            localization=localize_camera_from_anchor(
                anchor, self.measure_arm, self.hand_eye
            ),
            frame_id=self.tag_map.frame_id,
        )

        self.assertEqual(located[0].reference_tag_id, WORLD_TAG_ID)
        expected = np.subtract(
            np.add(MODULE_TAG_POSITION, MODULE_ORIGIN_OFFSET), WORLD_TAG_POSITION
        )
        np.testing.assert_allclose(
            located[0].pose_in_reference_tag[:3, 3], expected, atol=1e-9
        )

    def test_the_origin_says_the_pose_was_carried_over(self):
        """Gemessen und fortgeschrieben sind nicht gleich genau -- das muss dranstehen."""
        anchor = self._anchor()

        carried = localize_camera_from_anchor(anchor, self.measure_arm, self.hand_eye)

        self.assertEqual(carried.origin, ORIGIN_ROBOT_POSE)
        self.assertEqual(carried.world_tag_ids, ())
        self.assertEqual(carried.primary_tag_id, WORLD_TAG_ID)

    def test_an_optical_localization_is_marked_as_measured(self):
        tag_poses = [
            seen(camera_in_world(self.anchor_arm), pose(WORLD_TAG_POSITION), WORLD_TAG_ID)
        ]

        self.assertEqual(localize_camera(tag_poses, self.tag_map).origin, ORIGIN_WORLD_TAGS)

    def test_a_correct_anchor_shows_no_drift(self):
        anchor = self._anchor()
        again = flange_pose((0.10, 0.40, 0.45), rvec=(0.0, math.radians(30.0), 0.0))
        optical = localize_camera(
            [seen(camera_in_world(again), pose(WORLD_TAG_POSITION), WORLD_TAG_ID)],
            self.tag_map,
        )

        drift_m, drift_rad = anchor_drift(anchor, optical, again, self.hand_eye)

        self.assertLess(drift_m, 1e-9)
        self.assertLess(drift_rad, 1e-9)

    def test_a_wrong_anchor_is_exposed_by_the_drift(self):
        """Ein still weggewanderter Anker faellt sonst erst beim Danebengreifen auf."""
        anchor = self._anchor()
        wrong = handeye.RobotAnchor(
            pose_world_base=geometry.compose(
                anchor.pose_world_base, pose((0.03, 0.0, 0.0))
            ),
            world_tag_id=WORLD_TAG_ID,
        )
        again = flange_pose((0.10, 0.40, 0.45))
        optical = localize_camera(
            [seen(camera_in_world(again), pose(WORLD_TAG_POSITION), WORLD_TAG_ID)],
            self.tag_map,
        )

        drift_m, _ = anchor_drift(wrong, optical, again, self.hand_eye)

        self.assertAlmostEqual(drift_m, 0.03, places=9)


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class DateiTest(unittest.TestCase):
    def test_round_trips_through_a_file(self):
        original = hand_eye()

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sub" / "cam_flange.json"
            handeye.save_hand_eye(path, original)
            loaded = handeye.load_hand_eye(path)

        np.testing.assert_allclose(
            loaded.pose_flange_cam, original.pose_flange_cam, atol=1e-14
        )
        self.assertEqual(loaded.frame_id, "cam_flange")
        self.assertEqual(loaded.sample_count, 14)
        self.assertAlmostEqual(loaded.rms_position_m, 0.0012)

    def test_writes_the_documented_schema(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cam_flange.json"
            handeye.save_hand_eye(path, hand_eye())
            data = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(data["schema"], handeye.SCHEMA)
        self.assertIn("position", data["poseFlangeCam"])
        self.assertIn("orientation", data["poseFlangeCam"])
        self.assertTrue(data["createdAt"].endswith("Z"))

    def test_rejects_an_unknown_schema(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cam_flange.json"
            path.write_text(json.dumps({"schema": "falsch/9"}), encoding="utf-8")

            with self.assertRaises(ValueError) as caught:
                handeye.load_hand_eye(path)

        self.assertIn("falsch/9", str(caught.exception))

    def test_rejects_a_missing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(FileNotFoundError) as caught:
                handeye.load_hand_eye(Path(folder) / "fehlt.json")

        self.assertIn("fehlt.json", str(caught.exception))

    def test_survives_a_calibration_without_quality_figures(self):
        """NaN darf nicht als nacktes NaN in die Datei -- das waere kein JSON."""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cam_flange.json"
            handeye.save_hand_eye(path, handeye.HandEye(pose_flange_cam=pose_flange_cam()))
            text = path.read_text(encoding="utf-8")
            loaded = handeye.load_hand_eye(path)

        self.assertNotIn("NaN", text)
        self.assertTrue(math.isnan(loaded.rms_position_m))


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class RoboterposeAusJobTest(unittest.TestCase):
    def _parse(self, parameters):
        from vision_server.detection.apriltag import robot_pose_from_parameters

        return robot_pose_from_parameters(parameters)

    def test_reads_seven_floats_as_position_and_quaternion(self):
        result = self._parse((1.0, 2.0, 3.0, 0.0, 0.0, 0.0, 1.0))

        np.testing.assert_allclose(result[:3, 3], (1.0, 2.0, 3.0), atol=1e-15)

    def test_returns_none_without_parameters(self):
        self.assertIsNone(self._parse(()))
        self.assertIsNone(self._parse(None))

    def test_returns_none_for_too_few_values(self):
        self.assertIsNone(self._parse((1.0, 2.0, 3.0)))

    def test_returns_none_instead_of_guessing_on_junk(self):
        """Eine falsche Roboterpose verschiebt jede Modulpose unsichtbar."""
        self.assertIsNone(self._parse(("a", "b", "c", "d", "e", "f", "g")))
        self.assertIsNone(self._parse((0.0,) * 7))  # Quaternion der Laenge 0


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class SolverTest(unittest.TestCase):
    """Die einmalige Kalibrierfahrt gegen eine bekannte Wahrheit.

    Braucht kein OpenCV: der Solver rechnet in numpy, weil
    `cv2.calibrateHandEye` in OpenCV 5.0 nicht mehr nach Python exportiert ist.
    """

    def _samples(self):
        """Tag liegt fest, der Roboter faehrt verschiedene Posen an."""
        pose_base_tag = pose((0.50, 0.10, 0.30), rvec=(0.0, 0.0, math.radians(25.0)))
        arms = [
            flange_pose((0.20, 0.05, 0.50), rvec=(0.0, math.radians(15.0), 0.0)),
            flange_pose((0.10, -0.15, 0.45), rvec=(math.radians(20.0), 0.0, 0.0)),
            flange_pose((0.30, 0.20, 0.55), rvec=(0.0, 0.0, math.radians(35.0))),
            flange_pose(
                (-0.05, 0.10, 0.40), rvec=(math.radians(-18.0), math.radians(12.0), 0.0)
            ),
            flange_pose(
                (0.25, -0.05, 0.60), rvec=(0.0, math.radians(-25.0), math.radians(10.0))
            ),
        ]
        truth = pose_flange_cam()
        tags = [
            geometry.compose(geometry.invert(truth), geometry.invert(arm), pose_base_tag)
            for arm in arms
        ]
        return arms, tags

    def test_recovers_the_known_hand_eye_transform(self):
        arms, tags = self._samples()

        solved, spread_m, spread_deg = handeye.solve_hand_eye(arms, tags)

        np.testing.assert_allclose(solved, pose_flange_cam(), atol=1e-6)
        self.assertLess(spread_m, 1e-6)
        self.assertLess(spread_deg, math.degrees(NUMERIC_NOISE_RAD) * 10.0)

    def test_the_spread_is_zero_for_a_consistent_set(self):
        arms, tags = self._samples()

        spread_m, spread_rad = handeye.target_spread(arms, tags, pose_flange_cam())

        self.assertLess(spread_m, 1e-9)
        self.assertLess(spread_rad, NUMERIC_NOISE_RAD)

    def test_the_spread_exposes_a_wrong_transform(self):
        arms, tags = self._samples()
        wrong = geometry.compose(pose_flange_cam(), pose((0.01, 0.0, 0.0)))

        spread_m, _ = handeye.target_spread(arms, tags, wrong)

        self.assertGreater(spread_m, 1e-4)

    def test_rejects_too_few_poses(self):
        arms, tags = self._samples()

        with self.assertRaises(ValueError) as caught:
            handeye.solve_hand_eye(arms[:2], tags[:2])

        self.assertIn(str(handeye.MIN_SAMPLES), str(caught.exception))

    def test_rejects_mismatched_counts(self):
        arms, tags = self._samples()

        with self.assertRaises(ValueError):
            handeye.solve_hand_eye(arms, tags[:-1])


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class NeueKarteVerwirftAnkerTest(unittest.TestCase):
    """Eine neue Tag-Map macht den Anker ungueltig.

    Der Anker wurde gegen die Weltposen der alten Karte gerechnet. Ihn stehen
    zu lassen hiesse, jede Modulpose stumm um die Differenz zu verschieben --
    genau die Sorte Fehler, die erst beim Danebengreifen auffaellt.
    """

    def test_apply_tag_map_drops_the_anchor(self):
        from dataclasses import replace as dc_replace

        from vision_server.detection.apriltag import AprilTagDetectionSource
        from vision_server.profiles import AprilTagProfileConfig

        source = AprilTagDetectionSource(
            dc_replace(AprilTagProfileConfig(), tag_map_path=None, hand_eye_path=None)
        )
        source._tag_map = cell_map()
        source._anchor = handeye.RobotAnchor(
            pose_world_base=pose_world_base(), world_tag_id=WORLD_TAG_ID
        )
        source._drift = (0.001, 0.0001)

        problems = source.apply_tag_map(cell_map())

        self.assertIsNone(source._anchor)
        self.assertIsNone(source._drift)
        self.assertIsInstance(problems, list)


if __name__ == "__main__":
    unittest.main()

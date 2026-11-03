"""Zellen-Ueberwachung mit einem einzigen Welttag: gespeicherte Kamerapose.

Aus einem einzelnen Welttag waere die Kamerapose aus 2,5 m um Zentimeter
ungenau. Die fest montierte Kamera hat aber eine feste Pose: Der Pi schaetzt
sie aus Bildern mit mindestens zwei Welttags, merkt sie sich und prueft den
einen sichtbaren Welttag danach nur auf Konsistenz.
"""

import math
import tempfile
import types
import unittest
from pathlib import Path

try:
    import numpy as np

    from tagloc import calibration as calib
    from tagloc import geometry
    from tagloc.tagmap import TagEntry, TagMap
    from tests import test_joint_camera_pose as scene
    from vision_server.cellwatch import (
        NO_WORLD_TAG,
        CameraPoseKeeper,
        CellWatchOptions,
        parse_start,
    )
    from vision_server.detection.apriltag import AprilTagDetectionSource
    from vision_server.profiles import AprilTagProfileConfig
except Exception:  # pragma: no cover - numpy fehlt in dieser Umgebung
    np = None


def localization(solver, *, errors=None, pose=None, tag_ids=(584,)):
    return types.SimpleNamespace(
        solver=solver,
        pose_world_cam=geometry.identity() if pose is None else pose,
        world_tag_ids=tuple(tag_ids),
        tag_errors_px=dict(errors or {}),
        reprojection_error_px=1.0,
    )


def location(tag_id=7, position=(0.8, 0.8, 0.0)):
    return types.SimpleNamespace(
        tag_id=tag_id,
        pose=geometry.from_rotation_translation(np.eye(3), position),
        ambiguous=False,
        module_id="MOD-A",
    )


def tag_map(x=1.593):
    return TagMap(
        frame_id="world",
        entries={
            584: TagEntry(
                584, "world", 0.198, pose_in_world=geometry.from_rotation_translation(np.eye(3), (x, 0, 0))
            ),
            583: TagEntry(583, "world", 0.198, pose_in_world=geometry.identity()),
        },
    )


@unittest.skipUnless(np is not None, "numpy fehlt")
class KeeperTest(unittest.TestCase):
    OPTIONS = None

    def setUp(self):
        self.options = CellWatchOptions()
        self.tag_map = tag_map()
        self.keeper = CameraPoseKeeper()

    def joint(self, **kwargs):
        return localization("joint", errors={583: 3.0, 584: 3.4}, tag_ids=(583, 584), **kwargs)

    def stored(self, error_584):
        return localization("stored", errors={584: error_584})

    def remember(self, times=3):
        for _ in range(times):
            sightings, reason = self.keeper.judge([location()], self.joint(), self.options, self.tag_map)
            self.assertEqual(reason, "")
        return sightings

    def test_two_world_tags_are_remembered(self):
        self.assertIsNone(self.keeper.stored_for(self.tag_map))
        sightings = self.remember()
        self.assertEqual(list(sightings), [7])
        self.assertIsNotNone(self.keeper.stored_for(self.tag_map))

    def test_one_consistent_world_tag_is_enough(self):
        self.remember()
        # Auf dem Pi liegt der Eckenfehler bei ~3,4 px (Kartenfehler ~1 cm):
        # nur das, was ueber der Grundlinie liegt, zaehlt.
        sightings, reason = self.keeper.judge([location()], self.stored(3.9), self.options, self.tag_map)
        self.assertEqual((list(sightings), reason), ([7], ""))

    def test_an_inconsistent_world_tag_pauses_and_then_drops_the_pose(self):
        self.remember()
        for frame in range(CameraPoseKeeper.BAD_FRAMES_TO_DROP - 1):
            sightings, reason = self.keeper.judge([location()], self.stored(7.0), self.options, self.tag_map)
            self.assertIsNone(sightings)
            self.assertIn("Welt-Tag 584", reason)
            self.assertIn("Kamera bewegt?", reason)
        sightings, reason = self.keeper.judge([location()], self.stored(7.0), self.options, self.tag_map)
        self.assertIsNone(sightings)
        self.assertIn("Welt-Tag", reason)
        self.assertIn("verworfen", reason)
        self.assertIsNone(self.keeper.stored_for(self.tag_map))

    def test_a_good_frame_in_between_resets_the_count(self):
        self.remember()
        for _ in range(CameraPoseKeeper.BAD_FRAMES_TO_DROP - 1):
            self.keeper.judge([location()], self.stored(7.0), self.options, self.tag_map)
        self.keeper.judge([location()], self.stored(3.5), self.options, self.tag_map)
        self.keeper.judge([location()], self.stored(7.0), self.options, self.tag_map)
        self.assertIsNotNone(self.keeper.stored_for(self.tag_map))

    def test_without_a_stored_pose_one_world_tag_pauses(self):
        sightings, reason = self.keeper.judge(
            [location()], localization("single", errors={584: 0.5}), self.options, self.tag_map
        )
        self.assertIsNone(sightings)
        self.assertIn("Welt-Tag", reason)
        self.assertIn("noch nicht bekannt", reason)

    def test_no_world_tag_pauses_with_welt_tag_in_the_reason(self):
        sightings, reason = self.keeper.judge([], None, self.options, self.tag_map)
        self.assertIsNone(sightings)
        self.assertEqual(reason, NO_WORLD_TAG)
        self.assertIn("Welt-Tag", reason)

    def test_two_required_is_the_old_behaviour(self):
        self.remember()
        options = CellWatchOptions(min_world_tags=2)
        sightings, reason = self.keeper.judge([location()], self.stored(3.5), options, self.tag_map)
        self.assertIsNone(sightings)
        self.assertIn("zwei Welt-Tags", reason)

    def test_a_new_tag_map_drops_the_pose(self):
        self.remember()
        self.assertIsNotNone(self.keeper.stored_for(self.tag_map))
        self.assertIsNone(self.keeper.stored_for(tag_map(x=1.600)))

    def test_a_moved_camera_restarts_the_history_instead_of_averaging(self):
        self.remember()
        moved = geometry.from_rotation_translation(np.eye(3), (0.03, 0.0, 0.0))
        self.keeper.judge([location()], self.joint(pose=moved), self.options, self.tag_map)
        np.testing.assert_allclose(self.keeper.stored_for(self.tag_map)[:3, 3], [0.03, 0, 0], atol=1e-9)


@unittest.skipUnless(np is not None, "numpy fehlt")
class OptionsTest(unittest.TestCase):
    def test_min_world_tags_option(self):
        _, options = parse_start("{}")
        self.assertEqual(options.min_world_tags, 1)
        self.assertEqual(options.to_dict()["minWorldTags"], 1)
        self.assertEqual(options.to_dict()["storedPoseMarginPx"], 2.0)
        _, options = parse_start('{"minWorldTags": 2, "storedPoseMarginPx": 3}')
        self.assertEqual((options.min_world_tags, options.stored_pose_margin_px), (2, 3.0))
        for text in ('{"minWorldTags": 0}', '{"minWorldTags": 5}', '{"minWorldTags": 1.5}',
                     '{"minWorldTags": true}', '{"storedPoseMarginPx": 0}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_start(text)


@unittest.skipUnless(np is not None, "numpy fehlt")
class SceneTest(unittest.TestCase):
    """Die ganze Kette an der synthetischen Zelle: Quelle, Merken, Beurteilen."""

    MODULE = (0.8, 0.8, 0.0)

    def setUp(self):
        self.calibration = scene._calibration(calib.PINHOLE)
        self.module_pose = geometry.from_rvec_tvec((0, 0, 0.3), self.MODULE)
        self.tag_map = TagMap(
            frame_id="world",
            entries={
                **scene._tag_map().entries,
                7: TagEntry(7, "module", scene.TAG_SIZE_M, module_id="MOD-A"),
            },
        )
        self.rng = np.random.default_rng(5)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.source = AprilTagDetectionSource(
            AprilTagProfileConfig(
                calibration_path=Path(self.folder.name) / "c.json", tag_size_m=scene.TAG_SIZE_M
            ),
            calibration=self.calibration,
            tag_map=self.tag_map,
            camera=types.SimpleNamespace(),
        )
        self.image = np.zeros((scene.IMAGE_SIZE[1], scene.IMAGE_SIZE[0]), dtype=np.uint8)
        self.options = CellWatchOptions()
        self.keeper = CameraPoseKeeper()

    def observations(self, camera_pose, world_ids):
        """Die Ecken der gewuenschten Welttags und des Modul-Tags 7."""
        found = [
            o for o in scene._observations(camera_pose, self.calibration, self.rng)
            if o.tag_id in world_ids
        ]
        helper = TagMap(
            frame_id="world",
            entries={7: TagEntry(7, "world", scene.TAG_SIZE_M, pose_in_world=self.module_pose)},
        )
        original = scene._tag_map
        scene._tag_map = lambda: helper
        try:
            found += scene._observations(camera_pose, self.calibration, self.rng)
        finally:
            scene._tag_map = original
        return found

    def frame(self, camera_pose, world_ids, *, use_store=True):
        """Ein Bild durch Quelle und Beurteilung, wie `runner._install_cell_watch`."""
        observations = self.observations(camera_pose, world_ids)
        detector = types.SimpleNamespace(detect=lambda gray: observations)
        stored = self.keeper.stored_for(self.tag_map) if use_store else None
        locations, localization_, _ = self.source.observe_cell(self.image, detector, stored)
        return self.keeper.judge(locations, localization_, self.options, self.tag_map), localization_

    def module_error_mm(self, sightings):
        return 1000 * float(np.linalg.norm(np.subtract(sightings[7].position, self.MODULE)))

    def test_one_world_tag_with_the_stored_pose_is_as_good_as_four(self):
        truth = scene._true_world_cam()
        # Erst vier Welttags: die Pose wird gemerkt.
        for _ in range(5):
            (sightings, reason), loc = self.frame(truth, {583, 584, 585, 586})
            self.assertEqual((loc.solver, reason), ("joint", ""))
        single, stored = [], []
        for _ in range(60):
            (with_pose, reason), loc = self.frame(truth, {584})
            self.assertEqual((loc.solver, reason), ("stored", ""))
            stored.append(self.module_error_mm(with_pose))
            # Zum Vergleich: dieselbe Lage ohne gespeicherte Pose, nur aus dem einen Welttag.
            (bare, _), loc_single = self.frame(truth, {584}, use_store=False)
            self.assertEqual(loc_single.solver, "single")
            self.assertIsNone(bare)  # kein Keeper-Urteil ohne gemerkte Pose ...
            locations, loc_s, _ = self.source.observe_cell(
                self.image,
                types.SimpleNamespace(detect=lambda gray, o=self.observations(truth, {584}): o),
            )
            single.append(
                1000 * float(np.linalg.norm(np.subtract(locations[0].pose[:3, 3], self.MODULE)))
            )
        # Mit gemerkter Pose bleibt nur der eigene Messfehler des Modul-Tags
        # (~1 cm aus 2,5 m); ohne sie kommen Zentimeter der Kamerapose dazu.
        self.assertLess(float(np.median(stored)), 20.0)
        self.assertLess(float(np.percentile(stored, 95)), 40.0)
        self.assertLess(float(np.median(stored)), float(np.median(single)))
        self.assertGreater(float(np.percentile(single, 95)), 2 * float(np.percentile(stored, 95)))

    def test_a_moved_camera_is_noticed_with_one_world_tag(self):
        truth = scene._true_world_cam()
        for _ in range(5):
            self.frame(truth, {583, 584, 585, 586})
        # Unbewegt: nie eine Pause, auch ueber viele Bilder nicht.
        for _ in range(60):
            (sightings, reason), _ = self.frame(truth, {584})
            self.assertEqual(reason, "")
        # 1 cm und 0,3 Grad bewegt: jedes Bild faellt auf.
        shifted = geometry.compose(
            truth, geometry.from_rvec_tvec((math.radians(0.3), 0, 0), (0.01, 0, 0))
        )
        reasons = []
        for _ in range(CameraPoseKeeper.BAD_FRAMES_TO_DROP):
            (sightings, reason), _ = self.frame(shifted, {584})
            self.assertIsNone(sightings)
            reasons.append(reason)
        self.assertTrue(all("Welt-Tag" in reason for reason in reasons))
        self.assertIn("verworfen", reasons[-1])
        self.assertIsNone(self.keeper.stored_for(self.tag_map))

    def test_the_start_with_one_world_tag_pauses_until_two_were_seen(self):
        truth = scene._true_world_cam()
        (sightings, reason), _ = self.frame(truth, {584})
        self.assertIsNone(sightings)
        self.assertIn("noch nicht bekannt", reason)
        (sightings, reason), loc = self.frame(truth, {583, 584})
        self.assertEqual((loc.solver, reason), ("joint", ""))
        (sightings, reason), loc = self.frame(truth, {584})
        self.assertEqual((loc.solver, reason), ("stored", ""))
        self.assertIsNotNone(sightings)


if __name__ == "__main__":
    unittest.main()

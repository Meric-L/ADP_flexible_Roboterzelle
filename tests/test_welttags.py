"""The world-tag concept: what is fixed, what is measured, and who sees what.

Only the four world tags on the cell border stand still. Everything else --
the modules and the robot alike -- is movable and gets measured. The ceiling
camera is the only one that sees the robot and the world tags at the same
time; the eye-in-hand camera localises itself against a single world tag.

Needs only numpy: no cv2, no camera, no files. Every camera view is built
from known poses via `T_cam_tag = invert(T_world_cam) @ T_world_tag`, so the
expected result is hand-verifiable instead of rendered.
"""

import json
import math
import tempfile
import unittest
from pathlib import Path

try:
    import numpy as np

    from tagloc import geometry, tagmap
    from tagloc.localize import (
        SOURCE_CEILING,
        SOURCE_FLANGE,
        locate_modules,
        localize_camera,
        merge_by_module,
        merge_locations,
        world_tag_for_robot,
    )
    from tagloc.observations import TagPose
except Exception:  # pragma: no cover - numpy broken in this environment
    np = None
    geometry = None
    tagmap = None

#: Cell 2.4 m x 1.8 m, world tags in the four corners.
WORLD_TAG_POSES = {
    0: (0.0, 0.0, 0.0),
    1: (2.4, 0.0, 0.0),
    2: (2.4, 1.8, 0.0),
    3: (0.0, 1.8, 0.0),
}
ROBOT_TAG_IDS = (20, 21)
MODULE_TAG_ID = 7

#: Ein Tag, der in der Zelle haengt, aber NICHT in der Karte steht. Der
#: haeufigste reale Fall beim Aufbau -- und er muss trotzdem als Modul
#: herauskommen, sonst fehlt im Frontend die Box.
UNMAPPED_TAG_ID = 99
UNMAPPED_POSITION = (1.4, 1.0, 0.70)

#: The robot stands near the corner of world tag 2, the module near tag 1.
ROBOT_BASE = (2.10, 1.55, 0.00)
MODULE_POSITION = (2.05, 0.30, 0.90)


def pose(translation, *, rvec=(0.0, 0.0, 0.0)):
    return geometry.from_rvec_tvec(rvec, translation)


def world_poses() -> dict:
    """Return the true world pose of every tag in the cell."""
    poses = {tag_id: pose(position) for tag_id, position in WORLD_TAG_POSES.items()}
    # Two tags on the robot, offset against each other. Both describe the
    # same base, each through its own CAD offset.
    poses[20] = pose((ROBOT_BASE[0], ROBOT_BASE[1], 0.12))
    poses[21] = pose((ROBOT_BASE[0] + 0.15, ROBOT_BASE[1], 0.12))
    poses[MODULE_TAG_ID] = pose(
        (MODULE_POSITION[0], MODULE_POSITION[1], MODULE_POSITION[2] + 0.04)
    )
    # Haengt in der Zelle, steht aber bewusst in keiner Karte.
    poses[UNMAPPED_TAG_ID] = pose(UNMAPPED_POSITION)
    return poses


def cell_map() -> "tagmap.TagMap":
    """Return the cell's tag map: four world tags, two robot tags, one module."""
    truth = world_poses()
    entries = {
        tag_id: tagmap.TagEntry(
            tag_id=tag_id, role="world", size_m=0.10, pose_in_world=truth[tag_id]
        )
        for tag_id in WORLD_TAG_POSES
    }
    for tag_id, offset_x in zip(ROBOT_TAG_IDS, (0.0, -0.15)):
        entries[tag_id] = tagmap.TagEntry(
            tag_id=tag_id,
            role="robot",
            size_m=0.08,
            module_id="UR5e",
            instance_id="ur5e-1",
            tag_to_module=pose((offset_x, 0.0, -0.12)),
        )
    entries[MODULE_TAG_ID] = tagmap.TagEntry(
        tag_id=MODULE_TAG_ID,
        role="module",
        size_m=0.05,
        module_id="MOD-A",
        instance_id="mod-a-1",
        tag_to_module=pose((0.0, 0.0, -0.04)),
    )
    return tagmap.TagMap(
        frame_id="world", anchor_tag_id=0, tag_family="tag36h11", entries=entries
    )


def map_with(entries) -> "tagmap.TagMap":
    return tagmap.TagMap(frame_id="world", anchor_tag_id=0, entries=entries)


def view(pose_world_cam, tag_ids, *, error_px=0.5) -> list:
    """Return what a camera at `pose_world_cam` measures for these tags."""
    truth = world_poses()
    inverse = geometry.invert(pose_world_cam)
    return [
        TagPose(
            tag_id=tag_id,
            pose_cam_tag=geometry.compose(inverse, truth[tag_id]),
            reprojection_error_px=error_px,
        )
        for tag_id in tag_ids
    ]


def ceiling_view():
    """Camera 3 m above the middle of the cell -- it sees everything."""
    return pose((1.2, 0.9, 3.0))


def flange_view():
    """Camera 0.4 m next to world tag 2 -- it sees that tag and the module."""
    return pose((2.2, 1.6, 0.4))


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class RollenTest(unittest.TestCase):
    """Only the world tag is fixed; the robot is a module."""

    def test_only_the_world_role_counts_as_a_reference(self):
        self.assertEqual(tagmap.REFERENCE_ROLES, frozenset({"world"}))

    def test_the_robot_is_a_movable_module(self):
        entry = cell_map()[ROBOT_TAG_IDS[0]]

        self.assertTrue(entry.is_module)
        self.assertTrue(entry.is_robot)
        self.assertFalse(entry.is_reference)
        self.assertIsNone(entry.pose_in_world)

    def test_only_the_four_world_tags_serve_as_anchors(self):
        self.assertEqual(sorted(cell_map().reference_poses()), [0, 1, 2, 3])

    def test_an_unsurveyed_world_tag_is_no_anchor(self):
        """A world tag without a pose is not measured yet -- guessing is worse."""
        entry = tagmap.TagEntry(tag_id=0, role="world", size_m=0.1)

        self.assertTrue(entry.is_world)
        self.assertFalse(entry.is_reference)


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class ValidierungTest(unittest.TestCase):
    def test_accepts_the_complete_cell(self):
        self.assertEqual(tagmap.validate_tag_map(cell_map()), [])

    def test_reports_a_missing_world_tag(self):
        entries = dict(cell_map().entries)
        del entries[3]

        problems = tagmap.validate_tag_map(map_with(entries))

        self.assertTrue(any("gefunden 3" in problem for problem in problems))

    def test_reports_a_movable_tag_carrying_a_world_pose(self):
        entries = dict(cell_map().entries)
        entries[MODULE_TAG_ID] = tagmap.TagEntry(
            tag_id=MODULE_TAG_ID,
            role="module",
            size_m=0.05,
            pose_in_world=pose((1.0, 1.0, 0.0)),
        )

        problems = tagmap.validate_tag_map(map_with(entries))

        self.assertTrue(any(str(MODULE_TAG_ID) in problem for problem in problems))

    def test_reports_an_anchor_that_is_not_a_world_tag(self):
        problems = tagmap.validate_tag_map(
            tagmap.TagMap(
                frame_id="world", anchor_tag_id=MODULE_TAG_ID, entries=cell_map().entries
            )
        )

        self.assertTrue(any("Anker-Tag" in problem for problem in problems))

    def test_reports_a_cell_without_a_robot_tag(self):
        entries = {
            tag_id: entry
            for tag_id, entry in cell_map().entries.items()
            if tag_id not in ROBOT_TAG_IDS
        }

        problems = tagmap.validate_tag_map(map_with(entries))

        self.assertTrue(any("robot" in problem for problem in problems))


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class NaechsterWelttagTest(unittest.TestCase):
    def test_finds_the_nearest_world_tag_of_a_corner(self):
        tag_id, distance = tagmap.nearest_world_tag(cell_map(), pose((2.3, 1.7, 0.0)))

        self.assertEqual(tag_id, 2)
        self.assertAlmostEqual(distance, math.dist((2.3, 1.7, 0.0), (2.4, 1.8, 0.0)))

    def test_returns_none_without_a_surveyed_world_tag(self):
        self.assertIsNone(
            tagmap.nearest_world_tag(tagmap.empty_tag_map(), geometry.identity())
        )


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class KameraLokalisierungTest(unittest.TestCase):
    def test_the_ceiling_camera_uses_every_visible_world_tag(self):
        camera = ceiling_view()

        result = localize_camera(view(camera, [0, 1, 2, 3, 7, 20, 21]), cell_map())

        self.assertEqual(list(result.world_tag_ids), [0, 1, 2, 3])
        np.testing.assert_allclose(result.pose_world_cam, camera, atol=1e-9)

    def test_a_single_world_tag_is_enough_for_the_hand_camera(self):
        """The eye-in-hand case: one world tag, and the camera knows where it is."""
        camera = flange_view()

        result = localize_camera(view(camera, [2, MODULE_TAG_ID]), cell_map())

        self.assertEqual(list(result.world_tag_ids), [2])
        self.assertEqual(result.primary_tag_id, 2)
        np.testing.assert_allclose(result.pose_world_cam, camera, atol=1e-9)

    def test_names_the_world_tag_closest_to_the_camera(self):
        result = localize_camera(view(ceiling_view(), [0, 1, 2, 3]), cell_map())

        # The camera sits above (1.2, 0.9); all four corners are equally far
        # away, so the tie is broken by the lowest tag id.
        self.assertEqual(result.primary_tag_id, 0)

    def test_reports_no_spread_when_the_world_tags_agree(self):
        result = localize_camera(view(ceiling_view(), [0, 1, 2, 3]), cell_map())

        self.assertLess(result.spread_m, 1e-9)
        self.assertLess(result.spread_rad, 1e-9)

    def test_reports_the_spread_of_a_mis_surveyed_world_tag(self):
        """A tag entered 5 cm wrong must show up, not vanish into the average."""
        entries = dict(cell_map().entries)
        entries[3] = tagmap.TagEntry(
            tag_id=3, role="world", size_m=0.10, pose_in_world=pose((0.0, 1.85, 0.0))
        )

        result = localize_camera(view(ceiling_view(), [0, 1, 2, 3]), map_with(entries))

        self.assertGreater(result.spread_m, 0.01)

    def test_returns_none_without_any_world_tag(self):
        """Between two world tags the hand camera stays in its own frame."""
        self.assertIsNone(localize_camera(view(flange_view(), [MODULE_TAG_ID]), cell_map()))


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class ModulBezugTest(unittest.TestCase):
    def _ceiling(self):
        tag_map = cell_map()
        tag_poses = view(ceiling_view(), [0, 1, 2, 3, MODULE_TAG_ID, *ROBOT_TAG_IDS])
        return tag_map, locate_modules(
            tag_poses,
            tag_map,
            localization=localize_camera(tag_poses, tag_map),
            frame_id=tag_map.frame_id,
            source=SOURCE_CEILING,
        )

    def test_world_tags_are_not_reported_as_modules(self):
        _, located = self._ceiling()

        self.assertEqual(
            sorted(item.tag_id for item in located), [MODULE_TAG_ID, *ROBOT_TAG_IDS]
        )

    def test_reports_the_module_in_the_world_frame(self):
        _, located = self._ceiling()
        module = next(item for item in located if item.tag_id == MODULE_TAG_ID)

        np.testing.assert_allclose(module.pose[:3, 3], MODULE_POSITION, atol=1e-9)

    def test_relates_every_module_to_its_nearest_world_tag(self):
        _, located = self._ceiling()
        module = next(item for item in located if item.tag_id == MODULE_TAG_ID)

        # The module sits at (2.05, 0.30, 0.90), i.e. nearest to world tag 1.
        self.assertEqual(module.reference_tag_id, 1)
        np.testing.assert_allclose(
            module.pose_in_reference_tag[:3, 3],
            np.subtract(MODULE_POSITION, WORLD_TAG_POSES[1]),
            atol=1e-9,
        )

    def test_carries_the_source_and_the_role(self):
        _, located = self._ceiling()
        module = next(item for item in located if item.tag_id == MODULE_TAG_ID)
        robot = next(item for item in located if item.tag_id == ROBOT_TAG_IDS[0])

        self.assertEqual(module.source, SOURCE_CEILING)
        self.assertEqual(module.role, "module")
        self.assertEqual(robot.role, "robot")

    def test_without_a_camera_pose_there_is_no_world_tag_relation(self):
        tag_map = cell_map()

        located = locate_modules(
            view(flange_view(), [MODULE_TAG_ID]),
            tag_map,
            localization=None,
            frame_id="cam_flange",
        )

        self.assertEqual(located[0].reference_tag_id, -1)
        self.assertIsNone(located[0].pose_in_reference_tag)


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class RoboterTest(unittest.TestCase):
    def _ceiling_located(self):
        tag_map = cell_map()
        tag_poses = view(ceiling_view(), [0, 1, 2, 3, MODULE_TAG_ID, *ROBOT_TAG_IDS])
        return tag_map, locate_modules(
            tag_poses,
            tag_map,
            localization=localize_camera(tag_poses, tag_map),
            frame_id=tag_map.frame_id,
            source=SOURCE_CEILING,
        )

    def test_both_robot_tags_describe_the_same_base(self):
        """Each tag carries its own CAD offset, so both must land on one pose."""
        _, located = self._ceiling_located()
        robots = [item for item in located if item.role == "robot"]

        self.assertEqual(len(robots), 2)
        np.testing.assert_allclose(robots[0].pose, robots[1].pose, atol=1e-9)

    def test_merges_several_robot_tags_into_one_result(self):
        tag_map, located = self._ceiling_located()

        merged = merge_by_module(located, tag_map=tag_map)

        robots = [item for item in merged if item.role == "robot"]
        self.assertEqual(len(robots), 1)
        self.assertEqual(robots[0].attributes["tagIds"], list(ROBOT_TAG_IDS))
        self.assertEqual(robots[0].attributes["tagCount"], 2)
        np.testing.assert_allclose(robots[0].pose[:3, 3], ROBOT_BASE, atol=1e-9)

    def test_names_the_world_tag_the_robot_stands_closest_to(self):
        """The question only the ceiling camera can answer."""
        tag_map, located = self._ceiling_located()

        tag_id, distance = world_tag_for_robot(
            tag_map, merge_by_module(located, tag_map=tag_map)
        )

        self.assertEqual(tag_id, 2)
        self.assertAlmostEqual(distance, math.dist(ROBOT_BASE, WORLD_TAG_POSES[2]))

    def test_returns_none_without_a_located_robot(self):
        tag_map, located = self._ceiling_located()
        without_robot = [item for item in located if item.role != "robot"]

        self.assertIsNone(world_tag_for_robot(tag_map, without_robot))


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class ZusammenfuehrenTest(unittest.TestCase):
    """Ceiling gives the overview, the hand camera the precise value."""

    def _both(self):
        tag_map = cell_map()

        ceiling_poses = view(ceiling_view(), [0, 1, 2, 3, MODULE_TAG_ID, *ROBOT_TAG_IDS])
        ceiling = locate_modules(
            ceiling_poses,
            tag_map,
            localization=localize_camera(ceiling_poses, tag_map),
            frame_id=tag_map.frame_id,
            source=SOURCE_CEILING,
        )

        # The hand camera has driven to world tag 2 and sees it with the module.
        flange_poses = view(flange_view(), [2, MODULE_TAG_ID], error_px=0.1)
        flange = locate_modules(
            flange_poses,
            tag_map,
            localization=localize_camera(flange_poses, tag_map),
            frame_id=tag_map.frame_id,
            source=SOURCE_FLANGE,
        )
        return tag_map, ceiling, flange

    def test_the_hand_measurement_wins_over_the_ceiling(self):
        _, ceiling, flange = self._both()

        merged = merge_locations(ceiling, flange)

        module = next(item for item in merged if item.module_id == "MOD-A")
        self.assertEqual(module.source, SOURCE_FLANGE)

    def test_keeps_what_only_the_ceiling_saw(self):
        tag_map, ceiling, flange = self._both()

        merged = merge_locations(merge_by_module(ceiling, tag_map=tag_map), flange)

        robot = next(item for item in merged if item.role == "robot")
        self.assertEqual(robot.source, SOURCE_CEILING)
        np.testing.assert_allclose(robot.pose[:3, 3], ROBOT_BASE, atol=1e-9)

    def test_both_cameras_agree_on_the_module(self):
        """Different viewpoints, one world frame -- that is the whole point."""
        _, ceiling, flange = self._both()

        from_ceiling = next(item for item in ceiling if item.tag_id == MODULE_TAG_ID)
        from_flange = next(item for item in flange if item.tag_id == MODULE_TAG_ID)

        np.testing.assert_allclose(from_ceiling.pose, from_flange.pose, atol=1e-9)
        self.assertEqual(from_ceiling.reference_tag_id, from_flange.reference_tag_id)


ALTE_KARTE = {
    "schema": "wsc.vision.tagmap/1",
    "frameId": "world",
    "anchorTagId": 0,
    "tagFamily": "tag36h11",
    "tags": [
        {
            "tagId": 0,
            "role": "world",
            "sizeM": 0.10,
            "poseInWorld": {"position": [0, 0, 0], "orientation": [0, 0, 0, 1]},
        },
        {
            "tagId": 12,
            "role": "robot_table",
            "sizeM": 0.08,
            "poseInWorld": {"position": [1.24, 0.31, 0], "orientation": [0, 0, 0, 1]},
        },
        {
            "tagId": 4,
            "role": "reference",
            "sizeM": 0.10,
            "poseInWorld": {"position": [2.4, 0, 0], "orientation": [0, 0, 0, 1]},
        },
        {
            "tagId": 7,
            "role": "module",
            "sizeM": 0.05,
            "moduleId": "MOD-A",
            "instanceId": "mod-a-1",
            "poseInWorld": None,
        },
    ],
}


def geschrieben(folder, data) -> Path:
    path = Path(folder) / "tagmap.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class SchemaTest(unittest.TestCase):
    """Eine alte Karte wird migriert, nicht abgelehnt.

    Abgelehnt klingt sauberer, legt aber in der Praxis die ganze Erkennung
    stumm: `AprilTagDetectionSource.open` scheitert dann an einer Textdatei.
    """

    def _migriert(self):
        with tempfile.TemporaryDirectory() as folder:
            return tagmap.load_tag_map(geschrieben(folder, ALTE_KARTE))

    def test_loads_a_schema_one_map(self):
        self.assertEqual(sorted(self._migriert().entries), [0, 4, 7, 12])

    def test_keeps_module_assignment_of_the_old_map(self):
        """Modulnamen und Zuordnung gehen bei der Migration nicht verloren."""
        entry = self._migriert()[7]

        self.assertEqual(entry.module_id, "MOD-A")
        self.assertEqual(entry.instance_id, "mod-a-1")

    def test_turns_the_old_reference_role_into_a_world_tag(self):
        """`reference` war ein fester Anker mit Weltpose -- also ein Welttag."""
        entry = self._migriert()[4]

        self.assertEqual(entry.role, tagmap.WORLD_ROLE)
        self.assertTrue(entry.is_reference)

    def test_turns_the_robot_table_into_a_movable_robot_tag(self):
        """Der Tisch war der Denkfehler: er ist beweglich, kein Anker."""
        entry = self._migriert()[12]

        self.assertEqual(entry.role, tagmap.ROBOT_ROLE)
        self.assertFalse(entry.is_reference)

    def test_drops_the_world_pose_of_the_former_robot_table(self):
        """Die alte Weltpose darf nicht stillschweigend weiterbenutzt werden."""
        migriert = self._migriert()

        self.assertIsNone(migriert[12].pose_in_world)
        self.assertEqual(sorted(migriert.reference_poses()), [0, 4])

    def test_says_out_loud_what_it_migrated(self):
        """Still umdeuten waere der gefaehrliche Teil -- also gibt es Meldungen."""
        with tempfile.TemporaryDirectory() as folder:
            path = geschrieben(folder, ALTE_KARTE)
            with self.assertLogs("tagloc.tagmap", level="WARNING") as logs:
                tagmap.load_tag_map(path)

        zusammen = "\n".join(logs.output)
        self.assertIn("robot_table", zusammen)
        self.assertIn("IGNORIERT", zusammen)

    def test_migrates_a_legacy_role_in_a_current_file_too(self):
        """Eine halb umgestellte Datei darf auch nicht scheitern."""
        halb = dict(ALTE_KARTE, schema=tagmap.SCHEMA)

        with tempfile.TemporaryDirectory() as folder:
            loaded = tagmap.load_tag_map(geschrieben(folder, halb))

        self.assertEqual(loaded[12].role, tagmap.ROBOT_ROLE)

    def test_still_rejects_a_typo_in_the_role(self):
        """Ein Tippfehler bleibt ein Fehler -- sonst ist der Tag stumm nichts."""
        kaputt = {
            "schema": tagmap.SCHEMA,
            "tags": [{"tagId": 9, "sizeM": 0.05, "role": "wrold"}],
        }

        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError) as caught:
                tagmap.load_tag_map(geschrieben(folder, kaputt))

        self.assertIn("wrold", str(caught.exception))

    def test_still_rejects_an_unknown_schema(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError) as caught:
                tagmap.load_tag_map(geschrieben(folder, {"schema": "falsch/9", "tags": []}))

        self.assertIn("falsch/9", str(caught.exception))


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class PlatzhalterTest(unittest.TestCase):
    """Ohne Karteneintrag muss ein Tag trotzdem als Modul herauskommen.

    Das Frontend zeichnet je Detektion eine Box. Faellt ein unbekannter Tag
    unter den Tisch, fehlt die Box -- und der Aufbau sieht aus, als wuerde die
    Erkennung nicht laufen.
    """

    def test_an_unmapped_tag_becomes_a_placeholder_module(self):
        tag_map = cell_map()
        tag_poses = view(ceiling_view(), [0, 1, 2, 3, UNMAPPED_TAG_ID])

        located = locate_modules(
            tag_poses,
            tag_map,
            localization=localize_camera(tag_poses, tag_map),
            frame_id="world",
            source=SOURCE_CEILING,
        )

        platzhalter = next(item for item in located if item.tag_id == UNMAPPED_TAG_ID)
        self.assertEqual(platzhalter.module_id, f"TAG-{UNMAPPED_TAG_ID}")
        self.assertEqual(platzhalter.instance_id, f"tag-{UNMAPPED_TAG_ID}")
        # Ohne Karteneintrag gibt es keinen CAD-Versatz, die Pose ist die
        # Tag-Pose -- aber sie ist da, und zwar im Welt-KS.
        np.testing.assert_allclose(
            platzhalter.pose[:3, 3], UNMAPPED_POSITION, atol=1e-9
        )

    def test_without_any_map_every_tag_still_comes_out(self):
        """Der Fall 'keine Tag-Map': alles wird als Platzhalter gemeldet."""
        leer = tagmap.empty_tag_map()
        tag_poses = view(ceiling_view(), [0, 7, 20])

        located = locate_modules(
            tag_poses, leer, localization=None, frame_id="cam_ceiling"
        )

        self.assertEqual(
            sorted(item.module_id for item in located), ["TAG-0", "TAG-20", "TAG-7"]
        )
        self.assertEqual({item.frame_id for item in located}, {"cam_ceiling"})


if __name__ == "__main__":
    unittest.main()

"""Tests for the tag map: placement, closure error, file round trip.

Co-observations are synthesised from a known ground truth --
`T_cam_tag = invert(T_world_cam) @ T_world_tag`. That makes every result
verifiable by hand and needs neither images nor cv2.
"""

import json
import math
import tempfile
import unittest
from pathlib import Path

try:
    import numpy as np

    from tagloc import geometry, tagmap
except Exception:  # numpy broken in this environment
    np = None
    geometry = None
    tagmap = None


def pose(translation, *, rvec=(0.0, 0.0, 0.0)):
    return geometry.from_rvec_tvec(rvec, translation)


def truth_poses() -> dict:
    """Return four tags in the world frame; tag 0 is at the origin and is the anchor."""
    return {
        0: geometry.identity(),
        1: pose((0.40, 0.00, 0.00), rvec=(0.0, 0.0, math.radians(15.0))),
        2: pose((0.40, 0.30, 0.02), rvec=(0.0, math.radians(-20.0), 0.0)),
        3: pose((-0.15, 0.25, -0.05), rvec=(math.radians(8.0), 0.0, math.radians(90.0))),
    }


def camera_pose(translation, *, rvec):
    return geometry.from_rvec_tvec(rvec, translation)


def observation_from(world_poses, pose_world_cam, tag_ids) -> dict:
    """Return what a camera at this pose would see of these tags."""
    inverse = geometry.invert(pose_world_cam)
    return {tag_id: geometry.compose(inverse, world_poses[tag_id]) for tag_id in tag_ids}


#: Three camera positions. Without numpy they stay empty and all tests skip.
if np is not None:
    CAMERA_A = camera_pose((0.1, -0.8, 1.6), rvec=(math.radians(10.0), 0.2, -0.4))
    CAMERA_B = camera_pose((-0.6, 0.9, 1.2), rvec=(-0.3, math.radians(25.0), 1.1))
    CAMERA_C = camera_pose((0.8, 0.7, 1.9), rvec=(0.5, -0.5, 0.05))
else:
    CAMERA_A = CAMERA_B = CAMERA_C = None


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class PlaceTagsTest(unittest.TestCase):
    def test_reproduces_the_truth_when_the_anchor_is_the_world_origin(self):
        world = truth_poses()
        observations = [
            observation_from(world, CAMERA_A, [0, 1, 2]),
            observation_from(world, CAMERA_B, [0, 2, 3]),
            observation_from(world, CAMERA_C, [1, 2, 3]),
        ]

        placed = tagmap.place_tags(observations, anchor_tag_id=0)

        self.assertEqual(sorted(placed), [0, 1, 2, 3])
        for tag_id, expected in world.items():
            np.testing.assert_allclose(placed[tag_id], expected, atol=1e-12)

    def test_puts_the_anchor_at_the_origin(self):
        world = truth_poses()
        placed = tagmap.place_tags(
            [observation_from(world, CAMERA_A, [1, 2])], anchor_tag_id=1
        )

        np.testing.assert_allclose(placed[1], geometry.identity(), atol=1e-15)

    def test_places_all_tags_reachable_from_the_anchor(self):
        """Image 1 shows 0 and 1, image 2 shows 1 and 2 -- 2 links to the anchor only indirectly."""
        world = truth_poses()
        observations = [
            observation_from(world, CAMERA_A, [0, 1]),
            observation_from(world, CAMERA_B, [1, 2]),
        ]

        placed = tagmap.place_tags(observations, anchor_tag_id=0)

        self.assertEqual(sorted(placed), [0, 1, 2])
        np.testing.assert_allclose(placed[2], world[2], atol=1e-12)

    def test_leaves_out_a_tag_without_a_path_to_the_anchor(self):
        world = truth_poses()
        observations = [
            observation_from(world, CAMERA_A, [0, 1]),
            {9: pose((0.0, 0.0, 1.0))},  # captured alone, no edge
        ]

        placed = tagmap.place_tags(observations, anchor_tag_id=0)

        self.assertNotIn(9, placed)
        self.assertEqual(tagmap.missing_tags(observations, placed), [9])

    def test_reports_nothing_missing_when_everything_is_connected(self):
        world = truth_poses()
        observations = [observation_from(world, CAMERA_A, [0, 1, 2, 3])]

        placed = tagmap.place_tags(observations, anchor_tag_id=0)

        self.assertEqual(tagmap.missing_tags(observations, placed), [])

    def test_rejects_an_anchor_that_no_image_shows(self):
        world = truth_poses()
        observations = [observation_from(world, CAMERA_A, [1, 2])]

        with self.assertRaises(ValueError) as caught:
            tagmap.place_tags(observations, anchor_tag_id=0)
        self.assertIn("0", str(caught.exception))

    def test_collects_every_observed_tag_id(self):
        world = truth_poses()
        observations = [
            observation_from(world, CAMERA_A, [0, 1]),
            observation_from(world, CAMERA_B, [2, 3]),
        ]

        self.assertEqual(tagmap.observed_tag_ids(observations), {0, 1, 2, 3})


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class RelativePosesTest(unittest.TestCase):
    def test_cancels_the_camera_pose_out(self):
        world = truth_poses()
        from_a = tagmap.relative_poses([observation_from(world, CAMERA_A, [0, 1])])
        from_b = tagmap.relative_poses([observation_from(world, CAMERA_B, [0, 1])])

        np.testing.assert_allclose(from_a[(0, 1)][0], from_b[(0, 1)][0], atol=1e-12)

    def test_stores_both_directions_of_every_edge(self):
        world = truth_poses()
        edges = tagmap.relative_poses([observation_from(world, CAMERA_A, [0, 1])])

        self.assertEqual(sorted(edges), [(0, 1), (1, 0)])
        np.testing.assert_allclose(
            geometry.compose(edges[(0, 1)][0], edges[(1, 0)][0]),
            geometry.identity(),
            atol=1e-12,
        )


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class ResidualsTest(unittest.TestCase):
    def _ring(self, displacement_m: float):
        """Three tags as a closed ring; one edge is corrupted."""
        world = truth_poses()
        corrupted = dict(world)
        corrupted[2] = geometry.compose(
            world[2], pose((displacement_m, 0.0, 0.0))
        )
        return [
            observation_from(world, CAMERA_A, [0, 1]),
            observation_from(world, CAMERA_B, [1, 2]),
            observation_from(corrupted, CAMERA_C, [0, 2]),
        ]

    def test_stays_near_zero_for_consistent_observations(self):
        observations = self._ring(0.0)
        placed = tagmap.place_tags(observations, anchor_tag_id=0)

        residual_map = tagmap.residuals(observations, placed)

        self.assertEqual(sorted(residual_map), [(0, 1), (0, 2), (1, 2)])
        for position_m, angle_rad in residual_map.values():
            self.assertLess(position_m, 1e-9)
            self.assertLess(angle_rad, 1e-9)

    def test_grows_clearly_when_one_measurement_is_wrong(self):
        observations = self._ring(0.05)
        placed = tagmap.place_tags(observations, anchor_tag_id=0)

        residual_map = tagmap.residuals(observations, placed)

        # The corrupted edge and its counterpart must show the error; the
        # ring no longer closes.
        self.assertGreater(residual_map[(0, 2)][0], 0.01)
        self.assertGreater(residual_map[(1, 2)][0], 0.01)

    def test_reports_nothing_without_common_edges(self):
        self.assertEqual(tagmap.residuals([], {}), {})
        self.assertEqual(tagmap.format_residual_report({}), "keine gemeinsamen Kanten")

    def test_formats_the_largest_residual_first(self):
        report = tagmap.format_residual_report({(0, 1): (0.001, 0.01), (0, 2): (0.02, 0.2)})

        lines = report.splitlines()
        self.assertIn("Kante", lines[0])
        self.assertIn("0 - 2", lines[1])
        self.assertIn("0 - 1", lines[2])


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class TagMapQueryTest(unittest.TestCase):
    def _map(self) -> "tagmap.TagMap":
        return tagmap.TagMap(
            frame_id="world",
            anchor_tag_id=0,
            entries={
                0: tagmap.TagEntry(
                    tag_id=0, role="world", size_m=0.10, pose_in_world=geometry.identity()
                ),
                5: tagmap.TagEntry(
                    tag_id=5, role="module", size_m=0.04, module_id="MOD-A",
                    instance_id="mod-a-1",
                ),
                7: tagmap.TagEntry(tag_id=7, role="world", size_m=0.08),
                9: tagmap.TagEntry(
                    tag_id=9, role="robot", size_m=0.08, module_id="UR5e",
                    instance_id="ur5e-1",
                ),
            },
        )

    def test_prefers_the_size_from_the_map_over_the_fallback(self):
        tag_map = self._map()

        self.assertAlmostEqual(tag_map.size_for(5, 0.05), 0.04)
        self.assertAlmostEqual(tag_map.size_for(0, 0.05), 0.10)

    def test_falls_back_for_an_unknown_tag(self):
        self.assertAlmostEqual(self._map().size_for(99, 0.05), 0.05)

    def test_lists_only_tags_with_a_known_world_pose_as_references(self):
        # Tag 7 is a world tag but not surveyed yet -- so it's not an anchor.
        self.assertEqual(sorted(self._map().reference_poses()), [0])

    def test_lists_both_world_tags_regardless_of_the_survey(self):
        self.assertEqual(self._map().world_tag_ids(), [0, 7])

    def test_counts_the_robot_among_the_module_tags(self):
        """The robot is a module that is always in use -- not a separate case."""
        entries = self._map().module_entries()

        self.assertEqual(sorted(entry.tag_id for entry in entries), [5, 9])

    def test_lists_the_robot_tags_separately(self):
        entries = self._map().robot_entries()

        self.assertEqual([entry.tag_id for entry in entries], [9])
        self.assertEqual(entries[0].module_id, "UR5e")

    def test_a_module_tag_is_never_a_reference(self):
        tag_map = self._map()

        self.assertFalse(tag_map[5].is_reference)
        self.assertFalse(tag_map[9].is_reference)
        self.assertTrue(tag_map[0].is_reference)

    def test_supports_containment_and_item_access(self):
        tag_map = self._map()

        self.assertIn(5, tag_map)
        self.assertNotIn(99, tag_map)
        self.assertEqual(tag_map[5].module_id, "MOD-A")
        self.assertIsNone(tag_map.get(99))

    def test_an_empty_map_knows_nothing(self):
        empty = tagmap.empty_tag_map()

        self.assertEqual(empty.entries, {})
        self.assertEqual(empty.reference_poses(), {})
        self.assertAlmostEqual(empty.size_for(3, 0.05), 0.05)


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class WithWorldPosesTest(unittest.TestCase):
    def test_leaves_module_entries_without_a_world_pose(self):
        tag_map = tagmap.TagMap(
            entries={
                5: tagmap.TagEntry(tag_id=5, role="module", size_m=0.04, module_id="MOD-A"),
                7: tagmap.TagEntry(tag_id=7, role="world", size_m=0.08),
            }
        )
        placed = {5: pose((1.0, 0.0, 0.0)), 7: pose((0.0, 2.0, 0.0))}

        updated = tagmap.with_world_poses(tag_map, placed)

        self.assertIsNone(updated[5].pose_in_world)
        self.assertIsNotNone(updated[7].pose_in_world)
        np.testing.assert_allclose(updated[7].pose_in_world, placed[7], atol=1e-15)

    def test_keeps_role_module_id_and_cad_offset_of_existing_entries(self):
        offset = pose((0.0, 0.0, -0.04))
        tag_map = tagmap.TagMap(
            entries={
                7: tagmap.TagEntry(
                    tag_id=7,
                    role="robot",
                    size_m=0.08,
                    module_id="UR5e",
                    instance_id="ur5e-1",
                    tag_to_module=offset,
                )
            }
        )

        updated = tagmap.with_world_poses(tag_map, {7: pose((0.5, 0.0, 0.0))})

        self.assertEqual(updated[7].role, "robot")
        self.assertEqual(updated[7].module_id, "UR5e")
        self.assertAlmostEqual(updated[7].size_m, 0.08)
        np.testing.assert_allclose(updated[7].tag_to_module, offset, atol=1e-15)

    def test_creates_missing_entries_with_the_given_role_and_size(self):
        updated = tagmap.with_world_poses(
            tagmap.empty_tag_map(),
            {4: pose((0.1, 0.2, 0.3))},
            roles={4: "world"},
            sizes={4: 0.12},
        )

        self.assertEqual(updated[4].role, "world")
        self.assertAlmostEqual(updated[4].size_m, 0.12)
        self.assertTrue(updated[4].is_reference)

    def test_uses_the_default_size_for_unknown_tags(self):
        updated = tagmap.with_world_poses(
            tagmap.empty_tag_map(), {4: pose((0.0, 0.0, 0.0))}, default_size_m=0.07
        )

        self.assertAlmostEqual(updated[4].size_m, 0.07)
        self.assertEqual(updated[4].role, tagmap.WORLD_ROLE)


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class TagMapFileTest(unittest.TestCase):
    def _sample(self) -> "tagmap.TagMap":
        return tagmap.TagMap(
            frame_id="zelle",
            anchor_tag_id=3,
            tag_family="tag25h9",
            entries={
                3: tagmap.TagEntry(
                    tag_id=3, role="world", size_m=0.10, pose_in_world=geometry.identity()
                ),
                5: tagmap.TagEntry(
                    tag_id=5,
                    role="module",
                    size_m=0.04,
                    module_id="MOD-A",
                    instance_id="mod-a-1",
                    tag_to_module=pose((0.0, 0.0, -0.04)),
                ),
                4: tagmap.TagEntry(
                    tag_id=4,
                    role="world",
                    size_m=0.10,
                    pose_in_world=pose((1.0, -0.5, 0.25), rvec=(0.0, 0.0, math.radians(45.0))),
                ),
                7: tagmap.TagEntry(
                    tag_id=7,
                    role="robot",
                    size_m=0.08,
                    module_id="UR5e",
                    instance_id="ur5e-1",
                    tag_to_module=pose((0.0, 0.0, -0.12)),
                ),
            },
        )

    def test_round_trips_through_a_file(self):
        original = self._sample()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sub" / "tagmap.json"
            tagmap.save_tag_map(path, original)
            loaded = tagmap.load_tag_map(path)

        self.assertEqual(loaded.frame_id, "zelle")
        self.assertEqual(loaded.anchor_tag_id, 3)
        self.assertEqual(loaded.tag_family, "tag25h9")
        self.assertEqual(sorted(loaded.entries), [3, 4, 5, 7])
        for tag_id, entry in original.entries.items():
            other = loaded[tag_id]
            self.assertEqual(other.role, entry.role)
            self.assertAlmostEqual(other.size_m, entry.size_m)
            self.assertEqual(other.module_id, entry.module_id)
            self.assertEqual(other.instance_id, entry.instance_id)
            np.testing.assert_allclose(other.tag_to_module, entry.tag_to_module, atol=1e-14)
            if entry.pose_in_world is None:
                self.assertIsNone(other.pose_in_world)
            else:
                np.testing.assert_allclose(
                    other.pose_in_world, entry.pose_in_world, atol=1e-14
                )

    def test_writes_a_movable_tag_with_a_null_world_pose(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tagmap.json"
            tagmap.save_tag_map(path, self._sample())
            data = json.loads(path.read_text(encoding="utf-8"))

        by_id = {tag["tagId"]: tag for tag in data["tags"]}
        self.assertEqual(data["schema"], tagmap.SCHEMA)
        self.assertEqual([tag["tagId"] for tag in data["tags"]], [3, 4, 5, 7])
        self.assertIsNone(by_id[5]["poseInWorld"])
        self.assertIsNone(by_id[7]["poseInWorld"])  # der Roboter ist beweglich
        self.assertIn("tagToModule", by_id[5])
        self.assertNotIn("tagToModule", by_id[3])  # identity is omitted
        self.assertEqual(by_id[5]["moduleId"], "MOD-A")

    def test_rejects_an_unknown_schema(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tagmap.json"
            path.write_text(json.dumps({"schema": "falsch/9", "tags": []}), encoding="utf-8")

            with self.assertRaises(ValueError) as caught:
                tagmap.load_tag_map(path)

        self.assertIn("falsch/9", str(caught.exception))
        self.assertIn(tagmap.SCHEMA, str(caught.exception))

    def test_rejects_a_missing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fehlt.json"

            with self.assertRaises(FileNotFoundError) as caught:
                tagmap.load_tag_map(path)

        self.assertIn("fehlt.json", str(caught.exception))

    def test_defaults_role_and_pose_for_a_sparse_entry(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tagmap.json"
            path.write_text(
                json.dumps({"schema": tagmap.SCHEMA, "tags": [{"tagId": 2, "sizeM": 0.05}]}),
                encoding="utf-8",
            )
            loaded = tagmap.load_tag_map(path)

        self.assertEqual(loaded.frame_id, "world")
        self.assertEqual(loaded.tag_family, "tag36h11")
        self.assertEqual(loaded[2].role, tagmap.MODULE_ROLE)
        self.assertIsNone(loaded[2].pose_in_world)
        np.testing.assert_allclose(loaded[2].tag_to_module, geometry.identity(), atol=1e-15)


if __name__ == "__main__":
    unittest.main()

"""Tests fuer `tagloc.localize.locate_in_frame` -- reine Rechnung, ohne OpenCV."""

import unittest

try:
    import numpy as np

    from tagloc import geometry, tagmap
    from tagloc.localize import locate_in_frame
    from tagloc.observations import TagPose
except Exception:  # numpy in dieser Umgebung defekt
    np = None


def _tag_pose(tag_id: int, translation):
    return TagPose(
        tag_id=tag_id,
        pose_cam_tag=geometry.from_rvec_tvec((0.0, 0.0, 0.0), translation),
        reprojection_error_px=0.5,
    )


def _world_map():
    return tagmap.TagMap(
        frame_id="world",
        entries={
            0: tagmap.TagEntry(
                tag_id=0,
                role="world",
                size_m=0.10,
                pose_in_world=geometry.from_rvec_tvec((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
            ),
            5: tagmap.TagEntry(tag_id=5, role="module", size_m=0.04, module_id="MOD-A"),
        },
    )


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class LocateInFrameTest(unittest.TestCase):
    def test_uses_the_map_frame_when_a_reference_tag_is_visible(self):
        pose_world_cam, located = locate_in_frame(
            [_tag_pose(0, (0.0, 0.0, 2.0)), _tag_pose(5, (0.3, 0.0, 2.0))],
            _world_map(),
            camera_frame_id="cam_ceiling",
            max_reprojection_error_px=3.0,
        )

        np.testing.assert_allclose(pose_world_cam[:3, 3], (1.0, 0.0, -2.0), atol=1e-12)
        [module] = located
        self.assertEqual(module.module_id, "MOD-A")
        self.assertEqual(module.frame_id, "world")
        np.testing.assert_allclose(module.pose[:3, 3], (1.3, 0.0, 0.0), atol=1e-12)

    def test_stays_in_the_camera_frame_without_a_reference_tag(self):
        pose_world_cam, located = locate_in_frame(
            [_tag_pose(5, (0.3, 0.0, 2.0))],
            _world_map(),
            camera_frame_id="cam_ceiling",
            max_reprojection_error_px=3.0,
        )

        self.assertIsNone(pose_world_cam)
        [module] = located
        self.assertEqual(module.frame_id, "cam_ceiling")
        np.testing.assert_allclose(module.pose[:3, 3], (0.3, 0.0, 2.0), atol=1e-12)

    def test_reports_nothing_for_an_empty_image(self):
        self.assertEqual(
            locate_in_frame([], tagmap.empty_tag_map(), camera_frame_id="cam"), (None, [])
        )


if __name__ == "__main__":
    unittest.main()

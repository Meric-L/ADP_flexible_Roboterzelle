"""Golden values for the pose math in `tagloc.geometry`.

Needs only numpy: no cv2, no camera, no files.
"""

import math
import unittest

try:
    import numpy as np

    from tagloc import geometry
except Exception:  # numpy broken in this environment
    np = None
    geometry = None

SQRT_HALF = 0.7071067811865476


def rotation_z(angle_rad: float):
    """Return a rotation about the Z axis."""
    return geometry.rotation_from_rvec([0.0, 0.0, angle_rad])


def pose_z(angle_rad: float, translation=(0.0, 0.0, 0.0)):
    return geometry.from_rotation_translation(rotation_z(angle_rad), translation)


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class RvecRoundTripTest(unittest.TestCase):
    def test_returns_the_same_rvec_and_tvec_for_a_generic_rotation(self):
        rvec = np.array([0.3, -0.7, 1.1])
        tvec = np.array([0.12, -0.34, 1.56])

        back_rvec, back_tvec = geometry.to_rvec_tvec(geometry.from_rvec_tvec(rvec, tvec))

        np.testing.assert_allclose(back_rvec, rvec, atol=1e-12)
        np.testing.assert_allclose(back_tvec, tvec, atol=1e-15)

    def test_returns_the_identity_for_a_vanishing_rotation(self):
        pose = geometry.from_rvec_tvec([0.0, 0.0, 0.0], [1.0, 2.0, 3.0])

        np.testing.assert_allclose(pose[:3, :3], np.eye(3), atol=1e-15)
        np.testing.assert_allclose(geometry.to_rvec_tvec(pose)[0], np.zeros(3), atol=1e-15)

    def test_survives_a_rotation_angle_close_to_zero(self):
        rvec = np.array([1e-10, -2e-10, 3e-10])

        pose = geometry.from_rvec_tvec(rvec, [0.0, 0.0, 0.0])

        # At such small angles the rotation matrix is indistinguishable from
        # identity; the requirement is just that nothing divides by zero.
        np.testing.assert_allclose(pose[:3, :3], np.eye(3), atol=1e-9)
        self.assertLess(float(np.linalg.norm(geometry.to_rvec_tvec(pose)[0])), 1e-8)

    def test_survives_a_rotation_angle_close_to_pi(self):
        axis = np.array([1.0, 2.0, -2.0]) / 3.0
        rvec = axis * (math.pi - 1e-9)

        pose = geometry.from_rvec_tvec(rvec, [0.0, 0.0, 0.0])
        back_rvec, _ = geometry.to_rvec_tvec(pose)

        # At pi the axis sign is free -- so the reconstructed rotation
        # matrix is compared, not the rotation vector.
        np.testing.assert_allclose(
            geometry.rotation_from_rvec(back_rvec), pose[:3, :3], atol=1e-6
        )

    def test_survives_a_rotation_of_exactly_pi(self):
        rvec = np.array([0.0, 0.0, math.pi])

        pose = geometry.from_rvec_tvec(rvec, [0.0, 0.0, 0.0])
        back_rvec, _ = geometry.to_rvec_tvec(pose)

        np.testing.assert_allclose(
            geometry.rotation_from_rvec(back_rvec), pose[:3, :3], atol=1e-9
        )


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class InvertTest(unittest.TestCase):
    def test_composing_a_pose_with_its_inverse_yields_the_identity(self):
        pose = geometry.from_rvec_tvec([0.4, 0.1, -0.9], [0.25, -1.5, 3.0])

        np.testing.assert_allclose(
            geometry.compose(pose, geometry.invert(pose)), geometry.identity(), atol=1e-14
        )
        np.testing.assert_allclose(
            geometry.compose(geometry.invert(pose), pose), geometry.identity(), atol=1e-14
        )


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class QuaternionTest(unittest.TestCase):
    def test_returns_the_known_quaternion_for_a_quarter_turn_about_z(self):
        position, orientation = geometry.to_position_quaternion(
            pose_z(math.pi / 2.0, (1.0, 2.0, 3.0))
        )

        self.assertEqual(position, (1.0, 2.0, 3.0))
        np.testing.assert_allclose(orientation, [0.0, 0.0, SQRT_HALF, SQRT_HALF], atol=1e-15)

    def test_keeps_the_scalar_part_non_negative(self):
        for angle_deg in (0, 45, 90, 179, 180, 200, 270, 359):
            for axis in ([1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.3, -0.5, 0.81]):
                axis_vector = np.asarray(axis) / np.linalg.norm(axis)
                pose = geometry.from_rvec_tvec(axis_vector * math.radians(angle_deg), [0, 0, 0])
                _, orientation = geometry.to_position_quaternion(pose)
                self.assertGreaterEqual(orientation[3], 0.0, f"{angle_deg} deg um {axis}")

    def test_from_position_quaternion_reverses_to_position_quaternion(self):
        pose = geometry.from_rvec_tvec([0.9, -0.2, 0.35], [0.5, 0.25, -0.125])

        position, orientation = geometry.to_position_quaternion(pose)

        np.testing.assert_allclose(
            geometry.from_position_quaternion(position, orientation), pose, atol=1e-14
        )

    def test_accepts_the_quaternion_it_produced_for_a_quarter_turn(self):
        pose = geometry.from_position_quaternion(
            [0.0, 0.0, 0.0], [0.0, 0.0, SQRT_HALF, SQRT_HALF]
        )

        np.testing.assert_allclose(pose[:3, :3], rotation_z(math.pi / 2.0), atol=1e-15)

    def test_rejects_a_quaternion_of_length_zero(self):
        with self.assertRaises(ValueError):
            geometry.rotation_from_quaternion([0.0, 0.0, 0.0, 0.0])


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class ComposeTest(unittest.TestCase):
    def test_maps_a_point_from_the_rightmost_frame_into_the_leftmost(self):
        # b sits in a rotated 90 degrees and shifted by x=1; c sits in b at y=2.
        pose_a_b = pose_z(math.pi / 2.0, (1.0, 0.0, 0.0))
        pose_b_c = geometry.from_rotation_translation(np.eye(3), (0.0, 2.0, 0.0))
        point_c = np.array([3.0, 0.0, 0.0, 1.0])

        pose_a_c = geometry.compose(pose_a_b, pose_b_c)
        point_a = pose_a_c @ point_c

        # Point in b: (3, 2, 0). In a: 90 degrees about z turns it into
        # (-2, 3, 0), plus the origin offset (1, 0, 0).
        np.testing.assert_allclose(point_a[:3], [-1.0, 3.0, 0.0], atol=1e-14)
        np.testing.assert_allclose(point_a, pose_a_b @ (pose_b_c @ point_c), atol=1e-14)

    def test_is_associative(self):
        first = geometry.from_rvec_tvec([0.1, 0.2, 0.3], [1.0, 0.0, 0.0])
        second = geometry.from_rvec_tvec([-0.4, 0.05, 0.9], [0.0, 2.0, 0.0])
        third = geometry.from_rvec_tvec([0.7, -0.7, 0.1], [0.0, 0.0, 3.0])

        np.testing.assert_allclose(
            geometry.compose(geometry.compose(first, second), third),
            geometry.compose(first, geometry.compose(second, third)),
            atol=1e-14,
        )
        np.testing.assert_allclose(
            geometry.compose(first, second, third),
            geometry.compose(first, geometry.compose(second, third)),
            atol=1e-14,
        )

    def test_without_arguments_returns_the_identity(self):
        np.testing.assert_allclose(geometry.compose(), geometry.identity(), atol=1e-15)


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class AveragePosesTest(unittest.TestCase):
    def test_returns_the_pose_itself_for_identical_inputs(self):
        pose = geometry.from_rvec_tvec([0.2, -0.6, 0.4], [0.3, 0.9, -1.2])

        np.testing.assert_allclose(geometry.average_poses([pose] * 4), pose, atol=1e-13)

    def test_does_not_cancel_two_quaternions_of_opposite_sign(self):
        """q and -q are the same rotation -- averaging must not cancel them."""
        quaternion = np.array([0.0, 0.0, SQRT_HALF, SQRT_HALF])
        first = geometry.from_position_quaternion([0.0, 0.0, 0.0], quaternion)
        second = geometry.from_position_quaternion([0.0, 0.0, 0.0], -quaternion)

        averaged = geometry.average_poses([first, second])

        np.testing.assert_allclose(averaged[:3, :3], first[:3, :3], atol=1e-13)
        self.assertLess(geometry.rotation_distance_rad(averaged, first), 1e-9)

    def test_does_not_cancel_two_rotations_straddling_half_a_turn(self):
        """The scalar part's sign flips here -- the critical case."""
        first = pose_z(math.radians(179.0))
        second = pose_z(math.radians(181.0))

        averaged = geometry.average_poses([first, second])

        self.assertLess(
            geometry.rotation_distance_rad(averaged, pose_z(math.pi)), math.radians(0.01)
        )

    def test_averages_the_translations_arithmetically(self):
        poses = [
            geometry.from_rotation_translation(np.eye(3), (0.0, 0.0, 0.0)),
            geometry.from_rotation_translation(np.eye(3), (2.0, 4.0, 6.0)),
        ]

        np.testing.assert_allclose(
            geometry.average_poses(poses)[:3, 3], [1.0, 2.0, 3.0], atol=1e-15
        )

    def test_averages_two_rotations_to_the_one_in_between(self):
        averaged = geometry.average_poses([pose_z(0.0), pose_z(math.radians(90.0))])

        self.assertAlmostEqual(
            geometry.rotation_distance_rad(averaged, pose_z(math.radians(45.0))), 0.0, places=12
        )

    def test_rejects_an_empty_sequence(self):
        with self.assertRaises(ValueError):
            geometry.average_poses([])


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class DistanceTest(unittest.TestCase):
    def test_measures_the_distance_between_the_two_origins(self):
        first = geometry.from_rotation_translation(rotation_z(1.0), (0.0, 0.0, 0.0))
        second = geometry.from_rotation_translation(np.eye(3), (3.0, 4.0, 12.0))

        # 3-4-12 is a Pythagorean quadruple: the length is exactly 13.
        self.assertAlmostEqual(geometry.translation_distance_m(first, second), 13.0, places=12)
        self.assertAlmostEqual(geometry.translation_distance_m(second, first), 13.0, places=12)

    def test_measures_the_residual_rotation_angle(self):
        first = pose_z(math.radians(10.0), (5.0, 5.0, 5.0))
        second = pose_z(math.radians(40.0), (-1.0, 0.0, 0.0))

        self.assertAlmostEqual(
            geometry.rotation_distance_rad(first, second), math.radians(30.0), places=12
        )

    def test_is_zero_between_a_pose_and_itself(self):
        pose = geometry.from_rvec_tvec([0.5, 0.5, 0.5], [1.0, 1.0, 1.0])

        self.assertAlmostEqual(geometry.translation_distance_m(pose, pose), 0.0, places=15)
        self.assertAlmostEqual(geometry.rotation_distance_rad(pose, pose), 0.0, places=7)

    def test_reports_half_a_turn_as_pi(self):
        self.assertAlmostEqual(
            geometry.rotation_distance_rad(pose_z(0.0), pose_z(math.pi)), math.pi, places=7
        )


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class PoseDictTest(unittest.TestCase):
    def test_round_trips_through_the_json_shaped_dictionary(self):
        pose = geometry.from_rvec_tvec([0.25, -0.75, 1.5], [0.01, 0.02, 0.03])

        data = geometry.pose_to_dict(pose)

        self.assertEqual(sorted(data), ["orientation", "position"])
        self.assertIsInstance(data["position"], list)
        self.assertEqual(len(data["orientation"]), 4)
        np.testing.assert_allclose(geometry.pose_from_dict(data), pose, atol=1e-14)

    def test_writes_the_quaternion_in_xyzw_order(self):
        data = geometry.pose_to_dict(pose_z(math.pi / 2.0))

        np.testing.assert_allclose(
            data["orientation"], [0.0, 0.0, SQRT_HALF, SQRT_HALF], atol=1e-15
        )


if __name__ == "__main__":
    unittest.main()

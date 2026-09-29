"""Fahrziele des Layer-2-Laufs: eine Rechnung fuer Welttag und Modultag."""

import json
import math
import unittest

try:
    import numpy as np

    from tagloc import geometry
    from tagloc.tagmap import TagEntry, TagMap
    from vision_server.detection.apriltag import robot_pose_from_parameters
    from vision_server.layer2 import plan
except Exception:  # pragma: no cover - numpy fehlt in dieser Umgebung
    np = None

#: Basis leicht verdreht und verschoben -- in der Identitaet fiele ein
#: vertauschtes inv() in der Basisumrechnung nicht auf.
BASE_POSITION = (1.2, 0.8, 0.0)
BASE_YAW_RAD = math.radians(30.0)
FLANGE_CAM_POSITION = (0.04, -0.01, 0.09)


def yaw(angle_rad):
    c, s = math.cos(angle_rad), math.sin(angle_rad)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def base_pose():
    return geometry.from_rotation_translation(yaw(BASE_YAW_RAD), BASE_POSITION)


def flange_cam():
    return geometry.from_rotation_translation(np.eye(3), FLANGE_CAM_POSITION)


def tag_map(*world_tags):
    entries = {
        tag_id: TagEntry(tag_id, "world", 0.1, pose_in_world=pose) for tag_id, pose in world_tags
    }
    return TagMap(entries=entries)


def pose(position, rotation=None):
    return geometry.from_rotation_translation(
        np.eye(3) if rotation is None else rotation, position
    )


def module(module_id, position, *tags):
    return plan.ModuleRequest(
        module_id=module_id,
        name=module_id,
        pose_world_module=pose(position),
        tags=tuple(plan.TagRequest(tag_id, tag_pose) for tag_id, tag_pose in tags),
    )


@unittest.skipUnless(np is not None, "numpy fehlt")
class ViewTargetTest(unittest.TestCase):
    def test_the_camera_looks_down_on_a_tag_lying_face_up(self):
        target = plan.view_target(geometry.identity(), geometry.identity(), geometry.identity(), 0.3)
        np.testing.assert_allclose(target.camera_in_world[:3, 3], [0, 0, 0.3], atol=1e-12)
        # OpenCV-Kamera: +z ist die Blickrichtung -- nach unten auf den Tag.
        np.testing.assert_allclose(target.camera_in_world[:3, 2], [0, 0, -1], atol=1e-12)

    def test_the_chain_closes(self):
        tag = pose((1.5, 1.4, 0.4), yaw(0.7))
        target = plan.view_target(tag, base_pose(), flange_cam(), 0.3)
        # Flansch · T_flansch_cam = Kamera, Basis · Flansch_in_Basis = Flansch.
        np.testing.assert_allclose(
            geometry.compose(target.flange_in_world, flange_cam()), target.camera_in_world, atol=1e-12
        )
        np.testing.assert_allclose(
            geometry.compose(base_pose(), target.flange_in_base), target.flange_in_world, atol=1e-12
        )
        self.assertAlmostEqual(
            target.reach_m, float(np.linalg.norm(target.flange_in_base[:3, 3]))
        )

    def test_world_tag_and_module_tag_are_planned_alike(self):
        tag_in_world = pose((1.5, 1.4, 0.4), yaw(0.7))
        anchor = plan.StepSpec(purpose=plan.ANCHOR, name="Welttag", world_tag=(0, tag_in_world))
        # Dasselbe Tag, einmal als Modultag: Modulpose mal Tag-Lage.
        module_pose = pose((1.0, 1.0, 0.0))
        tag_in_module = geometry.compose(geometry.invert(module_pose), tag_in_world)
        measured = plan.StepSpec(
            purpose=plan.MEASURE,
            name="Modul",
            module=plan.ModuleRequest("M", "M", module_pose, (plan.TagRequest(7, tag_in_module),)),
        )
        first = plan.plan_step(anchor, base_pose(), flange_cam(), 0.3, 0.85)
        second = plan.plan_step(measured, base_pose(), flange_cam(), 0.3, 0.85)
        np.testing.assert_allclose(
            first.target.flange_in_base, second.target.flange_in_base, atol=1e-12
        )


@unittest.skipUnless(np is not None, "numpy fehlt")
class TagChoiceTest(unittest.TestCase):
    def test_the_tag_facing_the_base_wins(self):
        # Zwei Tags an einem Modul bei x = 2: einer zeigt nach -x (zur Basis
        # bei x = 0), einer nach +x (weg davon).
        facing = pose((2.0, 0, 0.2), np.array([[0, 0, -1], [0, 1, 0], [1, 0, 0]], dtype=float))
        away = pose((2.2, 0, 0.2), np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0]], dtype=float))
        tag_id, _ = plan.tag_facing_base([(8, away), (7, facing)], [0, 0, 0])
        self.assertEqual(tag_id, 7)


@unittest.skipUnless(np is not None, "numpy fehlt")
class PlanRunTest(unittest.TestCase):
    def request(self, *modules, reach_m=0.85):
        return plan.RunRequest(base_pose(), tuple(modules), 0.3, reach_m)

    def test_the_world_tag_comes_first_then_the_nearest_module(self):
        far = module("FAR", (1.9, 0.8, 0.0), (9, pose((0, 0, 0.3))))
        near = module("NEAR", (1.4, 0.9, 0.0), (8, pose((0, 0, 0.3))))
        steps = plan.plan_run(
            self.request(far, near, reach_m=5.0),
            tag_map((0, geometry.identity()), (1, pose((4.0, 4.0, 0.0)))),
            flange_cam(),
        )
        self.assertEqual([step.spec.purpose for step in steps], ["anchor", "measure", "measure"])
        # Welttag 0 liegt naeher an der Basis als Welttag 1.
        self.assertEqual(steps[0].tag_id, 0)
        self.assertEqual([step.spec.module_id for step in steps[1:]], ["NEAR", "FAR"])

    def test_a_target_out_of_reach_is_marked_not_dropped(self):
        far = module("FAR", (5.0, 5.0, 0.0), (9, pose((0, 0, 0.3))))
        steps = plan.plan_run(self.request(far), tag_map((0, geometry.identity())), flange_cam())
        self.assertEqual(len(steps), 2)
        self.assertFalse(steps[1].reachable)

    def test_without_a_surveyed_world_tag_there_is_no_anchor(self):
        with self.assertRaises(ValueError):
            plan.plan_run(self.request(), TagMap(), flange_cam())


@unittest.skipUnless(np is not None, "numpy fehlt")
class JsonTest(unittest.TestCase):
    def test_a_run_request_is_read(self):
        text = json.dumps(
            {
                "schema": plan.RUN_SCHEMA,
                "robotBase": {"position": [1, 2, 0], "orientation": [0, 0, 0, 1]},
                "modules": [
                    {
                        "moduleId": "MOD-A",
                        "name": "Modul A",
                        "pose": {"position": [2, 2, 0], "orientation": [0, 0, 0, 1]},
                        "tags": [
                            {
                                "tagId": 7,
                                "poseInModule": {"position": [0, 0, 0.25], "orientation": [0, 0, 0, 1]},
                            }
                        ],
                    }
                ],
                "options": {"standoffM": 0.25},
            }
        )
        request = plan.parse_run_request(text)
        self.assertEqual(request.modules[0].tags[0].tag_id, 7)
        self.assertEqual(request.standoff_m, 0.25)
        self.assertEqual(request.reach_m, plan.DEFAULT_REACH_M)
        np.testing.assert_allclose(request.robot_base[:3, 3], [1, 2, 0])

    def test_broken_requests_are_refused_with_a_reason(self):
        for text in (
            "kein json",
            json.dumps({"schema": "falsch"}),
            json.dumps({"schema": plan.RUN_SCHEMA, "robotBase": {"position": [1, 2]}}),
            json.dumps(
                {
                    "schema": plan.RUN_SCHEMA,
                    "robotBase": {"position": [0, 0, 0], "orientation": [0, 0, 0, 0]},
                }
            ),
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                plan.parse_run_request(text)

    def test_reports(self):
        reached = plan.parse_report(
            json.dumps(
                {
                    "schema": plan.REPORT_SCHEMA,
                    "stepIndex": 2,
                    "reached": True,
                    "flangeInBase": {"position": [0.3, 0, 0.4], "orientation": [0, 0, 0, 1]},
                }
            )
        )
        self.assertTrue(reached.reached)
        self.assertEqual(reached.step_index, 2)
        missed = plan.parse_report(
            json.dumps({"schema": plan.REPORT_SCHEMA, "stepIndex": 0, "reached": False, "reason": "x"})
        )
        self.assertFalse(missed.reached)
        self.assertEqual(missed.reason, "x")
        with self.assertRaises(ValueError):
            plan.parse_report(json.dumps({"schema": plan.REPORT_SCHEMA, "stepIndex": 0, "reached": True}))

    def test_the_job_parameters_are_what_the_anchor_reads(self):
        flange = pose((0.3, -0.2, 0.5), yaw(1.1))
        np.testing.assert_allclose(
            robot_pose_from_parameters(plan.pose_parameters(flange)), flange, atol=1e-12
        )


if __name__ == "__main__":
    unittest.main()

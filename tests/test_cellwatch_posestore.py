"""Die gemerkte Kamerapose uebersteht Neustarts und kommt aus dem Referenz-Scan.

Sonst pausiert die Ueberwachung nach jedem Neustart, bis einmal zwei Welttags
gleichzeitig zu sehen waren -- und das war oft nicht der Fall.
"""

import json
import tempfile
import types
import unittest
from pathlib import Path

try:
    import numpy as np

    from tagloc import geometry
    from tests.test_cellwatch import FakeClock, WALL
    from tests.test_cellwatch_storedpose import SceneTest, localization, location, tag_map
    from vision_server.cellwatch import (
        CameraPoseKeeper,
        CellWatch,
        CellWatchOptions,
        CellWatchPorts,
        camera_pose_from_payload,
    )
    from vision_server.detection.apriltag import _localization_summary
except Exception:  # pragma: no cover - numpy fehlt in dieser Umgebung
    np = None


def pose_at(x):
    return geometry.from_rotation_translation(np.eye(3), (x, 0.0, 2.5))


@unittest.skipUnless(np is not None, "numpy fehlt")
class KeeperFileTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "cellwatch_camera_pose.json"
        self.clock = FakeClock()
        self.tag_map = tag_map()
        self.options = CellWatchOptions()

    def keeper(self):
        return CameraPoseKeeper(path=self.path, clock=self.clock.monotonic, wall=lambda: WALL)

    def joint(self, x=0.8):
        return localization(
            "joint", errors={583: 3.0, 584: 3.4}, tag_ids=(583, 584), pose=pose_at(x)
        )

    def remember(self, keeper, times):
        for _ in range(times):
            keeper.judge([location()], self.joint(), self.options, self.tag_map)

    def test_the_pose_is_saved_once_enough_poses_are_in(self):
        keeper = self.keeper()
        self.remember(keeper, CameraPoseKeeper.SAVE_MIN_POSES - 1)
        self.assertFalse(self.path.exists())
        self.remember(keeper, 1)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["schema"], CameraPoseKeeper.SCHEMA)
        self.assertEqual(data["poseCount"], CameraPoseKeeper.SAVE_MIN_POSES)
        self.assertEqual(sorted(data["baselinePx"]), ["583", "584"])
        self.assertEqual(keeper.info()["source"], "measured")
        self.assertEqual(keeper.info()["savedAt"], WALL.isoformat(timespec="seconds"))

    def test_it_is_not_rewritten_more_often_than_every_ten_minutes(self):
        keeper = self.keeper()
        self.remember(keeper, 6)
        first = self.path.stat().st_mtime_ns
        self.path.write_text(self.path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        touched = self.path.stat().st_mtime_ns
        self.clock.t += CameraPoseKeeper.SAVE_INTERVAL_S - 1
        self.remember(keeper, 3)
        self.assertEqual(self.path.stat().st_mtime_ns, touched)  # nicht neu geschrieben
        self.clock.t += 2
        self.remember(keeper, 1)
        data = json.loads(self.path.read_text(encoding="utf-8"))  # wieder gueltig, ohne das Leerzeichen
        self.assertGreaterEqual(data["poseCount"], 6)
        self.assertGreater(first, 0)

    def test_after_a_restart_one_world_tag_is_enough_right_away(self):
        self.remember(self.keeper(), 6)
        restarted = self.keeper()
        self.assertTrue(restarted.load())
        self.assertEqual(restarted.info()["source"], "file")
        np.testing.assert_allclose(restarted.stored_for(self.tag_map)[:3, 3], [0.8, 0.0, 2.5], atol=1e-9)
        # Ein einziger Welttag, ohne dass zwei gleichzeitig zu sehen waren.
        stored = localization("stored", errors={584: 3.9})
        sightings, reason = restarted.judge([location()], stored, self.options, self.tag_map)
        self.assertEqual((list(sightings), reason), ([7], ""))

    def test_a_restored_pose_is_still_checked_against_the_world_tag(self):
        self.remember(self.keeper(), 6)
        restarted = self.keeper()
        restarted.load()
        # Die Kamera wurde waehrend der Pause angestossen: der Welttag passt nicht mehr.
        for _ in range(CameraPoseKeeper.BAD_FRAMES_TO_DROP):
            sightings, reason = restarted.judge(
                [location()], localization("stored", errors={584: 9.0}), self.options, self.tag_map
            )
            self.assertIsNone(sightings)
        self.assertIn("verworfen", reason)
        self.assertIsNone(restarted.stored_for(self.tag_map))
        self.assertFalse(self.path.exists())  # ein weiterer Neustart stellt sie nicht wieder her

    def test_a_changed_tag_map_makes_the_file_useless(self):
        self.remember(self.keeper(), 6)
        restarted = self.keeper()
        self.assertTrue(restarted.load())
        self.assertIsNone(restarted.stored_for(tag_map(x=1.600)))
        self.assertFalse(self.path.exists())

    def test_broken_or_foreign_files_are_ignored(self):
        for text in ("kein json", "{}", json.dumps({"schema": "falsch/1"}),
                     json.dumps({"schema": CameraPoseKeeper.SCHEMA, "poseWorldCam": {}})):
            with self.subTest(text=text):
                self.path.write_text(text, encoding="utf-8")
                keeper = self.keeper()
                self.assertFalse(keeper.load())
                self.assertIsNone(keeper.stored_for(self.tag_map))
        self.assertFalse(CameraPoseKeeper(path=None).load())
        missing = CameraPoseKeeper(path=Path(self.folder.name) / "fehlt.json")
        self.assertFalse(missing.load())

    def test_an_unwritable_place_does_not_break_the_watch(self):
        blocked = Path(self.folder.name) / "datei"
        blocked.write_text("x", encoding="utf-8")
        keeper = CameraPoseKeeper(path=blocked / "pose.json", clock=self.clock.monotonic)
        with self.assertLogs("vision_server.cellwatch", level="WARNING") as logs:
            self.remember(keeper, 20)  # Verzeichnis ist eine Datei: Speichern scheitert, kein Absturz
        self.assertIsNotNone(keeper.stored_for(self.tag_map))
        # Nur eine Warnung, nicht eine je Bild.
        self.assertEqual(sum("nicht gespeichert" in line for line in logs.output), 1)


@unittest.skipUnless(np is not None, "numpy fehlt")
class AdoptTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "pose.json"
        self.tag_map = tag_map()
        self.keeper = CameraPoseKeeper(path=self.path)

    def test_the_scan_pose_is_adopted_and_saved_when_there_is_none(self):
        self.assertTrue(self.keeper.adopt(pose_at(0.8), {583: 3.0, 584: 3.4}, self.tag_map))
        self.assertEqual(self.keeper.info()["source"], "scan")
        np.testing.assert_allclose(self.keeper.stored_for(self.tag_map)[:3, 3], [0.8, 0, 2.5])
        self.assertTrue(self.path.exists())
        # Der Eckenfehler aus dem Scan ist die Grundlinie fuer die Konsistenzpruefung.
        sightings, reason = self.keeper.judge(
            [location()], localization("stored", errors={584: 3.9}), CellWatchOptions(), self.tag_map
        )
        self.assertEqual(reason, "")

    def test_an_agreeing_scan_does_not_replace_the_averaged_pose(self):
        self.keeper.adopt(pose_at(0.800), {584: 3.0}, self.tag_map)
        self.assertFalse(self.keeper.adopt(pose_at(0.805), {584: 3.0}, self.tag_map))
        np.testing.assert_allclose(self.keeper.stored_for(self.tag_map)[:3, 3], [0.8, 0, 2.5])

    def test_a_clearly_different_scan_wins_because_the_camera_was_moved(self):
        self.keeper.adopt(pose_at(0.800), {584: 3.0}, self.tag_map)
        self.assertTrue(self.keeper.adopt(pose_at(0.900), {584: 2.5}, self.tag_map))
        np.testing.assert_allclose(self.keeper.stored_for(self.tag_map)[:3, 3], [0.9, 0, 2.5])

    def test_payload_pose_only_from_a_joint_solution(self):
        summary = _localization_summary(
            types.SimpleNamespace(
                pose_world_cam=pose_at(0.8), world_tag_ids=(583, 584), primary_tag_id=583,
                spread_m=0.001, spread_rad=0.0, origin="world_tags", solver="joint",
                reprojection_error_px=2.0, tag_errors_px={583: 3.0, 584: 3.4},
            )
        )
        self.assertEqual(summary["poseInWorld"]["position"], [0.8, 0.0, 2.5])
        self.assertEqual(summary["tagErrorsPx"], {"583": 3.0, "584": 3.4})
        json.dumps(summary, allow_nan=False)
        pose, errors = camera_pose_from_payload(json.dumps({"cameraLocalization": summary}))
        np.testing.assert_allclose(pose[:3, 3], [0.8, 0.0, 2.5])
        self.assertEqual(errors, {583: 3.0, 584: 3.4})
        for solver, origin in (("single", "world_tags"), ("average", "world_tags"), ("joint", "robot_pose")):
            other = _localization_summary(
                types.SimpleNamespace(
                    pose_world_cam=pose_at(0.8), world_tag_ids=(584,), primary_tag_id=584,
                    spread_m=0.0, spread_rad=0.0, origin=origin, solver=solver,
                    reprojection_error_px=1.0, tag_errors_px={},
                )
            )
            self.assertNotIn("poseInWorld", other, (solver, origin))
            self.assertIsNone(camera_pose_from_payload(json.dumps({"cameraLocalization": other})))
        for payload in ("{}", "kein json", json.dumps({"cameraLocalization": {"solver": "joint"}})):
            self.assertIsNone(camera_pose_from_payload(payload))


@unittest.skipUnless(np is not None, "numpy fehlt")
class CellWatchAdoptionTest(unittest.IsolatedAsyncioTestCase):
    async def test_start_adopts_the_pose_of_the_reference_scan(self):
        scan = {
            "frameId": "world",
            "resultState": 0,
            "detections": [
                {"moduleId": "MOD-A", "position": [1.0, 1.0, 0.0], "orientation": [0, 0, 0, 1],
                 "attributes": {"tagId": 7}}
            ],
            "cameraLocalization": {
                "solver": "joint",
                "poseInWorld": geometry.pose_to_dict(pose_at(0.8)),
                "tagErrorsPx": {"583": 3.0, "584": 3.4},
            },
        }
        adopted, statuses = [], []

        async def observe(options):
            return None, "kein Welt-Tag im Bild -- keine Verortung"

        async def publish(status):
            statuses.append(json.loads(status))

        watch = CellWatch(
            CellWatchPorts(
                observe=observe,
                busy_reason=lambda: "",
                result_for_job={"job-000001": json.dumps(scan)}.get,
                world_frame=lambda: "world",
                publish=publish,
                adopt_pose=lambda pose, errors: adopted.append((pose, errors)),
                pose_info=lambda: {"source": "scan", "poseCount": 1, "savedAt": None},
            )
        )
        await watch.start('{"referenceJobId": "job-000001"}', run_loop=False)
        [(pose, errors)] = adopted
        np.testing.assert_allclose(pose[:3, 3], [0.8, 0.0, 2.5])
        self.assertEqual(errors, {583: 3.0, 584: 3.4})
        self.assertEqual(statuses[-1]["cameraPose"]["source"], "scan")

        # Ein Scan ohne gemeinsame Pose (nur ein Welttag) wird nicht uebernommen.
        scan["cameraLocalization"] = {"solver": "single"}
        adopted.clear()
        watch._ports.result_for_job.__self__["job-000002"] = json.dumps(scan)
        await watch.start('{"referenceJobId": "job-000002"}', run_loop=False)
        self.assertEqual(adopted, [])


@unittest.skipUnless(np is not None, "numpy fehlt")
class RestartSceneTest(SceneTest):
    """Die ganze Kette an der synthetischen Zelle, mit Neustart dazwischen."""

    def test_after_a_restart_a_single_world_tag_gives_good_module_poses(self):
        from tests import test_joint_camera_pose as scene

        truth = scene._true_world_cam()
        path = Path(self.folder.name) / "pose.json"
        self.keeper = CameraPoseKeeper(path=path)
        for _ in range(8):
            self.frame(truth, {583, 584, 585, 586})
        self.assertTrue(path.exists())

        # Neustart: neuer Keeper, nur noch ein Welttag im Bild.
        self.keeper = CameraPoseKeeper(path=path)
        self.assertTrue(self.keeper.load())
        errors = []
        for _ in range(40):
            (sightings, reason), loc = self.frame(truth, {584})
            self.assertEqual((loc.solver, reason), ("stored", ""))
            errors.append(self.module_error_mm(sightings))
        self.assertLess(float(np.median(errors)), 20.0)
        self.assertLess(max(errors), 60.0)


if __name__ == "__main__":
    unittest.main()

"""Zellen-Ueberwachung: bestaetigte Aenderungen statt Alarm bei jedem Ausreisser."""

import json
import random
import tempfile
import types
import unittest
from datetime import datetime, timezone

try:
    import numpy as np
    from asyncua import Server, ua

    from tagloc import geometry
    from vision_server.cellwatch import (
        APPEARED,
        DISAPPEARED,
        MOVED,
        CellWatch,
        CellWatchOptions,
        CellWatchPorts,
        CellWatchTracker,
        ReferenceTag,
        TagSighting,
        parse_start,
        reference_from_payload,
        sightings_from_locations,
        yaw_deg,
    )
    from vision_server.errors import VisionErrorCode
except Exception:  # pragma: no cover - numpy/asyncua fehlen
    np = None

WALL = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
OPTIONS = None if np is None else CellWatchOptions(interval_s=1.0)


def sighting(x, y, yaw=0.0, *, ambiguous=False, module_id="MOD-A", noise=0.0, rng=None):
    if rng is not None and noise:
        x += rng.gauss(0, noise)
        y += rng.gauss(0, noise)
    return TagSighting((x, y, 0.0), yaw, ambiguous, module_id)


def reference(**tags):
    return {
        int(tag_id[1:]): ReferenceTag((x, y, 0.0), yaw, f"MOD-{tag_id}") for tag_id, (x, y, yaw) in tags.items()
    }


@unittest.skipUnless(np is not None, "numpy fehlt")
class TrackerTest(unittest.TestCase):
    def run_frames(self, tracker, frames, start=0.0, step=1.0):
        t = start
        for sightings in frames:
            tracker.update(t, WALL, sightings)
            t += step
        return t

    def test_noise_and_single_outliers_raise_no_alarm(self):
        rng = random.Random(1)
        tracker = CellWatchTracker(OPTIONS, reference(t7=(1.0, 1.0, 0.0)), 0.0)
        frames = []
        for index in range(60):
            if index % 10 == 5:
                frames.append({7: sighting(1.4, 1.0)})  # einzelner Ausreisser, 40 cm
            elif index % 13 == 0:
                frames.append({})  # Person vor dem Tag
            else:
                frames.append({7: sighting(1.0, 1.0, noise=0.008, rng=rng)})
        self.run_frames(tracker, frames)
        self.assertEqual(tracker.changes, [])

    def test_a_moved_module_is_confirmed_after_persist_and_clears_with_hysteresis(self):
        tracker = CellWatchTracker(OPTIONS, reference(t7=(1.0, 1.0, 0.0)), 0.0)
        t = self.run_frames(tracker, [{7: sighting(1.0, 1.0)}] * 5)
        # Um 12 cm verschoben: erst nach persistS (5 s) bestaetigt.
        t = self.run_frames(tracker, [{7: sighting(1.12, 1.0)}] * 4, start=t)
        self.assertEqual(tracker.changes, [])
        t = self.run_frames(tracker, [{7: sighting(1.12, 1.0)}] * 10, start=t)
        [change] = tracker.changes
        self.assertEqual(change["kind"], MOVED)
        self.assertAlmostEqual(change["distanceM"], 0.12, places=6)
        self.assertEqual(change["referencePosition"], [1.0, 1.0, 0.0])
        self.assertEqual(change["moduleId"], "MOD-t7")
        # Zurueck auf 3 cm: zwischen halbem und ganzem Schwellwert bleibt der Alarm.
        t = self.run_frames(tracker, [{7: sighting(1.03, 1.0)}] * 15, start=t)
        self.assertEqual(len(tracker.changes), 1)
        # Erst stabil unter 2,5 cm ueber persistS erlischt er.
        self.run_frames(tracker, [{7: sighting(1.005, 1.0)}] * 15, start=t)
        self.assertEqual(tracker.changes, [])

    def test_rotation_about_the_vertical_counts_but_not_for_ambiguous_tags(self):
        tracker = CellWatchTracker(OPTIONS, reference(t7=(1.0, 1.0, 0.0)), 0.0)
        self.run_frames(tracker, [{7: sighting(1.0, 1.0, yaw=25.0, ambiguous=True)}] * 20)
        self.assertEqual(tracker.changes, [])
        tracker = CellWatchTracker(OPTIONS, reference(t7=(1.0, 1.0, 175.0)), 0.0)
        self.run_frames(tracker, [{7: sighting(1.0, 1.0, yaw=-170.0)}] * 20)
        [change] = tracker.changes
        self.assertEqual(change["kind"], MOVED)
        self.assertAlmostEqual(change["rotationDeg"], 15.0, places=6)  # ueber +-180 gerechnet

    def test_a_short_occlusion_is_no_disappearance_but_a_long_one_is(self):
        tracker = CellWatchTracker(OPTIONS, reference(t7=(1.0, 1.0, 0.0), t8=(0.5, 0.5, 0.0)), 0.0)
        both = {7: sighting(1.0, 1.0), 8: sighting(0.5, 0.5)}
        only8 = {8: sighting(0.5, 0.5)}
        t = self.run_frames(tracker, [both] * 5)
        t = self.run_frames(tracker, [only8] * 15, start=t)  # 15 s verdeckt
        self.assertEqual(tracker.changes, [])
        t = self.run_frames(tracker, [both] * 3, start=t)
        t = self.run_frames(tracker, [only8] * 25, start=t)  # 25 s weg
        [change] = tracker.changes
        self.assertEqual((change["tagId"], change["kind"]), (7, DISAPPEARED))
        self.assertIsNone(change["currentPosition"])
        # Wieder da: erlischt, sobald er ueber persistS in der Mehrzahl ist.
        self.run_frames(tracker, [both] * 8, start=t)
        self.assertEqual(tracker.changes, [])

    def test_missing_needs_enough_valid_frames(self):
        """Pausen (keine gueltigen Bilder) zaehlen nicht als "fehlt"."""
        tracker = CellWatchTracker(OPTIONS, reference(t7=(1.0, 1.0, 0.0)), 0.0)
        tracker.update(0.0, WALL, {7: sighting(1.0, 1.0)})
        tracker.update(30.0, WALL, {})
        tracker.update(31.0, WALL, {})
        self.assertEqual(tracker.changes, [])
        tracker.update(32.0, WALL, {})
        self.assertEqual(tracker.changes[0]["kind"], DISAPPEARED)

    def test_a_new_tag_appears_only_when_seen_in_most_frames(self):
        tracker = CellWatchTracker(OPTIONS, reference(t7=(1.0, 1.0, 0.0)), 0.0)
        base = {7: sighting(1.0, 1.0)}
        new = {9: sighting(0.2, 0.3, module_id="MOD-N")}
        flicker = [{**base, **(new if i % 3 == 0 else {})} for i in range(20)]
        t = self.run_frames(tracker, flicker)
        self.assertEqual(tracker.changes, [])  # nur in jedem dritten Bild
        t = self.run_frames(tracker, [{**base, **new}] * 8, start=t)
        [change] = tracker.changes
        self.assertEqual((change["tagId"], change["kind"], change["moduleId"]), (9, APPEARED, "MOD-N"))
        self.assertIsNone(change["referencePosition"])
        self.run_frames(tracker, [base] * 8, start=t)
        self.assertEqual(tracker.changes, [])

    def test_stable_observation_becomes_the_reference(self):
        tracker = CellWatchTracker(OPTIONS, {}, 0.0)
        self.assertIsNone(tracker.stable_observation(0.0))
        self.run_frames(tracker, [{7: sighting(1.0, 1.0, yaw=30.0), 9: sighting(0.2, 0.2)}] * 3 + [{7: sighting(1.0, 1.0, yaw=30.0)}] * 4)
        observed = tracker.stable_observation(6.0)
        self.assertEqual(sorted(observed), [7])  # 9 nur in 3 von 6 Bildern des Fensters
        self.assertAlmostEqual(observed[7].yaw_deg, 30.0)


@unittest.skipUnless(np is not None, "numpy fehlt")
class JsonTest(unittest.TestCase):
    def test_start_options(self):
        job_id, options = parse_start('{"referenceJobId": "job-000003", "movedThresholdM": 0.08}')
        self.assertEqual(job_id, "job-000003")
        self.assertEqual(options.moved_threshold_m, 0.08)
        self.assertEqual(options.interval_s, 2.0)
        self.assertEqual(parse_start("{}")[0], None)
        for text in ("kaputt", '{"persistS": 0}', '{"referenceJobId": 3}', '{"schema": "falsch"}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_start(text)

    def test_reference_from_a_job_payload(self):
        pose = geometry.from_rvec_tvec((0, 0, 0.5), (1.0, 2.0, 0.0))
        position, orientation = geometry.to_position_quaternion(pose)
        payload = {
            "frameId": "world",
            "resultState": 0,
            "detections": [
                {"moduleId": "ROBOT", "position": list(position), "orientation": list(orientation),
                 "attributes": {"tagId": 20, "tagIds": [20, 21], "role": "robot"}},
                {"moduleId": "TAG-9", "position": [0, 0, 0], "orientation": [0, 0, 0, 1],
                 "attributes": {"tagId": 9, "ambiguous": True}},
            ],
        }
        reference = reference_from_payload(json.dumps(payload), "world")
        self.assertEqual(sorted(reference), [9, 20, 21])
        self.assertEqual(reference[21].module_id, "ROBOT")
        self.assertAlmostEqual(reference[20].yaw_deg, yaw_deg(pose))
        self.assertFalse(reference[9].has_yaw)
        with self.assertRaises(ValueError):
            reference_from_payload(json.dumps({**payload, "frameId": "cam_ceiling"}), "world")
        with self.assertRaises(ValueError):
            reference_from_payload(json.dumps({**payload, "resultState": 5, "errorCode": 5}), "world")

    def test_only_joint_camera_poses_count(self):
        location = types.SimpleNamespace(
            tag_id=7, pose=geometry.identity(), ambiguous=False, module_id="MOD-A"
        )
        joint = types.SimpleNamespace(solver="joint", reprojection_error_px=0.7)
        sightings, reason = sightings_from_locations([location], joint, OPTIONS)
        self.assertEqual(list(sightings), [7])
        for localization in (
            None,
            types.SimpleNamespace(solver="single", reprojection_error_px=0.2),
            types.SimpleNamespace(solver="joint", reprojection_error_px=9.0),
        ):
            sightings, reason = sightings_from_locations([location], localization, OPTIONS)
            self.assertIsNone(sightings)
            self.assertTrue(reason)


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t


@unittest.skipUnless(np is not None, "numpy fehlt")
class CellWatchTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.results = {}
        self.busy = ""
        self.frame = ({7: sighting(1.0, 1.0)}, "")
        self.statuses = []

    def watch(self):
        async def observe(options):
            return self.frame

        async def publish(status):
            self.statuses.append(json.loads(status))

        return CellWatch(
            CellWatchPorts(
                observe=observe,
                busy_reason=lambda: self.busy,
                result_for_job=self.results.get,
                world_frame=lambda: "world",
                publish=publish,
                monotonic=self.clock.monotonic,
                now=lambda: WALL,
            )
        )

    async def ticks(self, watch, count):
        for _ in range(count):
            self.clock.t += 1.0
            await watch.tick()

    @property
    def status(self):
        return self.statuses[-1]

    async def test_reference_from_a_job_then_a_confirmed_move(self):
        self.results["job-000001"] = json.dumps(
            {"frameId": "world", "resultState": 0, "detections": [
                {"moduleId": "MOD-A", "position": [1.0, 1.0, 0.0], "orientation": [0, 0, 0, 1],
                 "attributes": {"tagId": 7}}]}
        )
        watch = self.watch()
        self.assertEqual(
            await watch.start('{"referenceJobId": "job-000001"}', run_loop=False), VisionErrorCode.OK
        )
        self.assertEqual(self.status["state"], "watching")
        self.assertEqual(self.status["referenceJobId"], "job-000001")
        self.assertEqual(self.status["referenceSource"], "job")
        self.assertEqual(self.status["watchedTagIds"], [7])
        first_reference = self.status["referenceId"]

        self.frame = ({7: sighting(1.2, 1.0)}, "")
        await self.ticks(watch, 8)
        self.assertEqual(self.status["state"], "changed")
        self.assertEqual(self.status["changes"][0]["kind"], "moved")
        self.assertIn("MOD-A verschoben", self.status["message"])

        # "Ignorieren": die aktuelle Beobachtung wird die Referenz.
        self.assertEqual(await watch.start('{"referenceJobId": null}', run_loop=False), VisionErrorCode.OK)
        self.assertEqual(self.status["state"], "watching")
        self.assertNotEqual(self.status["referenceId"], first_reference)
        self.assertEqual(self.status["referenceSource"], "observation")
        await self.ticks(watch, 3)
        self.assertEqual(self.status["changes"], [])

    async def test_null_reference_is_recorded_first(self):
        watch = self.watch()
        await watch.start("{}", run_loop=False)
        self.assertIsNone(self.status["referenceId"])
        self.assertEqual(self.status["message"], "Referenz wird aufgenommen")
        await self.ticks(watch, 6)
        self.assertIsNotNone(self.status["referenceId"])
        self.assertEqual(self.status["watchedTagIds"], [7])

    async def test_pause_while_busy_or_without_a_statement(self):
        watch = self.watch()
        await watch.start("{}", run_loop=False)
        self.busy = "Job laeuft"
        await self.ticks(watch, 1)
        self.assertEqual((self.status["state"], self.status["message"]), ("paused", "Job laeuft"))
        self.busy = ""
        self.frame = (None, "weniger als zwei Welttags im Bild -- Kamerapose zu ungenau")
        await self.ticks(watch, 1)
        self.assertEqual(self.status["state"], "paused")
        self.frame = ({7: sighting(1.0, 1.0)}, "")
        await self.ticks(watch, 1)
        self.assertEqual(self.status["state"], "watching")

    async def test_refusals_and_stop(self):
        watch = self.watch()
        self.assertEqual(await watch.stop(), VisionErrorCode.INVALID_STATE)
        self.assertEqual(await watch.start("kaputt", run_loop=False), VisionErrorCode.INVALID_ARGUMENT)
        self.assertEqual(
            await watch.start('{"referenceJobId": "job-unbekannt"}', run_loop=False),
            VisionErrorCode.INVALID_ARGUMENT,
        )
        self.results["job-000002"] = json.dumps({"frameId": "cam_ceiling", "resultState": 0, "detections": []})
        self.assertEqual(
            await watch.start('{"referenceJobId": "job-000002"}', run_loop=False),
            VisionErrorCode.INVALID_STATE,
        )
        await watch.start("{}")  # mit echtem Takt
        self.assertEqual(await watch.stop(), VisionErrorCode.OK)
        self.assertEqual(self.status["state"], "off")
        self.assertEqual(self.status["changes"], [])


@unittest.skipUnless(np is not None, "numpy fehlt")
class ObserveCellTest(unittest.TestCase):
    """`observe_cell` an der synthetischen Zelle aus `test_joint_camera_pose`:
    Module je Tag im Welt-KS, Welttags nicht, und kein Seiteneffekt."""

    def test_modules_per_tag_without_side_effects(self):
        from pathlib import Path

        from tagloc import calibration as calib
        from tagloc.tagmap import TagEntry, TagMap
        from tests import test_joint_camera_pose as scene
        from vision_server.detection.apriltag import AprilTagDetectionSource
        from vision_server.profiles import AprilTagProfileConfig

        calibration = scene._calibration(calib.PINHOLE)
        module_pose = geometry.from_rvec_tvec((0, 0, 0.3), (0.8, 0.8, 0.0))
        tag_map = TagMap(
            frame_id="world",
            entries={**scene._tag_map().entries, 7: TagEntry(7, "module", scene.TAG_SIZE_M, module_id="MOD-A")},
        )
        observations = scene._observations(scene._true_world_cam(), calibration, np.random.default_rng(2))
        # Tag 7 wie ein Welttag projizieren -- dafuer kurz als Welttag eintragen.
        helper = TagMap(frame_id="world", entries={7: TagEntry(7, "world", scene.TAG_SIZE_M, pose_in_world=module_pose)})
        original = scene._tag_map
        scene._tag_map = lambda: helper
        try:
            observations += scene._observations(scene._true_world_cam(), calibration, np.random.default_rng(3))
        finally:
            scene._tag_map = original

        with tempfile.TemporaryDirectory() as folder:
            source = AprilTagDetectionSource(
                AprilTagProfileConfig(calibration_path=Path(folder) / "c.json", tag_size_m=scene.TAG_SIZE_M),
                calibration=calibration,
                tag_map=tag_map,
                camera=types.SimpleNamespace(),
            )
        source._anchor = "unberuehrt"
        detector = types.SimpleNamespace(detect=lambda gray: observations)
        image = np.zeros((scene.IMAGE_SIZE[1], scene.IMAGE_SIZE[0]), dtype=np.uint8)

        locations, localization = source.observe_cell(image, detector)

        self.assertEqual(localization.solver, "joint")
        self.assertEqual([location.tag_id for location in locations], [7])
        # Ein Modul-Tag ist ein Einzel-Tag-PnP: rund 1 cm Rauschen aus 2,5 m.
        np.testing.assert_allclose(locations[0].pose[:3, 3], [0.8, 0.8, 0.0], atol=0.03)
        self.assertEqual(source._anchor, "unberuehrt")
        self.assertEqual(source.world_tags, ())
        self.assertIsNone(source._localization)


class FakeNode:
    async def write_value(self, *args, **kwargs):
        pass


@unittest.skipUnless(np is not None, "numpy fehlt")
class ResultHistoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_the_last_results_are_kept_by_job_id(self):
        from vision_server.result_management import PublishedResult, ResultStore

        children = {name: FakeNode() for name in (
            "ResultId", "JobId", "IsPartial", "IsSimulated", "ResultState",
            "InternalRecipeId", "InternalConfigurationId", "CreationTime", "ResultContent",
        )}
        store = ResultStore(FakeNode(), FakeNode(), children)
        for index in range(ResultStore.HISTORY + 2):
            await store.publish(
                PublishedResult(
                    result_id=f"res-{index}", job_id=f"job-{index}", creation_time=WALL,
                    result_state=0, payload_json=f'{{"n": {index}}}', is_simulated=False,
                )
            )
        self.assertIsNone(store.result_for_job("job-0"))
        self.assertEqual(store.result_for_job(f"job-{ResultStore.HISTORY + 1}"), f'{{"n": {ResultStore.HISTORY + 1}}}')


@unittest.skipUnless(np is not None, "numpy/asyncua fehlen")
class CellWatchOpcUaTest(unittest.IsolatedAsyncioTestCase):
    async def test_methods_and_status_node(self):
        from vision_server.runner import _install_cell_watch

        server = Server()
        await server.init()
        idx = await server.register_namespace("http://launch-rm.de/vision")
        vision_system = await server.nodes.objects.add_object(
            ua.NodeId("VisionMachine", idx), ua.QualifiedName("VisionMachine", idx)
        )
        space = types.SimpleNamespace(
            vision_system=vision_system,
            own_idx=idx,
            cell_watch_status=await vision_system.add_variable(
                ua.NodeId("VisionMachine.CellWatchStatus", idx),
                ua.QualifiedName("CellWatchStatus", idx), "", ua.VariantType.String,
            ),
        )
        config = types.SimpleNamespace(
            vision_system_name="VisionMachine",
            apriltag=types.SimpleNamespace(
                tag_family="tag36h11", detector_backend="aruco",
                subpixel_corner_refinement=False, apriltag_quad_decimate=0.0,
            ),
        )
        source = types.SimpleNamespace(
            camera=types.SimpleNamespace(latest_frame=None),
            _tag_map=types.SimpleNamespace(frame_id="world"),
        )
        results = types.SimpleNamespace(result_for_job=lambda job_id: None)
        states = types.SimpleNamespace(state_names=lambda: ("Operational", "Ready"))
        methods = {}
        watch = await _install_cell_watch(space, config, source, results, methods, states, lambda: "")
        self.assertEqual(set(methods), {"StartCellWatch", "StopCellWatch"})
        status = json.loads(await space.cell_watch_status.read_value())
        self.assertEqual(status["state"], "off")

        node = server.get_node(ua.NodeId("VisionMachine.StartCellWatch", idx))
        self.assertEqual(await vision_system.call_method(node, "{}"), 0)
        await watch.tick()
        status = json.loads(await space.cell_watch_status.read_value())
        self.assertEqual((status["state"], status["message"]), ("paused", "kein Kamerabild"))
        node = server.get_node(ua.NodeId("VisionMachine.StopCellWatch", idx))
        self.assertEqual(await vision_system.call_method(node), 0)


if __name__ == "__main__":
    unittest.main()

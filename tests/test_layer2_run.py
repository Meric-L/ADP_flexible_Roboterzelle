"""Ablauf des Layer-2-Laufs gegen Fakes: ankern, neu planen, messen, abbrechen."""

import json
import unittest

try:
    import numpy as np

    from tagloc import geometry
    from tagloc.handeye import HandEye, RobotAnchor
    from tagloc.tagmap import TagEntry, TagMap
    from vision_server.errors import VisionErrorCode
    from vision_server.layer2 import plan
    from vision_server.layer2.run import Layer2Ports, Layer2Run
except Exception:  # pragma: no cover - numpy fehlt in dieser Umgebung
    np = None


def pose_dict(position):
    return {"position": list(position), "orientation": [0, 0, 0, 1]}


def request_json(*modules, reach_m=5.0):
    return json.dumps(
        {
            "schema": plan.RUN_SCHEMA,
            "robotBase": pose_dict((1.0, 1.0, 0.0)),
            "modules": [
                {
                    "moduleId": module_id,
                    "name": module_id,
                    "pose": pose_dict(position),
                    "tags": [{"tagId": tag_id, "poseInModule": pose_dict((0, 0, 0.2))}],
                }
                for module_id, position, tag_id in modules
            ],
            "options": {"reachM": reach_m},
        }
    )


def report(step_index, reached=True, reason=""):
    data = {"schema": plan.REPORT_SCHEMA, "stepIndex": step_index, "reached": reached}
    if reached:
        data["flangeInBase"] = pose_dict((0.3, 0.0, 0.4))
    else:
        data["reason"] = reason
    return json.dumps(data)


class FakeServer:
    """Steht fuer JobRunner, AprilTag-Quelle und die beiden Knoten."""

    def __init__(self, hand_eye=True):
        self.hand_eye = HandEye(pose_flange_cam=geometry.identity()) if hand_eye else None
        self.tag_map = TagMap(
            entries={0: TagEntry(0, "world", 0.1, pose_in_world=geometry.identity())}
        )
        self.anchor = None
        self.jobs = []
        self.job_code = VisionErrorCode.OK
        self.result = ""
        self.targets = []
        self.statuses = []
        self.is_busy = False

    def start_job(self, parameters):
        if self.is_busy:
            return "", VisionErrorCode.BUSY
        self.jobs.append(parameters)
        return f"job-{len(self.jobs)}", VisionErrorCode.OK

    async def read_result(self):
        return self.result

    async def publish(self, target, status):
        self.targets.append(target)
        self.statuses.append(json.loads(status))

    def ports(self):
        return Layer2Ports(
            start_job=self.start_job,
            busy=lambda: self.is_busy,
            read_anchor=lambda: self.anchor,
            read_hand_eye=lambda: self.hand_eye,
            read_tag_map=lambda: self.tag_map,
            read_result=self.read_result,
            publish=self.publish,
        )

    @property
    def status(self):
        return self.statuses[-1]

    @property
    def target(self):
        return json.loads(self.targets[-1]) if self.targets[-1] else None


def payload(frame_id, *tag_ids):
    return json.dumps(
        {
            "frameId": frame_id,
            "detections": [
                {"moduleId": f"TAG-{tag_id}", "position": [0, 0, 0], "attributes": {"tagId": tag_id}}
                for tag_id in tag_ids
            ],
        }
    )


@unittest.skipUnless(np is not None, "numpy fehlt")
class Layer2RunTest(unittest.IsolatedAsyncioTestCase):
    async def finish_job(self, run, server, code, anchor=None, result=""):
        if anchor is not None:
            server.anchor = anchor
        server.result = result
        run.on_job_finished(f"job-{len(server.jobs)}", code)
        await run.wait_idle()

    async def test_anchor_replan_and_measure(self):
        server = FakeServer()
        run = Layer2Run(server.ports())
        self.assertEqual(await run.start(request_json(("MOD-A", (1.5, 1.0, 0.0), 7))), VisionErrorCode.OK)
        self.assertEqual(server.target["purpose"], "anchor")
        self.assertEqual(server.target["tagId"], 0)
        coarse = server.status["steps"][1]["reachM"]

        self.assertEqual(await run.report(report(0)), VisionErrorCode.OK)
        # Die gemeldete Pose geht als Job-Parameter mit -- nur mit ihr ankert der Pi.
        self.assertEqual([float(v) for v in server.jobs[0]], [0.3, 0.0, 0.4, 0, 0, 0, 1])
        self.assertEqual(server.status["state"], "measuring")
        self.assertEqual(server.targets[-1], "")

        # Nur der Welttag im Bild: DETECTION_FAILED, aber geankert.
        measured_base = geometry.from_rotation_translation(np.eye(3), (1.1, 1.0, 0.0))
        await self.finish_job(
            run, server, VisionErrorCode.DETECTION_FAILED, anchor=RobotAnchor(measured_base, 0, 0.002)
        )
        self.assertEqual(server.status["steps"][0]["status"], "done")
        self.assertEqual(server.status["anchor"]["worldTagId"], 0)
        # Neu geplant mit der gemessenen Basis: das Modul ist jetzt 10 cm naeher.
        self.assertLess(server.status["steps"][1]["reachM"], coarse)
        self.assertEqual(server.target["stepIndex"], 1)
        self.assertEqual(server.target["moduleId"], "MOD-A")

        self.assertEqual(await run.report(report(1)), VisionErrorCode.OK)
        await self.finish_job(run, server, VisionErrorCode.OK, result=payload("world", 7))
        self.assertEqual(server.status["state"], "finished")
        self.assertEqual(server.status["steps"][1]["status"], "done")
        self.assertEqual(server.status["steps"][1]["result"]["frameId"], "world")
        self.assertEqual(server.status["message"], "1 von 1 Modulen gemessen.")
        self.assertFalse(run.active)

    async def test_no_new_anchor_aborts_the_run(self):
        server = FakeServer()
        run = Layer2Run(server.ports())
        await run.start(request_json(("MOD-A", (1.5, 1.0, 0.0), 7)))
        await run.report(report(0))
        await self.finish_job(run, server, VisionErrorCode.DETECTION_FAILED)
        self.assertEqual(server.status["state"], "aborted")
        self.assertEqual(server.status["steps"][0]["status"], "failed")
        self.assertEqual(server.status["steps"][1]["status"], "skipped")
        self.assertEqual(server.targets[-1], "")

    async def test_an_old_anchor_does_not_count(self):
        server = FakeServer()
        old = RobotAnchor(geometry.identity(), 0)
        server.anchor = old
        run = Layer2Run(server.ports())
        await run.start(request_json())
        await run.report(report(0))
        await self.finish_job(run, server, VisionErrorCode.OK)
        self.assertEqual(server.status["state"], "aborted")

    async def test_a_module_not_seen_or_not_in_world_is_skipped(self):
        server = FakeServer()
        run = Layer2Run(server.ports())
        await run.start(
            request_json(("MOD-A", (1.5, 1.0, 0.0), 7), ("MOD-B", (1.8, 1.0, 0.0), 8))
        )
        await run.report(report(0))
        await self.finish_job(
            run, server, VisionErrorCode.DETECTION_FAILED, anchor=RobotAnchor(geometry.identity(), 0)
        )
        await run.report(report(1))
        await self.finish_job(run, server, VisionErrorCode.OK, result=payload("cam_flange", 7))
        self.assertEqual(server.status["steps"][1]["status"], "skipped")
        await run.report(report(2))
        await self.finish_job(run, server, VisionErrorCode.OK, result=payload("world", 99))
        self.assertEqual(server.status["steps"][2]["status"], "skipped")
        self.assertEqual(server.status["state"], "finished")

    async def test_unreached_module_fails_and_the_run_goes_on(self):
        server = FakeServer()
        run = Layer2Run(server.ports())
        await run.start(
            request_json(("MOD-A", (1.5, 1.0, 0.0), 7), ("MOD-B", (1.8, 1.0, 0.0), 8))
        )
        await run.report(report(0))
        await self.finish_job(
            run, server, VisionErrorCode.DETECTION_FAILED, anchor=RobotAnchor(geometry.identity(), 0)
        )
        self.assertEqual(await run.report(report(1, reached=False, reason="Kollision")), VisionErrorCode.OK)
        self.assertEqual(server.status["steps"][1]["status"], "failed")
        self.assertEqual(server.status["steps"][1]["detail"], "Kollision")
        self.assertEqual(server.target["stepIndex"], 2)

    async def test_targets_out_of_reach_are_skipped_without_a_drive(self):
        server = FakeServer()
        run = Layer2Run(server.ports())
        await run.start(request_json(("FAR", (9.0, 9.0, 0.0), 7), reach_m=0.85))
        await run.report(report(0))
        await self.finish_job(
            run, server, VisionErrorCode.DETECTION_FAILED, anchor=RobotAnchor(geometry.identity(), 0)
        )
        self.assertEqual(server.status["steps"][1]["status"], "skipped")
        self.assertEqual(server.status["state"], "finished")
        self.assertEqual(len(server.jobs), 1)

    async def test_stop_aborts(self):
        server = FakeServer()
        run = Layer2Run(server.ports())
        await run.start(request_json(("MOD-A", (1.5, 1.0, 0.0), 7)))
        await run.report(report(0))
        await run.abort("Stop")
        self.assertEqual(server.status["state"], "aborted")
        self.assertEqual(server.targets[-1], "")
        # Das Ende des abgebrochenen Jobs aendert nichts mehr.
        await self.finish_job(
            run, server, VisionErrorCode.CANCELLED, anchor=RobotAnchor(geometry.identity(), 0)
        )
        self.assertEqual(server.status["state"], "aborted")

    async def test_refusals(self):
        server = FakeServer(hand_eye=False)
        run = Layer2Run(server.ports())
        self.assertEqual(await run.start(request_json()), VisionErrorCode.INVALID_STATE)

        server = FakeServer()
        run = Layer2Run(server.ports())
        self.assertEqual(await run.start("{}"), VisionErrorCode.INVALID_ARGUMENT)
        self.assertEqual(await run.report(report(0)), VisionErrorCode.INVALID_STATE)
        server.is_busy = True
        self.assertEqual(await run.start(request_json()), VisionErrorCode.BUSY)
        server.is_busy = False
        await run.start(request_json())
        self.assertEqual(await run.start(request_json()), VisionErrorCode.BUSY)
        self.assertEqual(await run.report(report(3)), VisionErrorCode.INVALID_STATE)
        self.assertEqual(await run.report("kaputt"), VisionErrorCode.INVALID_ARGUMENT)
        # Ein belegter JobRunner laesst den Schritt stehen: noch einmal melden geht.
        server.is_busy = True
        self.assertEqual(await run.report(report(0)), VisionErrorCode.BUSY)
        self.assertEqual(server.status["state"], "waitingForRobot")


if __name__ == "__main__":
    unittest.main()

"""StartLayer2Run/ReportRobotPose ueber OPC UA: Methoden, Knoten, Job-Start.

Ohne Kamera: JobRunner, AprilTag-Quelle und Ergebnisknoten sind Fakes, der
OPC-UA-Teil (`runner._install_layer2_run`) ist echt.
"""

import json
import types
import unittest

try:
    import numpy as np
    from asyncua import Server, ua

    from tagloc import geometry
    from tagloc.handeye import HandEye, RobotAnchor
    from tagloc.tagmap import TagEntry, TagMap
    from vision_server.config import VisionServerConfig
    from vision_server.errors import VisionErrorCode
    from vision_server.layer2 import plan
    from vision_server.runner import _install_layer2_run
except Exception:  # pragma: no cover - asyncua oder numpy fehlen
    np = None


class FakeJobs:
    def __init__(self):
        self.busy = False
        self.started = []
        self.listeners = []

    def start_single_job(self, meas, part, recipe, product, parameters):
        self.started.append((recipe, list(parameters)))
        return f"job-{len(self.started)}", VisionErrorCode.OK

    def add_finish_listener(self, listener):
        self.listeners.append(listener)

    def finish(self, code):
        for listener in self.listeners:
            listener(f"job-{len(self.started)}", code)


def pose_dict(position):
    return {"position": list(position), "orientation": [0, 0, 0, 1]}


@unittest.skipUnless(np is not None, "asyncua oder numpy fehlen")
class Layer2OpcUaTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = Server()
        await self.server.init()
        idx = await self.server.register_namespace("http://launch-rm.de/vision")
        vision_system = await self.server.nodes.objects.add_object(
            ua.NodeId("VisionMachine", idx), ua.QualifiedName("VisionMachine", idx)
        )

        async def variable(name):
            return await vision_system.add_variable(
                ua.NodeId(f"VisionMachine.{name}", idx), ua.QualifiedName(name, idx), "",
                ua.VariantType.String,
            )

        self.idx = idx
        self.space = types.SimpleNamespace(
            vision_system=vision_system,
            own_idx=idx,
            layer2_target=await variable("Layer2Target"),
            layer2_status=await variable("Layer2Status"),
        )
        self.results = types.SimpleNamespace(json_node=await variable("LatestResultJson"))
        self.source = types.SimpleNamespace(
            _anchor=None,
            _hand_eye=HandEye(pose_flange_cam=geometry.identity()),
            _tag_map=TagMap(entries={0: TagEntry(0, "world", 0.1, pose_in_world=geometry.identity())}),
        )
        self.jobs = FakeJobs()
        self.methods = {}
        self.run = await _install_layer2_run(
            self.space, VisionServerConfig(), self.jobs, self.source, self.results,
            self.methods, lambda: False,
        )

    async def call(self, name, text):
        node = self.server.get_node(ua.NodeId(f"VisionMachine.{name}", self.idx))
        return await self.space.vision_system.call_method(node, text)

    async def value(self, name):
        return await getattr(self.space, name).read_value()

    async def test_a_run_over_opcua(self):
        self.assertEqual(set(self.methods), {"StartLayer2Run", "ReportRobotPose"})
        self.assertEqual(json.loads(await self.value("layer2_status"))["state"], "idle")

        request = {
            "schema": plan.RUN_SCHEMA,
            "robotBase": pose_dict((0.5, 0.0, 0.0)),
            "modules": [
                {
                    "moduleId": "MOD-A",
                    "pose": pose_dict((0.9, 0.0, 0.0)),
                    "tags": [{"tagId": 7, "poseInModule": pose_dict((0, 0, 0.2))}],
                }
            ],
        }
        self.assertEqual(await self.call("StartLayer2Run", json.dumps(request)), 0)
        target = json.loads(await self.value("layer2_target"))
        self.assertEqual(target["purpose"], "anchor")

        report = {
            "schema": plan.REPORT_SCHEMA,
            "stepIndex": 0,
            "reached": True,
            "flangeInBase": target["flangeInBase"],
        }
        self.assertEqual(await self.call("ReportRobotPose", json.dumps(report)), 0)
        self.assertEqual(self.jobs.started[0][0], "apriltag")
        self.assertEqual(len(self.jobs.started[0][1]), 7)
        self.assertEqual(await self.value("layer2_target"), "")

        measured_base = geometry.from_rotation_translation(np.eye(3), (0.5, 0.0, 0.0))
        self.source._anchor = RobotAnchor(measured_base, 0)
        self.jobs.finish(VisionErrorCode.DETECTION_FAILED)
        await self.run.wait_idle()
        target = json.loads(await self.value("layer2_target"))
        self.assertEqual((target["stepIndex"], target["moduleId"], target["tagId"]), (1, "MOD-A", 7))

        # Falscher Schritt: abgelehnt mit INVALID_STATE.
        self.assertEqual(
            await self.call("ReportRobotPose", json.dumps({**report, "stepIndex": 0})),
            int(VisionErrorCode.INVALID_STATE),
        )
        self.assertEqual(await self.call("StartLayer2Run", "{}"), int(VisionErrorCode.BUSY))


if __name__ == "__main__":
    unittest.main()

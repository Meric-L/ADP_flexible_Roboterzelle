"""Hand-Auge-Kalibrierung ueber OPC UA, gegen Fakes: aufnehmen, rechnen, speichern."""

import json
import math
import tempfile
import unittest
from pathlib import Path

try:
    import numpy as np

    from tagloc import geometry
    from tagloc.handeye import HandEye, load_hand_eye, save_hand_eye
    from vision_server.errors import VisionErrorCode
    from vision_server.handeye_session import (
        SAMPLE_SCHEMA,
        START_SCHEMA,
        HandEyePorts,
        HandEyeSession,
        rotation_diversity,
    )
except Exception:  # pragma: no cover - numpy fehlt in dieser Umgebung
    np = None


def rotation(rx_deg=0.0, ry_deg=0.0, rz_deg=0.0):
    return geometry.from_rvec_tvec(
        tuple(math.radians(v) for v in (rx_deg, ry_deg, rz_deg)), (0.0, 0.0, 0.0)
    )[:3, :3]


def pose(position, rot=None):
    return geometry.from_rotation_translation(np.eye(3) if rot is None else rot, position)


#: Die Wahrheit, die die Kalibrierung finden soll.
FLANGE_CAM = pose((0.03, -0.05, 0.08), rotation(0, 0, 90))
TAG_IN_BASE = pose((0.5, 0.1, 0.0))


def flange_poses(single_axis=False):
    """Posen ueber dem Tag, um mehrere (oder nur eine) Achse gedreht."""
    angles = (
        [(0, 0, a) for a in (0, 20, 40, 60, 80)]
        if single_axis
        else [(0, 0, 0), (20, 0, 10), (-15, 10, 30), (0, -25, -20), (10, 20, 60), (-20, -10, 5)]
    )
    # Kamera nach unten (180 Grad um x), darauf die jeweilige Drehung.
    looking_down = rotation(180, 0, 0)
    return [
        pose((0.5 + 0.02 * i, 0.1 - 0.01 * i, 0.4), looking_down @ rotation(rx, ry, rz))
        for i, (rx, ry, rz) in enumerate(angles)
    ]


def cam_to_tag(flange_in_base):
    """`T_cam_tag = inv(T_flansch_cam) · inv(T_base_flansch) · T_base_tag`."""
    return geometry.compose(
        geometry.invert(FLANGE_CAM), geometry.invert(flange_in_base), TAG_IN_BASE
    )


def start_json(tag_id=12, tag_in_base=None):
    data = {"schema": START_SCHEMA, "tagId": tag_id}
    if tag_in_base is not None:
        data["tagInBase"] = geometry.pose_to_dict(tag_in_base)
    return json.dumps(data)


def sample_json(flange_in_base):
    return json.dumps({"schema": SAMPLE_SCHEMA, "flangeInBase": geometry.pose_to_dict(flange_in_base)})


class FakePi:
    """Steht fuer Kamera, Quelle und Knoten. Der Tag ist sichtbar, solange
    `visible` gilt; seine Pose folgt aus der zuletzt gemeldeten Flanschpose."""

    def __init__(self, folder):
        self.path = Path(folder) / "handeye" / "cam_flange.json"
        self.visible = True
        self.flange = None
        self.applied = []
        self.statuses = []
        self.is_busy = False

    async def measure_tag(self, tag_id):
        if not self.visible:
            return None, f"Tag {tag_id} nicht gesehen"
        return cam_to_tag(self.flange), ""

    async def publish(self, status):
        self.statuses.append(json.loads(status))

    def ports(self):
        return HandEyePorts(
            measure_tag=self.measure_tag,
            busy=lambda: self.is_busy,
            path=self.path,
            apply=self.applied.append,
            publish=self.publish,
        )

    @property
    def status(self):
        return self.statuses[-1]


@unittest.skipUnless(np is not None, "numpy fehlt")
class HandEyeSessionTest(unittest.IsolatedAsyncioTestCase):
    async def capture_all(self, session, pi, poses):
        for flange in poses:
            pi.flange = flange
            self.assertEqual(await session.capture(sample_json(flange)), VisionErrorCode.OK)

    async def test_capture_solve_save_and_apply(self):
        with tempfile.TemporaryDirectory() as folder:
            pi = FakePi(folder)
            session = HandEyeSession(pi.ports())
            self.assertEqual(await session.start(start_json(12, TAG_IN_BASE)), VisionErrorCode.OK)
            self.assertEqual(pi.status["state"], "capturing")
            self.assertTrue(session.active)
            await self.capture_all(session, pi, flange_poses())
            self.assertEqual(pi.status["acceptedCount"], 6)
            self.assertGreaterEqual(pi.status["rotationAxes"], 2)
            self.assertIn("Drehung um", pi.status["message"])

            self.assertEqual(await session.finish(), VisionErrorCode.OK)
            status = pi.status
            self.assertEqual(status["state"], "finished")
            self.assertFalse(session.active)
            result = status["result"]
            self.assertEqual(result["sampleCount"], 6)
            self.assertLess(result["rmsPositionM"], 1e-6)
            self.assertLess(result["knownTagCheckM"], 1e-6)
            self.assertLess(result["knownTagCheckDeg"], 1e-4)
            self.assertIsNone(result["backupPath"])
            # Die gespeicherte Kalibrierung steht im Status -- auch fuer eine
            # frische Sitzung, wie nach einem Neustart des Servers.
            fresh = HandEyeSession(pi.ports())
            await fresh.publish_idle()
            current = pi.status["current"]
            self.assertIsNone(pi.status["result"])
            self.assertEqual(current["sampleCount"], 6)
            np.testing.assert_allclose(
                geometry.pose_from_dict(current["poseFlangeCam"]), FLANGE_CAM, atol=1e-6
            )

            saved = load_hand_eye(pi.path)
            np.testing.assert_allclose(saved.pose_flange_cam, FLANGE_CAM, atol=1e-6)
            self.assertEqual(saved.frame_id, "cam_flange")
            # Aktiv ohne Neustart: die frisch geladene Datei ging an die Quelle.
            self.assertEqual(len(pi.applied), 1)
            np.testing.assert_allclose(pi.applied[0].pose_flange_cam, FLANGE_CAM, atol=1e-6)

    async def test_the_old_file_is_backed_up(self):
        with tempfile.TemporaryDirectory() as folder:
            pi = FakePi(folder)
            save_hand_eye(pi.path, HandEye(pose_flange_cam=geometry.identity(), frame_id="alt"))
            old = pi.path.read_text(encoding="utf-8")
            session = HandEyeSession(pi.ports())
            await session.start(start_json())
            await self.capture_all(session, pi, flange_poses())
            await session.finish()
            backup = Path(pi.status["result"]["backupPath"])
            self.assertEqual(backup.read_text(encoding="utf-8"), old)
            self.assertEqual(backup.parent, pi.path.parent)
            # Ohne tagInBase gibt es keine Gegenprobe.
            self.assertIsNone(pi.status["result"]["knownTagCheckM"])

    async def test_a_tag_not_seen_is_dropped_with_a_reason(self):
        with tempfile.TemporaryDirectory() as folder:
            pi = FakePi(folder)
            session = HandEyeSession(pi.ports())
            await session.start(start_json())
            pi.visible = False
            pi.flange = flange_poses()[0]
            self.assertEqual(
                await session.capture(sample_json(pi.flange)), VisionErrorCode.DETECTION_FAILED
            )
            sample = pi.status["samples"][0]
            self.assertFalse(sample["accepted"])
            self.assertIsNone(sample["camToTag"])
            self.assertIn("nicht gesehen", sample["detail"])
            self.assertEqual(pi.status["acceptedCount"], 0)

    async def test_too_few_or_single_axis_poses_keep_the_session_open(self):
        with tempfile.TemporaryDirectory() as folder:
            pi = FakePi(folder)
            session = HandEyeSession(pi.ports())
            await session.start(start_json())
            await self.capture_all(session, pi, flange_poses()[:2])
            self.assertEqual(await session.finish(), VisionErrorCode.INVALID_STATE)
            self.assertEqual(pi.status["state"], "capturing")
            self.assertIn("mindestens", pi.status["message"])

            await session.abort()
            await session.start(start_json())
            await self.capture_all(session, pi, flange_poses(single_axis=True))
            self.assertEqual(pi.status["rotationAxes"], 1)
            self.assertIn("andere Achse", pi.status["message"])
            self.assertEqual(await session.finish(), VisionErrorCode.INVALID_STATE)
            self.assertIn("Achse", pi.status["message"])
            self.assertFalse(pi.path.exists())
            self.assertEqual(pi.applied, [])

    async def test_refusals_and_abort(self):
        with tempfile.TemporaryDirectory() as folder:
            pi = FakePi(folder)
            session = HandEyeSession(pi.ports())
            self.assertEqual(await session.capture(sample_json(pose((0, 0, 0)))), VisionErrorCode.INVALID_STATE)
            self.assertEqual(await session.finish(), VisionErrorCode.INVALID_STATE)
            self.assertEqual(await session.abort(), VisionErrorCode.INVALID_STATE)
            self.assertEqual(await session.start("{}"), VisionErrorCode.INVALID_ARGUMENT)
            self.assertEqual(await session.start(start_json(tag_id=-1)), VisionErrorCode.INVALID_ARGUMENT)
            pi.is_busy = True
            self.assertEqual(await session.start(start_json()), VisionErrorCode.BUSY)
            pi.is_busy = False

            await session.start(start_json())
            self.assertEqual(await session.start(start_json()), VisionErrorCode.BUSY)
            self.assertEqual(await session.capture("kaputt"), VisionErrorCode.INVALID_ARGUMENT)
            pi.is_busy = True  # ein Job belegt die Kamera
            self.assertEqual(await session.capture(sample_json(pose((0, 0, 0)))), VisionErrorCode.BUSY)
            pi.is_busy = False

            self.assertEqual(await session.abort(), VisionErrorCode.OK)
            self.assertEqual(pi.status["state"], "idle")
            self.assertFalse(session.active)
            self.assertFalse(pi.path.exists())


def board_json(cols=5, rows=6, size=0.023, **extra):
    data = {"schema": START_SCHEMA, "board": {"type": "chessboard", "cols": cols, "rows": rows, "squareSizeM": size}}
    data.update(extra)
    return json.dumps(data)


@unittest.skipUnless(np is not None, "numpy fehlt")
class HandEyeBoardTest(unittest.IsolatedAsyncioTestCase):
    """Dasselbe mit einem Schachbrett statt eines Tags als festem Ziel."""

    def board_pi(self, folder):
        pi = FakePi(folder)
        measured = []

        async def measure_board(spec):
            measured.append(spec)
            if not pi.visible:
                return None, "Board nicht (vollstaendig) gesehen"
            return cam_to_tag(pi.flange), ""

        async def no_tag(tag_id):  # pragma: no cover - darf nicht gerufen werden
            raise AssertionError("mit Board wird kein Tag gemessen")

        ports = pi.ports()
        ports = HandEyePorts(
            measure_tag=no_tag,
            busy=ports.busy,
            path=ports.path,
            apply=ports.apply,
            publish=ports.publish,
            measure_board=measure_board,
        )
        return pi, ports, measured

    async def test_board_capture_solve_and_status(self):
        with tempfile.TemporaryDirectory() as folder:
            pi, ports, measured = self.board_pi(folder)
            session = HandEyeSession(ports)
            self.assertEqual(await session.start(board_json()), VisionErrorCode.OK)
            self.assertEqual(
                pi.status["target"], {"type": "chessboard", "cols": 5, "rows": 6, "squareSizeM": 0.023}
            )
            self.assertEqual(pi.status["tagId"], -1)
            for flange in flange_poses():
                pi.flange = flange
                self.assertEqual(await session.capture(sample_json(flange)), VisionErrorCode.OK)
            self.assertEqual((measured[0].cols, measured[0].rows), (5, 6))
            self.assertIn("Schachbrett 5x6", pi.status["samples"][0]["detail"])

            self.assertEqual(await session.finish(), VisionErrorCode.OK)
            np.testing.assert_allclose(load_hand_eye(pi.path).pose_flange_cam, FLANGE_CAM, atol=1e-6)

    async def test_board_not_seen_is_dropped(self):
        with tempfile.TemporaryDirectory() as folder:
            pi, ports, _ = self.board_pi(folder)
            session = HandEyeSession(ports)
            await session.start(board_json())
            pi.visible = False
            pi.flange = flange_poses()[0]
            self.assertEqual(
                await session.capture(sample_json(pi.flange)), VisionErrorCode.DETECTION_FAILED
            )
            self.assertIn("Board", pi.status["samples"][0]["detail"])

    async def test_board_refusals(self):
        with tempfile.TemporaryDirectory() as folder:
            pi, ports, _ = self.board_pi(folder)
            session = HandEyeSession(ports)
            # 6x6 Innenecken: um 180 Grad gedreht nicht zu unterscheiden.
            self.assertEqual(await session.start(board_json(cols=6, rows=6)), VisionErrorCode.INVALID_ARGUMENT)
            self.assertEqual(await session.start(board_json(size=0)), VisionErrorCode.INVALID_ARGUMENT)
            self.assertEqual(await session.start(board_json(tagId=3)), VisionErrorCode.INVALID_ARGUMENT)
            self.assertFalse(session.active)

    async def test_a_server_without_board_support_says_so(self):
        with tempfile.TemporaryDirectory() as folder:
            pi = FakePi(folder)
            session = HandEyeSession(pi.ports())
            await session.start(board_json())
            pi.flange = flange_poses()[0]
            self.assertEqual(
                await session.capture(sample_json(pi.flange)), VisionErrorCode.DETECTION_FAILED
            )
            self.assertIn("kein Schachbrett", pi.status["samples"][0]["detail"])


@unittest.skipUnless(np is not None, "numpy fehlt")
class RotationDiversityTest(unittest.TestCase):
    def test_axes_are_counted(self):
        self.assertEqual(rotation_diversity([]), (0.0, 0))
        spread, axes = rotation_diversity(flange_poses(single_axis=True))
        self.assertAlmostEqual(spread, 80.0, places=6)
        self.assertEqual(axes, 1)
        self.assertGreaterEqual(rotation_diversity(flange_poses())[1], 2)


if __name__ == "__main__":
    unittest.main()

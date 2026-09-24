"""Tests fuer den Watchdog der `SharedCamera`.

Die Hardware-Methoden (`_open_camera`, `_read_frame`, `_close_camera`) werden
ersetzt; so laesst sich ein `capture_array()` nachstellen, das wie auf Pi 1
(2026-09-22) ewig auf einen Frame wartet.
"""

import asyncio
import threading
import unittest

from vision_server.camera import SharedCamera
from vision_server.profiles import CameraStreamConfig

FAST_CONFIG = CameraStreamConfig(
    capture_fps=100.0,
    warmup_s=0.0,
    frame_timeout_s=0.05,
    max_capture_failures=3,
    max_reopen_attempts=2,
)


class ScriptedCamera(SharedCamera):
    """Jede Oeffnung bekommt ein Verhalten: "ok", "hang", "error" oder "fail_open"."""

    def __init__(self, behaviours: list[str], config: CameraStreamConfig = FAST_CONFIG):
        self.gave_up = 0
        super().__init__(config, on_give_up=self._record_give_up)
        self._behaviours = list(behaviours)
        self.opened: list[int] = []
        self.closed: list[int] = []
        self.release = threading.Event()

    def _record_give_up(self) -> None:
        self.gave_up += 1

    def _open_camera(self):
        handle = len(self.opened)
        behaviour = self._behaviours[min(handle, len(self._behaviours) - 1)]
        self.opened.append(handle)
        if behaviour == "fail_open":
            raise RuntimeError("Kamera nicht da")
        return (handle, behaviour)

    def _read_frame(self):
        handle, behaviour = self._camera
        if behaviour == "hang":
            self.release.wait(timeout=5.0)
            raise RuntimeError("zu spaet")
        if behaviour == "error":
            raise RuntimeError("Kamera lieferte kein Bild")
        return f"bild-{handle}"

    def _close_camera(self, camera) -> None:
        self.closed.append(camera[0])


async def _wait_until(condition, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("Bedingung nicht rechtzeitig erfuellt")
        await asyncio.sleep(0.01)


class WatchdogTest(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        for camera in getattr(self, "_cameras", []):
            camera.release.set()
            await camera.close()

    def _camera(self, behaviours: list[str]) -> ScriptedCamera:
        camera = ScriptedCamera(behaviours)
        self._cameras = [*getattr(self, "_cameras", []), camera]
        return camera

    async def test_delivers_frames_from_a_healthy_camera(self):
        camera = self._camera(["ok"])
        await camera.open()

        await _wait_until(lambda: camera.latest_frame is not None)

        self.assertEqual(camera.latest_frame.image, "bild-0")
        self.assertEqual(camera.opened, [0])

    async def test_reopens_a_hanging_camera_and_resumes(self):
        camera = self._camera(["hang", "ok"])
        await camera.open()

        await _wait_until(
            lambda: camera.latest_frame is not None and camera.latest_frame.image == "bild-1"
        )

        self.assertEqual(camera.opened, [0, 1])
        self.assertEqual(camera.closed, [0])
        self.assertEqual(camera.gave_up, 0)

    async def test_reopens_after_repeated_capture_errors(self):
        camera = self._camera(["error", "ok"])
        await camera.open()

        await _wait_until(lambda: camera.latest_frame is not None)

        self.assertEqual(camera.latest_frame.image, "bild-1")
        self.assertEqual(camera.opened, [0, 1])

    async def test_gives_up_when_reopening_never_brings_a_frame(self):
        camera = self._camera(["hang", "hang", "fail_open"])
        await camera.open()

        await _wait_until(lambda: camera.gave_up == 1)

        # Die erste Oeffnung plus max_reopen_attempts Neu-Oeffnungen.
        self.assertEqual(len(camera.opened), 1 + FAST_CONFIG.max_reopen_attempts)
        self.assertIsNone(camera.latest_frame)

    async def test_a_frame_between_hangs_resets_the_reopen_budget(self):
        """Haengt die Kamera alle paar Minuten einmal, heilt sie sich jedes Mal selbst."""
        camera = self._camera(["hang", "ok"])
        await camera.open()
        await _wait_until(lambda: camera.latest_frame is not None)

        for _ in range(FAST_CONFIG.max_reopen_attempts + 1):
            camera._behaviours = ["hang"] * len(camera.opened) + ["ok"]
            camera._camera = (camera._camera[0], "hang")
            opened_before = len(camera.opened)
            await _wait_until(lambda: len(camera.opened) > opened_before)
            await _wait_until(lambda: camera.latest_frame.image == f"bild-{opened_before}")

        self.assertEqual(camera.gave_up, 0)

    async def test_close_returns_while_a_capture_hangs(self):
        camera = self._camera(["hang"])
        await camera.open()
        await asyncio.sleep(0.02)

        await asyncio.wait_for(camera.close(), timeout=1.0)

        self.assertFalse(camera.is_open)


class _ScriptedCameraTest(unittest.IsolatedAsyncioTestCase):
    """Gemeinsames Aufraeumen: haengende Leser freigeben, dann schliessen."""

    async def asyncTearDown(self):
        for camera in getattr(self, "_cameras", []):
            camera.release.set()
            await camera.close()

    def _camera(self, behaviours: list[str]) -> ScriptedCamera:
        camera = ScriptedCamera(behaviours)
        self._cameras = [*getattr(self, "_cameras", []), camera]
        return camera


class StatusTest(_ScriptedCameraTest):
    """`status()` ist die einzige Wahrheit, aus der `DeviceHealth` entsteht.

    Ohne sie muesste `camera_health.py` den Zustand ein zweites Mal
    herleiten -- und die beiden Herleitungen wuerden auseinanderlaufen.
    """

    async def test_an_unopened_camera_is_not_running(self):
        camera = self._camera(["ok"])

        status = camera.status(now=0.0)

        self.assertFalse(status.running)
        self.assertFalse(status.has_handle)
        self.assertIsNone(status.frame_age_s)
        self.assertEqual(status.last_outcome, "none")

    async def test_reports_ok_while_frames_arrive(self):
        camera = self._camera(["ok"])
        await camera.open()
        await _wait_until(lambda: camera.latest_frame is not None)

        status = camera.status(now=asyncio.get_running_loop().time())

        self.assertTrue(status.running)
        self.assertTrue(status.has_handle)
        self.assertEqual(status.last_outcome, "ok")
        self.assertEqual(status.consecutive_failures, 0)
        self.assertEqual(status.reopen_attempts, 0)
        self.assertFalse(status.gave_up)

    async def test_reports_the_age_against_the_passed_clock(self):
        """Die Uhr kommt von aussen -- sonst waere die Methode nicht ohne
        laufenden Loop testbar."""
        camera = self._camera(["ok"])
        await camera.open()
        await _wait_until(lambda: camera.latest_frame is not None)
        captured_at = camera.latest_frame.timestamp

        status = camera.status(now=captured_at + 7.5)

        self.assertAlmostEqual(status.frame_age_s, 7.5, places=6)

    async def test_counts_reopen_attempts_until_it_gives_up(self):
        camera = self._camera(["hang", "hang", "fail_open"])
        await camera.open()

        await _wait_until(lambda: camera.gave_up == 1)
        status = camera.status(now=asyncio.get_running_loop().time())

        self.assertTrue(status.gave_up)
        self.assertEqual(status.last_outcome, "hung")
        self.assertFalse(status.has_handle)


class GiveUpHandlerTest(_ScriptedCameraTest):
    async def test_awaits_an_asynchronous_handler(self):
        """Der Runner schreibt hier ein letztes FAILURE, bevor der Prozess
        faellt -- ein synchron aufgerufener Handler kaeme nicht dazu."""
        camera = self._camera(["hang", "hang", "fail_open"])
        done = asyncio.Event()

        async def handler() -> None:
            await asyncio.sleep(0)
            done.set()

        camera.set_give_up_handler(handler)
        await camera.open()

        await asyncio.wait_for(done.wait(), timeout=2.0)


if __name__ == "__main__":
    unittest.main()

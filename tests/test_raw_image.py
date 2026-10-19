"""Tests fuer `SaveRawImage`: PNG-Ablage und die OPC-UA-Methode."""

import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

try:
    import cv2  # noqa: F401
    import numpy as np
    from asyncua import Server, ua

    from vision_server.errors import VisionErrorCode
    from vision_server.raw_image import save_raw_image
except Exception:  # pragma: no cover - numpy/OpenCV/asyncua fehlen
    np = None

NOW = datetime(2026, 10, 19, 14, 30, 12)


def test_image():
    return np.random.default_rng(0).integers(0, 256, (48, 64, 3), dtype=np.uint8)


@unittest.skipUnless(np is not None, "numpy/OpenCV/asyncua fehlen")
class SaveRawImageTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name) / "rohbilder"

    def test_png_is_lossless_and_named_by_frame_and_time(self):
        import cv2

        image = test_image()
        path = save_raw_image(image, self.directory, "cam_ceiling", NOW)
        self.assertEqual(path.name, "cam_ceiling_20261019-143012.png")
        self.assertTrue(np.array_equal(cv2.imread(str(path)), image))

    def test_creates_the_directory(self):
        self.assertFalse(self.directory.exists())
        save_raw_image(test_image(), self.directory, "cam", NOW)
        self.assertTrue(self.directory.is_dir())

    def test_never_overwrites(self):
        first = save_raw_image(test_image(), self.directory, "cam", NOW)
        second = save_raw_image(test_image(), self.directory, "cam", NOW)
        third = save_raw_image(test_image(), self.directory, "cam", NOW)
        self.assertEqual(
            [first.name, second.name, third.name],
            ["cam_20261019-143012.png", "cam_20261019-143012_2.png", "cam_20261019-143012_3.png"],
        )

    def test_raises_oserror_when_imwrite_fails(self):
        with mock.patch("cv2.imwrite", return_value=False):
            with self.assertRaises(OSError):
                save_raw_image(test_image(), self.directory, "cam", NOW)


@unittest.skipUnless(np is not None, "numpy/OpenCV/asyncua fehlen")
class SaveRawImageOpcUaTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from vision_server.runner import _install_save_raw_image

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name) / "rohbilder"
        server = Server()
        await server.init()
        idx = await server.register_namespace("http://launch-rm.de/vision")
        self.vision_system = await server.nodes.objects.add_object(
            ua.NodeId("VisionMachine", idx), ua.QualifiedName("VisionMachine", idx)
        )
        space = types.SimpleNamespace(vision_system=self.vision_system, own_idx=idx)
        config = types.SimpleNamespace(
            vision_system_name="VisionMachine",
            apriltag=types.SimpleNamespace(frame_id="cam_ceiling"),
            raw_image_dir=self.directory,
        )
        self.camera = types.SimpleNamespace(latest_frame=None)
        self.busy = {"value": False}
        self.methods = {}
        await _install_save_raw_image(
            space, config, types.SimpleNamespace(camera=self.camera), self.methods,
            lambda: self.busy["value"],
        )
        self.method = server.get_node(ua.NodeId("VisionMachine.SaveRawImage", idx))

    async def call(self):
        return await self.vision_system.call_method(self.method)

    async def test_saves_the_latest_frame(self):
        self.assertEqual(list(self.methods), ["SaveRawImage"])
        self.camera.latest_frame = types.SimpleNamespace(image=test_image())
        name, error = await self.call()
        self.assertEqual(error, int(VisionErrorCode.OK))
        # Ausserhalb des Repos (Tempverzeichnis): absoluter Pfad.
        self.assertEqual(Path(name).parent, self.directory)
        self.assertTrue(name.endswith(".png") and Path(name).name.startswith("cam_ceiling_"))
        self.assertTrue(Path(name).is_file())

    async def test_without_frame_is_invalid_state(self):
        name, error = await self.call()
        self.assertEqual((name, error), ("", int(VisionErrorCode.INVALID_STATE)))
        self.assertFalse(self.directory.exists())

    async def test_busy_job_is_rejected(self):
        self.camera.latest_frame = types.SimpleNamespace(image=test_image())
        self.busy["value"] = True
        name, error = await self.call()
        self.assertEqual((name, error), ("", int(VisionErrorCode.BUSY)))
        self.assertFalse(self.directory.exists())

    async def test_write_failure_is_internal(self):
        self.camera.latest_frame = types.SimpleNamespace(image=test_image())
        with mock.patch("cv2.imwrite", return_value=False):
            name, error = await self.call()
        self.assertEqual((name, error), ("", int(VisionErrorCode.INTERNAL)))


if __name__ == "__main__":
    unittest.main()

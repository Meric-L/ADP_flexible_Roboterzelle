"""Tests fuer den Livestream-Publisher.

`encode_frame` wird durch einen Fake ersetzt, damit die Tests ohne `cv2`
laufen und ohne echte Bilddaten auskommen. `max_stream_width=None` schaltet
das Herunterskalieren vor dem Encode ab -- das braeuchte ein echtes Array
mit `.shape` statt der hier verwendeten Platzhalter-Strings; eigens getestet
in `ResizeForStreamTest` unten.
"""

import asyncio
import threading
import unittest
from dataclasses import replace

from tagloc.modes import DEFAULT_OVERLAY_MODE, OVERLAY_MODES, normalise_mode
from vision_server.camera import CameraFrame
from vision_server.camera_stream import CameraStreamPublisher, _resize_for_stream
from vision_server.profiles import CameraStreamConfig

try:
    import cv2
    import numpy as np
except Exception:  # OpenCV nicht installiert
    cv2 = None
    np = None

FAST_CONFIG = CameraStreamConfig(stream_fps=50.0, max_stream_width=None)


class FakeCamera:
    def __init__(self) -> None:
        self.latest_frame: CameraFrame | None = None


class FakeNode:
    def __init__(self) -> None:
        self.written: list[str] = []

    async def write_value(self, value) -> None:
        self.written.append(value)


class FakeModeNode:
    """The writable node through which the frontend selects the overlay mode."""

    def __init__(self, value, *, fail: bool = False) -> None:
        self.value = value
        self.fail = fail
        self.reads = 0

    async def read_value(self):
        self.reads += 1
        if self.fail:
            raise RuntimeError("Knoten nicht lesbar")
        return self.value


class FakeAnnotator:
    """Record what it was called with, and mark the image recognisably."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def annotate(self, image, mode: str):
        self.calls.append((image, mode))
        return f"annotated:{image}:{mode}"


def fake_encode(image, quality: int) -> str:
    return f"encoded:{image}:{quality}"


def _fresh_frame(image: str) -> CameraFrame:
    """Ein Frame von eben -- aeltere gelten als veraltet und gehen nicht raus."""
    return CameraFrame(image=image, timestamp=asyncio.get_running_loop().time())


async def _run_briefly(publisher: CameraStreamPublisher, seconds: float) -> None:
    publisher.start()
    await asyncio.sleep(seconds)
    await publisher.stop()


class PublishLoopTest(unittest.IsolatedAsyncioTestCase):
    async def test_writes_the_encoded_frame_to_the_node(self):
        camera = FakeCamera()
        camera.latest_frame = _fresh_frame("frame-1")
        node = FakeNode()
        publisher = CameraStreamPublisher(camera, node, FAST_CONFIG, encode_frame=fake_encode)

        await _run_briefly(publisher, 0.1)

        self.assertGreaterEqual(len(node.written), 1)
        self.assertEqual(node.written[0], f"encoded:frame-1:{FAST_CONFIG.jpeg_quality}")

    async def test_writes_nothing_before_the_first_frame_exists(self):
        camera = FakeCamera()
        node = FakeNode()
        publisher = CameraStreamPublisher(camera, node, FAST_CONFIG, encode_frame=fake_encode)

        await _run_briefly(publisher, 0.06)

        self.assertEqual(node.written, [])

    async def test_stop_is_idempotent_and_cancels_the_loop(self):
        camera = FakeCamera()
        node = FakeNode()
        publisher = CameraStreamPublisher(camera, node, FAST_CONFIG, encode_frame=fake_encode)
        publisher.start()
        await publisher.stop()
        await publisher.stop()

    async def test_an_encode_failure_does_not_kill_the_loop(self):
        """Ein einzelner kaputter Frame darf den Stream nicht dauerhaft stoppen."""
        camera = FakeCamera()
        camera.latest_frame = _fresh_frame("bad")
        node = FakeNode()

        calls = 0

        def flaky_encode(image, quality):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("Encode kaputt")
            return "ok"

        config = replace(FAST_CONFIG, stream_fps=50.0)
        publisher = CameraStreamPublisher(camera, node, config, encode_frame=flaky_encode)

        # Grosszuegiges Fenster: der erste `run_in_executor(None, ...)`-Aufruf
        # zahlt die Thread-Pool-Anlaufzeit, die auf einer ausgelasteten
        # Maschine schon mal die erste Iteration allein aufbraucht.
        await _run_briefly(publisher, 0.5)

        self.assertGreaterEqual(calls, 2)
        self.assertIn("ok", node.written)


class OverlayModeTest(unittest.IsolatedAsyncioTestCase):
    def _publisher(self, mode: str, *, annotator, mode_node=None):
        camera = FakeCamera()
        camera.latest_frame = _fresh_frame("frame-1")
        node = FakeNode()
        publisher = CameraStreamPublisher(
            camera,
            node,
            replace(FAST_CONFIG, overlay_mode=mode),
            encode_frame=fake_encode,
            annotator=annotator,
            mode_node=mode_node,
        )
        return publisher, node

    async def test_encodes_the_raw_frame_in_mode_off(self):
        annotator = FakeAnnotator()
        publisher, node = self._publisher("off", annotator=annotator)

        await _run_briefly(publisher, 0.1)

        self.assertEqual(annotator.calls, [])
        self.assertEqual(node.written[0], f"encoded:frame-1:{FAST_CONFIG.jpeg_quality}")

    async def test_encodes_the_annotated_frame_in_mode_apriltag(self):
        annotator = FakeAnnotator()
        publisher, node = self._publisher("apriltag", annotator=annotator)

        await _run_briefly(publisher, 0.1)

        self.assertGreaterEqual(len(annotator.calls), 1)
        self.assertEqual(annotator.calls[0], ("frame-1", "apriltag"))
        self.assertEqual(
            node.written[0], f"encoded:annotated:frame-1:apriltag:{FAST_CONFIG.jpeg_quality}"
        )

    async def test_follows_the_mode_node(self):
        annotator = FakeAnnotator()
        publisher, node = self._publisher(
            "apriltag", annotator=annotator, mode_node=FakeModeNode("  OFF ")
        )

        await _run_briefly(publisher, 0.1)

        self.assertEqual(publisher.mode, "off")
        self.assertEqual(annotator.calls, [])
        self.assertEqual(node.written[0], f"encoded:frame-1:{FAST_CONFIG.jpeg_quality}")

    async def test_keeps_the_last_known_mode_when_the_node_cannot_be_read(self):
        """An unreadable node must not stop the stream."""
        annotator = FakeAnnotator()
        mode_node = FakeModeNode(None, fail=True)
        publisher, node = self._publisher(
            "calibration", annotator=annotator, mode_node=mode_node
        )

        await _run_briefly(publisher, 0.1)

        self.assertGreaterEqual(mode_node.reads, 1)
        self.assertEqual(publisher.mode, "calibration")
        self.assertEqual(annotator.calls[0], ("frame-1", "calibration"))
        self.assertGreaterEqual(len(node.written), 1)

    async def test_an_annotator_failure_does_not_kill_the_loop(self):
        camera = FakeCamera()
        camera.latest_frame = _fresh_frame("frame-1")
        node = FakeNode()

        calls = 0

        class FlakyAnnotator:
            def annotate(self, image, mode):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise RuntimeError("Overlay kaputt")
                return "markiert"

        publisher = CameraStreamPublisher(
            camera, node, FAST_CONFIG, encode_frame=fake_encode, annotator=FlakyAnnotator()
        )

        await _run_briefly(publisher, 0.1)

        self.assertGreaterEqual(calls, 2)
        self.assertIn(f"encoded:markiert:{FAST_CONFIG.jpeg_quality}", node.written)


@unittest.skipUnless(cv2 is not None, "OpenCV nicht verfuegbar")
class ResizeForStreamTest(unittest.TestCase):
    def test_leaves_a_narrower_image_untouched(self):
        image = np.zeros((480, 640, 3), dtype=np.uint8)

        result = _resize_for_stream(image, 960)

        self.assertIs(result, image)

    def test_downscales_a_wider_image_keeping_aspect_ratio(self):
        image = np.zeros((1520, 2028, 3), dtype=np.uint8)

        result = _resize_for_stream(image, 960)

        self.assertEqual(result.shape[1], 960)
        self.assertEqual(result.shape[0], round(1520 * 960 / 2028))


class StaleFrameTest(unittest.IsolatedAsyncioTestCase):
    """Ein haengendes capture darf im Frontend nicht wie ein lebendes Bild aussehen."""

    async def test_clears_the_node_once_instead_of_republishing_an_old_frame(self):
        camera = FakeCamera()
        loop = asyncio.get_running_loop()
        camera.latest_frame = CameraFrame(image="alt", timestamp=loop.time() - 10.0)
        node = FakeNode()
        publisher = CameraStreamPublisher(camera, node, FAST_CONFIG, encode_frame=fake_encode)

        await _run_briefly(publisher, 0.1)

        self.assertEqual(node.written, [""])

    async def test_resumes_as_soon_as_a_fresh_frame_arrives(self):
        camera = FakeCamera()
        loop = asyncio.get_running_loop()
        camera.latest_frame = CameraFrame(image="alt", timestamp=loop.time() - 10.0)
        node = FakeNode()
        publisher = CameraStreamPublisher(camera, node, FAST_CONFIG, encode_frame=fake_encode)

        publisher.start()
        await asyncio.sleep(0.06)
        camera.latest_frame = _fresh_frame("neu")
        await asyncio.sleep(0.06)
        await publisher.stop()

        self.assertEqual(node.written[0], "")
        self.assertIn(f"encoded:neu:{FAST_CONFIG.jpeg_quality}", node.written)


class OverlayTimeoutTest(unittest.IsolatedAsyncioTestCase):
    async def test_sends_the_raw_frame_while_the_overlay_hangs(self):
        release = threading.Event()
        calls = 0

        class HangingAnnotator:
            def annotate(self, image, mode):
                nonlocal calls
                calls += 1
                release.wait(timeout=5.0)
                return "markiert"

        camera = FakeCamera()
        camera.latest_frame = _fresh_frame("frame-1")
        node = FakeNode()
        publisher = CameraStreamPublisher(
            camera,
            node,
            replace(FAST_CONFIG, overlay_timeout_s=0.02),
            encode_frame=fake_encode,
            annotator=HangingAnnotator(),
        )

        try:
            await _run_briefly(publisher, 0.15)
        finally:
            release.set()

        self.assertIn(f"encoded:frame-1:{FAST_CONFIG.jpeg_quality}", node.written)
        # Solange der erste Lauf haengt, startet kein zweiter.
        self.assertEqual(calls, 1)


class NormaliseModeTest(unittest.TestCase):
    def test_keeps_every_known_mode(self):
        for mode in OVERLAY_MODES:
            self.assertEqual(normalise_mode(mode), mode)

    def test_falls_back_for_an_empty_value(self):
        for value in ("", None, "   "):
            self.assertEqual(normalise_mode(value), DEFAULT_OVERLAY_MODE)

    def test_falls_back_for_an_unknown_value(self):
        self.assertEqual(normalise_mode("tippfehler"), DEFAULT_OVERLAY_MODE)

    def test_ignores_case_and_surrounding_whitespace(self):
        self.assertEqual(normalise_mode("  OFF  "), "off")
        self.assertEqual(normalise_mode("Calibration"), "calibration")




class PreviewSourceTest(unittest.IsolatedAsyncioTestCase):
    """Mit einem kleinen ISP-Bild rechnet der Stream im `calibration`-Modus
    auf dem verkleinerten Vorschaubild -- genau das liess die Deckenkamera
    ruckeln, als noch jeder Modus auf dem vollen Frame rechnete.

    `apriltag` und `off` nutzen dagegen bewusst den vollen Frame
    (Absprache 2026-09-22): `apriltag`, damit der Stream dasselbe Bild zeigt
    wie der echte Job -- sonst laesst sich nicht vertrauenswuerdig sehen, ob
    der Pi ein Tag wirklich erkennt. `off` ist das Debug-Rohbild und soll
    zeigen, was der Pi tatsaechlich sieht (Fokus/Belichtung/Ausschnitt),
    nicht die Vorschau -- kostet ohne Erkennung kaum mehr.
    """

    async def test_apriltag_mode_overlays_and_encodes_the_full_frame(self):
        camera = FakeCamera()
        loop = asyncio.get_running_loop()
        camera.latest_frame = CameraFrame(image="voll", timestamp=loop.time(), preview="klein")
        node = FakeNode()
        annotator = FakeAnnotator()
        publisher = CameraStreamPublisher(
            camera, node, FAST_CONFIG, encode_frame=fake_encode, annotator=annotator
        )

        await _run_briefly(publisher, 0.1)

        self.assertEqual({image for image, _ in annotator.calls}, {"voll"})
        self.assertIn(
            f"encoded:annotated:voll:{DEFAULT_OVERLAY_MODE}:{FAST_CONFIG.jpeg_quality}",
            node.written,
        )

    async def test_calibration_mode_still_uses_the_preview(self):
        camera = FakeCamera()
        loop = asyncio.get_running_loop()
        camera.latest_frame = CameraFrame(image="voll", timestamp=loop.time(), preview="klein")
        node = FakeNode()
        annotator = FakeAnnotator()
        publisher = CameraStreamPublisher(
            camera,
            node,
            FAST_CONFIG,
            encode_frame=fake_encode,
            annotator=annotator,
            mode_node=FakeModeNode("calibration"),
        )

        await _run_briefly(publisher, 0.1)

        self.assertEqual({image for image, _ in annotator.calls}, {"klein"})

    async def test_raw_mode_sends_the_full_frame_unmarked(self):
        """'off' ist das Debug-Rohbild: soll zeigen, was der Pi tatsaechlich
        sieht (Fokus/Belichtung/Ausschnitt pruefen), nicht die Vorschau."""
        camera = FakeCamera()
        loop = asyncio.get_running_loop()
        camera.latest_frame = CameraFrame(image="voll", timestamp=loop.time(), preview="klein")
        node = FakeNode()
        annotator = FakeAnnotator()
        publisher = CameraStreamPublisher(
            camera,
            node,
            FAST_CONFIG,
            encode_frame=fake_encode,
            annotator=annotator,
            mode_node=FakeModeNode("off"),
        )

        await _run_briefly(publisher, 0.1)

        self.assertEqual(annotator.calls, [])
        self.assertIn(f"encoded:voll:{FAST_CONFIG.jpeg_quality}", node.written)

    async def test_without_a_preview_the_full_frame_is_used_as_before(self):
        camera = FakeCamera()
        camera.latest_frame = _fresh_frame("voll")
        node = FakeNode()
        publisher = CameraStreamPublisher(camera, node, FAST_CONFIG, encode_frame=fake_encode)

        await _run_briefly(publisher, 0.1)

        self.assertIn(f"encoded:voll:{FAST_CONFIG.jpeg_quality}", node.written)


if __name__ == "__main__":
    unittest.main()

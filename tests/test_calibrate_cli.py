"""`tagloc.cli.calibrate`: `--capture-to` auch im Headless-Zweig mit Kamera-Quelle."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from tagloc.cli import calibrate


class _FakeSource:
    def __init__(self, n_frames: int):
        self._frames = [np.full((8, 8), 10 * i, np.uint8) for i in range(n_frames)]

    def read(self):
        return self._frames.pop(0) if self._frames else None

    def close(self):
        pass


class _FakeSample:
    def count(self) -> int:
        return 12


class CaptureToHeadlessTest(unittest.TestCase):
    def _run(self, source: str, tmp: str) -> list[str]:
        ziel = Path(tmp) / "aufnahmen"
        with (
            mock.patch.object(calibrate.frame_sources, "open_source", return_value=_FakeSource(2)),
            mock.patch.object(calibrate, "detect_board", return_value=_FakeSample()),
            mock.patch.object(calibrate, "build_board", return_value=object()),
        ):
            rc = calibrate.main(
                [
                    "--source", source, "--no-gui", "--out", str(Path(tmp) / "k.json"),
                    "--capture-to", str(ziel), "--min-samples", "5",
                ]
            )
        self.assertEqual(rc, 1)  # unter 3 Aufnahmen wird nicht gerechnet
        return sorted(p.name for p in ziel.iterdir())

    def test_kamera_quelle_speichert_aufnahmen(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._run("camera:0", tmp), ["kalib_001.png", "kalib_002.png"])

    def test_bildordner_wird_nicht_kopiert(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._run(tmp, tmp), [])


if __name__ == "__main__":
    unittest.main()

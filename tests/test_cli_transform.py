"""Tests fuer `tagloc.cli.transform` -- reine Rechnung, ohne OpenCV.

Haelt fest, dass `transform --out` dasselbe Austauschformat schreibt, das
`detect --json` erzeugt und `transform --input` wieder liest.
"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

try:
    import numpy as np

    from tagloc.cli import transform
    from tagloc.cli._common import CLI_SCHEMA, poses_from_json, poses_to_json
    from tagloc.geometry import identity
except Exception:  # numpy in dieser Umgebung defekt
    np = None


def _run(argv) -> int:
    with contextlib.redirect_stdout(io.StringIO()):
        return transform.main(argv)


@unittest.skipUnless(np is not None, "numpy nicht verfuegbar")
class TransformOutputTest(unittest.TestCase):
    def test_writes_the_cli_schema_and_target_frame(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "posen.json"

            code = _run(["--position", "0.1", "0.2", "0.3", "--to", "welt", "--out", str(out)])
            data = json.loads(out.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertEqual(data["schema"], CLI_SCHEMA)
        self.assertEqual(data["frameId"], "welt")
        [entry] = data["poses"]
        self.assertEqual(entry["tagId"], -1)
        self.assertEqual(entry["moduleId"], "")
        self.assertEqual(entry["reprojErrorPx"], 0.0)
        self.assertFalse(entry["ambiguous"])
        np.testing.assert_allclose(entry["pose"]["position"], (0.1, 0.2, 0.3))

    def test_output_can_be_read_back_as_input(self):
        with tempfile.TemporaryDirectory() as folder:
            first = Path(folder) / "a.json"
            second = Path(folder) / "b.json"
            first.write_text(json.dumps(poses_to_json([(4, identity())], "cam")), encoding="utf-8")

            _run(["--input", str(first), "--out", str(second)])
            frame_id, pairs = poses_from_json(second)

        self.assertEqual(frame_id, "world")
        self.assertEqual([tag_id for tag_id, _ in pairs], [4])
        np.testing.assert_allclose(pairs[0][1], identity(), atol=1e-12)


if __name__ == "__main__":
    unittest.main()

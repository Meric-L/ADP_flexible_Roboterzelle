"""Datensatz einer Hand-Auge-Sitzung (`session.json`) und die eine Verfeinerungs-Pipeline.

Der Hand-Pi schreibt den Datensatz, `calibrate_handeye --session` rechnet ihn
nach. Beide benutzen `tagloc.handeye.refine_board_session`; hier wird gezeigt,
dass die Nachrechnung am Rechner dasselbe Ergebnis liefert wie der Pi.
"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    import numpy as np

    from tagloc import geometry as g
    from tagloc import handeye
    from tagloc.boards import BoardSpec, chessboard_object_points
    from tagloc.cli.calibrate_handeye import main as calibrate_main
    from tagloc.handeye import (
        SESSION_SCHEMA,
        RefinementError,
        UnknownSessionSchema,
        load_session,
        refine_board_session,
    )
    from vision_server.errors import VisionErrorCode
    from vision_server.handeye_session import HandEyeSession

    from tests.test_handeye_refine import SPEC, scene
    from tests.test_handeye_session import sample_json, start_json
    from tests.test_handeye_session_refine import BoardPi, board_6x7
except Exception:  # pragma: no cover - numpy/cv2 fehlt in dieser Umgebung
    np = None


@unittest.skipUnless(np is not None, "numpy/cv2 fehlt")
class SessionDatasetTest(unittest.IsolatedAsyncioTestCase):
    async def finished(self, folder, count=12, **options):
        flanges, corners, boards = scene(count=count, scale=1.01, sigma_px=0.2, seed=31)
        pi = BoardPi(folder, flanges, corners, boards)
        session = HandEyeSession(pi.ports())
        self.assertEqual(await session.start(board_6x7()), VisionErrorCode.OK)
        for flange in flanges:
            self.assertEqual(await session.capture(sample_json(flange)), VisionErrorCode.OK)
        self.assertEqual(await session.finish(), VisionErrorCode.OK)
        return pi, Path(pi.status["result"]["sessionPath"])

    async def test_the_cli_recomputes_what_the_pi_computed(self):
        with tempfile.TemporaryDirectory() as folder:
            pi, dataset = await self.finished(folder)
            out = Path(folder) / "neu.json"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(calibrate_main(["--session", str(dataset), "--out", str(out)]), 0)
            self.assertIn("Verfeinert:", stdout.getvalue())
            self.assertIn("Leave-one-out-Vorhersagefehler", stdout.getvalue())
            self.assertIn("HINWEIS: Brettmassstab", stdout.getvalue())  # 1 % Massstabsfehler

            on_pi = json.loads(pi.path.read_text(encoding="utf-8"))
            at_desk = json.loads(out.read_text(encoding="utf-8"))
            # Die Pose geht im Datensatz durch Quaternion -> Matrix -> Quaternion:
            # Abweichungen im 12. Ziffernbereich, in der Datei (6 Stellen) unsichtbar.
            self.assertEqual(set(at_desk), set(on_pi))
            for key, value in on_pi.items():
                if key == "poseFlangeCam":
                    for part in ("position", "orientation"):
                        np.testing.assert_allclose(at_desk[key][part], value[part], atol=1e-9)
                elif key in ("createdAt", "calibrationId"):
                    continue
                elif isinstance(value, float):
                    self.assertAlmostEqual(at_desk[key], value, delta=2e-6, msg=key)
                else:
                    self.assertEqual(at_desk[key], value, key)

    async def test_the_reader_gives_what_the_writer_wrote(self):
        with tempfile.TemporaryDirectory() as folder:
            pi, dataset = await self.finished(folder)
            for path in (dataset, dataset / "session.json"):
                loaded = load_session(path)
                self.assertEqual(loaded.path, dataset / "session.json")
                self.assertEqual(loaded.total_count, 12)
                self.assertEqual(len(loaded.flanges), 12)
                self.assertEqual(loaded.frame_id, "cam_flange")
                self.assertEqual(loaded.intrinsics_source, "calibration")
                self.assertTrue(loaded.can_refine)
                self.assertEqual(
                    (loaded.board["cols"], loaded.board["rows"]), (SPEC.cols, SPEC.rows)
                )
            refined = loaded.refine()
            result = pi.status["result"]
            np.testing.assert_allclose(
                g.pose_to_dict(refined.refinement.pose_flange_cam)["position"],
                result["poseFlangeCam"]["position"],
                atol=1e-9,
            )
            self.assertAlmostEqual(refined.refinement.board_scale, result["boardScale"], places=9)
            self.assertAlmostEqual(refined.loo.rms_px, result["looErrorRmsPx"], places=9)
            self.assertAlmostEqual(refined.solution.rms_position_m, result["rmsPositionM"], places=9)
            self.assertEqual(list(refined.solution.rejected), [])
            self.assertEqual(refined.kept, tuple(range(12)))

    async def test_a_tag_session_has_nothing_to_refine(self):
        from tests.test_handeye_session import FakePi, flange_poses, cam_to_tag  # noqa: F401

        with tempfile.TemporaryDirectory() as folder:
            pi = FakePi(folder)
            session = HandEyeSession(pi.ports())
            await session.start(start_json())
            for flange in flange_poses():
                pi.flange = flange
                await session.capture(sample_json(flange))
            await session.finish()
            loaded = load_session(Path(pi.status["result"]["sessionPath"]))
            self.assertFalse(loaded.can_refine)
            self.assertIsNone(loaded.board)
            self.assertIsNone(loaded.intrinsics)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(calibrate_main(["--session", str(loaded.path.parent)]), 0)
            self.assertIn("keine Verfeinerung", stdout.getvalue())

    def test_a_foreign_file_is_refused_with_a_clear_message(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "session.json"
            path.write_text(json.dumps({"schema": "wsc.vision.handeye.samples/1"}), encoding="utf-8")
            with self.assertRaises(UnknownSessionSchema) as caught:
                load_session(path)
            self.assertIn(SESSION_SCHEMA, str(caught.exception))
            with self.assertRaises(SystemExit) as exit_:
                calibrate_main(["--session", folder])
            self.assertIn("Unbekanntes Sitzungs-Schema", str(exit_.exception))


@unittest.skipUnless(np is not None, "numpy/cv2 fehlt")
class RefineBoardSessionTest(unittest.TestCase):
    def inputs(self, count=10):
        flanges, corners, boards = scene(count=count, scale=1.0, sigma_px=0.2, seed=41)
        from tagloc.calibration import CameraCalibration
        from tests.test_handeye_refine import K

        calibration = CameraCalibration(
            camera_matrix=K.copy(), distortion=np.zeros(5), image_size=(640, 480)
        )
        return flanges, list(corners), boards, SPEC, calibration

    def test_it_is_the_pipeline_written_out(self):
        flanges, corners, boards, spec, calibration = self.inputs()
        result = refine_board_session(flanges, corners, boards, spec, calibration)
        # Von Hand: dieselben Aufrufe, wie sie Server und CLI vorher je fuer sich machten.
        from tagloc import lens

        points = chessboard_object_points(spec)
        undistorted = [lens.undistort_points(c, calibration) for c in corners]
        expected, rejected, closed = handeye.refine_hand_eye_robust(
            flanges, undistorted, boards, points, calibration.camera_matrix
        )
        np.testing.assert_array_equal(result.refinement.pose_flange_cam, expected.pose_flange_cam)
        self.assertEqual(result.solution.rejected, rejected)
        np.testing.assert_array_equal(result.closed_form.pose_flange_cam, closed.pose_flange_cam)
        self.assertEqual(result.kept, tuple(i for i in range(10) if i not in rejected))
        fields = result.hand_eye_fields()
        self.assertEqual(set(fields), {
            "refined_reprojection_rms_px", "board_scale", "loo_error_rms_px", "loo_error_rms_m",
        })

    def test_without_enough_samples_for_leave_one_out_the_fields_are_fewer(self):
        flanges, corners, boards, spec, calibration = self.inputs(count=3)
        result = refine_board_session(flanges, corners, boards, spec, calibration)
        self.assertIsNone(result.loo)
        self.assertEqual(set(result.hand_eye_fields()), {"refined_reprojection_rms_px", "board_scale"})

    def test_value_errors_pass_through_and_other_errors_name_their_stage(self):
        flanges, corners, boards, spec, calibration = self.inputs()
        with self.assertRaises(ValueError):  # zu wenige Aufnahmen: Befund ueber die Daten
            refine_board_session(flanges[:2], corners[:2], boards[:2], spec, calibration)
        with mock.patch.object(handeye, "refine_hand_eye_robust", side_effect=RuntimeError("x")):
            with self.assertRaises(RefinementError) as caught:
                refine_board_session(flanges, corners, boards, spec, calibration)
        self.assertEqual(caught.exception.stage, handeye.STAGE_REFINEMENT)
        self.assertIsInstance(caught.exception.__cause__, RuntimeError)
        with mock.patch.object(handeye, "leave_one_out_errors", side_effect=ValueError("y")):
            with self.assertRaises(RefinementError) as caught:
                refine_board_session(flanges, corners, boards, spec, calibration)
        self.assertEqual(caught.exception.stage, handeye.STAGE_LEAVE_ONE_OUT)


if __name__ == "__main__":
    unittest.main()

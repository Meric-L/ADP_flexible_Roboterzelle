"""Tests fuer `SaveUseCaseImage`: Ablage je Versuchsreihe und die OPC-UA-Methode."""

import asyncio
import json
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
    from vision_server.use_case_image import (
        SCHEMA,
        next_trial_number,
        save_use_case_image,
    )
except Exception:  # pragma: no cover - numpy/OpenCV/asyncua fehlen
    np = None

NOW = datetime(2026, 10, 29, 15, 4, 5)


def test_image():
    return np.random.default_rng(0).integers(0, 256, (48, 64, 3), dtype=np.uint8)


def read_json(png: Path) -> dict:
    return json.loads(png.with_suffix(".json").read_text(encoding="utf-8"))


@unittest.skipUnless(np is not None, "numpy/OpenCV/asyncua fehlen")
class SaveUseCaseImageTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name) / "use_case"

    def save(self, series=1, client='{"reihe": 1}', pi=None):
        return save_use_case_image(test_image(), self.base, series, client, pi or {}, NOW)

    def test_png_is_lossless_in_the_series_folder(self):
        import cv2

        path, trial = self.save(series=3)
        self.assertEqual(trial, 1)
        self.assertEqual(path, self.base / "reihe_003" / "versuch_001.png")
        self.assertTrue(np.array_equal(cv2.imread(str(path)), test_image()))

    def test_trials_count_up_per_series(self):
        names = [self.save(series=1)[0].name for _ in range(3)]
        self.assertEqual(names, ["versuch_001.png", "versuch_002.png", "versuch_003.png"])
        # Eine neue Reihe beginnt wieder bei 1.
        self.assertEqual(self.save(series=2), (self.base / "reihe_002" / "versuch_001.png", 1))

    def test_continues_after_the_highest_trial_and_never_fills_gaps(self):
        folder = self.base / "reihe_001"
        folder.mkdir(parents=True)
        (folder / "versuch_005.json").write_text("{}")
        (folder / "notiz.txt").write_text("x")
        self.assertEqual(next_trial_number(folder), 6)
        path, trial = self.save()
        self.assertEqual((path.name, trial), ("versuch_006.png", 6))
        self.assertEqual((folder / "versuch_005.json").read_text(), "{}")

    def test_next_trial_number_of_a_missing_folder_is_one(self):
        self.assertEqual(next_trial_number(self.base / "gibt_es_nicht"), 1)

    def test_never_overwrites_a_file_that_appears_meanwhile(self):
        folder = self.base / "reihe_001"
        folder.mkdir(parents=True)
        (folder / "versuch_001.png").write_bytes(b"alt")
        # Zaehlung meint, 001 sei frei: die Ablage muss trotzdem ausweichen.
        with mock.patch("vision_server.use_case_image.next_trial_number", return_value=1):
            path, trial = self.save()
        self.assertEqual((path.name, trial), ("versuch_002.png", 2))
        self.assertEqual((folder / "versuch_001.png").read_bytes(), b"alt")

    def test_json_beside_the_image_holds_everything(self):
        client = {"schema": "wsc.usecase.trial/1", "series": 1, "plan": {"placeInBase": [0.5, 0.1, 0.0]}}
        path, _ = self.save(client=json.dumps(client), pi={"hostname": "pi2", "image_shape": [48, 64, 3]})
        document = read_json(path)
        self.assertEqual(document["schema"], SCHEMA)
        self.assertEqual(document["reihe"], 1)
        self.assertEqual(document["versuch"], 1)
        self.assertEqual(document["bild"], "versuch_001.png")
        self.assertEqual(document["gespeichert"], "2026-10-29T15:04:05")
        self.assertEqual(document["pi"], {"hostname": "pi2", "image_shape": [48, 64, 3]})
        self.assertEqual(document["client"], client)
        self.assertNotIn("client_raw", document)

    def test_invalid_client_json_is_kept_raw(self):
        for raw in ["{kaputt", "", "[1, 2]"]:
            with self.subTest(raw=raw):
                document = read_json(self.save(client=raw)[0])
                self.assertEqual(document["client_raw"], raw)
                self.assertNotIn("client", document)

    def test_values_json_cannot_hold_are_written_as_text(self):
        path, _ = self.save(pi={"pfad": Path("/x/y.json"), "wert": np.float32(1.5)})
        self.assertEqual(read_json(path)["pi"], {"pfad": "/x/y.json", "wert": "1.5"})

    def test_series_below_one_is_refused(self):
        with self.assertRaises(ValueError):
            self.save(series=0)
        self.assertFalse(self.base.exists())

    def test_encoding_failure_raises_oserror_and_leaves_nothing(self):
        with mock.patch("cv2.imencode", return_value=(False, None)):
            with self.assertRaises(OSError):
                self.save()
        self.assertEqual(list((self.base / "reihe_001").iterdir()), [])


@unittest.skipUnless(np is not None, "numpy/OpenCV/asyncua fehlen")
class SaveUseCaseImageOpcUaTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from vision_server.runner import _install_save_use_case_image

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name) / "use_case"
        calibration = Path(tmp.name) / "intrinsik.json"
        calibration.write_text('{"fx": 1000.0}')
        server = Server()
        await server.init()
        idx = await server.register_namespace("http://launch-rm.de/vision")
        self.vision_system = await server.nodes.objects.add_object(
            ua.NodeId("VisionMachine", idx), ua.QualifiedName("VisionMachine", idx)
        )
        space = types.SimpleNamespace(vision_system=self.vision_system, own_idx=idx)
        config = types.SimpleNamespace(
            vision_system_name="VisionMachine",
            vision_system_id="vision-hand",
            endpoint="opc.tcp://0.0.0.0:4840/raspi/server/",
            camera_stream=None,
            apriltag=types.SimpleNamespace(
                frame_id="cam_hand", calibration_path=calibration, hand_eye_path=None
            ),
            use_case_dir=self.base,
        )
        self.camera = types.SimpleNamespace(latest_frame=None)
        self.camera_source = types.SimpleNamespace(camera=self.camera)
        self.busy = {"value": False}
        self.methods = {}
        await _install_save_use_case_image(
            space, config, self.camera_source, self.methods,
            lambda: self.busy["value"],
        )
        self.method = server.get_node(ua.NodeId("VisionMachine.SaveUseCaseImage", idx))

    def frame(self, timestamp):
        return types.SimpleNamespace(image=test_image(), timestamp=timestamp)

    def fresh_frame(self):
        """Ein Bild, das eindeutig nach dem Aufruf aufgenommen wurde."""
        return self.frame(asyncio.get_running_loop().time() + 60)

    async def call(self, series=1, metadata='{"series": 1}'):
        """Ruft die Methode auf; liefert (FileName, Error) -- `ResultJson` ueberspringt dieser Helfer."""
        name, _result_json, error = await self.vision_system.call_method(
            self.method,
            ua.Variant(series, ua.VariantType.Int32),
            ua.Variant(metadata, ua.VariantType.String),
        )
        return name, error

    async def test_saves_image_and_json(self):
        self.assertEqual(list(self.methods), ["SaveUseCaseImage"])
        self.camera.latest_frame = self.fresh_frame()
        name, error = await self.call(series=4)
        self.assertEqual(error, int(VisionErrorCode.OK))
        # Ausserhalb des Repos (Tempverzeichnis): absoluter Pfad.
        self.assertEqual(Path(name), self.base / "reihe_004" / "versuch_001.png")
        document = read_json(Path(name))
        self.assertEqual(document["client"], {"series": 1})
        self.assertEqual(document["pi"]["frame_id"], "cam_hand")
        self.assertEqual(document["pi"]["image_shape"], [48, 64, 3])
        self.assertEqual(document["pi"]["vision_system_id"], "vision-hand")
        self.assertEqual(document["pi"]["calibration"]["content"], {"fx": 1000.0})
        self.assertIn("hostname", document["pi"])
        second, _ = await self.call(series=4)
        self.assertEqual(Path(second).name, "versuch_002.png")

    async def test_measures_the_placed_cube_and_answers_with_the_result(self):
        from use_case_eval import synthetic

        calibration = synthetic.make_calibration()
        pose = synthetic.look_down_pose(0.0, 0.0, 220.0, 15.0, 1.0)
        image = synthetic.render_trial(calibration, pose, 1.0, -0.5, 4.0, supersample=4)
        self.camera_source.calibration = calibration
        self.camera_source.intrinsics_source = "realsense_factory"
        self.camera.latest_frame = types.SimpleNamespace(
            image=image, timestamp=asyncio.get_running_loop().time() + 60
        )
        name, result_json, error = await self.vision_system.call_method(
            self.method,
            ua.Variant(2, ua.VariantType.Int32),
            ua.Variant('{"series": 2}', ua.VariantType.String),
        )
        self.assertEqual(error, int(VisionErrorCode.OK))
        result = json.loads(result_json)
        self.assertTrue(result["ok"], result.get("grund"))
        self.assertAlmostEqual(result["dx_mm"], 1.0, delta=0.1)
        self.assertAlmostEqual(result["dy_mm"], -0.5, delta=0.1)
        self.assertAlmostEqual(result["dtheta_deg"], 4.0, delta=0.2)
        document = read_json(Path(name))
        self.assertEqual(document["messung"], result)
        self.assertEqual(document["pi"]["intrinsics"]["source"], "realsense_factory")
        self.assertTrue((Path(name).parent / "versuch_001_messung.png").is_file())

    async def test_without_calibration_the_photo_is_kept_and_the_measurement_says_why(self):
        self.camera.latest_frame = self.fresh_frame()
        name, result_json, error = await self.vision_system.call_method(
            self.method,
            ua.Variant(1, ua.VariantType.Int32),
            ua.Variant("{}", ua.VariantType.String),
        )
        self.assertEqual(error, int(VisionErrorCode.OK))
        self.assertTrue(Path(name).is_file())
        self.assertFalse(json.loads(result_json)["ok"])
        self.assertFalse(read_json(Path(name))["messung"]["ok"])

    async def test_waits_for_a_frame_taken_after_the_call(self):
        # Das Bild von vor dem Aufruf (Roboter noch in Bewegung) zaehlt nicht.
        self.camera.latest_frame = self.frame(0.0)
        call = asyncio.create_task(self.call())
        await asyncio.sleep(0.15)
        self.assertFalse(call.done())
        self.camera.latest_frame = self.fresh_frame()
        name, error = await asyncio.wait_for(call, 5)
        self.assertEqual(error, int(VisionErrorCode.OK))
        self.assertTrue(Path(name).is_file())

    async def test_without_fresh_frame_is_invalid_state(self):
        self.camera.latest_frame = self.frame(0.0)
        with mock.patch(
            "vision_server.runner.save_use_case_image.USE_CASE_FRESH_FRAME_TIMEOUT_S", 0.2
        ):
            name, error = await self.call()
        self.assertEqual((name, error), ("", int(VisionErrorCode.INVALID_STATE)))
        self.assertFalse(self.base.exists())

    async def test_series_below_one_is_invalid_argument(self):
        self.camera.latest_frame = self.fresh_frame()
        name, error = await self.call(series=0)
        self.assertEqual((name, error), ("", int(VisionErrorCode.INVALID_ARGUMENT)))
        self.assertFalse(self.base.exists())

    async def test_busy_job_is_rejected(self):
        self.camera.latest_frame = self.fresh_frame()
        self.busy["value"] = True
        name, error = await self.call()
        self.assertEqual((name, error), ("", int(VisionErrorCode.BUSY)))
        self.assertFalse(self.base.exists())

    async def test_write_failure_is_internal(self):
        self.camera.latest_frame = self.fresh_frame()
        with mock.patch("cv2.imencode", return_value=(False, None)):
            name, error = await self.call()
        self.assertEqual((name, error), ("", int(VisionErrorCode.INTERNAL)))


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(np is not None, "numpy/OpenCV/asyncua fehlen")
class GeneralSaveTest(unittest.TestCase):
    """Die Ablage mit waehlbaren Ordner- und Dateinamen (Grundlage von `SaveImage`)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name) / "hoehentest"

    def test_next_series_number_counts_only_its_own_folders(self):
        from vision_server.use_case_image import next_series_number

        self.assertEqual(next_series_number(self.base, "lauf"), 1)
        for name in ("lauf_001", "lauf_004", "reihe_009"):
            (self.base / name).mkdir(parents=True)
        (self.base / "lauf_007.txt").write_text("keine Reihe")
        self.assertEqual(next_series_number(self.base, "lauf"), 5)
        self.assertEqual(next_series_number(self.base, "reihe"), 10)

    def test_saves_under_the_given_folder_and_names(self):
        path, number = save_use_case_image(
            test_image(), self.base, 3, '{"a": 1}', {}, NOW, {"ok": True},
            folder="lauf", stem="bild", series_key="lauf", number_key="bild_nr",
            schema="adp.hoehentest.image/1",
        )
        self.assertEqual((path, number), (self.base / "lauf_003" / "bild_001.png", 1))
        document = read_json(path)
        self.assertEqual(document["schema"], "adp.hoehentest.image/1")
        self.assertEqual((document["lauf"], document["bild_nr"], document["bild"]), (3, 1, "bild_001.png"))
        self.assertNotIn("reihe", document)
        self.assertEqual(document["messung"], {"ok": True})
        second, number = save_use_case_image(
            test_image(), self.base, 3, "{}", {}, NOW, folder="lauf", stem="bild",
            series_key="lauf", number_key="bild_nr", schema="adp.hoehentest.image/1",
        )
        self.assertEqual((second.name, number), ("bild_002.png", 2))


class SaveImageOpcUaTest(unittest.IsolatedAsyncioTestCase):
    """`SaveImage(Kind, Series, MetadataJson)` und die Weiterleitung `SaveUseCaseImage`."""

    async def asyncSetUp(self):
        if np is None:
            self.skipTest("numpy/OpenCV/asyncua fehlen")
        from vision_server.config import ImageKind
        from vision_server.runner import _install_save_image, _install_save_use_case_image

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        calibration = self.root / "intrinsik.json"
        calibration.write_text('{"fx": 1000.0}')
        server = Server()
        await server.init()
        idx = await server.register_namespace("http://launch-rm.de/vision")
        self.vision_system = await server.nodes.objects.add_object(
            ua.NodeId("VisionMachine", idx), ua.QualifiedName("VisionMachine", idx)
        )
        space = types.SimpleNamespace(vision_system=self.vision_system, own_idx=idx)
        kinds = {
            "usecase": ImageKind(self.root / "use-case", "reihe", "versuch", "reihe", "versuch", "adp.usecase.trial/1", "trial"),
            "hoehentest": ImageKind(self.root / "hoehentest", "lauf", "bild", "lauf", "bild_nr", "adp.hoehentest.image/1", "sheet"),
        }
        config = types.SimpleNamespace(
            vision_system_name="VisionMachine",
            vision_system_id="vision-hand",
            endpoint="opc.tcp://0.0.0.0:4840/raspi/server/",
            camera_stream=None,
            apriltag=types.SimpleNamespace(frame_id="cam_hand", calibration_path=calibration, hand_eye_path=None),
            use_case_dir=self.root / "use-case",
            image_kinds=kinds,
        )
        self.camera = types.SimpleNamespace(latest_frame=None)
        self.camera_source = types.SimpleNamespace(camera=self.camera)
        self.busy = {"value": False}
        self.methods = {}
        save = await _install_save_image(space, config, self.camera_source, self.methods, lambda: self.busy["value"])
        await _install_save_use_case_image(
            space, config, self.camera_source, self.methods, lambda: self.busy["value"], save=save
        )
        self.save_image = server.get_node(ua.NodeId("VisionMachine.SaveImage", idx))
        self.save_use_case = server.get_node(ua.NodeId("VisionMachine.SaveUseCaseImage", idx))

    def fresh_frame(self, image=None):
        return types.SimpleNamespace(
            image=test_image() if image is None else image, timestamp=asyncio.get_running_loop().time() + 60
        )

    async def call(self, kind, series, metadata="{}"):
        return await self.vision_system.call_method(
            self.save_image,
            ua.Variant(kind, ua.VariantType.String),
            ua.Variant(series, ua.VariantType.Int32),
            ua.Variant(metadata, ua.VariantType.String),
        )

    async def test_both_methods_are_there(self):
        self.assertEqual(sorted(self.methods), ["SaveImage", "SaveUseCaseImage"])

    async def test_series_zero_starts_a_new_run_and_later_images_go_into_it(self):
        from use_case_eval import synthetic

        calibration = synthetic.make_calibration()
        pose = synthetic.look_down_pose(0.0, 0.0, 250.0, 0.0, 0.0)
        image = synthetic.render_trial(calibration, pose, 0, 0, 0, with_cube=False, supersample=2)
        self.camera_source.calibration = calibration
        self.camera.latest_frame = self.fresh_frame(image)
        name, result_json, error = await self.call("hoehentest", 0, '{"plannedHeightM": 0.25}')
        self.assertEqual(error, int(VisionErrorCode.OK))
        self.assertEqual(Path(name), self.root / "hoehentest" / "lauf_001" / "bild_001.png")
        result = json.loads(result_json)
        self.assertTrue(result["ok"], result.get("grund"))
        self.assertAlmostEqual(result["camera_height_mm"], 250.0, delta=1.0)
        document = read_json(Path(name))
        self.assertEqual(document["schema"], "adp.hoehentest.image/1")
        self.assertEqual((document["lauf"], document["bild_nr"]), (1, 1))
        self.assertEqual(document["client"], {"plannedHeightM": 0.25})
        self.assertEqual(document["messung"], result)
        # No control image for a sheet measurement.
        self.assertFalse((Path(name).parent / "bild_001_messung.png").exists())
        self.camera.latest_frame = self.fresh_frame(image)
        second, _, error = await self.call("hoehentest", 1)
        self.assertEqual((error, Path(second).name), (int(VisionErrorCode.OK), "bild_002.png"))
        self.camera.latest_frame = self.fresh_frame(image)
        third, _, _ = await self.call("hoehentest", 0)
        self.assertEqual(Path(third).parent.name, "lauf_002")

    async def test_the_use_case_kind_and_the_old_method_save_the_same_way(self):
        self.camera.latest_frame = self.fresh_frame()
        name, _, error = await self.call("usecase", 2, '{"series": 2}')
        self.assertEqual(error, int(VisionErrorCode.OK))
        self.assertEqual(Path(name), self.root / "use-case" / "reihe_002" / "versuch_001.png")
        self.assertEqual(read_json(Path(name))["schema"], "adp.usecase.trial/1")
        self.camera.latest_frame = self.fresh_frame()
        old, _, error = await self.vision_system.call_method(
            self.save_use_case, ua.Variant(2, ua.VariantType.Int32), ua.Variant("{}", ua.VariantType.String)
        )
        self.assertEqual((error, Path(old).name), (int(VisionErrorCode.OK), "versuch_002.png"))
        # The old method keeps its rule: no series 0.
        _, _, error = await self.vision_system.call_method(
            self.save_use_case, ua.Variant(0, ua.VariantType.Int32), ua.Variant("{}", ua.VariantType.String)
        )
        self.assertEqual(error, int(VisionErrorCode.INVALID_ARGUMENT))

    async def test_unknown_kind_negative_series_and_busy_are_refused(self):
        self.camera.latest_frame = self.fresh_frame()
        self.assertEqual((await self.call("gibtsnicht", 1))[2], int(VisionErrorCode.INVALID_ARGUMENT))
        self.assertEqual((await self.call("hoehentest", -1))[2], int(VisionErrorCode.INVALID_ARGUMENT))
        self.busy["value"] = True
        self.assertEqual((await self.call("hoehentest", 0))[2], int(VisionErrorCode.BUSY))
        self.assertFalse((self.root / "hoehentest").exists())


class DefaultImageKindsTest(unittest.TestCase):
    def test_use_case_height_test_and_rotation_test_are_there(self):
        from vision_server.config import VisionServerConfig

        kinds = VisionServerConfig().image_kinds
        self.assertEqual(sorted(kinds), ["drehtest", "hoehentest", "lv2_messrauschen", "usecase"])
        rotation = kinds["drehtest"]
        self.assertEqual((rotation.base_dir.name, rotation.folder, rotation.stem), ("drehtest", "lauf", "bild"))
        self.assertEqual((rotation.schema, rotation.measure), ("adp.drehtest.image/1", "sheet"))
        noise = kinds["lv2_messrauschen"]
        self.assertEqual((noise.base_dir.name, noise.folder, noise.stem), ("LV2_Messrauschen", "lauf", "messung"))
        self.assertEqual((noise.schema, noise.measure), ("adp.lv2_messrauschen.image/1", "none"))

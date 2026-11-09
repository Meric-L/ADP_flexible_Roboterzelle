"""Helligkeitstest: `RecordLightLevel`, `LightTestStatus` und `SetExposureLock`.

Eine Stufe dauert Minuten (Standard 300 Bilder, je Bild zwei Detektoren), zu
lang fuer einen OPC-UA-Aufruf. `RecordLightLevel` startet die Aufnahme deshalb
nur und antwortet sofort; `LightTestStatus("")` liefert Fortschritt und, wenn
fertig, die Kennwerte; `LightTestStatus("abort")` bricht ab (das Bisherige
wird gespeichert). Die Auswertung steht in `vision_server/lighttest.py`.

`SetExposureLock("lock")` friert Belichtung, Gain und Weissabgleich auf den
Werten ein, die die Automatik gerade hat; `"unlock"` schaltet die Automatik
wieder an, `""` liest nur. Die Antwort traegt die mittlere Bildhelligkeit vor
und nach dem Umschalten: bleibt sie gleich, hat das Einfrieren gehalten.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from asyncua import Node, ua, uamethod

from ..address_space import VisionAddressSpace
from ..config import VisionServerConfig
from ..errors import VisionErrorCode, VisionJobError
from ..lighttest import (
    DATA_FOLDER,
    EXPOSURE_SCHEMA,
    RESULT_SCHEMA,
    STATUS_SCHEMA,
    dumps,
    frame_record,
    image_name,
    level_paths,
    michelson_contrast,
    parse_request,
    sample_indices,
    summarize,
    write_level,
)
from ..profiles import _REPO_ROOT
from ..state_machine import VisionStateMachines
from .uamethods import add_vision_method, json_method, repo_relative, text_reply

_log = logging.getLogger(__package__)

#: Nach dem Umschalten der Belichtung so lange auf neue Bilder warten, bevor die
#: Helligkeit "danach" gemessen wird (die Kamera braucht ein paar Frames).
EXPOSURE_SETTLE_S = 1.0


def measure_frame(source: Any, pupil: Any, image: Any, tag_id: int, sizes: Any):
    """Ein Bild: `(aruco, apriltag, Kontrast, mittlere Helligkeit)`. Worker-Thread."""
    from tagloc import frames as frame_tools
    from tagloc.geometry import to_position_quaternion
    from tagloc.pose import estimate_tag_poses

    gray = frame_tools.to_gray(image)
    found = [o for o in source._detector.detect(gray) if o.tag_id == tag_id]
    aruco: dict = {"detected": bool(found)}
    corners = None
    if found:
        corners = found[0].corners
        aruco["cornersPx"] = [[float(u), float(v)] for u, v in corners]
        poses = estimate_tag_poses(
            found,
            source._calibration_for(image),
            tag_map=sizes,
            default_size_m=source._config.tag_size_m,
            max_reprojection_error_px=source._config.max_reproj_error_px,
        )
        if poses:
            pose = poses[0]
            position, orientation = to_position_quaternion(pose.pose_cam_tag)
            aruco["poseInCamera"] = {
                "position": [float(v) for v in position],
                "orientation": [float(v) for v in orientation],
            }
            aruco["reprojectionErrorPx"] = float(pose.reprojection_error_px)
            aruco["ambiguous"] = bool(pose.is_ambiguous)
    apriltag = None
    if pupil is not None:
        seen = [o for o in pupil.detect(gray) if o.tag_id == tag_id]
        apriltag = {"detected": bool(seen)}
        if seen:
            apriltag["decisionMargin"] = seen[0].decision_margin
            apriltag["hamming"] = seen[0].hamming
            apriltag["cornersPx"] = [[float(u), float(v)] for u, v in seen[0].corners]
            if corners is None:
                corners = seen[0].corners
    contrast = michelson_contrast(gray, corners) if corners is not None else None
    return aruco, apriltag, contrast, float(gray.mean())


def save_png(path: Path, image: Any) -> None:
    import cv2

    if not cv2.imwrite(str(path), image):
        raise OSError(f"PNG nicht geschrieben: {path}")


class LightTest:
    """Eine laufende oder die letzte Stufe; einfaches Objekt, der Server ist single-threaded."""

    def __init__(
        self,
        source: Any,
        *,
        base_dir: Path,
        exposure: Callable[[], Any] | None = None,
        measure: Callable[..., Any] = measure_frame,
        save_image: Callable[[Path, Any], None] = save_png,
    ) -> None:
        self._source = source
        self._base_dir = base_dir
        self._exposure = exposure
        self._measure = measure
        self._save_image = save_image
        self._pupil: Any = None
        self._pupil_error: str | None = None
        self._pupil_tried = False
        self.state = "idle"
        self.progress = {"frames": 0, "of": 0}
        self.request = None
        self.result: dict | None = None
        self.error: str | None = None
        self.abort_requested = False
        self.task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self.state == "running"

    def status(self) -> dict:
        return {
            "schema": STATUS_SCHEMA,
            "state": self.state,
            "progress": dict(self.progress),
            "level": self.request.level if self.request else None,
            "tagId": self.request.tag_id if self.request else None,
            "result": self.result,
            "error": self.error,
        }

    def start(self, request) -> None:
        self.state = "running"
        self.request = request
        self.result = None
        self.error = None
        self.abort_requested = False
        self.progress = {"frames": 0, "of": request.frames}
        self.task = asyncio.create_task(self._run(request))

    def _build_pupil(self) -> None:
        """`pupil_apriltags` einmal bauen; fehlt die Bibliothek, laeuft der Test ohne."""
        if self._pupil_tried:
            return
        self._pupil_tried = True
        try:
            from tagloc.detector import PupilAprilTagDetector

            self._pupil = PupilAprilTagDetector(self._source._config.tag_family, nthreads=2)
        except Exception as error:  # ImportError, fehlende Familie ...
            self._pupil = None
            self._pupil_error = f"pupil_apriltags nicht nutzbar: {error}"
            _log.warning("Helligkeitstest: %s", self._pupil_error)

    async def _read_exposure(self) -> Any:
        if self._exposure is None:
            return None
        try:
            return await self._exposure()
        except Exception as error:
            return {"error": str(error)}

    async def _run(self, request) -> None:
        from ..detection.apriltag.capture import captured_at
        from ..measuretag import SizeLookup

        source = self._source
        records: list[dict] = []
        images: list[str] = []
        started = datetime.now().astimezone().isoformat(timespec="seconds")
        frame_error = None
        # Solange gemessen wird, haelt das Stream-Overlay an.
        source.measure_calls += 1
        try:
            await source.run_blocking(self._build_pupil)
            sizes = SizeLookup(
                {request.tag_id: request.size_m} if request.size_m else {},
                source._tag_map,
                source._config.tag_size_m,
            )
            exposure_start = await self._read_exposure()
            series, folder, level = await source.run_blocking(level_paths, self._base_dir, request.series)
            sample_at = set(sample_indices(request.frames, request.sample_images))
            image_size = None
            seen = source._latest_timestamp()
            for index in range(request.frames):
                if self.abort_requested:
                    break
                try:
                    frame = await source._next_frame(seen)
                except VisionJobError as error:
                    frame_error = error.message
                    break
                seen = frame.timestamp
                aruco, apriltag, contrast, mean = await source.run_blocking(
                    self._measure, source, self._pupil, frame.image, request.tag_id, sizes
                )
                records.append(
                    frame_record(
                        index, captured_at(frame).isoformat(), mean,
                        aruco=aruco, apriltag=apriltag, contrast=contrast,
                    )
                )
                image_size = list(frame.image.shape[1::-1])
                if index in sample_at:
                    name = image_name(level, len(images) + 1)
                    try:
                        await source.run_blocking(self._save_image, folder / name, frame.image)
                        images.append(name)
                    except Exception:
                        _log.exception("Helligkeitstest: Bild %s nicht gespeichert", name)
                self.progress = {"frames": index + 1, "of": request.frames}
            exposure_end = await self._read_exposure()
            summary = summarize(records, request.frames)
            config = source._config
            document = {
                "schema": RESULT_SCHEMA,
                "reihe": series,
                "stufe": level,
                "tagId": request.tag_id,
                "level": request.level,
                "client": request.client,
                "startedAt": started,
                "finishedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
                "aborted": self.abort_requested,
                "error": frame_error,
                "camera": {
                    "frameId": config.frame_id,
                    "imageSize": image_size,
                    "calibrationId": getattr(source._calibration, "calibration_id", ""),
                    "intrinsicsSource": getattr(source, "intrinsics_source", None),
                    "exposureStart": exposure_start,
                    "exposureEnd": exposure_end,
                },
                "detectors": {
                    "aruco": {
                        "family": config.tag_family,
                        "detectorParameters": getattr(config, "detector_parameters", None),
                        "subpixelCornerRefinement": getattr(config, "subpixel_corner_refinement", None),
                    },
                    "apriltag": None if self._pupil is None else {"library": "pupil_apriltags"},
                    "apriltagError": self._pupil_error,
                },
                "summary": summary,
                "images": images,
                "frames": records,
            }
            path = await source.run_blocking(write_level, folder, level, document)
            result = {key: value for key, value in document.items() if key != "frames"}
            result["file"] = repo_relative(path)
            result["folder"] = repo_relative(folder)
            self.result = result
            self.state = "aborted" if self.abort_requested else ("failed" if frame_error and not records else "done")
            self.error = frame_error
            _log.info("Helligkeitstest gespeichert: %s", result["file"])
        except Exception as error:
            _log.exception("Helligkeitstest fehlgeschlagen")
            self.state = "failed"
            self.error = str(error)
        finally:
            source.measure_calls -= 1


async def _image_mean(camera: Any, settle_s: float = 0.0) -> float | None:
    """Mittlere Helligkeit des neuesten Bilds; mit `settle_s` erst nach einer Wartezeit."""
    if settle_s:
        await asyncio.sleep(settle_s)
    frame = camera.latest_frame
    if frame is None:
        return None
    from tagloc import frames as frame_tools

    return float(frame_tools.to_gray(frame.image).mean())


async def install_light_test(
    space: VisionAddressSpace,
    config: VisionServerConfig,
    source: Any,
    methods: dict[str, Node],
    states: VisionStateMachines,
    busy: Callable[[], bool],
    *,
    base_dir: Path | None = None,
) -> LightTest:
    """Legt `RecordLightLevel`, `LightTestStatus` und `SetExposureLock` an (beide Pis)."""
    camera = source.camera
    test = LightTest(
        source,
        base_dir=base_dir or _REPO_ROOT.joinpath(*DATA_FOLDER),
        exposure=lambda: camera.exposure(None),
    )

    def refusal(code: VisionErrorCode, message: str):
        return text_reply(dumps({**test.status(), "error": f"{code.name}: {message}"}), code)

    async def record(request, _text: str):
        test.start(request)
        return text_reply(dumps(test.status()), VisionErrorCode.OK)

    handler = json_method(
        "RecordLightLevel",
        states,
        parse=parse_request,
        refuse=refusal,
        run=record,
        busy=lambda: busy() or test.running,
        busy_message="Job, Kalibrierung oder eine Helligkeitsstufe laeuft",
    )
    await add_vision_method(
        space, config, methods, "RecordLightLevel", handler,
        [ua.VariantType.String], [ua.VariantType.String, ua.VariantType.Int32],
    )

    @uamethod
    async def status_method(parent, command: str):
        command = (command or "").strip().lower()
        if command == "abort":
            if test.running:
                test.abort_requested = True
            return text_reply(dumps(test.status()), VisionErrorCode.OK)
        if command:
            return text_reply(
                dumps({**test.status(), "error": f"Unbekannter Befehl '{command}' (erlaubt: '', 'abort')"}),
                VisionErrorCode.INVALID_ARGUMENT,
            )
        return text_reply(dumps(test.status()), VisionErrorCode.OK)

    await add_vision_method(
        space, config, methods, "LightTestStatus", status_method,
        [ua.VariantType.String], [ua.VariantType.String, ua.VariantType.Int32],
    )

    @uamethod
    async def exposure_method(parent, command: str):
        command = (command or "").strip().lower()
        lock = {"lock": True, "unlock": False, "": None}.get(command, "invalid")
        if lock == "invalid":
            return text_reply(
                dumps({"schema": EXPOSURE_SCHEMA, "error": f"Unbekannter Befehl '{command}' (erlaubt: '', 'lock', 'unlock')"}),
                VisionErrorCode.INVALID_ARGUMENT,
            )
        if lock is not None and (busy() or test.running):
            return text_reply(
                dumps({"schema": EXPOSURE_SCHEMA, "error": "BUSY: Job, Kalibrierung oder eine Helligkeitsstufe laeuft"}),
                VisionErrorCode.BUSY,
            )
        before = await _image_mean(camera)
        try:
            result = await camera.exposure(lock)
        except Exception as error:
            _log.exception("SetExposureLock(%r) fehlgeschlagen", command)
            return text_reply(
                dumps({"schema": EXPOSURE_SCHEMA, "error": str(error)}), VisionErrorCode.INTERNAL
            )
        after = await _image_mean(camera, EXPOSURE_SETTLE_S if lock is not None else 0.0)
        return text_reply(
            dumps({"schema": EXPOSURE_SCHEMA, **result, "imageMeanBefore": before, "imageMeanAfter": after}),
            VisionErrorCode.OK,
        )

    await add_vision_method(
        space, config, methods, "SetExposureLock", exposure_method,
        [ua.VariantType.String], [ua.VariantType.String, ua.VariantType.Int32],
    )
    return test

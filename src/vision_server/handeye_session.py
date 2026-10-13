"""Hand-Auge-Kalibrierung per OPC UA: `T_flansch_cam` auf dem Hand-Pi.

Dasselbe Muster wie die Kamera-Kalibrierung (Start / Aufnahme / Fertig /
Abbruch), nur mit einem Partner draussen: der Roboter faehrt, das Frontend
meldet je Aufnahme die **gemeldete** Flanschpose (FK aus den echten
Gelenkwinkeln), und der Pi misst dazu den Tag im Kamera-KS.

Am Ende loest `tagloc.handeye.solve_hand_eye` (Park-Martin) ueber alle Paare.
Die Lage des Tags muss dafuer nicht bekannt sein, nur fest. Ist sie bekannt
(`tagInBase` aus dem Modulkatalog), dient sie als **Gegenprobe**, nicht als
Eingang -- die Kalibrierung soll nicht so genau sein wie der Katalog, sondern
so genau wie die Messung.

Ergebnis nach `data/handeye/<frame>.json`, die alte Datei vorher daneben
gesichert; danach sofort aktiv, ohne Neustart.

Hier steckt kein asyncua: Kamera, Datei und Knoten kommen als Funktionen
herein (`HandEyePorts`). Siehe
`doc/projektdoku/arbeitsplaene/handeye-ueber-opcua.md`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from tagloc.geometry import (
    Pose,
    average_poses,
    compose,
    invert,
    pose_to_dict,
    rotation_distance_rad,
    rvec_from_rotation,
    translation_distance_m,
)
from tagloc.handeye import MIN_SAMPLES, HandEye, load_hand_eye, save_hand_eye, solve_hand_eye

from .errors import VisionErrorCode
from .layer2.plan import _load, _pose

_log = logging.getLogger(__name__)

START_SCHEMA = "wsc.vision.handeye.start/1"
SAMPLE_SCHEMA = "wsc.vision.handeye.sample/1"
STATUS_SCHEMA = "wsc.vision.handeye.status/1"

IDLE = "idle"
CAPTURING = "capturing"
SOLVING = "solving"
FINISHED = "finished"
FAILED = "failed"

#: Ab so viel Drehung um eine Achse zaehlt sie als "deutlich gedreht".
#: Unterhalb davon bestimmt das Rauschen der Tag-Messung die Rotation mit.
AXIS_THRESHOLD_DEG = 5.0
#: Empfehlung an den Bediener -- `MIN_SAMPLES` ist nur das rechnerische Minimum.
RECOMMENDED_SAMPLES = 8


@dataclass(frozen=True)
class HandEyePorts:
    """Was die Session vom Server braucht."""

    #: Misst `T_cam_tag`: `(Pose, "")` oder `(None, Grund)`.
    measure_tag: Callable[[int], Awaitable[tuple[Any, str]]]
    #: Laeuft ein Job, eine Kamera-Kalibrierung oder ein Layer-2-Lauf?
    busy: Callable[[], bool]
    #: Ziel der Kalibrierdatei.
    path: Path
    #: Uebernimmt die neue Kalibrierung in die laufende Quelle.
    apply: Callable[[HandEye], None]
    #: Schreibt `HandEyeStatus`.
    publish: Callable[[str], Awaitable[None]]
    frame_id: str = "cam_flange"


@dataclass
class _Sample:
    flange_in_base: Pose
    cam_to_tag: Pose | None
    detail: str

    @property
    def accepted(self) -> bool:
        return self.cam_to_tag is not None


@dataclass
class _State:
    state: str = IDLE
    message: str = ""
    tag_id: int = -1
    tag_in_base: Pose | None = None
    samples: list[_Sample] = field(default_factory=list)
    result: dict | None = None


def rotation_diversity(poses_base_flange: list[Pose]) -> tuple[float, int]:
    """`(groesste Drehung zwischen zwei Posen in Grad, Zahl der Drehachsen)`.

    Die Achsen: die Drehvektoren aller Bewegungspaare, ihre Singulaerwerte
    normiert auf die Zahl der Paare. Dreht der Roboter nur um eine Achse,
    liegen alle Vektoren auf einer Linie -- dann ist `T_flansch_cam` nicht
    bestimmt, egal wie viele Aufnahmen.
    """
    count = len(poses_base_flange)
    if count < 2:
        return 0.0, 0
    vectors = []
    spread = 0.0
    for first in range(count):
        for second in range(first + 1, count):
            motion = compose(invert(poses_base_flange[first]), poses_base_flange[second])
            vectors.append(rvec_from_rotation(motion[:3, :3]).reshape(3))
            spread = max(
                spread, rotation_distance_rad(poses_base_flange[first], poses_base_flange[second])
            )
    singular = np.linalg.svd(np.asarray(vectors), compute_uv=False) / math.sqrt(len(vectors))
    axes = int(np.sum(singular >= math.radians(AXIS_THRESHOLD_DEG)))
    return math.degrees(spread), axes


def save_with_backup(path: Path, hand_eye: HandEye) -> Path | None:
    """Schreibt die Kalibrierung; eine vorhandene Datei wird vorher kopiert.

    Liefert den Pfad der Sicherung, `None` ohne alte Datei.
    """
    backup = None
    if path.is_file():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = path.with_name(f"{path.name}.{stamp}.bak")
        shutil.copy2(path, backup)
    save_hand_eye(path, hand_eye)
    return backup


class HandEyeSession:
    def __init__(self, ports: HandEyePorts) -> None:
        self._ports = ports
        self._s = _State()
        #: Eine Aufnahme misst ueber mehrere Bilder; eine zweite gleichzeitig
        #: haette ihre Flanschpose zum falschen Bild.
        self._measuring = False

    @property
    def active(self) -> bool:
        """Sperrt Layer-2-Lauf, Kamera-Kalibrierung und SetTagMap."""
        return self._s.state in (CAPTURING, SOLVING)

    # --- Eingaenge -----------------------------------------------------------

    async def start(self, options_json: str) -> VisionErrorCode:
        if self.active:
            _log.warning("StartHandEyeCalibration abgelehnt: Sitzung laeuft schon")
            return VisionErrorCode.BUSY
        if self._ports.busy():
            _log.warning("StartHandEyeCalibration abgelehnt: Job, Kalibrierung oder Lauf aktiv")
            return VisionErrorCode.BUSY
        try:
            tag_id, tag_in_base = parse_start(options_json)
        except ValueError as error:
            _log.warning("StartHandEyeCalibration abgelehnt: %s", error)
            return VisionErrorCode.INVALID_ARGUMENT
        self._s = _State(state=CAPTURING, tag_id=tag_id, tag_in_base=tag_in_base)
        self._s.message = self._hint()
        _log.info("Hand-Auge-Kalibrierung gestartet, Tag %d", tag_id)
        await self._publish()
        return VisionErrorCode.OK

    async def capture(self, sample_json: str) -> VisionErrorCode:
        """Misst den Tag zur gemeldeten Flanschpose. Antwortet erst danach:
        `OK` = Aufnahme angenommen, `DETECTION_FAILED` = verworfen (Grund
        im Status)."""
        s = self._s
        if s.state != CAPTURING:
            return VisionErrorCode.INVALID_STATE
        if self._measuring or self._ports.busy():
            return VisionErrorCode.BUSY
        try:
            flange_in_base = parse_sample(sample_json)
        except ValueError as error:
            _log.warning("CaptureHandEyeSample abgelehnt: %s", error)
            return VisionErrorCode.INVALID_ARGUMENT
        self._measuring = True
        try:
            cam_to_tag, reason = await self._ports.measure_tag(s.tag_id)
        except Exception as error:  # Kamera weg -- Bericht, kein Absturz
            _log.exception("Hand-Auge-Aufnahme fehlgeschlagen")
            cam_to_tag, reason = None, f"Messung fehlgeschlagen: {error}"
        finally:
            self._measuring = False
        if self._s is not s or s.state != CAPTURING:
            return VisionErrorCode.CANCELLED  # waehrend der Messung abgebrochen
        detail = reason if cam_to_tag is None else f"Tag {s.tag_id} gemessen"
        s.samples.append(_Sample(flange_in_base, cam_to_tag, detail))
        s.message = self._hint() if cam_to_tag is not None else f"Aufnahme verworfen: {reason}"
        await self._publish()
        return VisionErrorCode.OK if cam_to_tag is not None else VisionErrorCode.DETECTION_FAILED

    async def finish(self) -> VisionErrorCode:
        """Rechnet, speichert, aktiviert. Zu wenige oder zu aehnliche Posen:
        `INVALID_STATE`, die Sitzung bleibt offen fuer weitere Aufnahmen."""
        s = self._s
        if s.state != CAPTURING or self._measuring:
            return VisionErrorCode.INVALID_STATE
        accepted = [sample for sample in s.samples if sample.accepted]
        flanges = [sample.flange_in_base for sample in accepted]
        tags = [sample.cam_to_tag for sample in accepted]
        s.state = SOLVING
        s.message = f"Berechne aus {len(accepted)} Aufnahmen"
        await self._publish()
        try:
            pose_flange_cam, spread_m, spread_deg = await asyncio.to_thread(
                solve_hand_eye, flanges, tags
            )
        except ValueError as error:
            s.state = CAPTURING
            s.message = f"Nicht berechnet: {error}"
            await self._publish()
            return VisionErrorCode.INVALID_STATE

        hand_eye = HandEye(
            pose_flange_cam=pose_flange_cam,
            frame_id=self._ports.frame_id,
            rms_position_m=spread_m,
            rms_rotation_deg=spread_deg,
            sample_count=len(accepted),
        )
        try:
            backup = await asyncio.to_thread(save_with_backup, self._ports.path, hand_eye)
            loaded = await asyncio.to_thread(load_hand_eye, self._ports.path)
            self._ports.apply(loaded)
        except Exception as error:
            _log.exception("Hand-Auge-Kalibrierung nicht gespeichert")
            s.state = FAILED
            s.message = f"Berechnet, aber nicht gespeichert: {error}"
            await self._publish()
            return VisionErrorCode.INTERNAL

        s.result = {
            "poseFlangeCam": pose_to_dict(pose_flange_cam),
            "rmsPositionM": spread_m,
            "rmsRotationDeg": spread_deg,
            "sampleCount": len(accepted),
            "knownTagCheckM": None,
            "knownTagCheckDeg": None,
            "path": str(self._ports.path),
            "backupPath": str(backup) if backup is not None else None,
        }
        if s.tag_in_base is not None:
            reconstructed = average_poses(
                [compose(flange, pose_flange_cam, tag) for flange, tag in zip(flanges, tags)]
            )
            s.result["knownTagCheckM"] = translation_distance_m(reconstructed, s.tag_in_base)
            s.result["knownTagCheckDeg"] = math.degrees(
                rotation_distance_rad(reconstructed, s.tag_in_base)
            )
        s.state = FINISHED
        s.message = (
            f"Gespeichert und aktiv: Streuung {spread_m * 1000:.1f} mm / {spread_deg:.2f} Grad "
            f"ueber {len(accepted)} Posen"
        )
        _log.info("Hand-Auge-Kalibrierung: %s", s.message)
        await self._publish()
        return VisionErrorCode.OK

    async def abort(self) -> VisionErrorCode:
        if not self.active:
            return VisionErrorCode.INVALID_STATE
        self._s.state = IDLE
        self._s.message = "abgebrochen, nichts gespeichert"
        _log.info("Hand-Auge-Kalibrierung abgebrochen")
        await self._publish()
        return VisionErrorCode.OK

    async def publish_idle(self) -> None:
        await self._publish()

    # --- Status --------------------------------------------------------------

    def _hint(self) -> str:
        flanges = [sample.flange_in_base for sample in self._s.samples if sample.accepted]
        count = len(flanges)
        _, axes = rotation_diversity(flanges)
        if count < MIN_SAMPLES:
            return (
                f"{count} Aufnahmen -- mindestens {MIN_SAMPLES}, besser "
                f"{RECOMMENDED_SAMPLES}, aus deutlich verschiedenen Winkeln"
            )
        if axes < 2:
            return (
                f"{count} Aufnahmen, aber gedreht um nur {axes} Achse(n) -- "
                "den Flansch auch um eine andere Achse neigen"
            )
        if count < RECOMMENDED_SAMPLES:
            return f"{count} Aufnahmen, Drehung um {axes} Achsen -- berechenbar, mehr wird genauer"
        return f"{count} Aufnahmen, Drehung um {axes} Achsen -- bereit zum Berechnen"

    async def _publish(self) -> None:
        try:
            await self._ports.publish(self.status_json())
        except Exception:
            _log.exception("HandEyeStatus konnte nicht geschrieben werden")

    def status_json(self) -> str:
        s = self._s
        flanges = [sample.flange_in_base for sample in s.samples if sample.accepted]
        spread_deg, axes = rotation_diversity(flanges)
        return json.dumps(
            {
                "schema": STATUS_SCHEMA,
                "state": s.state,
                "message": s.message,
                "tagId": s.tag_id,
                "tagInBase": pose_to_dict(s.tag_in_base) if s.tag_in_base is not None else None,
                "acceptedCount": len(flanges),
                "rotationSpreadDeg": spread_deg,
                "rotationAxes": axes,
                "samples": [
                    {
                        "index": index,
                        "accepted": sample.accepted,
                        "detail": sample.detail,
                        "flangeInBase": pose_to_dict(sample.flange_in_base),
                        "camToTag": (
                            pose_to_dict(sample.cam_to_tag) if sample.cam_to_tag is not None else None
                        ),
                    }
                    for index, sample in enumerate(s.samples)
                ],
                "result": s.result,
            },
            allow_nan=False,
            default=float,
        )


# --- JSON ---------------------------------------------------------------------


def parse_start(text: str) -> tuple[int, Pose | None]:
    """`wsc.vision.handeye.start/1` lesen; ValueError mit Klartext."""
    data = _load(text, START_SCHEMA)
    tag_id = data.get("tagId")
    if not isinstance(tag_id, int) or isinstance(tag_id, bool) or tag_id < 0:
        raise ValueError("tagId fehlt oder ist keine nicht-negative Ganzzahl.")
    raw = data.get("tagInBase")
    return tag_id, (_pose(raw, "tagInBase") if raw is not None else None)


def parse_sample(text: str) -> Pose:
    """`wsc.vision.handeye.sample/1` lesen; ValueError mit Klartext."""
    return _pose(_load(text, SAMPLE_SCHEMA).get("flangeInBase"), "flangeInBase")

"""Hand-Auge-Kalibrierung per OPC UA: `T_flansch_cam` auf dem Hand-Pi.

Dasselbe Muster wie die Kamera-Kalibrierung (Start / Aufnahme / Fertig /
Abbruch), nur mit einem Partner draussen: der Roboter faehrt, das Frontend
meldet je Aufnahme die **gemeldete** Flanschpose (FK aus den echten
Gelenkwinkeln), und der Pi misst dazu das Ziel im Kamera-KS: einen Tag oder
ein Kalibrier-Schachbrett (`handeye-schachbrett-automatik.md`).

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

from tagloc.boards import CHESSBOARD, BoardSpec, chessboard_is_unambiguous
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
from tagloc.handeye import (
    MIN_SAMPLES,
    HandEye,
    load_hand_eye,
    save_hand_eye,
    solve_hand_eye_robust,
)

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

    #: Misst `T_cam_tag`: `(Pose, "")` oder `(None, Grund)`, optional mit einem
    #: dritten Element `Info` (`reprojectionErrorPx`).
    measure_tag: Callable[[int], Awaitable[tuple]]
    #: Laeuft ein Job, eine Kamera-Kalibrierung oder ein Layer-2-Lauf?
    busy: Callable[[], bool]
    #: Ziel der Kalibrierdatei.
    path: Path
    #: Uebernimmt die neue Kalibrierung in die laufende Quelle.
    apply: Callable[[HandEye], None]
    #: Schreibt `HandEyeStatus`.
    publish: Callable[[str], Awaitable[None]]
    frame_id: str = "cam_flange"
    #: Misst `T_cam_board` eines Schachbretts; `None`: kann der Server nicht.
    measure_board: Callable[[BoardSpec], Awaitable[tuple]] | None = None


@dataclass
class _Sample:
    flange_in_base: Pose
    cam_to_tag: Pose | None
    detail: str
    #: Mittlerer Reprojektionsfehler der Bilder dieser Aufnahme, auch wenn sie
    #: verworfen wurde (z. B. weil er zu gross war). `None`, wenn nichts gefunden wurde.
    reprojection_error_px: float | None = None
    #: Nach `finish`: Abweichung der Brettlage dieser Aufnahme vom Mittel der
    #: verwendeten, und ob sie als Ausreisser verworfen wurde. Vorher `None`.
    residual_m: float | None = None
    residual_deg: float | None = None
    rejected: bool | None = None

    @property
    def accepted(self) -> bool:
        return self.cam_to_tag is not None


@dataclass
class _State:
    state: str = IDLE
    message: str = ""
    tag_id: int = -1
    tag_in_base: Pose | None = None
    #: Gesetzt, wenn statt eines Tags ein Schachbrett gemessen wird.
    board: BoardSpec | None = None
    samples: list[_Sample] = field(default_factory=list)
    result: dict | None = None


def _finite_or_none(value) -> float | None:
    """Endliche Zahl oder `None` -- `json.dumps(allow_nan=False)` vertraegt kein NaN."""
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


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
            tag_id, tag_in_base, board = parse_start(options_json)
        except ValueError as error:
            _log.warning("StartHandEyeCalibration abgelehnt: %s", error)
            return VisionErrorCode.INVALID_ARGUMENT
        self._s = _State(state=CAPTURING, tag_id=tag_id, tag_in_base=tag_in_base, board=board)
        self._s.message = self._hint()
        _log.info("Hand-Auge-Kalibrierung gestartet, Ziel %s", self._target_label())
        await self._publish()
        return VisionErrorCode.OK

    async def capture(self, sample_json: str) -> VisionErrorCode:
        """Misst das Ziel zur gemeldeten Flanschpose. Antwortet erst danach:
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
            cam_to_tag, reason, info = await self._measure(s)
        except Exception as error:  # Kamera weg -- Bericht, kein Absturz
            _log.exception("Hand-Auge-Aufnahme fehlgeschlagen")
            cam_to_tag, reason, info = None, f"Messung fehlgeschlagen: {error}", {}
        finally:
            self._measuring = False
        if self._s is not s or s.state != CAPTURING:
            return VisionErrorCode.CANCELLED  # waehrend der Messung abgebrochen
        detail = reason if cam_to_tag is None else f"{self._target_label()} gemessen"
        s.samples.append(
            _Sample(
                flange_in_base,
                cam_to_tag,
                detail,
                reprojection_error_px=_finite_or_none(info.get("reprojectionErrorPx")),
            )
        )
        s.message = self._hint() if cam_to_tag is not None else f"Aufnahme verworfen: {reason}"
        await self._publish()
        if cam_to_tag is not None:
            return VisionErrorCode.OK
        return VisionErrorCode.DETECTION_FAILED

    async def finish(self) -> VisionErrorCode:
        """Rechnet, speichert, aktiviert. Zu wenige oder zu aehnliche Posen:
        `INVALID_STATE`, die Sitzung bleibt offen fuer weitere Aufnahmen."""
        s = self._s
        if s.state != CAPTURING or self._measuring:
            return VisionErrorCode.INVALID_STATE
        # Indizes statt Vergleichen: `_Sample` haelt numpy-Posen, `==` und
        # `list.index` wuerden daran scheitern.
        accepted_indices = [index for index, sample in enumerate(s.samples) if sample.accepted]
        accepted = [s.samples[index] for index in accepted_indices]
        flanges = [sample.flange_in_base for sample in accepted]
        tags = [sample.cam_to_tag for sample in accepted]
        s.state = SOLVING
        s.message = f"Berechne aus {len(accepted)} Aufnahmen"
        await self._publish()
        try:
            solution = await asyncio.to_thread(solve_hand_eye_robust, flanges, tags)
        except ValueError as error:
            s.state = CAPTURING
            s.message = f"Nicht berechnet: {error}"
            await self._publish()
            return VisionErrorCode.INVALID_STATE

        pose_flange_cam = solution.pose_flange_cam
        # Verworfene Aufnahmen gehen nicht in die Kalibrierung, ihr Residuum
        # steht trotzdem im Status -- damit man sieht, welche Pose schlecht war.
        rejected_positions = set(solution.rejected)
        for position, (sample, (residual_m, residual_deg)) in enumerate(
            zip(accepted, solution.residuals)
        ):
            sample.residual_m, sample.residual_deg = residual_m, residual_deg
            sample.rejected = position in rejected_positions
        used = [
            (flange, tag)
            for index, (flange, tag) in enumerate(zip(flanges, tags))
            if index not in solution.rejected
        ]
        flanges, tags = [u[0] for u in used], [u[1] for u in used]
        hand_eye = HandEye(
            pose_flange_cam=pose_flange_cam,
            frame_id=self._ports.frame_id,
            rms_position_m=solution.rms_position_m,
            rms_rotation_deg=solution.rms_rotation_deg,
            sample_count=solution.used_count,
            max_position_m=solution.max_position_m,
            max_rotation_deg=solution.max_rotation_deg,
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
            # Echtes RMS der Abweichung der Brettlage ueber die verwendeten
            # Aufnahmen; das Maximum steht getrennt daneben.
            "rmsPositionM": solution.rms_position_m,
            "rmsRotationDeg": solution.rms_rotation_deg,
            "maxPositionM": solution.max_position_m,
            "maxRotationDeg": solution.max_rotation_deg,
            "sampleCount": solution.used_count,
            "rejectedCount": len(solution.rejected),
            #: Indizes in `samples[]`, in der Reihenfolge des Verwerfens.
            "rejectedSamples": [accepted_indices[i] for i in solution.rejected],
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
        dropped = (
            f", {len(solution.rejected)} Ausreisser verworfen"
            if solution.rejected
            else ""
        )
        s.message = (
            f"Gespeichert und aktiv: RMS {solution.rms_position_m * 1000:.1f} mm / "
            f"{solution.rms_rotation_deg:.2f} Grad (max {solution.max_position_m * 1000:.1f} mm) "
            f"ueber {solution.used_count} Posen{dropped}"
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

    async def _measure(self, s: _State) -> tuple[Any, str, dict]:
        """`(Pose | None, Grund, Info)`. Die Messfunktionen duerfen auch nur
        `(Pose, Grund)` liefern."""
        if s.board is None:
            result = await self._ports.measure_tag(s.tag_id)
        elif self._ports.measure_board is None:
            return None, "Dieser Server kann kein Schachbrett messen", {}
        else:
            result = await self._ports.measure_board(s.board)
        return result[0], result[1], (result[2] if len(result) > 2 else {})

    def _target_label(self) -> str:
        board = self._s.board
        if board is None:
            return f"Tag {self._s.tag_id}"
        return f"Schachbrett {board.cols}x{board.rows} Ecken"

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

    def _current(self) -> dict | None:
        """Die Kalibrierung, die gerade in der Datei steht -- auch nach einem
        Neustart, wenn keine Sitzung ein `result` hat. Damit kann das Frontend
        die Kamera am Flansch zeigen. `None` ohne (lesbare) Datei."""
        try:
            hand_eye = load_hand_eye(self._ports.path)
        except Exception:
            return None
        return {
            "poseFlangeCam": pose_to_dict(hand_eye.pose_flange_cam),
            "calibrationId": hand_eye.calibration_id,
            "rmsPositionM": hand_eye.rms_position_m if math.isfinite(hand_eye.rms_position_m) else None,
            "rmsRotationDeg": hand_eye.rms_rotation_deg if math.isfinite(hand_eye.rms_rotation_deg) else None,
            "maxPositionM": hand_eye.max_position_m if math.isfinite(hand_eye.max_position_m) else None,
            "maxRotationDeg": hand_eye.max_rotation_deg if math.isfinite(hand_eye.max_rotation_deg) else None,
            "sampleCount": hand_eye.sample_count,
        }

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
                "target": (
                    {
                        "type": CHESSBOARD,
                        "cols": s.board.cols,
                        "rows": s.board.rows,
                        "squareSizeM": s.board.square_size_m,
                    }
                    if s.board is not None
                    else {"type": "tag", "tagId": s.tag_id}
                ),
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
                        # Mittel der Bilder dieser Aufnahme, auch bei verworfenen.
                        "reprojectionErrorPx": sample.reprojection_error_px,
                        # Erst nach `FinishHandEyeCalibration`, sonst `null`.
                        "residualM": sample.residual_m,
                        "residualDeg": sample.residual_deg,
                        "rejected": sample.rejected,
                    }
                    for index, sample in enumerate(s.samples)
                ],
                "result": s.result,
                "current": self._current(),
            },
            allow_nan=False,
            default=float,
        )


# --- JSON ---------------------------------------------------------------------


def parse_start(text: str) -> tuple[int, Pose | None, BoardSpec | None]:
    """`wsc.vision.handeye.start/1` lesen: `(tagId, tagInBase, Board)`.

    Genau eins von `tagId` und `board`; mit Board ist `tagId` -1.
    ValueError mit Klartext.
    """
    data = _load(text, START_SCHEMA)
    has_tag = data.get("tagId") is not None
    has_board = data.get("board") is not None
    if has_tag == has_board:
        raise ValueError("Genau eins von tagId und board angeben.")
    if has_board:
        return -1, None, _parse_board(data["board"])
    tag_id = data.get("tagId")
    if not isinstance(tag_id, int) or isinstance(tag_id, bool) or tag_id < 0:
        raise ValueError("tagId fehlt oder ist keine nicht-negative Ganzzahl.")
    raw = data.get("tagInBase")
    return tag_id, (_pose(raw, "tagInBase") if raw is not None else None), None


def _parse_board(raw: Any) -> BoardSpec:
    if not isinstance(raw, dict) or raw.get("type") != CHESSBOARD:
        raise ValueError('board: nur {"type": "chessboard", ...} wird unterstuetzt.')
    cols, rows, size = raw.get("cols"), raw.get("rows"), raw.get("squareSizeM")
    for name, value in (("cols", cols), ("rows", rows)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 2:
            raise ValueError(f"board.{name}: Innenecken als Ganzzahl >= 2.")
    if not isinstance(size, (int, float)) or isinstance(size, bool) or not 0 < size < 1:
        raise ValueError("board.squareSizeM: Feldkante in Metern, zwischen 0 und 1.")
    spec = BoardSpec(type=CHESSBOARD, cols=cols, rows=rows, square_size_m=float(size))
    if not chessboard_is_unambiguous(spec):
        raise ValueError(
            f"board: {cols}x{rows} Innenecken -- cols + rows muss ungerade sein (z. B. 5x6), "
            "sonst ist das Brett um 180 Grad gedreht nicht vom Original zu unterscheiden."
        )
    return spec


def parse_sample(text: str) -> Pose:
    """`wsc.vision.handeye.sample/1` lesen; ValueError mit Klartext."""
    return _pose(_load(text, SAMPLE_SCHEMA).get("flangeInBase"), "flangeInBase")

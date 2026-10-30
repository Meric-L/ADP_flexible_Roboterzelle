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
from pathlib import Path
from typing import Any

import numpy as np

from tagloc.boards import CHESSBOARD, BoardSpec, chessboard_is_unambiguous
from tagloc.geometry import (
    Pose,
    average_poses,
    compose,
    pose_to_dict,
    rotation_distance_rad,
    translation_distance_m,
)
from tagloc.handeye import (
    AXIS_THRESHOLD_DEG,
    BOARD_SCALE_WARNING,
    MIN_SAMPLES,
    SESSION_FILE,
    STAGE_LEAVE_ONE_OUT,
    STAGE_REFINEMENT,
    BoardRefinement,
    HandEye,
    RefinementError,
    SessionSample,
    load_hand_eye,
    refine_board_session,
    rotation_diversity,
    save_hand_eye,
    session_json,
    session_payload,
    solve_hand_eye_robust,
)
from tagloc.jsonio import finite_or_none, utc_stamp

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

#: Empfehlung an den Bediener -- `MIN_SAMPLES` ist nur das rechnerische Minimum.
RECOMMENDED_SAMPLES = 8
#: `finish` verlangt Drehung um so viele Achsen (`rotation_diversity`); bei
#: weniger ist `T_flansch_cam` nicht bestimmt, selbst wenn der Solver rechnet.
REQUIRED_AXES = 3
#: Ab dieser Abweichung des geschaetzten Brettmassstabs vom Nennmass
#: (0,3 %) gibt es einen Hinweis: Feldgroesse oder Intrinsik stimmen nicht.
SCALE_WARNING = BOARD_SCALE_WARNING
#: Werte von `intrinsicsSource`, bei denen die Intrinsik nicht von uns stammt.
FACTORY_INTRINSICS = "realsense_factory"
PLACEHOLDER_INTRINSICS = "placeholder"


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
    #: Das dritte Element (`Info`) darf zusaetzlich `corners` (kanonische Ecken
    #: `(N, 2)` im verzerrten Bild), `calibration` (`CameraCalibration`),
    #: `intrinsicsSource` und `image` enthalten -- ohne sie gibt es keine
    #: Verfeinerung, nur die Park-Martin-Loesung.
    measure_board: Callable[[BoardSpec], Awaitable[tuple]] | None = None
    #: Woher die Intrinsik der Kamera stammt (`calibration`,
    #: `realsense_factory`, `placeholder`); `None`: unbekannt.
    intrinsics_source: Callable[[], str | None] | None = None
    #: Wohin die Datensaetze der Sitzungen kommen; `None`: `sessions/` neben `path`.
    sessions_dir: Path | None = None


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
    #: Brett-Modus: kanonische Ecken `(N, 2)` im verzerrten Bild, Mittel ueber
    #: die verwendeten Bilder der Aufnahme. `None` bei Tags.
    corners_px: np.ndarray | None = None
    #: Ein Bild der Aufnahme fuer den Datensatz, nicht im Status.
    image: Any = None
    #: Nach `finish`: Vorhersagefehler der Verfeinerung ohne diese Aufnahme
    #: (px; m im Kamera-KS). `None` vorher, bei Tags und bei Ausreissern.
    loo_error_px: float | None = None
    loo_error_m: float | None = None

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
    #: Intrinsik und ihre Herkunft, wie sie die Brett-Messung verwendet hat.
    calibration: Any = None
    intrinsics_source: str | None = None
    #: Ordnername des Datensatzes (UTC), gesetzt beim Start.
    stamp: str = ""


def _intrinsics_warnings(source: str | None) -> list[str]:
    """Hinweise zur Herkunft der Intrinsik."""
    if source == FACTORY_INTRINSICS:
        return [
            "Intrinsik: Werkswerte der Kamera, keine eigene Kalibrierung -- "
            "Massstab und Verzeichnung koennen abweichen; Kamera neu kalibrieren."
        ]
    if source == PLACEHOLDER_INTRINSICS:
        return ["Intrinsik: Platzhalter, die Posen sind nicht massshaltig."]
    return []


#: Meldung im Log je gescheitertem Schritt von `refine_board_session`.
_REFINEMENT_FAILURES = {
    STAGE_REFINEMENT: "Gemeinsame Verfeinerung fehlgeschlagen",
    STAGE_LEAVE_ONE_OUT: "Leave-one-out fehlgeschlagen",
}


def _refine_board(
    spec: BoardSpec, calibration, samples: list[_Sample], flanges: list[Pose], tags: list[Pose]
) -> BoardRefinement | None:
    """`tagloc.handeye.refine_board_session` ueber die angenommenen Aufnahmen.

    Liefert `None`, wenn nicht moeglich (fehlende Ecken/Intrinsik, Rechenfehler;
    der Fehler steht im Log). `ValueError` (Posen zu aehnlich) geht an den
    Aufrufer. Thread-tauglich.
    """
    if calibration is None or not samples or any(sample.corners_px is None for sample in samples):
        return None
    try:
        return refine_board_session(
            flanges, [sample.corners_px for sample in samples], tags, spec, calibration
        )
    except RefinementError as error:
        _log.exception("%s -- Park-Martin bleibt", _REFINEMENT_FAILURES[error.stage])
        return None


def _write_sample_images(folder: Path, samples: list[_Sample]) -> dict[int, str]:
    """Ein PNG je Aufnahme mit Bild; `{Index: Dateiname}`. Fehler nur ins Log."""
    images: dict[int, str] = {}
    try:
        import cv2

        for index, sample in enumerate(samples):
            image = sample.image
            if image is None:
                continue
            name = f"sample_{index:03d}.png"
            if cv2.imwrite(str(folder / name), np.asarray(image)):
                images[index] = name
    except Exception:
        _log.exception("Bilder des Hand-Auge-Datensatzes nicht geschrieben")
    return images


def _write_session_dataset(
    folder: Path, state: _State, outcome: str, intrinsics_source: str | None, frame_id: str
) -> None:
    """`session.json` plus ein PNG je Aufnahme mit Bild nach `folder` schreiben.

    Ein Ordner je Sitzung (Zeit des Starts); ein zweiter Versuch derselben
    Sitzung ueberschreibt ihn. Die Datei reicht, um mit
    `calibrate_handeye --session` neu zu rechnen: gemeldete Flanschposen,
    gemessene Brettposen, Ecken, Intrinsik und Brettgeometrie. Format und
    Leser: `tagloc.handeye.session_payload` / `load_session`.
    """
    folder.mkdir(parents=True, exist_ok=True)
    images = _write_sample_images(folder, state.samples)
    payload = session_payload(
        outcome=outcome,
        frame_id=frame_id,
        tag_id=state.tag_id,
        tag_in_base=state.tag_in_base,
        board=state.board,
        intrinsics_source=intrinsics_source,
        calibration=state.calibration,
        samples=[
            SessionSample(
                accepted=sample.accepted,
                detail=sample.detail,
                flange_in_base=sample.flange_in_base,
                cam_to_target=sample.cam_to_tag,
                reprojection_error_px=sample.reprojection_error_px,
                corners_px=sample.corners_px,
                image=images.get(index),
            )
            for index, sample in enumerate(state.samples)
        ],
        result=state.result,
    )
    (folder / SESSION_FILE).write_text(session_json(payload), encoding="utf-8")


def save_with_backup(path: Path, hand_eye: HandEye) -> Path | None:
    """Schreibt die Kalibrierung; eine vorhandene Datei wird vorher kopiert.

    Liefert den Pfad der Sicherung, `None` ohne alte Datei.
    """
    backup = None
    if path.is_file():
        stamp = utc_stamp()
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
        self._s = _State(
            state=CAPTURING,
            tag_id=tag_id,
            tag_in_base=tag_in_base,
            board=board,
            stamp=utc_stamp(),
        )
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
        sample = _Sample(
            flange_in_base,
            cam_to_tag,
            detail,
            reprojection_error_px=finite_or_none(info.get("reprojectionErrorPx")),
        )
        if cam_to_tag is not None and info.get("corners") is not None:
            sample.corners_px = np.asarray(info["corners"], dtype=np.float64).reshape(-1, 2)
            sample.image = info.get("image")
            if info.get("calibration") is not None:
                s.calibration = info["calibration"]
            if info.get("intrinsicsSource"):
                s.intrinsics_source = str(info["intrinsicsSource"])
        s.samples.append(sample)
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
        _, axes = rotation_diversity(flanges)
        if len(accepted) >= MIN_SAMPLES and axes < REQUIRED_AXES:
            # Der Solver rechnet auch bei zwei Achsen -- aber mit einer
            # Rotation, die das Rauschen mitbestimmt. Lieber weiter aufnehmen.
            s.message = (
                f"Nicht berechnet: nur {axes} Drehachse(n) erreicht, noetig sind "
                f"{REQUIRED_AXES}. Den Flansch um alle drei Achsen kippen und "
                "drehen (je mindestens "
                f"{AXIS_THRESHOLD_DEG:.0f} Grad), dann erneut berechnen."
            )
            await self._persist("refused")
            await self._publish()
            return VisionErrorCode.INVALID_STATE
        s.state = SOLVING
        s.message = f"Berechne aus {len(accepted)} Aufnahmen"
        await self._publish()
        refined: BoardRefinement | None = None
        try:
            if s.board is not None:
                refined = await asyncio.to_thread(
                    _refine_board, s.board, s.calibration, accepted, flanges, tags
                )
            if refined is not None:
                solution = refined.solution
            else:
                solution = await asyncio.to_thread(solve_hand_eye_robust, flanges, tags)
        except ValueError as error:
            s.state = CAPTURING
            s.message = f"Nicht berechnet: {error}"
            await self._persist("failed")
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
        used_samples = [
            sample
            for index, sample in enumerate(accepted)
            if index not in solution.rejected
        ]
        flanges, tags = [u[0] for u in used], [u[1] for u in used]
        rms_position_m, rms_rotation_deg = solution.rms_position_m, solution.rms_rotation_deg
        max_position_m, max_rotation_deg = solution.max_position_m, solution.max_rotation_deg
        intrinsics_source = self._intrinsics_source()
        warnings = _intrinsics_warnings(intrinsics_source)
        if s.board is not None:
            if refined is None and used_samples:
                warnings.append(
                    "Keine gemeinsame Verfeinerung gegen die Bildpunkte moeglich -- "
                    "Ergebnis nur aus Park-Martin."
                )
        if refined is not None:
            # Das verfeinerte X ersetzt Park-Martin (`solution` ist dafuer gerechnet).
            if refined.loo is not None:
                for sample, error_px, error_m in zip(
                    used_samples, refined.loo.error_px, refined.loo.error_m
                ):
                    sample.loo_error_px, sample.loo_error_m = error_px, error_m
            scale = refined.refinement.board_scale
            if abs(scale - 1.0) > SCALE_WARNING:
                warnings.append(
                    f"Brettmassstab weicht {abs(scale - 1.0) * 100:.1f} % vom Nennmass ab "
                    "-- Brett nachmessen / Intrinsik pruefen."
                )
        extra = refined.hand_eye_fields() if refined is not None else {}
        hand_eye = HandEye(
            pose_flange_cam=pose_flange_cam,
            frame_id=self._ports.frame_id,
            rms_position_m=rms_position_m,
            rms_rotation_deg=rms_rotation_deg,
            sample_count=solution.used_count,
            max_position_m=max_position_m,
            max_rotation_deg=max_rotation_deg,
            intrinsics_source=intrinsics_source or "",
            **extra,
        )
        try:
            backup = await asyncio.to_thread(save_with_backup, self._ports.path, hand_eye)
            loaded = await asyncio.to_thread(load_hand_eye, self._ports.path)
            self._ports.apply(loaded)
        except Exception as error:
            _log.exception("Hand-Auge-Kalibrierung nicht gespeichert")
            s.state = FAILED
            s.message = f"Berechnet, aber nicht gespeichert: {error}"
            await self._persist("failed")
            await self._publish()
            return VisionErrorCode.INTERNAL

        s.result = {
            "poseFlangeCam": pose_to_dict(pose_flange_cam),
            # Echtes RMS der Abweichung der Brettlage ueber die verwendeten
            # Aufnahmen; das Maximum steht getrennt daneben.
            "rmsPositionM": rms_position_m,
            "rmsRotationDeg": rms_rotation_deg,
            "maxPositionM": max_position_m,
            "maxRotationDeg": max_rotation_deg,
            "sampleCount": solution.used_count,
            "rejectedCount": len(solution.rejected),
            #: Indizes in `samples[]`, in der Reihenfolge des Verwerfens.
            "rejectedSamples": [accepted_indices[i] for i in solution.rejected],
            "knownTagCheckM": None,
            "knownTagCheckDeg": None,
            "path": str(self._ports.path),
            "backupPath": str(backup) if backup is not None else None,
            # Nur mit Brett und Verfeinerung, sonst `null`. Reprojektion aller
            # Brettecken nach der gemeinsamen Verfeinerung (px).
            "refinedReprojectionRmsPx": None,
            #: Brettmassstab gegen das Nennmass (1.0 = Brett wie angegeben).
            "boardScale": None,
            #: Vorhersagefehler ohne die jeweilige Aufnahme (RMS ueber alle):
            #: der ehrliche Genauigkeitswert, px und m (Kamera-KS).
            "looErrorRmsPx": None,
            "looErrorRmsM": None,
            "refinementConverged": None,
            #: Wo das Brett/der Tag im Basis-KS liegt (Mittel von F·X·C bzw.
            #: das verfeinerte `T_base_brett`), Position in m.
            "targetInBase": pose_to_dict(
                refined.refinement.pose_base_board
                if refined is not None
                else average_poses(
                    [compose(flange, pose_flange_cam, tag) for flange, tag in zip(flanges, tags)]
                )
            ),
            "warnings": warnings,
            "sessionPath": None,
        }
        if refined is not None:
            s.result["refinedReprojectionRmsPx"] = refined.refinement.reprojection_rms_px
            s.result["boardScale"] = refined.refinement.board_scale
            s.result["refinementConverged"] = refined.refinement.converged
            if refined.loo is not None:
                s.result["looErrorRmsPx"] = refined.loo.rms_px
                s.result["looErrorRmsM"] = refined.loo.rms_m
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
            f"Gespeichert und aktiv: RMS {rms_position_m * 1000:.1f} mm / "
            f"{rms_rotation_deg:.2f} Grad (max {max_position_m * 1000:.1f} mm) "
            f"ueber {solution.used_count} Posen{dropped}"
        )
        if refined is not None:
            s.message += f", Reprojektion {refined.refinement.reprojection_rms_px:.2f} px"
            if refined.loo is not None:
                s.message += f", Vorhersagefehler {refined.loo.rms_m * 1000:.1f} mm"
        _log.info("Hand-Auge-Kalibrierung: %s", s.message)
        s.result["sessionPath"] = await self._persist("finished")
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

    def _intrinsics_source(self) -> str | None:
        """Herkunft der Intrinsik: erst die Quelle des Servers, sonst was die
        Messungen gemeldet haben; `None`, wenn unbekannt."""
        if self._ports.intrinsics_source is not None:
            try:
                source = self._ports.intrinsics_source()
            except Exception as error:
                _log.debug("Herkunft der Intrinsik nicht lesbar: %s", error)
                source = None
            if source:
                return str(source)
        return self._s.intrinsics_source

    async def _persist(self, outcome: str) -> str | None:
        """Datensatz der Sitzung schreiben; Fehler nur ins Log (nie ein Grund,
        die Kalibrierung scheitern zu lassen). Liefert den Ordner oder `None`."""
        s = self._s
        folder = (self._ports.sessions_dir or self._ports.path.parent / "sessions") / s.stamp
        try:
            await asyncio.to_thread(
                _write_session_dataset, folder, s, outcome, self._intrinsics_source(),
                self._ports.frame_id,
            )
        except Exception:
            _log.exception("Hand-Auge-Datensatz nicht geschrieben: %s", folder)
            return None
        return str(folder)

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
        except Exception as error:
            _log.debug("Keine lesbare Hand-Auge-Datei %s: %s", self._ports.path, error)
            return None
        return {
            "poseFlangeCam": pose_to_dict(hand_eye.pose_flange_cam),
            "calibrationId": hand_eye.calibration_id,
            "rmsPositionM": finite_or_none(hand_eye.rms_position_m),
            "rmsRotationDeg": finite_or_none(hand_eye.rms_rotation_deg),
            "maxPositionM": finite_or_none(hand_eye.max_position_m),
            "maxRotationDeg": finite_or_none(hand_eye.max_rotation_deg),
            "sampleCount": hand_eye.sample_count,
            "refinedReprojectionRmsPx": finite_or_none(hand_eye.refined_reprojection_rms_px),
            "boardScale": finite_or_none(hand_eye.board_scale),
            "looErrorRmsPx": finite_or_none(hand_eye.loo_error_rms_px),
            "looErrorRmsM": finite_or_none(hand_eye.loo_error_rms_m),
            "intrinsicsSource": hand_eye.intrinsics_source or None,
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
                #: Herkunft der Intrinsik: `calibration`, `realsense_factory`,
                #: `placeholder` oder `null` (unbekannt).
                "intrinsicsSource": self._intrinsics_source(),
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
                        # Vorhersagefehler ohne diese Aufnahme (Brett-Modus,
                        # nach `finish`, nicht bei Ausreissern), sonst `null`.
                        "looErrorPx": sample.loo_error_px,
                        "looErrorM": sample.loo_error_m,
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

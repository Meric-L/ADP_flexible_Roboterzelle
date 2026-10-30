"""Kamera-Kalibrierung: Session bauen, Info-Knoten, Methoden `StartCalibration` und Co."""

import asyncio
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from asyncua import Node, ua, uamethod

from ..calibration_session import CalibrationSession
from ..config import VisionServerConfig
from ..detection import DetectionSource
from ..errors import VisionErrorCode
from .uamethods import code_reply
from .wiring import Wiring

_log = logging.getLogger(__package__)


#: Takt, in dem `CalibrationProgress` waehrend der Hintergrund-Auswertung
#: geschrieben wird, solange der Livestream pausiert ist (siehe
#: `_pause_stream_during_processing`). Ein neu verbundener Client sieht
#: `processing: true` damit spaetestens nach dieser Zeit.
CALIBRATION_PROGRESS_INTERVAL_S = 1.0


def _build_calibration_session(
    sources: Mapping[str, DetectionSource],
    opened: Mapping[str, bool],
    config: VisionServerConfig,
) -> CalibrationSession | None:
    """Baut die (wiederverwendbare) Kalibrier-Session, falls moeglich.

    Nur wenn die `apriltag`-Quelle offen ist -- dieselbe `SharedCamera` wie
    Job und Livestream, kein zweiter Kamera-Zugriff -- und `config.apriltag`
    gesetzt ist (auf den echten Pis der Fall, siehe `vision_server/server.py`; lokale
    Entwicklung/Tests ohne explizite Konfiguration lassen das Feature aus).
    """
    if config.apriltag is None:
        return None
    source = sources.get("apriltag")
    if source is None or not opened.get("apriltag") or getattr(source, "camera", None) is None:
        return None
    return CalibrationSession(source.camera, config.apriltag)


def _calibration_info_payload(calibration: Any, path: Any) -> dict:
    """JSON-faehige Kurzfassung der gerade *aktiven* Kalibrierung.

    Anders als `CalibrationSession.progress`/`last_result` (Fortschritt
    *einer Session*) beschreibt das hier, was `AprilTagDetectionSource`
    tatsaechlich fuer Posen benutzt -- direkt nach dem Laden beim Start und
    nach jeder interaktiven Neu-Kalibrierung.

    `calibrationId`/`createdAt` liest diese Funktion aus der Datei nach,
    statt sie hier ein zweites Mal zu erzeugen: `CameraCalibration` selbst
    kennt `createdAt` gar nicht, und `calibration_id` ist bei einem frisch
    berechneten Objekt noch leer -- das Format entsteht erst beim Schreiben
    in `tagloc.calibration.save_calibration`. Einzige Ausnahme: die
    Platzhalter-Kalibrierung, zu der keine Datei existiert.
    """
    from tagloc.calibration import PLACEHOLDER_CALIBRATION_ID

    is_placeholder = calibration.calibration_id == PLACEHOLDER_CALIBRATION_ID
    info = {
        "placeholder": is_placeholder,
        "frameId": calibration.frame_id,
        "rms": None if is_placeholder else round(calibration.rms_reprojection_error, 4),
        "samples": calibration.sample_count,
        "board": dict(calibration.board),
        "model": calibration.model,
        "calibrationId": calibration.calibration_id or None,
        "createdAt": None,
        "path": str(path),
    }
    if not is_placeholder:
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            info["calibrationId"] = raw.get("calibrationId")
            info["createdAt"] = raw.get("createdAt")
        except OSError:
            pass
    return info


async def _write_calibration_info(node: Node | None, calibration: Any, path: Any) -> None:
    """Schreibt `_calibration_info_payload` in `ActiveCalibrationInfo`.

    Fehler landen nur im Log -- ein nicht schreibbarer Info-Knoten darf
    weder den Start noch eine gerade erfolgreich gespeicherte Kalibrierung
    zu Fall bringen.
    """
    if node is None or calibration is None:
        return
    try:
        await node.write_value(json.dumps(_calibration_info_payload(calibration, path)))
    except Exception:
        _log.exception("ActiveCalibrationInfo konnte nicht geschrieben werden")


async def write_initial_calibration_info(wiring: Wiring) -> None:
    """Schreibt nach dem Oeffnen der Quellen, welche Kalibrierung gerade gilt.

    Was `open()` gerade geladen hat (echte Datei oder Platzhalter) -- ohne das
    waere `ActiveCalibrationInfo` leer, bis zum ersten `StartCalibration`.
    """
    config = wiring.config
    if config.apriltag is not None and wiring.opened.get("apriltag"):
        await _write_calibration_info(
            wiring.space.active_calibration_info,
            getattr(wiring.apriltag_source, "_calibration", None),
            config.apriltag.calibration_path,
        )


def connect_live_calibration(wiring: Wiring) -> None:
    """Haengt die Live-Uebernahme an die Session (falls es eine gibt)."""
    session = wiring.calibration_session
    if session is None:
        return

    async def _apply_live_calibration(calibration: Any) -> None:
        """Bringt Erkennung, Overlay und den Info-Knoten sofort auf den
        neuen Stand -- kein Server-Neustart noetig, siehe
        `AprilTagDetectionSource.apply_calibration`."""
        source, annotator = wiring.apriltag_source, wiring.annotator
        if source is not None and hasattr(source, "apply_calibration"):
            source.apply_calibration(calibration)
        if annotator is not None and hasattr(annotator, "apply_calibration"):
            annotator.apply_calibration(calibration)
        await _write_calibration_info(
            wiring.space.active_calibration_info,
            calibration,
            wiring.config.apriltag.calibration_path,
        )

    session.set_on_calibrated(_apply_live_calibration)


async def _write_calibration_progress(wiring: Wiring) -> None:
    """Schreibt `CalibrationProgress` direkt aus der Session.

    Sonst schreibt nur die Publish-Schleife des Livestreams diesen
    Knoten (`CameraStreamPublisher._publish_progress`) -- und genau
    die ist waehrend der Auswertung pausiert. Ohne diesen eigenen
    Schreibpfad bliebe der Knoten dann auf dem letzten Stand der
    Aufnahme-Phase stehen, `processing: true` waere nie sichtbar
    (gefunden 2026-09-28).
    """
    try:
        await wiring.space.calibration_progress.write_value(
            json.dumps(wiring.calibration_session.progress)
        )
    except Exception:
        _log.exception("Kalibrier-Fortschritt konnte nicht veroeffentlicht werden")


async def _pause_stream_during_processing(wiring: Wiring) -> None:
    """Waehrend `CalibrationSession._process()` im Hintergrund
    rechnet, den Livestream pausieren, damit der Pi die volle CPU
    fuer die Ecken-Erkennung hat (2026-09-28, auf Vorschlag/mit
    Bestaetigung, nachdem die vorherige Deckenkamera-Auswertung
    wiederholt haengen blieb). Laeuft selbst als Hintergrund-Task --
    `FinishCalibration` darf darauf nicht warten, siehe dort.

    `CalibrationProgress` wird in dieser Zeit hier geschrieben statt
    vom (pausierten) Livestream: einmal sofort (`processing: true`),
    danach im Takt `CALIBRATION_PROGRESS_INTERVAL_S` und ein letztes
    Mal mit `result`, bevor der Livestream wieder uebernimmt.
    """
    stream = wiring.camera_stream
    if stream is not None:
        await stream.stop()
    await _write_calibration_progress(wiring)
    while True:
        try:
            await asyncio.wait_for(
                wiring.calibration_session.wait_for_processing(),
                timeout=CALIBRATION_PROGRESS_INTERVAL_S,
            )
            break
        except TimeoutError:
            await _write_calibration_progress(wiring)
    await _write_calibration_progress(wiring)
    if stream is not None:
        stream.start()


def _start_calibration_method(wiring: Wiring):
    """Handler fuer `1:StartCalibration` -- setzt eine neue Session auf; Aufnahmen
    kommen danach ausschliesslich ueber `CaptureCalibrationSample`.

    Kein Kalibrierdurchlauf gegen einen laufenden Job oder eine
    zweite Session gleichzeitig -- beide teilen sich Kamera und
    Detektor.
    """

    def busy_reason() -> str | None:
        if wiring.jobs.busy:
            return "StartCalibration waehrend laufendem Job abgelehnt"
        if wiring.layer2_active():
            return "StartCalibration waehrend laufendem Layer-2-Lauf abgelehnt"
        if wiring.hand_eye_active():
            return "StartCalibration waehrend Hand-Auge-Kalibrierung abgelehnt"
        if wiring.calibration_busy():
            # `.busy` statt `.running`: eine Hintergrund-Auswertung aus
            # einem vorherigen `FinishCalibration` liest noch aus dem
            # `_pending_dir` der Session -- ein neuer `start()` waehrend-
            # dessen wuerde den gerade genau darunter wegleeren.
            return "StartCalibration waehrend laufender Session/Auswertung abgelehnt"
        return None

    @uamethod
    async def method(parent):
        reason = busy_reason()
        if reason is not None:
            _log.warning(reason)
            return code_reply(VisionErrorCode.BUSY)
        if not wiring.states.is_ready():
            return code_reply(VisionErrorCode.INVALID_STATE)
        wiring.calibration_session.start()
        if wiring.annotator is not None:
            wiring.annotator.set_calibration_session(wiring.calibration_session)
        _log.info("Kalibrier-Session gestartet")
        return code_reply(VisionErrorCode.OK)

    return method


def _capture_calibration_method(wiring: Wiring):
    """Handler fuer `1:CaptureCalibrationSample` -- merkt sich das aktuelle
    Kamerabild, manuell ausgeloest (z. B. per Leertaste im
    Stream-Viewer). Keine Board-Erkennung in diesem Aufruf, die
    passiert erst nach `FinishCalibration` (siehe
    `CalibrationSession.capture`). `Error=OK` heisst nur "Bild
    gespeichert"; `INVALID_STATE` heisst keine Session aktiv oder
    gerade kein Kamera-Frame -- die Session laeuft weiter, ein
    erneuter Versuch ist ok.
    """

    @uamethod
    async def method(parent):
        session = wiring.calibration_session
        if not session.running:
            return code_reply(VisionErrorCode.INVALID_STATE)
        stored = await session.capture()
        error = VisionErrorCode.OK if stored else VisionErrorCode.INVALID_STATE
        return code_reply(error)

    return method


def _finish_calibration_method(wiring: Wiring):
    """Handler fuer `1:FinishCalibration` -- beendet die Aufnahme-Phase und stoesst
    die Auswertung im Hintergrund an; kehrt sofort zurueck, OHNE auf
    das Ergebnis zu warten (`CalibrationSession.finish()`, siehe
    dort -- eine synchrone Auswertung mehrerer
    Vollaufloesungs-Aufnahmen haette auf der Deckenkamera dasselbe
    Timeout-Risiko wie eine einzelne haengende `detect_board`-
    Aufnahme, live 2026-09-28 gefunden). Das Frontend beobachtet das
    Ergebnis ausschliesslich ueber `CalibrationProgress`
    (`processing` -> `result`).

    `Summary` ist immer gueltiges JSON, auch im Fehlerfall (dann mit
    `message` statt `samples`/... ), damit das Frontend nicht
    zwischen Erfolgs- und Fehlerform unterscheiden muss.
    """

    @uamethod
    async def method(parent):
        session = wiring.calibration_session
        if not session.running:
            return (
                ua.Variant('{"message": "keine Session aktiv"}', ua.VariantType.String),
                ua.Variant(int(VisionErrorCode.INVALID_STATE), ua.VariantType.Int32),
            )
        error, summary = await session.finish()
        # Der Annotator bleibt in beiden Faellen bewusst an der Session
        # haengen (kein `set_calibration_session(None)` hier): der
        # Livestream veroeffentlicht so weiter `progress` samt `result`,
        # statt in den session-losen Zustand `{"running": false}`
        # zurueckzufallen -- das gilt auch fuer die sofortige Ablehnung
        # mit zu wenigen Aufnahmen, deren `result` sonst nie in
        # `CalibrationProgress` ankaeme. Geloest wird das erst durch das
        # naechste `StartCalibration`/`AbortCalibration`.
        if error == VisionErrorCode.OK:
            asyncio.ensure_future(_pause_stream_during_processing(wiring))
        else:
            await _write_calibration_progress(wiring)
        return (
            ua.Variant(json.dumps(summary), ua.VariantType.String),
            ua.Variant(int(error), ua.VariantType.Int32),
        )

    return method


def _abort_calibration_method(wiring: Wiring):
    """Handler fuer `1:AbortCalibration` -- stoppt ohne zu speichern."""

    @uamethod
    async def method(parent):
        session = wiring.calibration_session
        if not session.running:
            return code_reply(VisionErrorCode.INVALID_STATE)
        await session.abort()
        if wiring.annotator is not None:
            wiring.annotator.set_calibration_session(None)
        _log.info("Kalibrier-Session abgebrochen")
        return code_reply(VisionErrorCode.OK)

    return method


async def install_calibration_methods(wiring: Wiring) -> None:
    """Legt die vier Kalibriermethoden an (nur mit Session und `CalibrationProgress`).

    Die Knoten landen in `wiring.methods` und damit auch unter `VisionProgram`.
    """
    space = wiring.space
    if wiring.calibration_session is None or space.calibration_progress is None:
        return
    for name, handler, outputs in (
        ("StartCalibration", _start_calibration_method(wiring), [ua.VariantType.Int32]),
        ("CaptureCalibrationSample", _capture_calibration_method(wiring), [ua.VariantType.Int32]),
        (
            "FinishCalibration",
            _finish_calibration_method(wiring),
            [ua.VariantType.String, ua.VariantType.Int32],
        ),
        ("AbortCalibration", _abort_calibration_method(wiring), [ua.VariantType.Int32]),
    ):
        wiring.methods[name] = await space.vision_system.add_method(
            ua.NodeId(f"{wiring.method_prefix}.{name}", space.own_idx),
            ua.QualifiedName(name, space.own_idx),
            handler,
            [],
            outputs,
        )

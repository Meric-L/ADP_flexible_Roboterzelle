"""Kamera-Kalibrierung: Session bauen, Info-Knoten, Methoden `StartCalibration` und Co."""

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from asyncua import Node

from ..calibration_session import CalibrationSession
from ..config import VisionServerConfig
from ..detection import DetectionSource

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

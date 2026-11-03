"""Kalibrierpruefung als OPC-UA-Job.

Laeuft als Subprozess ueber `ScriptDetectionSource`, seine Standardausgabe
wird zur `attributes.message` des Ergebnisses.

Bewusst eine **Pruefung**, keine Kalibrierung: Eine Kamera zu kalibrieren
braucht einen Bediener, der ein Brett durchs Blickfeld bewegt -- das ist die
Aufgabe von `python -m tagloc.cli.calibrate`. Was ein Remote-Job beantworten
kann, ist "ist diese Zelle messbereit?": Gibt es eine Kalibrierung, zu
welcher Kamera und Aufloesung sie gehoert, wie gut sie war und ob eine
Tag-Karte existiert.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))


#: Hostname -> Rahmen-ID. Spiegelt `vision_server.server.PI_IDENTITIES` (zweites
#: Tupelelement). Nicht importiert, weil `vision_server.server` beim Import
#: asyncua laedt -- zu schwer fuer einen Subprozess pro Job. Dass beide Tabellen
#: gleich bleiben, prueft `tests/test_jobs_calibrate.py`.
FRAME_BY_HOST = {
    "ADP-Roboter-Lokalisierung": "cam_ceiling",
    "ADP-HandInEye-Kamera-Pi": "cam_flange",
}


def _frame_id() -> str:
    """Rahmen-ID dieses Pi: `VISION_FRAME_ID`, sonst nach Hostname, sonst "world"."""
    import os
    import socket

    return os.getenv("VISION_FRAME_ID") or FRAME_BY_HOST.get(socket.gethostname(), "world")


def main() -> str:
    from tagloc.identity import calibration_identity, tag_map_identity

    frame_id = _frame_id()
    calibration_path = REPO_ROOT / "data" / "calibration" / f"{frame_id}.json"
    tag_map_path = REPO_ROOT / "config" / "tagmap.json"

    parts = [f"Rahmen {frame_id}"]
    if not calibration_path.is_file():
        return (
            f"Rahmen {frame_id}: KEINE KALIBRIERUNG unter {calibration_path}. "
            "Erzeugen mit: python -m tagloc.cli.calibrate --source picamera "
            "--board chessboard --cols 7 --rows 10 --square-size-m 0.023 "
            f"--frame-id {frame_id} --out {calibration_path}"
        )

    try:
        from tagloc.calibration import load_calibration

        calibration = load_calibration(calibration_path)
    except Exception as error:  # eine kaputte Datei ist ein Befund, kein Absturz
        return f"Rahmen {frame_id}: Kalibrierung nicht lesbar ({error})"

    age_days = (
        datetime.now(timezone.utc)
        - datetime.fromtimestamp(calibration_path.stat().st_mtime, tz=timezone.utc)
    ).days
    parts.append(f"Kalibrierung {calibration_identity(calibration_path)}")
    parts.append(f"{calibration.image_size[0]}x{calibration.image_size[1]}")
    parts.append(f"Modell {calibration.model}")
    parts.append(f"RMS {calibration.rms_reprojection_error:.3f} px")
    parts.append(f"{calibration.sample_count} Aufnahmen")
    parts.append(f"Alter {age_days} d")
    parts.append(
        f"Tag-Map {tag_map_identity(tag_map_path)}"
        if tag_map_path.is_file()
        else "Tag-Map FEHLT -- Posen bleiben im Kamera-KS"
    )
    return ", ".join(parts)


if __name__ == "__main__":
    print(main())

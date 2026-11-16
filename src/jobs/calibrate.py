"""Calibration check run as an OPC UA job; its stdout becomes the result message.

This is a check, not a calibration: it reports whether the cell is ready to measure
(calibration present, its identity and quality, tag map present). Calibrating needs
an operator and is done with `python -m tagloc.cli.calibrate`.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))


#: Hostname -> frame ID. Mirrors `vision_server.server.PI_IDENTITIES` (second
#: tuple element). Not imported, because importing `vision_server.server` loads
#: asyncua -- too heavy for one subprocess per job. `tests/test_jobs_calibrate.py`
#: checks that both tables stay equal.
FRAME_BY_HOST = {
    "ADP-Roboter-Lokalisierung": "cam_ceiling",
    "ADP-HandInEye-Kamera-Pi": "cam_flange",
}


def _frame_id() -> str:
    """Return this Pi's frame ID: `VISION_FRAME_ID`, else by hostname, else "world"."""
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
    except Exception as error:  # a broken file is a finding, not a crash
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

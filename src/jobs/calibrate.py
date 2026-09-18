"""Calibration check as an OPC-UA job.

Runs as a subprocess via `ScriptDetectionSource`, its stdout becomes the
result's `attributes.message`.

Deliberately a **check**, not a calibration: calibrating a camera needs an
operator moving a board through the field of view -- that's the job of
`python -m tagloc.cli.calibrate`. What a remote job can answer is "is this
cell ready to measure?" -- is there a calibration, which camera and
resolution it belongs to, how good it was, and whether a tag map exists.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))


def _frame_id() -> str:
    """Return this Pi's frame ID, as determined by `OPCUA/server.py`."""
    import os
    import socket

    mapping = {"pi-decke": "cam_ceiling", "pi-hand": "cam_flange"}
    return os.getenv("VISION_FRAME_ID") or mapping.get(socket.gethostname(), "world")


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
            f"--board charuco --frame-id {frame_id} --out {calibration_path}"
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

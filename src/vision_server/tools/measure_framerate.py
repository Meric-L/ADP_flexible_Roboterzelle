"""Misst, was die Kamera-Hardware an Framerate wirklich hergibt -- min, max,
Durchschnitt.

Wichtig: `SharedCamera._capture_loop` (camera.py) drosselt bewusst auf
`capture_fps` (Standard 15 Hz, an der Deckenkamera 10 Hz laut
`PI_CAMERA_STREAM_PRESETS` in server.py) -- das ist die Kadenz, mit der der
Server selbst neue Frames von der Hardware abholt (unabhaengig davon, wie oft
der OPC-UA-Knoten oder der MJPEG-Stream daraus schreiben, siehe `stream_fps`/
`http_fps`), nicht zwingend die Kadenz, die die Hardware maximal koennte.
Dieses Skript umgeht die Drosselung absichtlich: es liest ueber
`SharedCamera.direct_reader()` -- dieselben Oeffnen-/Lese-Wege wie der Server
(Picamera2/RealSense/OpenCV) -- in einer engen Schleife, ohne zu warten, um
die tatsaechliche Kamera-Obergrenze zu finden -- z. B. um zu pruefen, ob
`capture_fps` noch Luft nach oben hat.

Nutzt automatisch Backend und Aufloesung, die fuer den Pi konfiguriert sind
(`vision_server.server.vision_config()`), damit das Ergebnis zum echten
Setup passt -- Ueberschreiben per `--backend`/`--resolution`/`--realsense-fps`
moeglich, z. B. um vor einer Config-Aenderung zu pruefen, ob eine hoehere
`realsense_fps` ueberhaupt etwas bringt.

**Braucht exklusiven Kamerazugriff** wie das CLI-Kalibriertool -- Server
vorher stoppen:

    sudo systemctl stop opcua-server.service
    PYTHONPATH=src python3 src/vision_server/tools/measure_framerate.py
    sudo systemctl start opcua-server.service
"""

import argparse
import contextlib
import logging
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from vision_server.camera import SharedCamera
from vision_server.server import vision_config


def _measure(read_frame: Callable[[], Any], duration_s: float) -> list[float]:
    """Liest Frames am Stueck, ohne zwischen Aufnahmen zu warten. Gibt die
    Zeitstempel jedes erfolgreichen Reads zurueck."""
    timestamps: list[float] = []
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        read_frame()
        timestamps.append(time.monotonic())
    return timestamps


def _summarise(timestamps: list[float], duration_s: float) -> None:
    if len(timestamps) < 2:
        print(f"Nur {len(timestamps)} Frame(s) in {duration_s:.1f}s -- zu wenig fuer eine Auswertung.")
        return

    intervals = [b - a for a, b in zip(timestamps, timestamps[1:])]
    fps_per_interval = [1.0 / interval for interval in intervals if interval > 0]
    span = timestamps[-1] - timestamps[0]
    overall_avg = (len(timestamps) - 1) / span if span > 0 else 0.0

    print(f"\nFrames:            {len(timestamps)}")
    print(f"Zeitraum:          {span:.2f} s")
    print(f"Durchschnitt (gesamt, Frames/Zeitraum): {overall_avg:.2f} fps")
    if fps_per_interval:
        print(f"Minimum (langsamster Einzelabstand):    {min(fps_per_interval):.2f} fps")
        print(f"Maximum (schnellster Einzelabstand):    {max(fps_per_interval):.2f} fps")
        sorted_fps = sorted(fps_per_interval)
        median = sorted_fps[len(sorted_fps) // 2]
        print(f"Median:                                  {median:.2f} fps")


def run(args: argparse.Namespace) -> int:
    config = vision_config().camera_stream
    if args.backend:
        config = replace(config, backend=args.backend)
    if args.resolution:
        config = replace(config, resolution=tuple(args.resolution))
    if args.realsense_fps:
        config = replace(config, realsense_fps=args.realsense_fps)

    resolution = config.realsense_resolution if config.backend == "realsense" else config.resolution
    print(f"Backend: {config.backend}, Aufloesung: {resolution[0]}x{resolution[1]}")

    with contextlib.ExitStack() as stack:
        try:
            read_frame = stack.enter_context(SharedCamera(config).direct_reader())
        except Exception as error:
            print(f"FEHLER: Kamera liess sich nicht oeffnen: {error}", file=sys.stderr)
            print(
                "Laeuft der Server noch? Kamera-Backends lassen nur einen offenen "
                "Zugriff zu -- vorher `sudo systemctl stop opcua-server.service`.",
                file=sys.stderr,
            )
            return 1

        print(f"Aufwaermen ({args.warmup_s:.0f}s) ...")
        _measure(read_frame, args.warmup_s)

        print(f"Messe ({args.duration_s:.0f}s) ...")
        timestamps = _measure(read_frame, args.duration_s)
        _summarise(timestamps, args.duration_s)

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Misst die rohe Framerate der Kamera-Hardware")
    parser.add_argument("--backend", choices=["picamera2", "realsense", "opencv"])
    parser.add_argument("--resolution", type=int, nargs=2, metavar=("WIDTH", "HEIGHT"))
    parser.add_argument("--realsense-fps", type=int)
    parser.add_argument("--duration", dest="duration_s", type=float, default=10.0)
    parser.add_argument("--warmup", dest="warmup_s", type=float, default=2.0)
    args = parser.parse_args(argv)
    # Frueher stellte der Import von `vision_server.server` das nebenbei ein;
    # ohne das fehlten hier die Meldungen von `vision_config` und `SharedCamera`.
    logging.basicConfig(level=logging.INFO)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())

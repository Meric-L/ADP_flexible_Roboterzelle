"""Measure the frame rate the camera hardware can really deliver: min, max, average.

Note: `SharedCamera._capture_loop` (camera.py) deliberately throttles to `capture_fps`
(default 15 Hz). That is the cadence at which the server itself fetches new frames from
the hardware (independent of how often the OPC UA node or the MJPEG stream write from
them, see `stream_fps`/`http_fps`), not necessarily the maximum the hardware could reach.
This script deliberately bypasses the throttle: it calls the same open/read methods as
`SharedCamera` (Picamera2/RealSense/OpenCV) in a tight loop, without waiting, to find
the actual camera upper limit -- e.g. to check whether `capture_fps` still has headroom.

It automatically uses the backend and resolution configured for the Pi
(`vision_server.server.vision_config()`), so the result matches the real setup.
Overriding via `--backend`/`--resolution`/`--realsense-fps` is possible, e.g. to check
before a config change whether a higher `realsense_fps` makes any difference at all.

**Requires exclusive camera access**, like the CLI calibration tool -- stop the server first:

    sudo systemctl stop opcua-server.service
    PYTHONPATH=src python3 src/vision_server/tools/measure_framerate.py
    sudo systemctl start opcua-server.service
"""

import argparse
import asyncio
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from vision_server.camera import SharedCamera
from vision_server.server import vision_config


async def _measure(shared: SharedCamera, executor: ThreadPoolExecutor, duration_s: float) -> list[float]:
    """Read frames back to back, without waiting between captures.

    Return the timestamp of each successful read.
    """
    loop = asyncio.get_running_loop()
    timestamps: list[float] = []
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        await loop.run_in_executor(executor, shared._read_frame)
        timestamps.append(time.monotonic())
    return timestamps


def _summarise(timestamps: list[float], duration_s: float) -> None:
    """Print the overall average and the min, max and median frame rate."""
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


async def run(args: argparse.Namespace) -> int:
    """Open the camera, measure its frame rate and print the result. Return the exit code."""
    config = vision_config().camera_stream
    if args.backend:
        config = replace(config, backend=args.backend)
    if args.resolution:
        config = replace(config, resolution=tuple(args.resolution))
    if args.realsense_fps:
        config = replace(config, realsense_fps=args.realsense_fps)

    resolution = config.realsense_resolution if config.backend == "realsense" else config.resolution
    print(f"Backend: {config.backend}, Aufloesung: {resolution[0]}x{resolution[1]}")

    shared = SharedCamera(config)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="framerate-test")
    loop = asyncio.get_running_loop()
    try:
        shared._camera = await loop.run_in_executor(executor, shared._open_camera)
    except Exception as error:
        print(f"FEHLER: Kamera liess sich nicht oeffnen: {error}", file=sys.stderr)
        print(
            "Laeuft der Server noch? Kamera-Backends lassen nur einen offenen "
            "Zugriff zu -- vorher `sudo systemctl stop opcua-server.service`.",
            file=sys.stderr,
        )
        return 1

    try:
        print(f"Aufwaermen ({args.warmup_s:.0f}s) ...")
        await _measure(shared, executor, args.warmup_s)

        print(f"Messe ({args.duration_s:.0f}s) ...")
        timestamps = await _measure(shared, executor, args.duration_s)
        _summarise(timestamps, args.duration_s)
    finally:
        await loop.run_in_executor(executor, shared._close_camera, shared._camera)
        executor.shutdown(wait=False)

    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse the command-line arguments and run the frame rate measurement."""
    parser = argparse.ArgumentParser(description="Misst die rohe Framerate der Kamera-Hardware")
    parser.add_argument("--backend", choices=["picamera2", "realsense", "opencv"])
    parser.add_argument("--resolution", type=int, nargs=2, metavar=("WIDTH", "HEIGHT"))
    parser.add_argument("--realsense-fps", type=int)
    parser.add_argument("--duration", dest="duration_s", type=float, default=10.0)
    parser.add_argument("--warmup", dest="warmup_s", type=float, default=2.0)
    args = parser.parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())

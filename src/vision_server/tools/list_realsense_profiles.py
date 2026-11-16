"""List the resolution/FPS/format combinations the connected RealSense color sensor supports.

Use this instead of guessing a higher `realsense_resolution` and hoping that
`pipeline.start()` works. `_open_realsense` (camera.py) requests `bgr8`, so the
sorted output highlights which resolution/FPS combinations are available in
exactly this format. Pick new values for `realsense_resolution` and
`realsense_fps` from that list, not from other formats.

Needs no exclusive camera access. It only queries device capabilities and
opens no pipeline, so the server can keep running in parallel.
"""

import sys


def main() -> int:
    import pyrealsense2 as rs

    from vision_server.camera import realsense_color_profiles

    devices = rs.context().query_devices()
    if len(devices) == 0:
        print("Keine RealSense gefunden (Kabel/USB-Port pruefen).", file=sys.stderr)
        return 1

    device = devices[0]
    print(f"Geraet: {device.get_info(rs.camera_info.name)}")

    profiles = realsense_color_profiles(device)

    if not profiles:
        print("Kamera gefunden, aber keine Farb-Profile gemeldet.", file=sys.stderr)
        return 1

    bgr8 = sorted(
        ((w, h) for w, h, fps, fmt in profiles if fmt == "bgr8" and fps >= 30),
        key=lambda wh: wh[0] * wh[1],
        reverse=True,
    )
    print("\nbgr8 @ >=30fps (das, was _open_realsense anfordert), nach Aufloesung sortiert:")
    if bgr8:
        for width, height in dict.fromkeys(bgr8):
            print(f"  {width}x{height}")
    else:
        print("  keine -- bgr8 gibt es hier nur bei niedrigerer fps, siehe unten")

    print("\nAlle Farb-Profile:")
    for width, height, fps, fmt in sorted(profiles, key=lambda p: (-p[0] * p[1], -p[2])):
        print(f"  {width}x{height}@{fps}fps ({fmt})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Listet die vom Farbsensor der angeschlossenen RealSense tatsaechlich
unterstuetzten Aufloesung/FPS/Format-Kombinationen -- statt eine hoehere
`realsense_resolution` zu raten und auf `pipeline.start()` zu hoffen.

`_open_realsense` (camera.py) fordert `bgr8` an; die sortierte Ausgabe hebt
deshalb hervor, welche Aufloesung/FPS-Kombinationen in genau diesem Format
verfuegbar sind -- das ist die Liste, aus der ein neuer Wert fuer
`realsense_resolution`/`realsense_fps` kommen sollte, kein anderes Format.

Braucht keinen exklusiven Kamerazugriff -- fragt nur die Geraete-Faehigkeiten
ab, oeffnet keine Pipeline (der Server kann parallel weiterlaufen). Das
Einsammeln teilt es sich mit `_open_realsense` (`camera.realsense_color_profiles`),
braucht deshalb `src/` im Pfad:

    PYTHONPATH=src python3 src/vision_server/tools/list_realsense_profiles.py
"""

import sys

from vision_server.camera import realsense_color_profiles


def main() -> int:
    import pyrealsense2 as rs

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

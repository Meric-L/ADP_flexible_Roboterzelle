"""Listet die vom Farbsensor der angeschlossenen RealSense tatsaechlich
unterstuetzten Aufloesung/FPS/Format-Kombinationen -- statt eine hoehere
`realsense_resolution` zu raten und auf `pipeline.start()` zu hoffen.

`_open_realsense` (camera.py) fordert `bgr8` an; die sortierte Ausgabe hebt
deshalb hervor, welche Aufloesung/FPS-Kombinationen in genau diesem Format
verfuegbar sind -- das ist die Liste, aus der ein neuer Wert fuer
`realsense_resolution`/`realsense_fps` kommen sollte, kein anderes Format.

Braucht keinen exklusiven Kamerazugriff -- fragt nur die Geraete-Faehigkeiten
ab, oeffnet keine Pipeline (der Server kann parallel weiterlaufen).
"""

import sys


def main() -> int:
    import pyrealsense2 as rs

    devices = rs.context().query_devices()
    if len(devices) == 0:
        print("Keine RealSense gefunden (Kabel/USB-Port pruefen).", file=sys.stderr)
        return 1

    device = devices[0]
    print(f"Geraet: {device.get_info(rs.camera_info.name)}")

    profiles: set[tuple[int, int, int, str]] = set()
    for sensor in device.query_sensors():
        for profile in sensor.get_stream_profiles():
            if profile.stream_type() != rs.stream.color:
                continue
            video = profile.as_video_stream_profile()
            profiles.add((video.width(), video.height(), profile.fps(), profile.format().name))

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

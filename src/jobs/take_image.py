"""Bilderkennungsscript: sucht per Raspberry-Pi-Kamera einen QR-Code.

Wird vom Bilderkennungs-Job als Subprozess ausgefuehrt (siehe
`vision_server/detection/script_runner.py`). Faehrt die Kamera ueber
Picamera2 hoch, sucht bis zu `SCAN_DURATION_S` lang nach einem QR-Code und
gibt dessen dekodierten Text auf stdout aus -- das ist der Vertrag mit
`ScriptDetectionSource`, die stdout unveraendert als `attributes.message`
durchreicht. Wird in der Zeit keiner gefunden, wird `NO_QR_CODE_MESSAGE`
ausgegeben; das ist ein regulaeres Ergebnis, kein Fehler. Ein tatsaechlicher
Kamerafehler wirft, der Prozess beendet dann mit Exit-Code != 0 und die
Fehlermeldung landet auf stderr -- so behandelt `ScriptDetectionSource` das
bereits als `DETECTION_FAILED`.

Setzt voraus, dass `picamera2` und OpenCV auf dem Pi installiert sind (siehe
`src/apriltag/caputure.py` fuer die urspruengliche Vorlage).
"""

import time

import cv2
from picamera2 import Picamera2

SCAN_DURATION_S = 30.0
RESOLUTION = (2028, 1520)
FRAME_RATE = 30
WARMUP_S = 2.0
NO_QR_CODE_MESSAGE = "Kein QR Code gefunden"


def scan_qr_code(duration: float = SCAN_DURATION_S) -> str | None:
    """Haelt die Kamera `duration` Sekunden offen und sucht nach einem QR-Code.

    Bricht sofort ab, sobald ein QR-Code dekodiert werden konnte; sonst laeuft
    die volle Dauer durch und es wird `None` geliefert.
    """
    camera = Picamera2()
    camera.configure(
        camera.create_video_configuration(
            main={"size": RESOLUTION}, controls={"FrameRate": FRAME_RATE}
        )
    )
    camera.start()
    time.sleep(WARMUP_S)

    detector = cv2.QRCodeDetector()
    deadline = time.monotonic() + duration
    try:
        while time.monotonic() < deadline:
            frame = cv2.cvtColor(camera.capture_array(), cv2.COLOR_RGB2BGR)
            data, _, _ = detector.detectAndDecode(frame)
            if data:
                return data
        return None
    finally:
        camera.stop()
        camera.close()


def main() -> str:
    return scan_qr_code() or NO_QR_CODE_MESSAGE


if __name__ == "__main__":
    print(main())

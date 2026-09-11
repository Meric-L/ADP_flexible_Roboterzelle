"""QR-Code-Erkennung ueber die mit dem Livestream geteilte Kamera.

Ersetzt den frueheren Subprozess-Job (`src/jobs/take_image.py`): Livestream
und QR-Erkennung brauchen dieselbe physische Kamera, ein zweiter eigener
Picamera2-Open waere fehlgeschlagen (siehe `camera.py`). Diese Quelle liest
deshalb nur noch aus der `SharedCamera` mit, statt die Hardware selbst
anzusprechen.
"""

import asyncio
import time
from typing import Any

from ..camera import SharedCamera
from ..profiles import CameraStreamConfig
from .base import Detection, DetectionRequest, DetectionSource

NO_QR_CODE_MESSAGE = "Kein QR Code gefunden"
POLL_INTERVAL_S = 0.05


class ImageRecognitionDetectionSource(DetectionSource):
    """Sucht bis zu `qr_scan_duration_s` lang einen QR-Code im Kamerabild."""

    profile_id = "image_recognition"
    is_simulated = False
    frame_id = "world"

    def __init__(self, config: CameraStreamConfig, camera: SharedCamera | None = None) -> None:
        self._config = config
        #: Von aussen uebergeben, wenn `camera_stream.py` denselben Handle
        #: fuer den Node-Publisher braucht (siehe `runner.py`).
        self.camera = camera if camera is not None else SharedCamera(config)
        self._detector: Any = None

    async def open(self) -> None:
        import cv2

        self._detector = cv2.QRCodeDetector()
        await self.camera.open()

    async def close(self) -> None:
        await self.camera.close()
        await self.shutdown_executor()

    async def acquire_and_detect(self, request: DetectionRequest) -> list[Detection]:
        message = await self._scan_qr_code()
        return [
            Detection(
                module_id=self.profile_id.upper(),
                instance_id="det-1",
                position=(0.0, 0.0, 0.0),
                orientation=(0.0, 0.0, 0.0, 1.0),
                confidence=1.0,
                attributes={"message": message, "recipeId": request.recipe_id},
            )
        ]

    async def _scan_qr_code(self) -> str:
        """Wertet neue Frames aus der geteilten Kamera aus, bis einer decodiert.

        Ueberspringt Frames, die seit dem letzten Durchlauf nicht neu sind
        (Zeitstempel-Vergleich) — sonst wuerde derselbe Frame mehrfach teuer
        decodiert, waehrend die Kamera noch am naechsten Capture arbeitet.
        """
        deadline = time.monotonic() + self._config.qr_scan_duration_s
        seen_timestamp: float | None = None
        while time.monotonic() < deadline:
            frame = self.camera.latest_frame
            if frame is not None and frame.timestamp != seen_timestamp:
                seen_timestamp = frame.timestamp
                data, _, _ = await self.run_blocking(
                    self._detector.detectAndDecode, frame.image
                )
                if data:
                    return data
            await asyncio.sleep(POLL_INTERVAL_S)
        return NO_QR_CODE_MESSAGE

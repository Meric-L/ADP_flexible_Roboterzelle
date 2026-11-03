"""Bilder der geteilten Kamera holen: Frische, Kalibrierung zur Bildgroesse, Overlay-Stand.

`CaptureMixin` ist der Teil der AprilTag-Quelle, den alle Messpfade (Job,
MeasureTags, MeasureBoard, Bezugskanten, Zellen-Ueberwachung) gemeinsam
brauchen. Er erwartet am Objekt `camera`, `_config`, `_calibration` und
`latest_tags`; die setzt `AprilTagDetectionSource.__init__`.
"""

import asyncio
import time
from datetime import datetime, timedelta, timezone

from ...errors import VisionErrorCode, VisionJobError
from .snapshot import TagSnapshot

POLL_INTERVAL_S = 0.02


def captured_at(frame) -> datetime:
    """Aufnahmezeit eines Kamerabilds in UTC.

    `frame.timestamp` ist die Loop-Zeit beim Eintreffen des Bilds; hier wird
    sie ueber das Alter auf die Wanduhr umgerechnet -- der Zeitpunkt des
    Bilds, nicht der des Aufrufs. Nur im Event-Loop aufrufen.
    """
    age_s = asyncio.get_running_loop().time() - frame.timestamp
    return datetime.now(timezone.utc) - timedelta(seconds=age_s)


class CaptureMixin:
    """Auf frische Bilder warten und sie passend zur Kalibrierung auswerten."""

    def _latest_timestamp(self):
        """Zeitstempel des neuesten schon vorhandenen Bilds, oder `None`.

        Wer nur Bilder **nach** dem Aufruf will, uebergibt ihn als
        `seen_timestamp` an `_next_frame`: Sonst kaeme als erstes das Bild, das
        schon vor dem Aufruf im Puffer lag -- bis zu eine Bildperiode alt.
        """
        latest = self.camera.latest_frame
        return latest.timestamp if latest is not None else None

    async def _next_frame(self, seen_timestamp: float | None):
        """Wartet auf ein Bild, das neuer ist als das zuletzt verarbeitete.

        Mit eigenem Timeout, denn der Job-Timeout des Runners kann einen
        haengenden Worker-Thread nicht beenden (siehe `base.run_blocking`).
        """
        deadline = time.monotonic() + self._config.capture_timeout_s
        while time.monotonic() < deadline:
            frame = self.camera.latest_frame
            if frame is not None and frame.timestamp != seen_timestamp:
                return frame
            await asyncio.sleep(POLL_INTERVAL_S)
        raise VisionJobError(
            VisionErrorCode.DETECTION_FAILED,
            f"Kein Kamerabild innerhalb von {self._config.capture_timeout_s:.1f} s",
        )

    def _calibration_for(self, image):
        """Die Kalibrierung, passend zur Aufloesung dieses Bilds."""
        from tagloc import frames as frame_tools
        from tagloc.calibration import check_resolution, scale_to_resolution

        size = frame_tools.image_size(image)
        calibration = self._calibration
        if tuple(calibration.image_size) != tuple(size):
            if not self._config.allow_resolution_mismatch:
                check_resolution(calibration, size)
            calibration = scale_to_resolution(calibration, size)
        return calibration

    def _remember_tags(self, tag_poses, image, origin: str) -> None:
        """Haelt `tag_poses` als neuestes Ergebnis fuers Livestream-Overlay fest."""
        from tagloc import frames as frame_tools

        self.latest_tags = TagSnapshot(
            tag_poses=tuple(tag_poses),
            image_size=tuple(frame_tools.image_size(image)),
            origin=origin,
            taken_at=time.monotonic(),
        )

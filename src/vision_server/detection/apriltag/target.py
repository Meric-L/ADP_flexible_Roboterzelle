"""Die Zielscheibe messen: `MeasureTarget` (Visual Servoing beim Ablegen).

`TargetMeasureMixin` liefert `T_cam_sheet` aus frischen Bildern, ohne
Seiteneffekte. Er erwartet am Objekt `_config`, `_calibration` sowie
`intrinsics_source` und die Methoden von `CaptureMixin`.
"""

from ...errors import VisionJobError
from .capture import captured_at


class TargetMeasureMixin:
    """Zielscheibe in Kamerakoordinaten messen."""

    async def measure_target(self, request) -> dict:
        """`MeasureTarget`: Blattpose, ueber frische Bilder gemittelt.

        Nur Bilder, die **nach** dem Aufruf eintreffen: eines aus der Fahrt
        waere eine Messung an der falschen Stelle.
        """
        from ...measuretarget import NO_FRAME, TargetFrame, build_result

        seen_timestamp = self._latest_timestamp()
        frames: list[TargetFrame] = []
        error = None
        for _ in range(request.frames):
            try:
                frame = await self._next_frame(seen_timestamp)
            except VisionJobError:
                error = NO_FRAME
                break
            seen_timestamp = frame.timestamp
            sheet, image_size = await self.run_blocking(self._sheet_in_camera, frame.image)
            frames.append(TargetFrame(captured_at(frame), image_size, sheet))
        return build_result(
            frames,
            frames_requested=request.frames,
            frame_id=self._config.frame_id,
            intrinsics_source=self.intrinsics_source,
            calibration_id=getattr(self._calibration, "calibration_id", ""),
            error=error if not frames else None,
        )

    def _sheet_in_camera(self, image):
        """Ein Bild: `(measure_sheet-Ergebnis, Bildgroesse)`. Worker-Thread."""
        from tagloc import frames as frame_tools
        from use_case_eval.measure import measure_sheet

        return measure_sheet(image, self._calibration_for(image)), frame_tools.image_size(image)

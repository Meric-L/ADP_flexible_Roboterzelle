"""Kalibrier-Schachbrett messen: `measure_board` (Hand-Auge) und `MeasureBoard` (Zentrieren).

`BoardMeasureMixin` findet das Brett im Kamerabild und liefert seine Pose
(`measure_board`) bzw. die Zentrierhilfe samt Teilraster (`measure_board_view`).
Beides seiteneffektfrei. Er erwartet am Objekt `_config`, `_calibration`
sowie `intrinsics_source` und die Methoden von `CaptureMixin`.
"""

from dataclasses import replace
from typing import Any

from ...errors import VisionJobError
from .capture import captured_at

#: `MeasureBoard` sucht das Brett auf einem Bild dieser laengsten Kante (siehe `_observe_board`).
OBSERVE_MAX_SIDE = 640


class BoardMeasureMixin:
    """Das Schachbrett im Kamerabild messen."""

    async def measure_board(self, spec) -> tuple[Any | None, str, dict]:
        """`T_cam_board` eines Schachbretts aus `samples_per_job` frischen Bildern.

        Wie `measure_tag`, nur mit einem Kalibrier-Schachbrett als festem
        Ziel: viele Ecken statt vier, also eine genauere und eindeutige Pose.
        Liefert `(Pose, "", Info)` oder `(None, Grund, Info)`. `Info` hat den
        mittleren `reprojectionErrorPx` (auch fuer verworfene Bilder). Bei Erfolg
        zusaetzlich fuer die Hand-Auge-Verfeinerung und den Datensatz:
        `corners` (kanonische Ecken `(N, 2)` im verzerrten Bild, Mittel ueber
        die verwendeten Bilder -- der Roboter steht), `calibration` (die dazu
        verwendete Intrinsik), `intrinsicsSource` und `image` (das letzte
        verwendete Bild).
        """
        import numpy as np

        from tagloc.tagmap import merge_tag_poses

        poses = []
        corner_sets = []
        errors: list[float] = []
        reasons: list[str] = []
        calibration = None
        last_image = None
        seen_timestamp = self._latest_timestamp()
        samples = max(1, self._config.samples_per_job)
        for _ in range(samples):
            try:
                frame = await self._next_frame(seen_timestamp)
            except VisionJobError as error:
                return None, error.message, {"reprojectionErrorPx": None}
            seen_timestamp = frame.timestamp
            pose, reason, error_px, corners, used_calibration = await self.run_blocking(
                self._measure_board, frame.image, spec
            )
            if error_px is not None:
                errors.append(error_px)
            if pose is None:
                reasons.append(reason)
                continue
            poses.append(pose)
            corner_sets.append(corners)
            calibration, last_image = used_calibration, frame.image
        info = {"reprojectionErrorPx": sum(errors) / len(errors) if errors else None}
        if poses:
            info.update(
                corners=np.mean(np.stack(corner_sets), axis=0),
                calibration=calibration,
                intrinsicsSource=self.intrinsics_source,
                image=last_image,
            )
            return merge_tag_poses(poses), "", info
        return None, reasons[-1] if reasons else "Board nicht gesehen", info

    def _measure_board(self, image, spec):
        """Ein Bild, ein Schachbrett:
        `(Pose | None, Grund, Reprojektionsfehler | None, Ecken | None,
        Kalibrierung | None)`. Die Ecken sind kanonisch geordnet, im
        verzerrten Bild. Worker-Thread."""
        from tagloc import frames as frame_tools
        from tagloc.boards import (
            canonical_chessboard_corners,
            detect_board,
            estimate_board_pose,
        )

        gray = frame_tools.to_gray(image)
        sample = detect_board(gray, spec)
        if sample is None:
            return None, "Board nicht (vollstaendig) gesehen -- alle Ecken muessen im Bild sein", None, None, None
        corners = canonical_chessboard_corners(gray, sample.corners, spec)
        calibration = self._calibration_for(image)
        pose, error_px = estimate_board_pose(corners, spec, calibration)
        # Ein Board mit Dutzenden Ecken passt viel besser als ein Tag;
        # ein grosser Fehler heisst Unschaerfe, ein welliges Brett oder ein
        # falsches Raster.
        if error_px > self._config.max_reproj_error_px:
            return None, (
                f"Board-Pose passt schlecht ({error_px:.1f} px) -- Feldgroesse und "
                "Eckenzahl pruefen"
            ), error_px, None, None
        return pose, "", error_px, corners, calibration

    def default_board_spec(self):
        """Das Brett aus der Konfiguration der Kamera."""
        from tagloc.boards import BoardSpec

        return BoardSpec(
            type=self._config.calibration_board_type,
            cols=self._config.calibration_board_cols,
            rows=self._config.calibration_board_rows,
            square_size_m=self._config.calibration_board_square_size_m,
            marker_size_m=self._config.calibration_board_marker_size_m,
            dictionary=self._config.calibration_board_dictionary,
        )

    async def measure_board_view(self, request) -> dict:
        """`MeasureBoard`: das Brett fuer das Zentrieren messen, ohne Fahrbefehle.

        Nur Bilder nach dem Aufruf. Ganzes Brett: Pose und Ecken. Nur teilweise
        sichtbar: Schwerpunkt und Bildraender eines Teilrasters. Siehe
        `vision_server.measureboard`. Seiteneffektfrei.
        """
        from ...measureboard import NO_FRAME, build_result

        spec = request.board or self.default_board_spec()
        seen_timestamp = self._latest_timestamp()
        frames = []
        error = None
        last_image = None
        for index in range(request.frames):
            try:
                frame = await self._next_frame(seen_timestamp)
            except VisionJobError:
                error = NO_FRAME
                break
            seen_timestamp = frame.timestamp
            last_image = frame.image
            # Das Teilraster braucht nur das letzte Bild -- und die Suche ist der
            # teuerste Teil, wenn das Brett nicht ganz im Bild ist.
            observed = await self.run_blocking(
                self._observe_board, frame.image, spec, index == request.frames - 1
            )
            frames.append(replace(observed, captured_at=captured_at(frame)))
        diagnosis = None
        if request.diagnose and last_image is not None:
            diagnosis = await self.run_blocking(self._diagnose_board, last_image, spec)
        result = build_result(
            frames,
            spec=spec,
            frames_requested=request.frames,
            frame_id=self._config.frame_id,
            intrinsics_source=self.intrinsics_source,
            calibration_id=getattr(self._calibration, "calibration_id", ""),
            max_error_px=self._config.max_reproj_error_px,
            error=error if not frames else None,
        )
        if diagnosis is not None:
            result["diagnosis"] = diagnosis
        return result

    def _diagnose_board(self, image, spec):
        """Erkennungsversuche am letzten Bild, nur auf Anfrage. Worker-Thread."""
        from tagloc import frames as frame_tools
        from tagloc.boards import diagnose_board

        return diagnose_board(frame_tools.to_gray(image), spec)

    def _observe_board(self, image, spec, with_partial: bool = True):
        """Ein Bild fuer `MeasureBoard`: ganzes Brett, Teilraster oder nichts. Worker-Thread.

        Gesucht wird auf einem auf `OBSERVE_MAX_SIDE` (laengste Kante) verkleinerten Bild, damit die
        Antwort auch ohne Brett im Bild schnell kommt (ein Fehlversuch kostet am
        vollen Bild Sekunden, am Pi ein Vielfaches). Die Ecken werden danach im
        vollen Bild nachgeschaerft."""
        from datetime import datetime, timezone

        from tagloc import frames as frame_tools
        from tagloc.boards import (
            canonical_chessboard_corners,
            detect_board,
            detect_partial_board,
            estimate_board_pose,
        )

        from ...measureboard import FrameBoard

        gray = frame_tools.to_gray(image)
        size = frame_tools.image_size(image)
        now = datetime.now(timezone.utc)
        sample = detect_board(gray, spec, max_side=OBSERVE_MAX_SIDE)
        if sample is None:
            partial = detect_partial_board(gray, spec, max_side=OBSERVE_MAX_SIDE) if with_partial else None
            return FrameBoard(now, size, partial=partial)
        corners = canonical_chessboard_corners(gray, sample.corners, spec)
        pose, error_px = estimate_board_pose(corners, spec, self._calibration_for(image))
        return FrameBoard(
            now, size, complete=True, pose=pose, reprojection_error_px=error_px, corners=corners
        )

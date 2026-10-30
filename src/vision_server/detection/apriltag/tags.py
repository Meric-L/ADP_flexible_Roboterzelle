"""Einzelne Tags messen, ohne Welt-KS: `MeasureTags` und `measure_tag` (Hand-Auge).

`TagMeasureMixin` liefert Tag-Posen im Kamera-KS aus frischen Bildern -- ohne
Karten-Versatz, ohne Anker, ohne Seiteneffekte ausser dem Overlay-Stand
(`latest_tags`). Er erwartet am Objekt `_config`, `_detector`, `_calibration`,
`_tag_map` sowie `intrinsics_source` und die Methoden von `CaptureMixin`.
"""

import math
from typing import Any

from ...errors import VisionJobError
from .capture import captured_at


class TagMeasureMixin:
    """Tags in Kamerakoordinaten messen."""

    async def measure_tags(self, request) -> dict:
        """`MeasureTags`: Tags in Kamerakoordinaten, ueber frische Bilder gemittelt.

        Nur Bilder, die **nach** dem Aufruf eintreffen -- ein Bild aus der
        Fahrt, das noch im Puffer liegt, waere eine Messung an der falschen
        Stelle. Ohne Welt-KS, ohne CAD-Versatz, ohne Anker: genau das, was die
        Regelung der Steuerung braucht. Seiteneffektfrei.
        """
        from ...measuretag import NO_FRAME, FrameMeasurement, SizeLookup, build_result

        sizes = SizeLookup(request.sizes_m, self._tag_map, self._config.tag_size_m)
        seen_timestamp = self._latest_timestamp()
        frames: list[FrameMeasurement] = []
        error = None
        for _ in range(request.frames):
            try:
                frame = await self._next_frame(seen_timestamp)
            except VisionJobError:
                error = NO_FRAME
                break
            seen_timestamp = frame.timestamp
            tag_poses, image_size = await self.run_blocking(
                self._detect_in_camera, frame.image, sizes, request.tag_ids
            )
            # Zeitpunkt des Bilds, nicht des Aufrufs.
            frames.append(FrameMeasurement(captured_at(frame), image_size, tuple(tag_poses)))
        return build_result(
            frames,
            frames_requested=request.frames,
            frame_id=self._config.frame_id,
            sizes=sizes,
            intrinsics_source=self.intrinsics_source,
            calibration_id=getattr(self._calibration, "calibration_id", ""),
            error=error if not frames else None,
        )

    def _detect_in_camera(self, image, sizes, tag_ids):
        """Ein Bild: `(TagPosen, Bildgroesse)`, auf `tag_ids` gefiltert. Worker-Thread."""
        from tagloc import frames as frame_tools
        from tagloc.pose import estimate_tag_poses

        tag_poses = estimate_tag_poses(
            self._detector.detect(frame_tools.to_gray(image)),
            self._calibration_for(image),
            tag_map=sizes,
            default_size_m=self._config.tag_size_m,
            max_reprojection_error_px=self._config.max_reproj_error_px,
        )
        self._remember_tags(tag_poses, image, "measure")
        if tag_ids is not None:
            tag_poses = [tag_pose for tag_pose in tag_poses if tag_pose.tag_id in tag_ids]
        return tag_poses, frame_tools.image_size(image)

    async def measure_tag(self, tag_id: int) -> tuple[Any | None, str, dict]:
        """`T_cam_tag` eines Tags aus `samples_per_job` frischen Bildern.

        Fuer die Hand-Auge-Kalibrierung: rein im Kamera-KS, ohne Tag-Map-
        Versatz und ohne Welt-KS -- genau die Groesse, die `solve_hand_eye`
        braucht. Liefert `(Pose, "", Info)` oder `(None, Grund, Info)`; `Info`
        hat den mittleren `reprojectionErrorPx` aller Bilder mit dem Tag.
        """
        from tagloc.tagmap import merge_tag_poses

        poses = []
        errors = []
        ambiguous = 0
        seen_timestamp = self._latest_timestamp()
        samples = max(1, self._config.samples_per_job)
        for _ in range(samples):
            try:
                frame = await self._next_frame(seen_timestamp)
            except VisionJobError as error:
                return None, error.message, {"reprojectionErrorPx": None}
            seen_timestamp = frame.timestamp
            tag_pose = await self.run_blocking(self._measure_tag, frame.image, tag_id)
            if tag_pose is None:
                continue
            if math.isfinite(tag_pose.reprojection_error_px):
                errors.append(tag_pose.reprojection_error_px)
            if tag_pose.is_ambiguous:
                # Zwei gleich gute Loesungen: welche stimmt, weiss niemand --
                # eine falsch gekippte Messung verdirbt die ganze Kalibrierung.
                ambiguous += 1
                continue
            poses.append(tag_pose.pose_cam_tag)
        info = {"reprojectionErrorPx": sum(errors) / len(errors) if errors else None}
        if poses:
            return merge_tag_poses(poses), "", info
        if ambiguous:
            return None, (
                f"Tag {tag_id} mehrdeutig ({ambiguous} von {samples} Bildern) -- "
                "schraeger auf den Tag schauen"
            ), info
        return None, f"Tag {tag_id} nicht gesehen", info

    def _measure_tag(self, image, tag_id: int):
        """Ein Bild, ein Tag. Laeuft im Worker-Thread."""
        from tagloc import frames as frame_tools
        from tagloc.pose import estimate_tag_poses

        tag_poses = estimate_tag_poses(
            self._detector.detect(frame_tools.to_gray(image)),
            self._calibration_for(image),
            tag_map=self._tag_map,
            default_size_m=self._config.tag_size_m,
            max_reprojection_error_px=self._config.max_reproj_error_px,
        )
        self._remember_tags(tag_poses, image, "measure")
        return next((pose for pose in tag_poses if pose.tag_id == tag_id), None)

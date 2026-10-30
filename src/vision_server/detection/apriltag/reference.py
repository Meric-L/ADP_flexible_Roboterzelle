"""Tag-Position ueber Bezugskanten einmessen: `MeasureTagReference` und `PlanTagReferenceView`.

`ReferenceMixin` misst die Lage eines Tags zu den Kanten einer Flaeche im
Rohbild (`tagloc.reference_edges`) und plant die Kamerapose, aus der Tag und
Kanten ins Bild passen. Er erwartet am Objekt `_config`, `_calibration`,
`_tag_map`, `reference_debug_sink` sowie `intrinsics_source`, `_detect_in_camera`
(aus `TagMeasureMixin`) und die Methoden von `CaptureMixin`.
"""

import logging

from ...errors import VisionJobError
from .capture import captured_at

_log = logging.getLogger(__package__)


class ReferenceMixin:
    """Bezugskanten-Einmessung eines Tags."""

    async def measure_tag_reference(self, request, artifacts: dict | None = None) -> dict:
        """`MeasureTagReference`: Tag-Position ueber Bezugskanten einmessen.

        Wie `measure_tags`: nur Bilder, die **nach** dem Aufruf eintreffen,
        seiteneffektfrei, ohne Welt-KS. Je Bild wird der Tag (`request.tag_id`)
        erkannt; aus den Bildern mit Tag entsteht eine gemeinsame Tag-Pose (der
        Roboter steht). Darauf misst `measure_reference_lines` die Kanten im
        Rohbild, `fit_in_plane_correction` rechnet je Bild die Korrektur,
        `average_corrections` mittelt sie.

        Mehrdeutiger Tag: Gibt es in mindestens einem Bild eine eindeutige
        Tag-Pose, gilt deren gemittelte Drehung fuer alle Bilder (der Roboter
        steht, die Pose ist dieselbe). Ist **kein** Bild eindeutig, ist die
        Drehung unbelastbar (Spiegelloesung): die Korrektur uebernimmt dann nur
        die Position, die Drehung bleibt aus dem Startwert
        (`tagreference.keep_guess_rotation`), und es gibt eine Warnung.

        Fehler (`error`): `NO_FRAME` (kein frisches Bild) bzw. `TAG_NOT_SEEN`
        (Tag in keinem Bild). Nicht messbare Kanten sind kein Fehler, sie
        senken nur `dofs`.

        `artifacts`: ist es ein dict, fuellt die Methode es fuer das
        Mess-Protokoll: `frames` (Rohbilder mit Tag) und `overlay` (Debug-
        Ansicht des letzten Bilds, BGR, volle Aufloesung; fehlt, wenn die
        Kantensuche nicht lief).
        """
        from ...tagreference import NO_FRAME, TAG_NOT_SEEN, build_result

        sizes = self._reference_sizes(request)
        size_m = sizes.size_for(request.tag_id)
        captured = await self._capture_reference_frames(request, sizes)

        common = {
            "captured_at": captured[0][2] if captured else None,
            "captured_until": captured[-1][2] if captured else None,
            "image_size": captured[-1][3] if captured else None,
            "intrinsics_source": self.intrinsics_source,
        }
        seen = [item for item in captured if item[1] is not None]
        if not captured:
            return build_result(**common, error=NO_FRAME)
        if not seen:
            return build_result(**common, error=TAG_NOT_SEEN)
        return await self._reference_result(request, seen, common, size_m, artifacts)

    def _reference_sizes(self, request):
        """Tag-Groessen fuer die Einmessung: die der Anfrage, sonst Karte, sonst Standard."""
        from ...measuretag import SizeLookup

        return SizeLookup(
            {request.tag_id: request.tag_size_m} if request.tag_size_m else {},
            self._tag_map,
            self._config.tag_size_m,
        )

    async def _capture_reference_frames(self, request, sizes) -> list[tuple]:
        """Frische Bilder holen und den Tag darin suchen.

        Liefert je Bild `(Bild, TagPose | None, Aufnahmezeit, Bildgroesse)`.
        Bricht ohne weiteres Bild still ab; die Aufrufer unterscheiden
        `NO_FRAME` (keines) und `TAG_NOT_SEEN` (keines mit Tag).
        """
        seen_timestamp = self._latest_timestamp()
        captured: list[tuple] = []
        for _ in range(request.frames):
            try:
                frame = await self._next_frame(seen_timestamp)
            except VisionJobError:
                break
            seen_timestamp = frame.timestamp
            tag_poses, image_size = await self.run_blocking(
                self._detect_in_camera, frame.image, sizes, frozenset({request.tag_id})
            )
            tag_pose = tag_poses[0] if tag_poses else None
            captured.append((frame.image, tag_pose, captured_at(frame), image_size))
        return captured

    async def _reference_result(self, request, seen, common, size_m, artifacts) -> dict:
        """Aus den Bildern mit Tag Kanten und Korrektur messen und das Ergebnis bauen."""
        from tagloc.reference_edges import average_corrections

        from ...tagreference import (
            build_result,
            combine_tag_pose,
            reference_warnings,
            summarize_lines,
        )

        tag_poses = [item[1] for item in seen]
        tag_pose, tag_ambiguous = combine_tag_pose(tag_poses)
        if artifacts is not None:
            artifacts["frames"] = [item[0] for item in seen]
        line_frames, corrections = await self.run_blocking(
            self._measure_reference,
            [item[0] for item in seen],
            tag_pose,
            tag_ambiguous,
            request,
            size_m,
            artifacts,
        )
        correction, spread = average_corrections(corrections)
        warnings = reference_warnings(
            correction=correction,
            lines=summarize_lines(line_frames),
            search_band_m=request.search_band_m,
            tag_ambiguous=tag_ambiguous,
            intrinsics_source=self.intrinsics_source,
        )
        return build_result(
            **{**common, "captured_at": seen[0][2], "captured_until": seen[-1][2]},
            frames_used=len(seen),
            tag_poses=tag_poses,
            tag_pose=tag_pose,
            tag_ambiguous=tag_ambiguous,
            line_frames=line_frames,
            correction=correction,
            spread=spread,
            warnings=warnings,
        )

    def _measure_reference(
        self, images, tag_pose, tag_ambiguous, request, size_m, artifacts=None
    ):
        """Kanten und Korrektur je Bild: `(Messungen je Bild, Korrekturen je Bild)`. Worker-Thread."""
        from tagloc import frames as frame_tools
        from tagloc.reference_edges import fit_in_plane_correction, measure_reference_lines

        from ...tagreference import keep_guess_rotation

        guess = request.pose_module_tag_guess
        line_frames = []
        corrections = []
        want_debug = artifacts is not None or self.reference_debug_sink is not None
        for index, image in enumerate(images, start=1):
            debug: list | None = [] if want_debug else None
            calibration = self._calibration_for(image)
            measurements = measure_reference_lines(
                frame_tools.to_gray(image),
                calibration,
                tag_pose,
                guess,
                request.lines,
                tag_size_m=size_m,
                search_band_m=request.search_band_m,
                debug=debug,
            )
            if debug is not None:
                self._show_reference_debug(
                    debug, image, calibration, tag_pose, size_m, request, index, len(images), artifacts
                )
            correction = fit_in_plane_correction(measurements, guess)
            if tag_ambiguous:
                correction = keep_guess_rotation(correction, guess)
            line_frames.append(measurements)
            corrections.append(correction)
        return line_frames, corrections

    def _show_reference_debug(
        self, debug, image, calibration, tag_pose, size_m, request, index, total, artifacts
    ) -> None:
        """Gibt die Debug-Ansicht der Kantensuche an Livestream und Mess-Protokoll weiter.

        Der Livestream (`reference_debug_sink`) bekommt jedes Bild, das
        Mess-Protokoll (`artifacts["overlay"]`) nur das letzte. Ein Fehler hier
        wird geloggt und verschluckt: die Anzeige darf die Messung nie stoeren.
        """
        from tagloc import frames as frame_tools
        from tagloc.reference_edges import tag_outline_px
        from tagloc.reference_overlay import draw_reference_debug

        # Importe vor dem `try`: ein fehlendes Modul ist ein Installationsfehler
        # und soll auffallen, nicht als Anzeigefehler verschluckt werden.
        try:
            title = f"Tag {request.tag_id} - Messung Bild {index}/{total}"
            corners = tag_outline_px(calibration, tag_pose, size_m, 0.0)
            if self.reference_debug_sink is not None:
                self.reference_debug_sink(
                    debug, corners, frame_tools.image_size(image), title, request.overlay
                )
            if artifacts is not None and index == total:
                artifacts["overlay"] = draw_reference_debug(
                    image,
                    debug,
                    tag_corners_px=corners,
                    title=title,
                    layers=request.overlay,
                )
        except Exception:
            # Die Anzeige darf die Messung nie stoeren.
            _log.exception("Debug-Ansicht der Einmessung nicht erzeugt")

    def plan_tag_reference_view(self, request) -> dict:
        """`PlanTagReferenceView`: Kamerapose im Tag-KS, aus der Tag und Kanten ins Bild passen.

        Rechnet nur mit der Kalibrierung (Brennweite, Bildgroesse), ohne
        Kamerabild -- kein Warten, keine Seiteneffekte.
        """
        from tagloc.reference_edges import plan_reference_view

        from ...tagreference import build_view

        sizes = self._reference_sizes(request)
        plan = plan_reference_view(
            request.pose_module_tag_guess,
            request.lines,
            self._calibration,
            tag_size_m=sizes.size_for(request.tag_id),
            line_extent_m=request.line_extent_m,
            tilt_deg=request.tilt_deg,
        )
        return build_view(plan)

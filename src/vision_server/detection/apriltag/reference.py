"""Measure tag position against reference edges: ``MeasureTagReference`` and ``PlanTagReferenceView``.

``ReferenceMixin`` measures the position of a tag relative to the edges of a surface in
the raw image (``tagloc.reference_edges``), and plans the camera pose from which tag and
edges fit into the image. It expects the object to provide ``_config``, ``_calibration``,
``_tag_map``, ``reference_debug_sink``, ``intrinsics_source``, ``_detect_in_camera``
(from ``TagMeasureMixin``) and the methods of ``CaptureMixin``.
"""

import logging

from ...errors import VisionJobError
from .capture import captured_at

_log = logging.getLogger(__package__)


class ReferenceMixin:
    """Reference-edge measurement of a tag."""

    async def measure_tag_reference(self, request, artifacts: dict | None = None) -> dict:
        """``MeasureTagReference``: measure the tag position against reference edges.

        As with ``measure_tags``, only images that arrive **after** the call are used. The
        method has no side effects and does not use the world frame. Each image is searched
        for the tag (``request.tag_id``). The images containing the tag yield one common
        tag pose (the robot is standing still). On that, ``measure_reference_lines``
        measures the edges in the raw image, ``fit_in_plane_correction`` computes the
        correction per image, and ``average_corrections`` averages them.

        Ambiguous tag: if at least one image has an unambiguous tag pose, its averaged
        rotation applies to all images (the robot is standing still, so the pose is the
        same). If **no** image is unambiguous, the rotation is unreliable (mirror solution).
        Then the correction takes only the position, the rotation stays at the starting
        value (``tagreference.keep_guess_rotation``), and a warning is issued.

        Errors (``error``): ``NO_FRAME`` (no fresh image), or ``TAG_NOT_SEEN`` (tag in no
        image). Edges that cannot be measured are not an error; they only lower ``dofs``.

        Args:
            request: The measurement request.
            artifacts: If a dict, the method fills it for the measurement log: ``frames``
                (raw images with the tag) and ``overlay`` (debug view of the last image,
                BGR, full resolution; missing if the edge search did not run).

        Returns:
            The result dict built by ``tagreference.build_result``.
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
        """Return the tag sizes for the measurement: the request's, else the map's, else the default."""
        from ...measuretag import SizeLookup

        return SizeLookup(
            {request.tag_id: request.tag_size_m} if request.tag_size_m else {},
            self._tag_map,
            self._config.tag_size_m,
        )

    async def _capture_reference_frames(self, request, sizes) -> list[tuple]:
        """Fetch fresh images and search them for the tag.

        Returns, per image, ``(image, TagPose | None, capture_time, image_size)``. Stops
        quietly when no further image arrives. The callers distinguish ``NO_FRAME`` (none
        at all) from ``TAG_NOT_SEEN`` (none containing the tag).
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
        """Measure edges and correction from the images containing the tag, and build the result."""
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
        """Measure edges and correction per image.

        Returns ``(measurements per image, corrections per image)``. Runs in a worker
        thread.
        """
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
        """Pass the edge-search debug view to the live stream and the measurement log.

        The live stream (``reference_debug_sink``) gets every image. The measurement log
        (``artifacts["overlay"]``) gets only the last one. An error here is logged and
        swallowed: the display must never disturb the measurement.
        """
        from tagloc import frames as frame_tools
        from tagloc.reference_edges import tag_outline_px
        from tagloc.reference_overlay import draw_reference_debug

        # Imports come before the ``try``: a missing module is an installation error and
        # should surface, not be swallowed as a display error.
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
            # The display must never disturb the measurement.
            _log.exception("Debug-Ansicht der Einmessung nicht erzeugt")

    def plan_tag_reference_view(self, request) -> dict:
        """``PlanTagReferenceView``: plan the camera pose in the tag frame from which tag and edges fit the image.

        Computes only from the calibration (focal length, image size), without a camera
        image. No waiting, no side effects.
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

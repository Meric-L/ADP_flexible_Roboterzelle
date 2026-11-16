"""Measure single tags without the world CS: `MeasureTags` and `measure_tag` (hand-eye).

`TagMeasureMixin` provides tag poses in the camera CS from fresh images --
without map offset, without anchor, and without side effects apart from the
overlay state (`latest_tags`). It expects `_config`, `_detector`, `_calibration`
and `_tag_map` on the object, as well as `intrinsics_source` and the methods of
`CaptureMixin`.
"""

import math
from typing import Any

from ...errors import VisionJobError
from .capture import captured_at


class TagMeasureMixin:
    """Measure tags in camera coordinates."""

    async def measure_tags(self, request) -> dict:
        """`MeasureTags`: tags in camera coordinates, averaged over fresh images.

        Only images that arrive **after** the call are used. An image from the
        drive that is still in the buffer would be a measurement at the wrong
        place. No world CS, no CAD offset, no anchor: exactly what the control
        loop of the machine controller needs. Side-effect free.
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
            # Time of the image, not of the call.
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
        """One image: ``(tag poses, image size)``, filtered to `tag_ids`. Runs in the worker thread."""
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
        """Return the ``T_cam_tag`` of a tag from `samples_per_job` fresh images.

        For hand-eye calibration: purely in the camera CS, without tag-map offset
        and without world CS -- exactly the quantity `solve_hand_eye` needs.
        Returns `(Pose, "", Info)` or `(None, reason, Info)`. `Info` holds the mean
        `reprojectionErrorPx` over all images that show the tag.
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
                # Two equally good solutions: nobody knows which one is right.
                # One wrongly tilted measurement would spoil the whole calibration.
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
        """One image, one tag. Runs in the worker thread."""
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

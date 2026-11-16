"""Measure the calibration chessboard: `measure_board` (hand-eye) and `MeasureBoard` (centering).

`BoardMeasureMixin` finds the board in the camera image and returns its pose
(`measure_board`), or the centering aid with the partial grid
(`measure_board_view`). Both are free of side effects. It expects the object to
provide `_config`, `_calibration`, `intrinsics_source` and the methods of
`CaptureMixin`.
"""

from dataclasses import replace
from typing import Any

from ...errors import VisionJobError
from .capture import captured_at

#: `MeasureBoard` searches for the board on an image with this longest edge (see `_observe_board`).
OBSERVE_MAX_SIDE = 640


class BoardMeasureMixin:
    """Measure the chessboard in the camera image."""

    async def measure_board(self, spec, *, samples: int | None = None) -> tuple[Any | None, str, dict]:
        """Return `T_cam_board` of a chessboard from `samples_per_job` fresh images.

        Like `measure_tag`, but with a calibration chessboard as the fixed
        target: many corners instead of four, so a more accurate and unambiguous
        pose. Returns `(Pose, "", Info)` or `(None, reason, Info)`. `Info` holds
        the mean `reprojectionErrorPx` (also for rejected images). On success it
        also holds the data for the hand-eye refinement and the dataset:
        `corners` (canonical corners `(N, 2)` in the distorted image, averaged
        over the images used -- the robot is standing still), `calibration` (the
        intrinsics used for it), `intrinsicsSource` and `image` (the last image
        used).
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
        samples = max(1, self._config.samples_per_job if samples is None else samples)
        for _ in range(samples):
            try:
                frame = await self._next_frame(seen_timestamp)
            except VisionJobError as error:
                return None, error.message, {"reprojectionErrorPx": None}
            seen_timestamp = frame.timestamp
            last_image = frame.image
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
            calibration = used_calibration
        info = {"reprojectionErrorPx": sum(errors) / len(errors) if errors else None,
                "image": last_image, "capturedAt": captured_at(frame).isoformat() if last_image is not None else None,
                "calibration": calibration if calibration is not None else
                (self._calibration_for(last_image) if last_image is not None else None),
                "intrinsicsSource": self.intrinsics_source}
        if poses:
            info.update(
                corners=(corner_sets[-1] if spec.type == "charuco" else np.mean(np.stack(corner_sets), axis=0)),
                calibration=calibration,
                intrinsicsSource=self.intrinsics_source,
                image=last_image,
            )
            return merge_tag_poses(poses), "", info
        return None, reasons[-1] if reasons else "Board nicht gesehen", info

    async def capture_board_raw(self, spec):
        """Capture exactly one fresh raw image with an online check."""
        return await self.measure_board(spec, samples=1)

    def _measure_board(self, image, spec):
        """Process one image with one chessboard.

        Returns `(pose | None, reason, reprojection error | None,
        corners | None, calibration | None)`. The corners are in canonical
        order, in the distorted image. Runs in a worker thread."""
        from tagloc import frames as frame_tools
        from tagloc.boards import (
            CHARUCO,
            canonical_chessboard_corners,
            detect_board,
            estimate_board_pose,
        )

        gray = frame_tools.to_gray(image)
        sample = detect_board(gray, spec)
        if sample is None:
            reason = ("ChArUco-Brett nicht erkannt" if spec.type == CHARUCO else
                      "Schachbrett nicht vollstaendig gesehen -- alle Ecken muessen im Bild sein")
            return None, reason, None, None, None
        if spec.type == CHARUCO:
            if sample.count() < 25:
                return None, f"Nur {sample.count()} ChArUco-Ecken erkannt (mindestens 25)", None, None, None
            corners = sample.corners.reshape(-1, 2)
        else:
            corners = canonical_chessboard_corners(gray, sample.corners, spec)
        calibration = self._calibration_for(image)
        pose, error_px = estimate_board_pose(corners, spec, calibration,
                                             ids=sample.ids if spec.type == CHARUCO else None)
        # A board with dozens of corners fits much better than a tag; a large
        # error means blur, a warped board, or a wrong grid.
        if error_px > self._config.max_reproj_error_px:
            return None, (
                f"Board-Pose passt schlecht ({error_px:.1f} px) -- Feldgroesse und "
                "Eckenzahl pruefen"
            ), error_px, None, None
        return pose, "", error_px, corners, calibration

    def default_board_spec(self):
        """Return the board from the camera configuration."""
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
        """`MeasureBoard`: measure the board for centering, without motion commands.

        Only images taken after the call. Whole board: pose and corners. Only
        partly visible: centroid and image edges of a partial grid. See
        `vision_server.measureboard`. Free of side effects.
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
            # The partial grid only needs the last image -- and the search is the
            # most expensive part when the board is not fully in the image.
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
        """Run detection attempts on the last image, only on request. Runs in a worker thread."""
        from tagloc import frames as frame_tools
        from tagloc.boards import diagnose_board

        return diagnose_board(frame_tools.to_gray(image), spec)

    def _observe_board(self, image, spec, with_partial: bool = True):
        """Process one image for `MeasureBoard`: whole board, partial grid or nothing.

        Searches on an image scaled down to `OBSERVE_MAX_SIDE` (longest edge), so
        the answer comes quickly even without a board in view (a failed attempt
        costs seconds on the full image, many times more on the Pi). The corners
        are then refined on the full image. Runs in a worker thread."""
        from datetime import datetime, timezone

        from tagloc import frames as frame_tools
        from tagloc.boards import (
            CHARUCO,
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
            partial = detect_partial_board(gray, spec, max_side=OBSERVE_MAX_SIDE) if with_partial and spec.type != CHARUCO else None
            return FrameBoard(now, size, partial=partial)
        if spec.type == CHARUCO:
            corners = sample.corners.reshape(-1, 2)
            if sample.count() < 25:
                return FrameBoard(now, size, partial=(corners, spec.cols - 1, spec.rows - 1))
        else:
            corners = canonical_chessboard_corners(gray, sample.corners, spec)
        pose, error_px = estimate_board_pose(corners, spec, self._calibration_for(image),
                                             ids=sample.ids if spec.type == CHARUCO else None)
        return FrameBoard(
            now, size, complete=True, pose=pose, reprojection_error_px=error_px, corners=corners
        )

"""MeasureBoard: measure the calibration chessboard before the hand-eye capture.

For centering: the controller moves the robot over the board, calls `MeasureBoard`, and
corrects until the whole board is centered in the image. Only then does it record
`CaptureHandEyeSample`. Like `MeasureTags`: the Pi only measures and sends no motion
commands.

* **Whole board in the image:** `complete`, with `T_cam_board` (camera frame as in
  OpenCV), the corners in pixels and the reprojection error.
* **Board only partly in the image:** detection needs the whole grid. A smaller sub-grid
  does fit into any visible part, however. From that come the centroid of the found
  corners in pixels and the image edges they lie close to -- enough for centering, but
  no board pose: the origin of a sub-grid is arbitrary.
* **Nothing found:** `found: false`, no error.

There is no asyncua and no camera here. See
`doc/project_docs/work-plans/hand-eye-board-centring.md`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from tagloc.jsonio import finite_or_none

from .measuretag import DEFAULT_FRAMES, FRAME_CONVENTION, MAX_FRAMES, NO_FRAME, near_edge, view_angle_deg

REQUEST_SCHEMA = "wsc.vision.measureboard.request/1"
RESULT_SCHEMA = "wsc.vision.measureboard.result/1"



@dataclass(frozen=True)
class BoardRequest:
    #: `None` = the board size from the camera configuration.
    board: object | None
    frames: int = DEFAULT_FRAMES
    #: Troubleshooting: attach `diagnosis` to the result (costs some detection attempts).
    diagnose: bool = False


@dataclass(frozen=True)
class FrameBoard:
    """One evaluated image."""

    captured_at: datetime
    image_size: tuple[int, int]
    #: Whole board found.
    complete: bool = False
    pose: object | None = None
    reprojection_error_px: float | None = None
    #: All corners in fixed order, raw (distorted), `(N, 2)`; only when `complete`.
    corners: object | None = None
    #: Sub-grid: `(corners (N, 2), cols, rows)`; only when not `complete`.
    partial: tuple | None = None


def parse_request(text: str, parse_board) -> BoardRequest:
    """Parse `wsc.vision.measureboard.request/1`; raises ValueError with plain text.

    `parse_board` is the board parser of the hand-eye session -- the same rules, so that
    both calls mean the same board. Empty or `{}` is valid: board from the configuration,
    default number of images.
    """
    try:
        data = json.loads(text) if text and text.strip() else {}
    except json.JSONDecodeError as error:
        raise ValueError(f"Kein JSON: {error}") from None
    if not isinstance(data, dict) or data.get("schema", REQUEST_SCHEMA) != REQUEST_SCHEMA:
        raise ValueError(f"Erwartet schema „{REQUEST_SCHEMA}“.")
    board = parse_board(data["board"]) if data.get("board") is not None else None
    frames = data.get("frames", DEFAULT_FRAMES)
    if isinstance(frames, bool) or not isinstance(frames, int) or not 1 <= frames <= MAX_FRAMES:
        raise ValueError(f"frames muss eine Ganzzahl von 1 bis {MAX_FRAMES} sein.")
    diagnose = data.get("diagnose", False)
    if not isinstance(diagnose, bool):
        raise ValueError("diagnose muss true oder false sein.")
    return BoardRequest(board, int(frames), diagnose)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds")


def build_result(
    frames: list[FrameBoard],
    *,
    spec,
    frames_requested: int,
    frame_id: str,
    intrinsics_source: str,
    calibration_id: str,
    max_error_px: float,
    error: str | None = None,
    message: str = "",
) -> dict:
    """Return the result JSON (`wsc.vision.measureboard.result/1`) as a dict.

    Pose and reprojection error: average over the images with the whole board.
    Pixels (`cornersPx`, `centerPx`, `offsetPx`, `partial`): from the last image.
    """
    from tagloc.geometry import to_position_quaternion
    from tagloc.tagmap import merge_tag_poses

    complete_frames = [frame for frame in frames if frame.complete]
    last = frames[-1] if frames else None
    width, height = last.image_size if last is not None else (0, 0)
    centre = (width / 2.0, height / 2.0)

    result = {
        "schema": RESULT_SCHEMA,
        "capturedAt": _iso(frames[0].captured_at) if frames else None,
        "capturedUntil": _iso(frames[-1].captured_at) if frames else None,
        "frameId": frame_id,
        "frameConvention": FRAME_CONVENTION,
        "imageSize": [width, height] if frames else None,
        "intrinsicsSource": intrinsics_source,
        "calibrationId": calibration_id,
        "board": {
            "type": spec.type,
            "cols": spec.cols,
            "rows": spec.rows,
            "squareSizeM": spec.square_size_m,
        },
        "framesRequested": frames_requested,
        "framesUsed": len(frames),
        "found": False,
        "complete": False,
        "frameCount": len(complete_frames),
        "poseInCamera": None,
        "poseOk": None,
        "reprojectionErrorPx": None,
        "distanceM": None,
        "viewAngleDeg": None,
        "centerPx": None,
        "offsetPx": None,
        "cornersPx": None,
        "nearEdge": [],
        "partial": None,
        "error": error,
        "message": message,
    }
    if not frames:
        return result

    corners = None
    if complete_frames:
        pose = merge_tag_poses([frame.pose for frame in complete_frames])
        position, orientation = to_position_quaternion(pose)
        errors = [frame.reprojection_error_px for frame in complete_frames if frame.reprojection_error_px is not None]
        mean_error = float(np.mean(errors)) if errors else None
        corners = np.asarray(complete_frames[-1].corners, dtype=np.float64).reshape(-1, 2)
        result.update(
            found=True,
            complete=True,
            poseInCamera={"position": list(position), "orientation": list(orientation)},
            # Good enough for the hand-eye capture? The same threshold as there.
            poseOk=bool(mean_error is not None and mean_error <= max_error_px),
            reprojectionErrorPx=finite_or_none(mean_error),
            distanceM=float(np.linalg.norm(np.asarray(pose)[:3, 3])),
            viewAngleDeg=view_angle_deg(pose),
        )
    elif last.partial is not None:
        partial_corners, grid_cols, grid_rows = last.partial
        corners = np.asarray(partial_corners, dtype=np.float64).reshape(-1, 2)
        result["found"] = True
        result["partial"] = {
            "gridCols": int(grid_cols),
            "gridRows": int(grid_rows),
            "cornerCount": int(len(corners)),
            "bboxPx": [
                float(corners[:, 0].min()),
                float(corners[:, 1].min()),
                float(corners[:, 0].max()),
                float(corners[:, 1].max()),
            ],
        }

    if corners is not None:
        mean = corners.mean(axis=0)
        result["centerPx"] = [float(mean[0]), float(mean[1])]
        # From the image center to the board: the camera has to move there so that the
        # board moves to the middle (x to the right, y down).
        result["offsetPx"] = [float(mean[0] - centre[0]), float(mean[1] - centre[1])]
        result["nearEdge"] = near_edge(corners.tolist(), (width, height))
        if result["complete"]:
            result["cornersPx"] = [[float(u), float(v)] for u, v in corners]
    return result


def dumps(result: dict) -> str:
    return json.dumps(result, allow_nan=False, default=float)


__all__ = [
    "NO_FRAME",
    "RESULT_SCHEMA",
    "BoardRequest",
    "FrameBoard",
    "build_result",
    "dumps",
    "parse_request",
]

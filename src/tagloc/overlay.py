"""Draws detection results onto the image -- for livestream and CLI.

Standard marker rendering is an **axis cross in the tag** (red X, green Y,
blue Z), plus the outline and ID. That's exactly what `draw_tag_overlay`
does: the outline shows *that* something was detected, the axis cross shows
*how* the pose sits -- a twisted cross is obvious at a glance, a wrong number
is not.

One rule for this module: it only draws, it never computes. Poses arrive
already finished. That lets the same code run for the stream, a CLI window,
and a still image.
"""

import logging
import os
import sys
from collections.abc import Sequence
from typing import Any

import numpy as np

from .calibration import CameraCalibration
from .geometry import to_rvec_tvec
from .modes import DEFAULT_OVERLAY_MODE, OVERLAY_MODE_LABELS, OVERLAY_MODES, normalise_mode
from .observations import TagPose

# BGR, because OpenCV.
COLOR_OK = (80, 220, 80)
COLOR_AMBIGUOUS = (40, 170, 255)
COLOR_TEXT = (255, 255, 255)
COLOR_SHADOW = (0, 0, 0)
COLOR_BOARD = (255, 190, 60)

_log = logging.getLogger(__name__)

#: Auf ~640 px Bildbreite abgestimmt (Hand-Pi, bzw. frueher ueberall der
#: verkleinerte Stream). "apriltag"/"off" zeichnen seit 2026-09-22 auf dem
#: vollen Frame (bis 4056 px an der Deckenkamera, siehe camera_stream.py) --
#: ohne Skalierung wurde ein 2-px-Strich dort beim Reinzoomen unlesbar statt
#: deutlicher. `_scale_for` haelt Strichstaerke/Schriftgroesse/Abstaende
#: proportional zur tatsaechlichen Bildbreite, nie kleiner als die
#: Referenzwerte -- die sind schon das Minimum fuer Lesbarkeit.
_REFERENCE_WIDTH = 640
_FONT_SCALE = 0.5
_THICKNESS = 2


def _scale_for(image) -> float:
    """Faktor Bildbreite / `_REFERENCE_WIDTH`, nach unten auf 1 begrenzt."""
    width = np.asarray(image).shape[1]
    return max(1.0, width / _REFERENCE_WIDTH)


def _put_text(image, text: str, origin: tuple[int, int], color=COLOR_TEXT) -> None:
    """Draw text with a dark outline -- otherwise unreadable on a light background."""
    import cv2

    scale = _scale_for(image)
    font_scale = _FONT_SCALE * scale
    thickness = max(1, round(_THICKNESS * scale))
    cv2.putText(
        image, text, origin, cv2.FONT_HERSHEY_SIMPLEX, font_scale, COLOR_SHADOW, thickness + 2
    )
    cv2.putText(
        image, text, origin, cv2.FONT_HERSHEY_SIMPLEX, font_scale, color, max(1, thickness - 1)
    )


def draw_tag_outline(image, tag_pose: TagPose, color) -> None:
    """Draw the tag's quadrilateral outline and mark its first corner."""
    import cv2

    if tag_pose.observation is None:
        return
    scale = _scale_for(image)
    thickness = max(1, round(_THICKNESS * scale))
    corners = np.asarray(tag_pose.observation.corners, dtype=np.int32).reshape(-1, 1, 2)
    cv2.polylines(image, [corners], isClosed=True, color=color, thickness=thickness)
    # Bold first corner: makes a rotated detection visible at a glance.
    first = tuple(int(value) for value in tag_pose.observation.corners[0])
    cv2.circle(image, first, max(2, round(5 * scale)), color, -1)


def draw_tag_axes(
    image, tag_pose: TagPose, calibration: CameraCalibration, axis_length_m: float
) -> None:
    """Draw the coordinate cross into the tag: X red, Y green, Z blue."""
    import cv2

    thickness = max(1, round(_THICKNESS * _scale_for(image)))
    rvec, tvec = to_rvec_tvec(tag_pose.pose_cam_tag)
    cv2.drawFrameAxes(
        image,
        np.asarray(calibration.camera_matrix, dtype=np.float64),
        np.asarray(calibration.distortion, dtype=np.float64),
        rvec,
        tvec,
        axis_length_m,
        thickness,
    )


def draw_tag_overlay(
    image,
    tag_poses: Sequence[TagPose],
    calibration: CameraCalibration,
    *,
    tag_map: Any = None,
    default_size_m: float = 0.05,
    show_distance: bool = True,
) -> Any:
    """Mark all detected tags in the image. Modifies `image` in-place.

    Per-tag label: ID, mapped module (if the map knows it), distance and
    reprojection error. Ambiguous poses are drawn orange instead of green --
    exactly the information you look for while debugging.
    """
    scale = _scale_for(image)
    for tag_pose in tag_poses:
        color = COLOR_AMBIGUOUS if tag_pose.is_ambiguous else COLOR_OK
        draw_tag_outline(image, tag_pose, color)
        size_m = (
            tag_map.size_for(tag_pose.tag_id, default_size_m)
            if tag_map is not None
            else default_size_m
        )
        draw_tag_axes(image, tag_pose, calibration, size_m * 0.5)

        label = f"#{tag_pose.tag_id}"
        entry = tag_map.get(tag_pose.tag_id) if tag_map is not None else None
        if entry is not None and entry.module_id:
            label += f" {entry.module_id}"
        elif entry is not None:
            label += f" [{entry.role}]"
        if show_distance:
            distance = float(np.linalg.norm(np.asarray(tag_pose.pose_cam_tag)[:3, 3]))
            label += f"  {distance:.3f} m"
        if not np.isnan(tag_pose.reprojection_error_px):
            label += f"  e={tag_pose.reprojection_error_px:.2f}px"
        if tag_pose.is_ambiguous:
            label += "  MEHRDEUTIG"

        if tag_pose.observation is not None:
            center = tag_pose.observation.center()
            origin = (int(center[0] - 40 * scale), int(center[1] - 12 * scale))
        else:  # pragma: no cover - a pose without an observation doesn't occur in practice
            origin = (round(10 * scale), round(30 * scale))
        _put_text(image, label, origin, color)
    return image


def draw_board_overlay(image, board_sample: Any, *, coverage=None) -> Any:
    """Mark the found board corners -- the stream's calibration mode.

    Lets the frontend see *what* the calibration sees: whether the board is
    detected at all, where in the image it sits, and where samples are
    still missing.
    """
    import cv2

    scale = _scale_for(image)
    margin = round(12 * scale)
    if board_sample is not None:
        radius = max(2, round(4 * scale))
        for point in np.asarray(board_sample.corners, dtype=np.float64).reshape(-1, 2):
            cv2.circle(image, (int(point[0]), int(point[1])), radius, COLOR_BOARD, -1)
        count = len(np.asarray(board_sample.corners).reshape(-1, 2))
        _put_text(image, f"Board: {count} Ecken", (margin, round(28 * scale)), COLOR_BOARD)
    else:
        _put_text(image, "Board: nicht erkannt", (margin, round(28 * scale)), COLOR_AMBIGUOUS)
    if coverage is not None:
        _put_text(
            image,
            f"Abdeckung x {coverage[0] * 100:.0f} %  y {coverage[1] * 100:.0f} %",
            (margin, round(52 * scale)),
            COLOR_BOARD,
        )
    return image


def draw_status_bar(image, lines: Sequence[str]) -> Any:
    """Write status lines at the bottom left -- mode, calibration, frame count."""
    shape = np.asarray(image).shape
    height = shape[0]
    scale = max(1.0, shape[1] / _REFERENCE_WIDTH)
    margin = round(12 * scale)
    line_height = round(22 * scale)
    for index, line in enumerate(reversed(list(lines))):
        _put_text(image, line, (margin, height - margin - index * line_height))
    return image


class Window:
    """A display window that doesn't crash without a screen.

    `cv2.imshow` raises on a headless Pi or in an SSH session without X
    forwarding -- and only on the first frame, i.e. mid-run. The error is
    reported once here, then execution continues without a window: the
    point of the call is detection, not the picture.

    `wait_ms=0` waits for a keypress. The sensible default in image-folder
    mode: otherwise images fly by too fast to hit the abort key.
    """

    def __init__(self, title: str, *, enabled: bool = True) -> None:
        self.title = title
        self._enabled = bool(enabled) and has_display()
        self._warned = not self._enabled

    @property
    def enabled(self) -> bool:
        """Return whether a window is actually shown."""
        return self._enabled

    def show(self, image, wait_ms: int = 1) -> str:
        """Show the image and return the pressed key, lowercased.

        Empty string means "no key" -- also when no window is open at all.
        """
        if not self._enabled:
            return ""
        try:
            import cv2

            cv2.imshow(self.title, image)
            key = cv2.waitKey(int(wait_ms)) & 0xFF
        except Exception as error:
            self._enabled = False
            if not self._warned:
                self._warned = True
                _log.warning(
                    "Kein Fenster moeglich (%s) -- es wird ohne Anzeige weitergearbeitet", error
                )
            return ""
        return "" if key == 255 else chr(key).lower()

    def close(self) -> None:
        if not self._enabled:
            return
        try:
            import cv2

            cv2.destroyWindow(self.title)
            cv2.waitKey(1)  # without this the window sticks around on some backends
        except Exception:  # pragma: no cover - reason doesn't matter during cleanup
            _log.debug("Fenster liess sich nicht schliessen")


def has_display() -> bool:
    """Return whether a screen is reachable at all.

    On Linux without `DISPLAY` and without `WAYLAND_DISPLAY` there is none --
    the normal case on the Pi over SSH. Other systems just try, and
    `Window.show` catches the failure.
    """
    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return True


def summarise(tag_poses: Sequence[TagPose], tag_map: Any = None) -> str:
    """Return a one-liner about what was detected -- for status line and log."""
    if not tag_poses:
        return "keine Tags erkannt"
    parts = []
    for tag_pose in sorted(tag_poses, key=lambda item: item.tag_id):
        entry = tag_map.get(tag_pose.tag_id) if tag_map is not None else None
        name = entry.module_id if entry is not None and entry.module_id else str(tag_pose.tag_id)
        parts.append(f"{name}{'!' if tag_pose.is_ambiguous else ''}")
    return f"{len(tag_poses)} Tags: " + ", ".join(parts)


__all__ = [
    "DEFAULT_OVERLAY_MODE",
    "OVERLAY_MODES",
    "OVERLAY_MODE_LABELS",
    "Window",
    "draw_board_overlay",
    "draw_status_bar",
    "draw_tag_axes",
    "draw_tag_outline",
    "draw_tag_overlay",
    "has_display",
    "normalise_mode",
    "summarise",
]

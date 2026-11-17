"""Overlay modes of the live stream. Standard library only.

Kept separate because `vision_server.camera_stream` needs them without pulling in
numpy or OpenCV: the live stream must keep running even when no detection is
configured. `tagloc.overlay` re-exports both so that drawing code finds them there.
"""

#: Order = order of selection in the frontend.
OVERLAY_MODES = ("off", "apriltag", "undistorted", "tagreference")
DEFAULT_OVERLAY_MODE = "apriltag"

#: Labels shown to the operator.
OVERLAY_MODE_LABELS = {
    "off": "Rohbild",
    "apriltag": "AprilTags markieren",
    "undistorted": "Entzerrt",
    "tagreference": "Tag einmessen",
}

#: Shown in the image when "undistorted" has no valid calibration.
NO_CALIBRATION_HINT = "Keine Kalibrierung -- Rohbild"


def normalise_mode(mode) -> str:
    """Map a mode set from outside to a valid value.

    The value comes from a writable OPC UA node, i.e. from the frontend. An unknown,
    empty or mistyped value must not stop the stream -- fall back to the default mode.
    """
    if not mode:
        return DEFAULT_OVERLAY_MODE
    candidate = str(mode).strip().lower()
    return candidate if candidate in OVERLAY_MODES else DEFAULT_OVERLAY_MODE

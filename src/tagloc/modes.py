"""Overlay modes of the livestream. Stdlib only.

Split out because `vision_server.camera_stream` needs it without pulling in
numpy or OpenCV: the livestream must keep running even without detection
configured. `tagloc.overlay` re-exports both so drawing code finds them there.
"""

#: Order = order of selection in the frontend.
OVERLAY_MODES = ("off", "apriltag", "calibration")
DEFAULT_OVERLAY_MODE = "apriltag"

#: Labels shown to the operator.
OVERLAY_MODE_LABELS = {
    "off": "Rohbild",
    "apriltag": "AprilTags markieren",
    "calibration": "Kalibrierboard markieren",
}


def normalise_mode(mode) -> str:
    """Map an externally set mode to a valid value.

    The value comes from a writable OPC-UA node, i.e. the frontend. An
    unknown, empty or mistyped value must not stop the stream -- fall back
    to the default mode instead.
    """
    if not mode:
        return DEFAULT_OVERLAY_MODE
    candidate = str(mode).strip().lower()
    return candidate if candidate in OVERLAY_MODES else DEFAULT_OVERLAY_MODE

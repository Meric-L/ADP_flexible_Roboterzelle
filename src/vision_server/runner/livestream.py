"""Live stream: overlay (annotator), publisher and MJPEG server."""

import logging
from collections.abc import Mapping
from typing import Any

from asyncua import ua

from ..address_space import VisionAddressSpace
from ..camera_stream import CameraStreamPublisher
from ..detection import DetectionSource
from ..mjpeg_server import MjpegServer
from .sources import _camera_owner

_log = logging.getLogger(__package__)


def _build_annotator(source: DetectionSource):
    """Build the stream overlay for a source, if it can provide one.

    Stream and job use the same loaded calibration and tag map -- if the image shows
    something different from the job result, it is not due to two configurations. The
    detector is deliberately NOT the same: until 2026-09-24 the job and the stream ran on
    a shared instance, both with `subpixel_corner_refinement` from the config (currently
    `True` -- layer 1 needs it, see `AprilTagProfileConfig`). Measured live on pi1
    (py-spy dump): the job thread (`vision-apriltag_0`) and the overlay thread ran
    `detector.detect()` AT THE SAME TIME with `CORNER_REFINE_APRILTAG` active -- together
    enough CPU contention to make the 20 s job timeout trip again. The stream does not
    need the refinement (demo image, lower resolution than the job), so it gets its OWN
    detector instance with the refinement permanently off, independent of the config value
    for the job path.

    Since 21.10.2026 the overlay draws the tags from detection at full resolution
    (`source.latest_tags`) and only rarely detects itself, in the background and not during
    a job (`set_busy`). The own detector remains for this -- a detector is not built for
    two threads.
    """
    job_detector = getattr(source, "_detector", None)
    calibration = getattr(source, "_calibration", None)
    config = getattr(source, "_config", None)
    camera_config = getattr(source, "_camera_config", None)
    if job_detector is None or calibration is None or config is None:
        return None

    from tagloc.detector import build_detector

    from ..stream_overlay import AprilTagStreamAnnotator

    # Detector parameters as in the job, otherwise the overlay would show other tags --
    # except for corner refinement: it is deliberately left out of the stream (see above,
    # CPU contention with the job), even if `cornerRefinementMethod` is explicitly in the
    # parameters.
    stream_parameters = {
        name: value
        for name, value in config.detector_parameters.items()
        if name != "cornerRefinementMethod"
    }
    stream_detector = build_detector(
        config.tag_family,
        config.detector_backend,
        subpixel_corner_refinement=False,
        detector_parameters=stream_parameters,
    )

    return AprilTagStreamAnnotator(
        config,
        detector=stream_detector,
        calibration=calibration,
        tag_map=getattr(source, "_tag_map", None),
        detection_max_width=getattr(camera_config, "max_stream_width", None),
        full_resolution_tags=lambda: getattr(source, "latest_tags", None),
        detect_interval_s=getattr(camera_config, "overlay_detect_interval_s", 5.0),
    )


def _start_camera_stream(
    space: VisionAddressSpace,
    sources: Mapping[str, DetectionSource],
    opened: Mapping[str, bool],
) -> tuple[CameraStreamPublisher | None, Any]:
    """Start the live stream publisher, if configured and the camera is ready.

    Uses the same `SharedCamera` that detection also holds open -- a second camera is not
    worth it here, see `camera.py`. Additionally returns the annotator, so that
    `install_vision_machine` can later attach a running `CalibrationSession` to it
    (`set_calibration_session`).
    """
    if space.latest_camera_frame is None:
        return None, None
    source = _camera_owner(sources, opened)
    if source is None:
        _log.error(
            "Livestream-Knoten konfiguriert, aber keine geoeffnete Quelle haelt eine "
            "Kamera -- kein Stream"
        )
        return None, None
    annotator = None
    try:
        annotator = _build_annotator(source)
    except Exception:
        _log.exception("Stream-Overlay nicht verfuegbar, Stream laeuft ohne Markierung")
    stream = CameraStreamPublisher(
        source.camera,
        space.latest_camera_frame,
        space.config.camera_stream,
        annotator=annotator,
        mode_node=space.camera_stream_mode,
        progress_node=space.calibration_progress,
        preview_status_node=space.tag_reference_preview_status,
    )
    stream.start()
    _log.info(
        "Livestream aus Profil '%s'%s",
        source.profile_id,
        " mit Overlay" if annotator is not None else " ohne Overlay",
    )
    return stream, annotator


async def _start_mjpeg_server(
    space: VisionAddressSpace, stream: CameraStreamPublisher
) -> MjpegServer | None:
    """Start the MJPEG stream and publish its port in the address space.

    If binding fails (port in use), the server continues without it: the node stays at 0
    and the frontend falls back to `LatestCameraFrame`. The live stream is a debugging
    tool, not a reason to abort the startup.
    """
    config = space.config.camera_stream
    if config is None or config.http_port <= 0 or space.camera_stream_http_port is None:
        return None
    server = MjpegServer(
        stream, config.http_port, tag_reference_dir=space.config.tag_reference_debug_dir
    )
    try:
        await server.start()
    except OSError:
        _log.exception(
            "MJPEG-Livestream auf Port %d nicht startbar -- nur der OPC-UA-Rueckfallweg",
            config.http_port,
        )
        return None
    await space.camera_stream_http_port.write_value(
        ua.Variant(server.port, ua.VariantType.Int32)
    )
    return server

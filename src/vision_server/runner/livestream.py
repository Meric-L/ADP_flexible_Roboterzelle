"""Livestream: Overlay (Annotator), Publisher und MJPEG-Server."""

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

    Stream and job use the same loaded calibration and tag map -- if the
    image shows something different from the job result, it's not because
    of two configurations. Der Detektor ist bewusst NICHT derselbe: Job und
    Stream liefen bis 2026-09-24 auf einer geteilten Instanz, beide mit
    `subpixel_corner_refinement` aus der Config (aktuell `True` -- Layer 1
    braucht das, siehe `AprilTagProfileConfig`). Live auf pi1 gemessen
    (py-spy-Dump): der Job-Thread (`vision-apriltag_0`) und der
    Overlay-Thread liefen dabei GLEICHZEITIG in `detector.detect()` mit
    aktivem `CORNER_REFINE_APRILTAG` -- zusammen genug CPU-Konkurrenz, um
    den 20s-Job-Timeout wieder reissen zu lassen. Der Stream braucht die
    Verfeinerung nicht (Demo-Bild, kleinere Aufloesung als der Job) --
    deshalb bekommt er eine EIGENE Detektor-Instanz mit der Verfeinerung
    fest aus, unabhaengig vom Config-Wert fuer den Job-Pfad.

    Seit 21.10.2026 zeichnet das Overlay die Tags aus der Erkennung in voller
    Aufloesung (`source.latest_tags`) und erkennt nur noch selten selbst, im
    Hintergrund und nicht waehrend eines Jobs (`set_busy`). Der eigene
    Detektor bleibt dafuer -- ein Detektor ist nicht fuer zwei Threads gebaut.
    """
    job_detector = getattr(source, "_detector", None)
    calibration = getattr(source, "_calibration", None)
    config = getattr(source, "_config", None)
    camera_config = getattr(source, "_camera_config", None)
    if job_detector is None or calibration is None or config is None:
        return None

    from tagloc.detector import build_detector

    from ..stream_overlay import AprilTagStreamAnnotator

    # Detektor-Parameter wie beim Job, sonst zeigt das Overlay andere Tags --
    # bis auf die Eckenverfeinerung: sie bleibt im Stream bewusst aus (siehe
    # oben, CPU-Konkurrenz zum Job), auch wenn `cornerRefinementMethod`
    # ausdruecklich in den Parametern steht.
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
        interval_s=getattr(camera_config, "overlay_interval_s", 0.5),
        detection_max_width=getattr(camera_config, "max_stream_width", None),
        full_resolution_tags=lambda: getattr(source, "latest_tags", None),
        detect_interval_s=getattr(camera_config, "overlay_detect_interval_s", 5.0),
    )


def _start_camera_stream(
    space: VisionAddressSpace,
    sources: Mapping[str, DetectionSource],
    opened: Mapping[str, bool],
) -> tuple[CameraStreamPublisher | None, Any]:
    """Startet den Livestream-Publisher, falls konfiguriert und Kamera bereit.

    Nutzt dieselbe `SharedCamera`, die auch die Erkennung offen haelt — eine
    zweite Kamera lohnt sich hier nicht, siehe `camera.py`. Gibt den
    Annotator zusaetzlich zurueck, damit `install_vision_machine` ihm spaeter
    eine laufende `CalibrationSession` anhaengen kann (`set_calibration_session`).
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
    """Startet den MJPEG-Stream und nennt seinen Port im Adressraum.

    Scheitert das Binden (Port belegt), laeuft der Server ohne weiter: der
    Knoten bleibt auf 0 und das Frontend faellt auf `LatestCameraFrame`
    zurueck. Der Livestream ist ein Debugwerkzeug, kein Grund fuer einen
    Startabbruch.
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

"""Kamerazustand: Watchdog der geteilten Kamera als `DeviceHealth` der Anlagensicht."""

import logging
from collections.abc import Mapping

from ..address_space import VisionAddressSpace
from ..asset_model import VisionAssetNodes
from ..camera import exit_process
from ..camera_health import CameraHealthPublisher, HealthAlarms, write_device_health
from ..detection import DetectionSource
from ..nodeset_ids import DeviceHealth
from .sources import _camera_owner

_log = logging.getLogger(__package__)


def _camera_config_of(space: VisionAddressSpace, source: DetectionSource):
    """Die Konfiguration der Kamera, die wirklich laeuft.

    `space.config.camera_stream` kann `None` sein, waehrend die Quelle sehr
    wohl eine Kamera mit eigenen Schwellen haelt (`detection/apriltag.py`
    haelt sie in `_camera_config`). Gleiches Muster wie `_build_annotator`.
    """
    from ..profiles import CameraStreamConfig

    return (
        getattr(source, "_camera_config", None)
        or space.config.camera_stream
        or CameraStreamConfig()
    )


async def _start_camera_health(
    space: VisionAddressSpace,
    assets: VisionAssetNodes | None,
    sources: Mapping[str, DetectionSource],
    opened: Mapping[str, bool],
) -> CameraHealthPublisher | None:
    """Verbindet den Kamera-Watchdog mit `DeviceHealth` der Anlagensicht.

    Der Watchdog in `camera.py` erkennt haengende Kameras, meldet das aber nur
    ins Log. Hier wird daraus ein Wert, den ein generischer OPC-UA-Client
    sieht -- ohne Kenntnis dieses Repos.

    Bewusst nicht an `_start_camera_stream` gehaengt: der Livestream ist eine
    Debughilfe und kann fehlen, waehrend die Job-Quelle eine Kamera haelt.
    Ohne Part 2 oder ohne Zustandsknoten passiert nichts.
    """
    if assets is None or not assets.device_health:
        return None
    source = _camera_owner(sources, opened)
    if source is None:
        # Knoten da, Kamera nicht: FAILURE ist die ehrliche Antwort, und sie
        # bleibt stehen -- es gibt nichts, was sie spaeter widerlegen koennte.
        _log.warning(
            "Zustandsknoten der Anlagensicht vorhanden, aber keine geoeffnete "
            "Kamera -- DeviceHealth bleibt auf FAILURE"
        )
        await write_device_health(assets.device_health, DeviceHealth.FAILURE)
        return None
    # Der normkonforme Ereignisweg von Part 2. Emittiert wird von
    # `VisionMachine`, weil Clients ohnehin genau diesen Knoten abonnieren;
    # `SourceNode` nennt die Komponente, um die es geht -- den Bildsensor,
    # falls sein Modell bekannt ist, sonst die Anlagenwurzel.
    alarms = (
        HealthAlarms(
            space.server,
            space.vision_system,
            assets.health_alarms,
            source=assets.image_sensor or assets.root,
        )
        if assets.health_alarms
        else None
    )
    publisher = CameraHealthPublisher(
        source.camera,
        assets.device_health,
        _camera_config_of(space, source),
        alarms=alarms,
    )
    # Der Watchdog reisst den Prozess, wenn er aufgibt; vorher soll noch ein
    # letztes FAILURE rausgehen.
    source.camera.set_give_up_handler(publisher.give_up_handler(exit_process))
    publisher.start()
    _log.info(
        "Kamerazustand (OPC 40100-2) aus Profil '%s' auf %d Knoten, %d Alarme",
        source.profile_id,
        len(assets.device_health),
        len(assets.health_alarms),
    )
    return publisher

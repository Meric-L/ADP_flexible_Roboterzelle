"""Camera state: watchdog of the shared camera as `DeviceHealth` of the asset view."""

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
    """Return the configuration of the camera that is actually running.

    `space.config.camera_stream` can be `None` while the source does hold a camera with
    its own thresholds (`detection/apriltag.py` keeps it in `_camera_config`). Same
    pattern as `_build_annotator`.
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
    """Connect the camera watchdog to `DeviceHealth` of the asset view.

    The watchdog in `camera.py` detects hanging cameras, but only reports them to the
    log. Here it becomes a value that a generic OPC UA client can see -- without knowing
    this repo.

    Deliberately not attached to `_start_camera_stream`: the live stream is a debugging
    aid and may be missing, while the job source holds a camera. Without part 2 or
    without state nodes, nothing happens.
    """
    if assets is None or not assets.device_health:
        return None
    source = _camera_owner(sources, opened)
    if source is None:
        # Nodes exist, camera does not: FAILURE is the honest answer, and it stays --
        # nothing could later disprove it.
        _log.warning(
            "Zustandsknoten der Anlagensicht vorhanden, aber keine geoeffnete "
            "Kamera -- DeviceHealth bleibt auf FAILURE"
        )
        await write_device_health(assets.device_health, DeviceHealth.FAILURE)
        return None
    # The standard-conform event path of part 2. Emitted by `VisionMachine`, because
    # clients subscribe to exactly this node anyway; `SourceNode` names the component
    # concerned -- the image sensor if its model is known, otherwise the asset root.
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
    # The watchdog kills the process only after `give_up_after_s` without a frame; a last
    # FAILURE should still go out before that.
    source.camera.set_give_up_handler(publisher.give_up_handler(exit_process))
    publisher.start()
    _log.info(
        "Kamerazustand (OPC 40100-2) aus Profil '%s' auf %d Knoten, %d Alarme",
        source.profile_id,
        len(assets.device_health),
        len(assets.health_alarms),
    )
    return publisher

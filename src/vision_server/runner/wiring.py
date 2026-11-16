"""Shared state while the vision system is assembled (`Wiring`).

`install_vision_machine` creates the OPC UA methods before all parts exist: the
calibration session, the annotator, the layer-2 run and the hand-eye session
are only created later in the sequence. The handlers read them only when a
method is called, long after that. Earlier this was solved by late name lookup
of closures over local variables; since the split into modules, this object
holds the fields and each step sets its own.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from asyncua import Node, Server

from ..address_space import VisionAddressSpace
from ..calibration_session import CalibrationSession
from ..camera_stream import CameraStreamPublisher
from ..config import VisionServerConfig
from ..detection import DetectionSource
from ..job import JobRunner
from ..result_management import ResultStore
from ..state_machine import VisionStateMachines


@dataclass
class Wiring:
    """State shared between the assembly steps."""

    server: Server
    space: VisionAddressSpace
    config: VisionServerConfig
    states: VisionStateMachines
    results: ResultStore
    sources: Mapping[str, DetectionSource]
    jobs: JobRunner
    #: Per profile: whether the source could be opened (see `install`).
    opened: dict[str, bool] = field(default_factory=dict)
    #: The opened AprilTag source; `None` if there is none or it did not open.
    apriltag_source: Any = None
    #: Live-stream publisher; `None` without a stream node or without a camera.
    camera_stream: CameraStreamPublisher | None = None
    #: Overlay of the live stream (`stream_overlay.AprilTagStreamAnnotator`).
    annotator: Any = None
    #: `None` if `config.apriltag` is not set.
    calibration_session: CalibrationSession | None = None
    #: Layer-2 run; `None` except on the hand Pi.
    layer2_run: Any = None
    #: Hand-eye session; `None` except on the hand Pi.
    hand_eye_session: Any = None
    #: Additional method nodes by name. They also end up as `mirror_methods`
    #: under `VisionProgram`; the order of the entries stays the order of creation.
    methods: dict[str, Node] = field(default_factory=dict)

    def calibration_busy(self) -> bool:
        """Return whether a calibration session or its background evaluation is running."""
        return self.calibration_session is not None and self.calibration_session.busy

    def calibration_processing(self) -> bool:
        """Return whether the calibration is currently computing in the background."""
        return self.calibration_session is not None and self.calibration_session.processing

    def hand_eye_active(self) -> bool:
        """Return whether a hand-eye session is open."""
        return self.hand_eye_session is not None and self.hand_eye_session.active

    def layer2_active(self) -> bool:
        """Return whether a layer-2 run is active."""
        return self.layer2_run is not None and self.layer2_run.active

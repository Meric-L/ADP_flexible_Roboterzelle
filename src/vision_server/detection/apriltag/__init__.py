"""AprilTag detection on the camera shared with the livestream.

One source for both cameras. The ceiling camera and the camera on the robot
(eye-in-hand) use the same class and the same code. They differ only in the
`AprilTagProfileConfig` that `vision_server/server.py` sets per Pi, and in what
they get to see:

* if world tags from the map are in the image, the camera pose is derived from
  them, and every module pose is reported in the world frame, together with the
  world tag it is closest to
* otherwise everything stays in the camera frame, and `frameConvention` tells
  the reader where +Z points

**Only the world tags are fixed** -- four of them at the edge of the cell. The
robot is a module like any other: it carries its own tags and is measured, not
read from a file. The ceiling camera is the only one that sees both the robot
and the world tags, so it reports which world tag the robot stands next to. The
robot camera then localizes itself optically against that tag, which needs no
hand-eye calibration.

The actual computation lives in `tagloc`; this package is the OPC UA adapter.
It is split by task into modules that are combined as mixins into the one
class `AprilTagDetectionSource`:

| Module | Task |
| --- | --- |
| `resources.py` | Load calibration, tag map, hand-eye, detector (`open`), swap them (`apply_*`), `close` |
| `capture.py` | Wait for fresh images, calibration matched to the image size, overlay state |
| `job.py` | `acquire_and_detect`: evaluate images, localize modules, build detections |
| `tags.py` | `MeasureTags` and `measure_tag` (hand-eye): tags in the camera frame |
| `board.py` | `measure_board` (hand-eye) and `MeasureBoard`: the chessboard |
| `reference.py` | `MeasureTagReference` and `PlanTagReferenceView`: reference edges |
| `cell.py` | `observe_cell`: one image for the cell watch |
| `snapshot.py` | `TagSnapshot` for the livestream overlay |

The import path `vision_server.detection.apriltag` and all names importable
there so far remain available.
"""

from collections.abc import Callable
from typing import Any

from ...camera import SharedCamera
from ...profiles import AprilTagProfileConfig, CameraStreamConfig
from ..base import DetectionSource
from .board import OBSERVE_MAX_SIDE, BoardMeasureMixin
from .capture import POLL_INTERVAL_S, CaptureMixin
from .cell import CellObserveMixin
from .job import (
    PAYLOAD_DIGITS,
    SOURCE_BY_FRAME,
    JobMixin,
    robot_pose_from_parameters,
)
# Not in `__all__`, but imported under this path by tests and `cellwatch`:
from .job import _localization_summary, _merge_world_tags, _observed_world_tags  # noqa: F401
from .reference import ReferenceMixin
from .resources import ResourcesMixin
from .snapshot import TagSnapshot
from .tags import TagMeasureMixin

__all__ = [
    "OBSERVE_MAX_SIDE",
    "PAYLOAD_DIGITS",
    "POLL_INTERVAL_S",
    "SOURCE_BY_FRAME",
    "AprilTagDetectionSource",
    "TagSnapshot",
    "robot_pose_from_parameters",
]


class AprilTagDetectionSource(
    ResourcesMixin,
    CaptureMixin,
    JobMixin,
    TagMeasureMixin,
    BoardMeasureMixin,
    ReferenceMixin,
    CellObserveMixin,
    DetectionSource,
):
    """Localize modules by their AprilTags.

    Holds the source state (camera, detector, calibration, map, anchor). The
    methods live in the mixins; see the module docstring.
    """

    profile_id = "apriltag"
    is_simulated = False

    def __init__(
        self,
        config: AprilTagProfileConfig,
        camera_config: CameraStreamConfig | None = None,
        *,
        camera: SharedCamera | None = None,
        detector: Any = None,
        calibration: Any = None,
        tag_map: Any = None,
    ) -> None:
        self._config = config
        self._camera_config = camera_config or CameraStreamConfig()
        #: Passed in from outside when `camera_stream.py` needs the same handle
        #: (see `runner/livestream.py`).
        self.camera = camera if camera is not None else SharedCamera(self._camera_config)
        #: Injectable so that tests run without OpenCV and without files.
        self._detector = detector
        self._calibration = calibration
        self._tag_map = tag_map
        #: Camera pose of the last evaluated image, set by `_locate`. `None` until
        #: the first image, and again whenever no world tag was visible and no
        #: anchor could carry it.
        self._localization = None
        #: Hand-eye, calibrated once -- the camera sits rigidly on the robot.
        self._hand_eye = None
        #: Where the robot base is in the world frame. Set at the first world tag
        #: in view, then carries over all modules even when no world tag is visible
        #: any more. Deliberately lives across jobs: anchoring happens once per
        #: localization run, not once per module.
        self._anchor = None
        #: Last measured drift against the anchor, `None` while unchecked.
        self._drift = None
        #: World tags of the last evaluated image, set by `_locate`.
        self._world_tag_sample: list = []
        #: Running `MeasureTags` calls (`runner/measure_tags.py`); the stream overlay
        #: keeps going as long as this is non-zero.
        self.measure_calls = 0
        #: Latest result at full resolution, whatever path produced it
        #: (`TagSnapshot`). Only ever replaced, never modified -- the overlay reads
        #: it from its own thread without a lock.
        self.latest_tags: TagSnapshot | None = None
        #: Called for each measured image by `MeasureTagReference` with
        #: `(debug, corners_px, image_size, title, layers)` (measurement thread),
        #: so the livestream shows the edge search live. `None` = nobody listens.
        self.reference_debug_sink: Callable[..., None] | None = None
        self.frame_id = config.frame_id
        self.frame_convention = config.frame_convention
        self.configuration_id = self._build_configuration_id()

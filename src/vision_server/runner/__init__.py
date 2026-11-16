"""Assembly of the vision system, either as an install or as its own process.

The entry points are `install_vision_machine` (install into a server, `install.py`) and
`run` (own process, `standalone.py`). The install is split into steps, one module per
step. `wiring.Wiring` holds what the steps share (session, annotator, layer-2 run,
hand-eye session), because the method handlers only read it when called:

| Module | Task |
| --- | --- |
| `install.py` | `install_vision_machine`: the steps in fixed order |
| `wiring.py` | `Wiring`: shared state and the lock queries |
| `uamethods.py` | Building blocks of the OPC UA methods: replies, setup, JSON wrapper |
| `sources.py` | Find camera holders, open sources |
| `jobs.py` | `StartSingleJob`, `Stop`, `StartContinuous`, `Abort`, `Halt`, `Reset` |
| `calibration.py` | Calibration session, `ActiveCalibrationInfo`, `StartCalibration` and co. |
| `tag_map.py` | `TagMapJson` and `SetTagMap` |
| `livestream.py` | Overlay, live stream publisher, MJPEG server |
| `health.py` | Camera watchdog as `DeviceHealth` |
| `layer2.py`, `hand_eye.py` | Layer-2 run and hand-eye calibration (hand Pi) |
| `measure_board.py`, `measure_tags.py` | Measurements on request (board, tags) |
| `tag_reference.py` | Tag measurement via reference edges |
| `save_raw_image.py`, `save_use_case_image.py` | Raw image and use-case photo |
| `cell_watch.py` | Cell watch (ceiling camera) |
| `part10.py` | `VisionProgram` on the same jobs |
| `machine.py` | `VisionMachine`, `aclose`, event loop watchdog |
| `standalone.py` | `run`: own process |

All modules log under the name `vision_server.runner`. The import path and the names
that used to be defined here are kept.
"""

from .calibration import (  # noqa: F401
    CALIBRATION_PROGRESS_INTERVAL_S,
    _build_calibration_session,
    _calibration_info_payload,
    _write_calibration_info,
)
from .cell_watch import _install_cell_watch  # noqa: F401
from .hand_eye import _install_hand_eye_session  # noqa: F401
from .health import _camera_config_of, _start_camera_health  # noqa: F401
from .install import install_vision_machine
from .layer2 import _install_layer2_run  # noqa: F401
from .livestream import (  # noqa: F401
    _build_annotator,
    _start_camera_stream,
    _start_mjpeg_server,
)
from .machine import (  # noqa: F401
    LOOP_LAG_INTERVAL_S,
    LOOP_LAG_WARN_S,
    VisionMachine,
    _watch_loop_lag,
)
from .measure_board import _install_measure_board  # noqa: F401
from .measure_tags import _Activity, _install_measure_tags  # noqa: F401
from .save_raw_image import _install_save_raw_image  # noqa: F401
from .save_use_case_image import (  # noqa: F401
    USE_CASE_FRESH_FRAME_TIMEOUT_S,
    USE_CASE_MIN_FRAME_AGE_S,
    USE_CASE_POLL_INTERVAL_S,
    _build_use_case_pi_meta,
    _file_meta,
    _install_save_use_case_image,
    _load_file_content,
)
from .sources import _camera_owner, _open_source  # noqa: F401
from .standalone import run
from .tag_map import _write_tag_map_file  # noqa: F401
from .tag_reference import _install_tag_reference  # noqa: F401

__all__ = ["VisionMachine", "install_vision_machine", "run"]

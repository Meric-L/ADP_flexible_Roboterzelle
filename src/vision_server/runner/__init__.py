"""Vision system assembly, as an install into a server or as its own process.

The entry points are `install_vision_machine` (see `install.py`) and `run`
(see `standalone.py`). `wiring.Wiring` holds the state that the install steps
share, because the method handlers read it only when they are called.
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

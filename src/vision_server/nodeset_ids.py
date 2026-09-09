"""NodeIds und Namespace-URIs des OPC 40100 (Machine Vision) Nodesets."""

from asyncua import ua

MACHINE_VISION_NAMESPACE_URI = "http://opcfoundation.org/UA/MachineVision"

VISION_SYSTEM_TYPE = 1003
RESULT_TYPE = 2002

EVENT_JOB_STARTED = 1013
EVENT_STATE_CHANGED = 1018
EVENT_READY = 1023
EVENT_RESULT_READY = 1024
EVENT_ACQUISITION_DONE = 1025

STATE_INITIALIZED = 5056
STATE_READY = 5057
STATE_SINGLE_EXECUTION = 5058
STATE_CONTINUOUS_EXECUTION = 5059

OUTER_STATE_NAMES = ("Preoperational", "Halted", "Error", "Operational")


def mv(identifier: int, mv_idx: int) -> ua.NodeId:
    """Baut eine NodeId im Machine-Vision-Namespace."""
    return ua.NodeId(identifier, mv_idx)

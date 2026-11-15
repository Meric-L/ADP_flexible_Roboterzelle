"""NodeIds and namespace URIs of the companion specs in use.

Part 1 (OPC 40100-1) carries the job and result path. Part 2 (OPC 40100-2,
"AMCM" for Asset Management and Condition Monitoring) describes what the system
consists of and how it is doing -- camera, lens, computing unit. Part 2 brings
DI and Machinery along as dependencies. See
`src/vision_server/nodesets/README.md` for the pinned versions and the reasons.
"""

from enum import IntEnum

from asyncua import ua

MACHINE_VISION_NAMESPACE_URI = "http://opcfoundation.org/UA/MachineVision"

#: Part 2 and its dependencies. The trailing slash belongs to the URI -- unlike
#: Part 1, and a missing one makes `get_namespace_index` blind.
DI_NAMESPACE_URI = "http://opcfoundation.org/UA/DI/"
MACHINERY_NAMESPACE_URI = "http://opcfoundation.org/UA/Machinery/"
AMCM_NAMESPACE_URI = "http://opcfoundation.org/UA/MachineVision/AMCM/"

VISION_SYSTEM_TYPE = 1003
RESULT_TYPE = 2002

#: Machinery: the standard entry folder `Objects/Machines`. It comes ready-made from
#: the nodeset (`Organizes` from `i=85`) and is the intended place for machine
#: instances. `VisionMachine` hangs there, so that only `VisionProgram` remains
#: under `Objects` as the operator interface.
MACHINES_FOLDER = 1001

#: Part 2: root type of the asset view, with folders per component type
#: (ComputingDevices, ImageSensors, Lenses, SoftwareComponents, ...).
VISION_SYSTEM_ASSET_TYPE = 1008
VISION_ITEM_FOLDER_TYPE = 1005
VISION_COMPUTING_DEVICE_TYPE = 1010
VISION_IMAGE_SENSOR_TYPE = 1020
VISION_LENS_TYPE = 1022

#: Part 2: state block per component. The nodeset declares it as `Optional` on
#: each item type and on the root, and references it via `HasAddIn` -- so with
#: `instantiate_optional=False` it is never created on its own. All of its
#: children are optional as well, so the block comes out empty, and
#: `DeviceHealth` is added separately (asset_model._add_health).
VISION_HEALTH_INFO_TYPE = 1004

#: DI: DataType `DeviceHealthEnumeration` (OPC 10000-100) and the interface
#: `IDeviceHealthType`, which brings it to the state block.
DI_DEVICE_HEALTH_ENUMERATION = 6244
DI_DEVICE_HEALTH_INTERFACE = 15051

#: DI: the second part of `IDeviceHealthType`. Besides the state variable, the
#: standard provides a folder with real OPC UA alarms per state --
#: `DeviceHealthDiagnosticAlarmType` and its four concrete subtypes, all derived
#: from `AlarmConditionType`. Only this gives a client an event with timestamp,
#: source and severity, instead of a number it has to poll.
DI_FAILURE_ALARM_TYPE = 15292
DI_CHECK_FUNCTION_ALARM_TYPE = 15441
DI_OFF_SPEC_ALARM_TYPE = 15590
#: `MaintenanceRequiredAlarmType` (15739) is deliberately not used: without a wear
#: counter it could never fire, and an alarm that never comes is a promise we
#: cannot keep.


class DeviceHealth(IntEnum):
    """DI's `DeviceHealthEnumeration` according to NAMUR NE 107.

    Written by hand instead of read from the nodeset: `load_data_type_definitions()`
    fails on 40100 because of abstract structure base types (asyncua issue
    #1693). It is therefore written as
    `ua.Variant(int(value), ua.VariantType.Int32)` -- this is how OPC UA transfers
    an enumeration anyway.
    """

    NORMAL = 0
    FAILURE = 1
    CHECK_FUNCTION = 2
    OFF_SPEC = 3
    MAINTENANCE_REQUIRED = 4

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


def node_id(identifier: int, namespace_index: int) -> ua.NodeId:
    """Build a NodeId in a namespace index that is determined at runtime.

    Namespace indices are never hard-coded -- they shift as soon as another
    nodeset is added. That is exactly what happened with Part 2.
    """
    return ua.NodeId(identifier, namespace_index)


#: Old name, kept for the existing call sites.
mv = node_id

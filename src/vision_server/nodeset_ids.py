"""NodeIds und Namespace-URIs der benutzten Companion Specs.

Part 1 (OPC 40100-1) traegt den Job- und Ergebnispfad. Part 2 (OPC 40100-2,
"AMCM" fuer Asset Management and Condition Monitoring) beschreibt, woraus das
System besteht und wie es ihm geht -- Kamera, Objektiv, Recheneinheit. Part 2
bringt DI und Machinery als Abhaengigkeiten mit, siehe
`src/OPCUA/nodesets/README.md` fuer die gepinnten Versionen und warum.
"""

from enum import IntEnum

from asyncua import ua

MACHINE_VISION_NAMESPACE_URI = "http://opcfoundation.org/UA/MachineVision"

#: Part 2 und seine Abhaengigkeiten. Der Schraegstrich am Ende gehoert dazu --
#: anders als bei Part 1, und ein fehlender macht `get_namespace_index` blind.
DI_NAMESPACE_URI = "http://opcfoundation.org/UA/DI/"
MACHINERY_NAMESPACE_URI = "http://opcfoundation.org/UA/Machinery/"
AMCM_NAMESPACE_URI = "http://opcfoundation.org/UA/MachineVision/AMCM/"

VISION_SYSTEM_TYPE = 1003
RESULT_TYPE = 2002

#: Machinery: der Standard-Einstiegsordner `Objects/Machines`. Er kommt fertig
#: aus dem Nodeset (`Organizes` von `i=85`) und ist die vorgesehene Stelle fuer
#: Maschineninstanzen. Dort haengt `VisionMachine`, damit unter `Objects` nur
#: noch `VisionProgram` als Bedienoberflaeche steht.
MACHINES_FOLDER = 1001

#: Part 2: Wurzeltyp der Anlagensicht, mit Ordnern je Komponentenart
#: (ComputingDevices, ImageSensors, Lenses, SoftwareComponents, ...).
VISION_SYSTEM_ASSET_TYPE = 1008
VISION_ITEM_FOLDER_TYPE = 1005
VISION_COMPUTING_DEVICE_TYPE = 1010
VISION_IMAGE_SENSOR_TYPE = 1020
VISION_LENS_TYPE = 1022

#: Part 2: Zustandsblock je Komponente. Im Nodeset an jedem Item-Typ und an
#: der Wurzel als `Optional` deklariert und per `HasAddIn` referenziert --
#: mit `instantiate_optional=False` entsteht er also nie von selbst.
#: Alle seine Kinder sind ebenfalls optional, der Block kommt deshalb leer
#: heraus und `DeviceHealth` wird einzeln nachgelegt (asset_model._add_health).
VISION_HEALTH_INFO_TYPE = 1004

#: DI: DataType `DeviceHealthEnumeration` (OPC 10000-100) und das Interface
#: `IDeviceHealthType`, das sie an den Zustandsblock bringt.
DI_DEVICE_HEALTH_ENUMERATION = 6244
DI_DEVICE_HEALTH_INTERFACE = 15051


class DeviceHealth(IntEnum):
    """DI's `DeviceHealthEnumeration` nach NAMUR NE 107.

    Von Hand statt aus dem Nodeset gelesen: `load_data_type_definitions()`
    scheitert bei 40100 an abstrakten Struktur-Basistypen (asyncua-Issue
    #1693). Geschrieben wird deshalb als
    `ua.Variant(int(wert), ua.VariantType.Int32)` -- so uebertraegt OPC UA
    eine Enumeration ohnehin.
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
    """Baut eine NodeId in einem zur Laufzeit ermittelten Namensraum.

    Namensraumindizes werden nie hartkodiert -- sie verschieben sich, sobald ein
    weiteres Nodeset dazukommt. Genau das ist mit Part 2 passiert.
    """
    return ua.NodeId(identifier, namespace_index)


#: Alter Name, bleibt fuer die vorhandenen Aufrufstellen.
mv = node_id

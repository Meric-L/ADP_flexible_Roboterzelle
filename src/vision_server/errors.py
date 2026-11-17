"""Error codes for the `Error` output of the 40100 methods."""

from enum import IntEnum

from asyncua import ua


ENUM_NAME = "VisionErrorCode"
ENUM_VALUES = (
    ("OK", "OK"),
    ("INVALID_STATE", "Im aktuellen Zustand nicht möglich (z. B. noch kein Kamerabild)."),
    ("INVALID_ARGUMENT", "Ungültiger Parameter."),
    ("BUSY", "Der Pi ist beschäftigt – danach erneut versuchen."),
    ("UNKNOWN_RECIPE", "Unbekanntes Rezept."),
    ("DETECTION_FAILED", "Erkennung fehlgeschlagen."),
    ("INTERNAL", "Interner Fehler auf dem Pi."),
    ("CANCELLED", "Abgebrochen."),
)


async def install_error_enum(server, namespace_index: int) -> ua.NodeId:
    """Install the OPC UA Enumeration DataType and its German EnumValues."""
    node_id = ua.NodeId(ENUM_NAME, namespace_index)
    datatype = await server.create_custom_data_type(
        node_id, ua.QualifiedName(ENUM_NAME, namespace_index), ua.ObjectIds.Enumeration,
        description="Fehlercodes des Vision-Systems",
    )
    values = []
    for index, (name, description) in enumerate(ENUM_VALUES):
        value = ua.EnumValueType()
        value.Value = index
        value.DisplayName = ua.LocalizedText(name)
        value.Description = ua.LocalizedText(description, "de")
        values.append(value)
    await datatype.add_property(
        ua.NodeId(f"{ENUM_NAME}.EnumValues", namespace_index),
        ua.QualifiedName("EnumValues", namespace_index),
        values,
        ua.VariantType.ExtensionObject,
    )
    return node_id


class VisionErrorCode(IntEnum):
    """Stable numeric vision error codes used in OPC UA responses."""

    OK = 0
    INVALID_STATE = 1
    INVALID_ARGUMENT = 2
    BUSY = 3
    UNKNOWN_RECIPE = 4
    DETECTION_FAILED = 5
    INTERNAL = 6
    CANCELLED = 7


class VisionJobError(Exception):
    """Error carrying a code for the `Error` output."""

    def __init__(self, code: VisionErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

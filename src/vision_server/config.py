"""Konfiguration des Vision-Servers."""

from dataclasses import dataclass
from pathlib import Path

DEFAULT_NODESET_PATH = (
    Path(__file__).resolve().parent.parent / "OPCUA" / "Opc.Ua.MachineVision.NodeSet2.xml"
)


@dataclass(frozen=True)
class VisionServerConfig:
    """Alle Betriebsparameter einer Vision-Server-Instanz."""

    endpoint: str = "opc.tcp://0.0.0.0:4841/vision/machine/"
    application_uri: str = "urn:launch-rm:vision:machine"
    server_name: str = "Vision Machine"
    namespace_uri: str = "http://launch-rm.de/vision"
    vision_system_name: str = "VisionMachine"
    vision_system_id: str = "vision-hello-01"
    configuration_id: str = "hello-world-config"
    detection_profile: str = "hello_world"
    detection_latency: float = 0.25
    nodeset_path: Path = DEFAULT_NODESET_PATH
    known_recipe_ids: frozenset[str] = frozenset(
        {"", "hello-world", "calibration", "image-recognition"}
    )
    max_id_length: int = 128
    max_parameters: int = 16

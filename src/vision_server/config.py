"""Konfiguration des Vision-Servers."""

from dataclasses import dataclass
from pathlib import Path

from .profiles import AprilTagProfileConfig

DEFAULT_NODESET_PATH = (
    Path(__file__).resolve().parent.parent / "OPCUA" / "Opc.Ua.MachineVision.NodeSet2.xml"
)

#: RecipeId -> Erkennungsprofil. Tupel von Paaren, weil ein dict als
#: dataclass-Default verboten ist und ein Mapping die frozen dataclass
#: unhashbar machen wuerde. Einzige Wahrheit fuer Zulassung *und* Routing.
DEFAULT_RECIPE_PROFILES: tuple[tuple[str, str], ...] = (
    ("", "hello_world"),
    ("hello-world", "hello_world"),
    ("calibration", "calibration"),
    ("image-recognition", "image_recognition"),
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
    detection_latency: float = 0.25
    nodeset_path: Path = DEFAULT_NODESET_PATH
    recipe_profiles: tuple[tuple[str, str], ...] = DEFAULT_RECIPE_PROFILES
    max_id_length: int = 128
    max_parameters: int = 16
    frame_id: str = "world"
    #: Muss ueber der laengsten Job-Laufzeit liegen: der QR-Scan in
    #: `src/jobs/take_image.py` haelt die Kamera allein schon 30 s offen.
    job_timeout: float = 40.0
    apriltag: AprilTagProfileConfig | None = None

    @property
    def known_recipe_ids(self) -> frozenset[str]:
        """Zugelassene RecipeIds; Property, damit job.py unveraendert bleibt."""
        return frozenset(recipe for recipe, _ in self.recipe_profiles)

    @property
    def detection_profiles(self) -> frozenset[str]:
        """Alle referenzierten Profile."""
        return frozenset(profile for _, profile in self.recipe_profiles)

    def profile_for(self, recipe_id: str | None) -> str:
        """Profil zur RecipeId. Nur nach erfolgreicher Zulassung aufrufen."""
        return dict(self.recipe_profiles)[recipe_id or ""]

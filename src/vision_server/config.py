"""Konfiguration des Vision-Servers."""

from dataclasses import dataclass
from pathlib import Path

from .profiles import AprilTagProfileConfig, AssetConfig, CameraStreamConfig

_OPCUA_DIR = Path(__file__).resolve().parent.parent / "OPCUA"

DEFAULT_NODESET_PATH = _OPCUA_DIR / "Opc.Ua.MachineVision.NodeSet2.xml"

#: Part 2 und seine Abhaengigkeiten, in Importreihenfolge: DI, dann Machinery,
#: dann AMCM. Versionen sind gepinnt, siehe src/OPCUA/nodesets/README.md.
DEFAULT_AMCM_NODESET_PATHS: tuple[Path, ...] = (
    _OPCUA_DIR / "nodesets" / "Opc.Ua.Di.NodeSet2.xml",
    _OPCUA_DIR / "nodesets" / "Opc.Ua.Machinery.NodeSet2.xml",
    _OPCUA_DIR / "nodesets" / "Opc.Ua.MachineVision.AMCM.NodeSet2.xml",
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
    #: Frist fuer `JobRunner.stop()`, bis der Abbruch inkl. Aufraeumen und
    #: Zustandswechsel abgeschlossen sein muss.
    stop_timeout: float = 5.0
    #: Pause zwischen zwei Durchlaeufen im Dauerbetrieb. Ohne Pause liefe die
    #: Erkennung so schnell wie die Kamera Bilder gibt und belegte den Pi
    #: vollstaendig -- der Dauerbetrieb soll beobachten, nicht verdraengen.
    continuous_interval_s: float = 1.0
    apriltag: AprilTagProfileConfig | None = None
    #: `None` = Part 2 nicht laden. Kostet gemessen ~13 MB RSS und ~1,6 s
    #: Startzeit; fuer den Job-Pfad ist es nicht noetig.
    assets: AssetConfig | None = None
    amcm_nodeset_paths: tuple[Path, ...] = DEFAULT_AMCM_NODESET_PATHS
    #: `None` = kein Livestream-Knoten, keine geteilte Kamera geoeffnet (z. B.
    #: lokale Entwicklung ohne Kamera). Auf dem Pi setzt `OPCUA/server.py` sie.
    camera_stream: CameraStreamConfig | None = None

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

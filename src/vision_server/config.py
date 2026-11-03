"""Konfiguration des Vision-Servers."""

from dataclasses import dataclass, field, fields, replace
from pathlib import Path

from .profiles import _REPO_ROOT, AprilTagProfileConfig, AssetConfig, CameraStreamConfig

#: Die Nodesets liegen im Paket, das sie braucht -- frueher lagen sie unter
#: `src/OPCUA/`, weil das Verzeichnis aelter war als `vision_server/`
#: (Altlast C5, erledigt).
_NODESET_DIR = Path(__file__).resolve().parent / "nodesets"

DEFAULT_NODESET_PATH = _NODESET_DIR / "Opc.Ua.MachineVision.NodeSet2.xml"

#: Part 2 und seine Abhaengigkeiten, in Importreihenfolge: DI, dann Machinery,
#: dann AMCM. Versionen sind gepinnt, siehe nodesets/README.md.
DEFAULT_AMCM_NODESET_PATHS: tuple[Path, ...] = (
    _NODESET_DIR / "Opc.Ua.Di.NodeSet2.xml",
    _NODESET_DIR / "Opc.Ua.Machinery.NodeSet2.xml",
    _NODESET_DIR / "Opc.Ua.MachineVision.AMCM.NodeSet2.xml",
)

#: RecipeId -> Erkennungsprofil. Tupel von Paaren, weil ein dict als
#: dataclass-Default verboten ist und ein Mapping die frozen dataclass
#: unhashbar machen wuerde. Einzige Wahrheit fuer Zulassung *und* Routing.
#: Bewusst ohne Eintrag fuer `""`: ein Start ohne gewaehlten Job wird mit
#: UNKNOWN_RECIPE abgelehnt, statt stillschweigend irgendetwas zu fahren.
DEFAULT_RECIPE_PROFILES: tuple[tuple[str, str], ...] = (
    ("calibration", "calibration"),
    ("apriltag", "apriltag"),
)


@dataclass(frozen=True)
class ImageKind:
    """Zweck eines Bildes fuer `SaveImage`: Ablage, Dateinamen, Schema und Messung."""

    base_dir: Path  #: z. B. data/hoehentest
    folder: str  #: "lauf" -> lauf_NNN
    stem: str  #: "bild" -> bild_NNN.png/.json
    series_key: str  #: Schluessel der Reihennummer in der JSON ("lauf")
    number_key: str  #: Schluessel der Bildnummer in der JSON ("bild_nr")
    schema: str  #: z. B. "adp.hoehentest.image/1"
    measure: str  #: "trial" | "sheet" | "none"


_DEFAULT_USE_CASE_DIR = _REPO_ROOT / "data" / "use-case"


def _default_image_kinds() -> dict[str, "ImageKind"]:
    """Standard-Zwecke: Use-Case-Foto, Hoehentest und Drehtest."""
    return {
        "usecase": ImageKind(
            base_dir=_DEFAULT_USE_CASE_DIR,
            folder="reihe",
            stem="versuch",
            series_key="reihe",
            number_key="versuch",
            schema="adp.usecase.trial/1",
            measure="trial",
        ),
        "hoehentest": ImageKind(
            base_dir=_REPO_ROOT / "data" / "hoehentest",
            folder="lauf",
            stem="bild",
            series_key="lauf",
            number_key="bild_nr",
            schema="adp.hoehentest.image/1",
            measure="sheet",
        ),
        "drehtest": ImageKind(
            base_dir=_REPO_ROOT / "data" / "drehtest",
            folder="lauf",
            stem="bild",
            series_key="lauf",
            number_key="bild_nr",
            schema="adp.drehtest.image/1",
            measure="sheet",
        ),
    }


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
    nodeset_path: Path = DEFAULT_NODESET_PATH
    recipe_profiles: tuple[tuple[str, str], ...] = DEFAULT_RECIPE_PROFILES
    max_id_length: int = 128
    max_parameters: int = 16
    frame_id: str = "world"
    #: Must exceed the longest job runtime. The AprilTag job is decisive:
    #: `samples_per_job` captures, each waiting up to `capture_timeout_s`,
    #: plus detection and margin. War 20.0 -- live auf pi1 gemessen (A/B-Test
    #: 2026-09-24, ohne Livestream/Overlay-Konkurrenz, `subpixel_corner_
    #: refinement=True` auf vollen 12 MP): 1 von 4 Jobs riss trotzdem den
    #: 20s-Timeout (echter `asyncio.wait_for`-Timeout laut job.py, kein
    #: "kein Tag im Bild"). 3 Samples je bis zu `capture_timeout_s` Wartezeit
    #: plus Detektionszeit (py-spy: ~46% des Profils in `_locate -> detect`)
    #: plus `warmup_s` liessen dem alten Budget zu wenig Puffer. Zusaetzliche
    #: Sicherheitsmarge, bis `apriltag_quad_decimate` (siehe `profiles.py`)
    #: die Detektionszeit selbst gesenkt hat -- danach ggf. wieder senken.
    job_timeout: float = 30.0
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
    #: lokale Entwicklung ohne Kamera). Auf dem Pi setzt `vision_server/server.py` sie.
    camera_stream: CameraStreamConfig | None = None
    #: Ablage fuer `SaveRawImage` (verlustfreie PNG-Vollbilder). `None` = die
    #: Methode nicht anlegen.
    raw_image_dir: Path | None = _REPO_ROOT / "data" / "rohbilder"
    #: Ablage fuer `SaveUseCaseImage` (PNG + Metadaten-JSON je Versuch unter
    #: `reihe_NNN/versuch_NNN.*`). `None` = die Methode nicht anlegen.
    #: Bleibt als Kurzform fuer den Zweck "usecase" in `image_kinds` (siehe
    #: `__post_init__`): `None` entfernt ihn, ein anderer Pfad ersetzt dessen
    #: `base_dir`, sofern der Eintrag noch den Standardpfad hat.
    use_case_dir: Path | None = _DEFAULT_USE_CASE_DIR
    #: Zwecke fuer `SaveImage` (Schluessel = `Kind`). Leer = Methode nicht anlegen.
    image_kinds: dict[str, ImageKind] = field(default_factory=_default_image_kinds)
    #: Mess-Protokoll von `MeasureTagReference` (siehe `tagreference_log.py`).
    #: `None` = nichts schreiben; `GET /tagreference.jpg` liefert dann 404.
    tag_reference_debug_dir: Path | None = _REPO_ROOT / "data" / "tagreference"

    def __post_init__(self) -> None:
        """Haelt `use_case_dir` und den Zweck "usecase" in `image_kinds` zusammen."""
        kinds = dict(self.image_kinds)
        usecase = kinds.get("usecase")
        if usecase is not None:
            if self.use_case_dir is None:
                del kinds["usecase"]
            elif usecase.base_dir == _DEFAULT_USE_CASE_DIR and self.use_case_dir != usecase.base_dir:
                kinds["usecase"] = replace(usecase, base_dir=Path(self.use_case_dir))
        object.__setattr__(self, "image_kinds", kinds)

    def __hash__(self) -> int:
        """Hashbar bleiben (Konfiguration dient als Schluessel); das dict `image_kinds` zaehlt als Paare."""
        values = tuple(
            tuple(sorted(value.items())) if isinstance(value, dict) else value
            for value in (getattr(self, f.name) for f in fields(self))
        )
        return hash(values)

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

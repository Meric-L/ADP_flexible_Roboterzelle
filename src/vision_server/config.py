"""Configuration of the vision server."""

from dataclasses import dataclass
from pathlib import Path

from .profiles import _REPO_ROOT, AprilTagProfileConfig, AssetConfig, CameraStreamConfig

#: The nodesets live in this package, next to the code that needs them.
_NODESET_DIR = Path(__file__).resolve().parent / "nodesets"

DEFAULT_NODESET_PATH = _NODESET_DIR / "Opc.Ua.MachineVision.NodeSet2.xml"

#: Part 2 and its dependencies, in import order: DI, then Machinery, then AMCM.
#: Versions are pinned; see ``nodesets/README.md``.
DEFAULT_AMCM_NODESET_PATHS: tuple[Path, ...] = (
    _NODESET_DIR / "Opc.Ua.Di.NodeSet2.xml",
    _NODESET_DIR / "Opc.Ua.Machinery.NodeSet2.xml",
    _NODESET_DIR / "Opc.Ua.MachineVision.AMCM.NodeSet2.xml",
)

#: RecipeId -> detection profile; the single source for admission and routing. A tuple of
#: pairs keeps the frozen dataclass hashable. No entry for ``""``: a start without a
#: selected job is rejected with UNKNOWN_RECIPE.
DEFAULT_RECIPE_PROFILES: tuple[tuple[str, str], ...] = (
    ("calibration", "calibration"),
    ("apriltag", "apriltag"),
)


@dataclass(frozen=True)
class VisionServerConfig:
    """All operating parameters of one vision server instance."""

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
    #: Must exceed the longest job runtime: ``samples_per_job`` captures (each up to
    #: ``capture_timeout_s``) plus detection and a margin. 20 s was too tight at 12 MP
    #: with subpixel refinement.
    job_timeout: float = 30.0
    #: Deadline for ``JobRunner.stop()``: the abort, including cleanup and the state
    #: change, must be complete by then.
    stop_timeout: float = 5.0
    #: Pause between two runs in continuous mode, so detection does not occupy the Pi
    #: completely.
    continuous_interval_s: float = 1.0
    apriltag: AprilTagProfileConfig | None = None
    #: ``None`` = do not load Part 2 (costs about 13 MB RSS and 1.6 s startup).
    assets: AssetConfig | None = None
    amcm_nodeset_paths: tuple[Path, ...] = DEFAULT_AMCM_NODESET_PATHS
    #: ``None`` = no livestream node, no shared camera (e.g. development without one).
    camera_stream: CameraStreamConfig | None = None
    #: Storage for ``SaveRawImage`` (lossless PNG full frames). ``None`` = do not create
    #: the method.
    raw_image_dir: Path | None = _REPO_ROOT / "data" / "rohbilder"
    #: Storage for ``SaveUseCaseImage`` (PNG plus metadata JSON per trial, under
    #: ``reihe_NNN/versuch_NNN.*``). ``None`` = do not create the method.
    use_case_dir: Path | None = _REPO_ROOT / "data" / "use-case"
    #: Measurement log of ``MeasureTagReference`` (see ``tagreference_log.py``).
    #: ``None`` = write nothing; ``GET /tagreference.jpg`` then returns 404.
    tag_reference_debug_dir: Path | None = _REPO_ROOT / "data" / "tagreference"

    @property
    def known_recipe_ids(self) -> frozenset[str]:
        """Return the admitted RecipeIds. A property, so that job.py stays unchanged."""
        return frozenset(recipe for recipe, _ in self.recipe_profiles)

    @property
    def detection_profiles(self) -> frozenset[str]:
        """Return all referenced profiles."""
        return frozenset(profile for _, profile in self.recipe_profiles)

    def profile_for(self, recipe_id: str | None) -> str:
        """Return the profile for a RecipeId. Call only after admission has succeeded."""
        return dict(self.recipe_profiles)[recipe_id or ""]

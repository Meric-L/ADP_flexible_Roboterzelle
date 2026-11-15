"""Configuration of the vision server."""

from dataclasses import dataclass
from pathlib import Path

from .profiles import _REPO_ROOT, AprilTagProfileConfig, AssetConfig, CameraStreamConfig

#: The nodesets live in the package that needs them. They used to live under
#: ``src/OPCUA/``, because that directory predates ``vision_server/`` (tech debt C5, done).
_NODESET_DIR = Path(__file__).resolve().parent / "nodesets"

DEFAULT_NODESET_PATH = _NODESET_DIR / "Opc.Ua.MachineVision.NodeSet2.xml"

#: Part 2 and its dependencies, in import order: DI, then Machinery, then AMCM.
#: Versions are pinned; see ``nodesets/README.md``.
DEFAULT_AMCM_NODESET_PATHS: tuple[Path, ...] = (
    _NODESET_DIR / "Opc.Ua.Di.NodeSet2.xml",
    _NODESET_DIR / "Opc.Ua.Machinery.NodeSet2.xml",
    _NODESET_DIR / "Opc.Ua.MachineVision.AMCM.NodeSet2.xml",
)

#: RecipeId -> detection profile. A tuple of pairs, because a dict is not allowed as a
#: dataclass default, and a mapping would make the frozen dataclass unhashable. The
#: single source of truth for both admission *and* routing.
#: Deliberately no entry for ``""``: a start without a selected job is rejected with
#: UNKNOWN_RECIPE, instead of silently running something.
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
    #: Must exceed the longest job runtime. The AprilTag job is the deciding one:
    #: ``samples_per_job`` captures, each waiting up to ``capture_timeout_s``, plus
    #: detection and a margin. It was 20.0 -- measured live on pi1 (A/B test 2026-09-24,
    #: without livestream/overlay competition, ``subpixel_corner_refinement=True`` at
    #: full 12 MP): 1 of 4 jobs still hit the 20 s timeout (a real ``asyncio.wait_for``
    #: timeout according to job.py, not "no tag in image"). Three samples, each with up to
    #: ``capture_timeout_s`` of waiting, plus detection time (py-spy: ~46% of the profile
    #: in ``_locate -> detect``) plus ``warmup_s`` left the old budget too little buffer.
    #: Extra safety margin, until ``apriltag_quad_decimate`` (see ``profiles.py``) has
    #: itself reduced the detection time -- then lower it again if appropriate.
    job_timeout: float = 30.0
    #: Deadline for ``JobRunner.stop()``: the abort, including cleanup and the state
    #: change, must be complete by then.
    stop_timeout: float = 5.0
    #: Pause between two runs in continuous operation. Without it, detection would run as
    #: fast as the camera delivers images and occupy the Pi completely. Continuous mode is
    #: meant to observe, not to crowd out other work.
    continuous_interval_s: float = 1.0
    apriltag: AprilTagProfileConfig | None = None
    #: ``None`` = do not load Part 2. Measured cost: about 13 MB RSS and about 1.6 s of
    #: startup time. Not needed for the job path.
    assets: AssetConfig | None = None
    amcm_nodeset_paths: tuple[Path, ...] = DEFAULT_AMCM_NODESET_PATHS
    #: ``None`` = no livestream node, no shared camera opened (e.g. local development
    #: without a camera). On the Pi, ``vision_server/server.py`` sets it.
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

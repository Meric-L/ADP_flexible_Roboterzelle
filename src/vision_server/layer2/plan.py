"""Approach targets of the layer 2 run: where the flange must go so that the hand camera sees a tag.

World tags and module tags go through the same planning (``plan_step``); they differ
only in where the tag pose in the world frame comes from. The camera may look at a tag
obliquely if the frontal view is out of reach (work plan ``layer2-schraege-ansicht.md``).

The chain for each target::

    T_tag_cam      = 180 degrees around x, ``standoff_m`` along +z of the tag
    T_world_cam    = T_world_tag · T_tag_cam
    T_world_flange = T_world_cam · inv(T_flansch_cam)
    T_base_flange  = inv(T_world_base) · T_world_flange     <- approach target

The approach step (``approach_standoff_m``, a larger distance) measures the tag pose in
a wide image, and the following close step is planned again from that pose. The anchor
step sets the world base from the world tag; ``T_world_base`` is the rough base from
layer 1 until then, and the measured one afterwards, which is why planning is repeated.

Tag frame as in ``tagloc.pose``: +x right, +y towards the top edge of the print, +z out
of the tag. Camera frame in OpenCV convention: +z forward, +x right, +y down.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace

import numpy as np

from tagloc.geometry import (
    Pose,
    compose,
    from_rotation_translation,
    invert,
    pose_from_dict,
    pose_to_dict,
    rotation_from_rvec,
)
from tagloc.tagmap import TagMap, nearest_world_tag

RUN_SCHEMA = "wsc.vision.layer2.run/1"
REPORT_SCHEMA = "wsc.vision.layer2.report/1"
TARGET_SCHEMA = "wsc.vision.layer2.target/1"
STATUS_SCHEMA = "wsc.vision.layer2.status/1"

#: Distance of the camera in front of the tag. The value the hand-eye plan works with:
#: close enough for a 50 mm tag, far enough for sharpness.
DEFAULT_STANDOFF_M = 0.3
#: Reach of the flange around the base -- UR5e 0.85 m, FR3 0.855 m. A note only: a target
#: outside this range is shown and skipped, not hidden.
DEFAULT_REACH_M = 0.85
#: Distance of the approach image. Twice as far as the close image: the rough pose from
#: layer 1 may be off by a few centimetres, and the tag should still be in the image.
#: ``0`` disables the approach.
DEFAULT_APPROACH_STANDOFF_M = 0.6

#: Up to this tilt (degrees from the perpendicular to the tag) the camera may look at a
#: tag obliquely. From about 60 degrees on, a tag in the image becomes so narrow that its
#: pose becomes uncertain.
DEFAULT_MAX_TILT_DEG = 60.0
#: The steps in which the planner tries the tilt.
_TILT_STEPS_DEG = (0.0, 15.0, 30.0, 45.0, 60.0, 75.0)
#: A target with some margin to the reach limit is preferred -- at the edge the IK often
#: finds no configuration with the requested orientation.
_COMFORTABLE_REACH = 0.9

ANCHOR = "anchor"
MEASURE = "measure"
#: Purpose of the approach step (standoff from ``approach_standoff_m``). Always placed
#: directly before the anchor or measurement step whose tag pose it measures.
APPROACH = "approach"

#: Camera looks at the tag: a 180-degree rotation around x turns the +z of the tag against
#: the viewing direction of the camera.
_FACING = np.diag([1.0, -1.0, -1.0])


@dataclass(frozen=True)
class TagRequest:
    """A tag on a module, with its pose in the module frame."""

    tag_id: int
    #: ``T_module_tag`` from the module catalog.
    pose_in_module: Pose


@dataclass(frozen=True)
class ModuleRequest:
    """A module to measure, with its rough pose and its tags."""

    module_id: str
    name: str
    #: ``T_world_module`` from layer 1, rough.
    pose_world_module: Pose
    tags: tuple[TagRequest, ...]


@dataclass(frozen=True)
class RunRequest:
    """A complete layer 2 run request: base pose, modules and planning options."""

    #: ``T_world_base`` from layer 1, rough.
    robot_base: Pose
    modules: tuple[ModuleRequest, ...]
    standoff_m: float = DEFAULT_STANDOFF_M
    reach_m: float = DEFAULT_REACH_M
    #: ``0`` = without approach.
    approach_standoff_m: float = DEFAULT_APPROACH_STANDOFF_M
    #: ``0`` = always frontal.
    max_tilt_deg: float = DEFAULT_MAX_TILT_DEG

    def standoff_for(self, purpose: str) -> float:
        """Return the standoff distance for a step purpose."""
        return self.approach_standoff_m if purpose == APPROACH else self.standoff_m


@dataclass(frozen=True)
class RobotReport:
    """The robot's answer to one step."""

    step_index: int
    reached: bool
    #: The pose the robot **reports** -- never the target. ``None`` without a move.
    flange_in_base: Pose | None = None
    reason: str = ""


@dataclass(frozen=True)
class StepSpec:
    """What a step looks at -- independent of where the base is."""

    purpose: str
    name: str
    module_id: str = ""
    #: World tag: its single pose. Module: ``None``, the tags come from ``module``.
    world_tag: tuple[int, Pose] | None = None
    module: ModuleRequest | None = None
    #: The tag with its world pose, as measured by the approach image. Replaces the rough
    #: pose from layer 1; ``None`` until measured.
    seen_tag: tuple[int, Pose] | None = None

    def tag_candidates(self) -> list[tuple[int, Pose]]:
        """Return all tags of this step with their pose in the world frame."""
        if self.seen_tag is not None:
            return [self.seen_tag]
        if self.world_tag is not None:
            return [self.world_tag]
        assert self.module is not None
        return [
            (tag.tag_id, compose(self.module.pose_world_module, tag.pose_in_module))
            for tag in self.module.tags
        ]


@dataclass(frozen=True)
class ViewTarget:
    """A camera pose from which the tag is seen, with the matching flange pose and reach."""

    camera_in_world: Pose
    flange_in_world: Pose
    flange_in_base: Pose
    #: Distance of the flange from the base, in metres.
    reach_m: float
    #: How obliquely the camera looks at the tag, in degrees; 0 = frontal.
    tilt_deg: float = 0.0


@dataclass(frozen=True)
class PlannedStep:
    """A planned step: the chosen tag, its world pose, the target, and whether it is reachable."""

    spec: StepSpec
    tag_id: int
    tag_in_world: Pose
    target: ViewTarget
    reachable: bool


def view_target(
    pose_world_tag: Pose,
    pose_world_base: Pose,
    pose_flange_cam: Pose,
    standoff_m: float,
    tilt_deg: float = 0.0,
) -> ViewTarget:
    """Compute where the camera and flange must be to see the tag.

    ``tilt_deg`` tilts the camera around the tag centre towards the robot base. It stays
    ``standoff_m`` from the tag and keeps looking at the centre of the tag, only obliquely
    instead of perpendicularly. 0 = frontal.
    """
    pose_tag_cam = from_rotation_translation(_FACING, [0.0, 0.0, standoff_m])
    if tilt_deg:
        # Base in the tag frame, projected into the tag plane: the +z tilts towards it.
        towards_base = (invert(pose_world_tag) @ np.append(pose_world_base[:3, 3], 1.0))[:3]
        towards_base[2] = 0.0
        length = float(np.linalg.norm(towards_base))
        if length > 1e-9:
            axis = np.cross([0.0, 0.0, 1.0], towards_base / length)
            tilt = np.eye(4)
            tilt[:3, :3] = rotation_from_rvec(axis * math.radians(tilt_deg))
            pose_tag_cam = tilt @ pose_tag_cam
    camera_in_world = compose(pose_world_tag, pose_tag_cam)
    flange_in_world = compose(camera_in_world, invert(pose_flange_cam))
    flange_in_base = compose(invert(pose_world_base), flange_in_world)
    return ViewTarget(
        camera_in_world=camera_in_world,
        flange_in_world=flange_in_world,
        flange_in_base=flange_in_base,
        reach_m=float(np.linalg.norm(flange_in_base[:3, 3])),
        tilt_deg=float(tilt_deg),
    )


def tag_facing_base(
    candidates: list[tuple[int, Pose]], base_position
) -> tuple[int, Pose]:
    """Return the tag whose face points most towards the base.

    A tag on the back would mean driving around the module; one on top of the module can
    be seen well from above.
    """
    base_position = np.asarray(base_position, dtype=np.float64)
    best: tuple[float, int, Pose] | None = None
    for tag_id, pose in candidates:
        towards_base = base_position - pose[:3, 3]
        length = float(np.linalg.norm(towards_base)) or 1.0
        score = float(pose[:3, 2] @ towards_base) / length
        if best is None or score > best[0]:
            best = (score, tag_id, pose)
    if best is None:
        raise ValueError("Schritt ohne Tag")
    return best[1], best[2]


def plan_step(
    spec: StepSpec,
    pose_world_base: Pose,
    pose_flange_cam: Pose,
    standoff_m: float,
    reach_m: float,
    max_tilt_deg: float = 0.0,
) -> PlannedStep:
    """Plan one step, whether it is a world tag or a module.

    The most frontal view with margin to the reach; if there is none, the most frontal
    one within reach; otherwise the nearest one to the base (then ``reachable = False``
    and the step is skipped).
    """
    tag_id, tag_in_world = tag_facing_base(spec.tag_candidates(), pose_world_base[:3, 3])
    tilts = [tilt for tilt in _TILT_STEPS_DEG if tilt <= max_tilt_deg] or [0.0]
    targets = [
        view_target(tag_in_world, pose_world_base, pose_flange_cam, standoff_m, tilt)
        for tilt in tilts
    ]
    target = next(
        (t for t in targets if t.reach_m <= reach_m * _COMFORTABLE_REACH),
        next((t for t in targets if t.reach_m <= reach_m), None),
    ) or min(targets, key=lambda t: t.reach_m)
    return PlannedStep(
        spec=spec,
        tag_id=tag_id,
        tag_in_world=tag_in_world,
        target=target,
        reachable=target.reach_m <= reach_m,
    )


def plan_run(
    request: RunRequest, tag_map: TagMap, pose_flange_cam: Pose
) -> list[PlannedStep]:
    """Step 0 at the world tag, then the modules, nearest first.

    With approach, each of these steps is preceded by one with ``purpose`` ``approach``:
    approach world tag, anchor, approach module A, module A, and so on.

    Planned with the rough base from layer 1; after anchoring, the run plans the remaining
    steps again with ``plan_step``.

    Raises:
        ValueError: The tag map knows no measured world tag.
    """
    nearest = nearest_world_tag(tag_map, request.robot_base)
    if nearest is None:
        raise ValueError("Die Tag-Map kennt keinen vermessenen Welttag -- kein Anker moeglich.")
    world_tag_id = nearest[0]
    anchor = StepSpec(
        purpose=ANCHOR,
        name=f"Welttag {world_tag_id}",
        world_tag=(world_tag_id, tag_map.reference_poses()[world_tag_id]),
    )
    plan = [
        plan_step(
            anchor,
            request.robot_base,
            pose_flange_cam,
            request.standoff_m,
            request.reach_m,
            request.max_tilt_deg,
        )
    ]
    modules = [
        plan_step(
            StepSpec(purpose=MEASURE, name=module.name, module_id=module.module_id, module=module),
            request.robot_base,
            pose_flange_cam,
            request.standoff_m,
            request.reach_m,
            request.max_tilt_deg,
        )
        for module in request.modules
        if module.tags
    ]
    # From near to far: the robot works its way outwards, instead of driving criss-cross
    # through the cell.
    modules.sort(key=lambda step: step.target.reach_m)
    steps = plan + modules
    if request.approach_standoff_m <= 0:
        return steps
    with_approach: list[PlannedStep] = []
    for step in steps:
        with_approach.append(
            plan_step(
                replace(step.spec, purpose=APPROACH),
                request.robot_base,
                pose_flange_cam,
                request.approach_standoff_m,
                request.reach_m,
                request.max_tilt_deg,
            )
        )
        with_approach.append(step)
    return with_approach


def find_detection(detections, tag_id: int) -> dict | None:
    """Return the detection that contains this tag.

    The tag may be a detection on its own or be merged with other tags of the same module
    (``tagIds``).
    """
    for detection in detections:
        attributes = detection.get("attributes") or {}
        if attributes.get("tagId") == tag_id or tag_id in (attributes.get("tagIds") or []):
            return detection
    return None


def tag_in_world_from_detection(detection: dict, tag_id: int, tag_map: TagMap) -> Pose:
    """Return the world pose of tag ``tag_id`` from a detection in the world frame.

    If the tag map knows the tag, the reported pose is that of the **module**
    (``T_world_tag · tag_to_module``), so the CAD offset is calculated back. Unknown tags
    come without an offset, as ``TAG-<id>``.

    Raises:
        ValueError: The pose is missing or invalid.
    """
    pose = _pose(
        {"position": detection.get("position"), "orientation": detection.get("orientation")},
        f"Detection Tag {tag_id}",
    )
    entry = tag_map.get(tag_id)
    if entry is not None:
        pose = compose(pose, invert(entry.tag_to_module))
    return pose


# --- JSON ------------------------------------------------------------------


def _pose(value, where: str) -> Pose:
    """Parse a JSON pose dict, raising ``ValueError`` with the location ``where`` on error."""
    if not isinstance(value, dict):
        raise ValueError(f"{where}: Pose fehlt (position, orientation).")
    position = value.get("position")
    orientation = value.get("orientation")
    if not _numbers(position, 3) or not _numbers(orientation, 4):
        raise ValueError(f"{where}: position [x,y,z] und orientation [qx,qy,qz,qw] noetig.")
    if math.sqrt(sum(float(v) ** 2 for v in orientation)) < 1e-9:
        raise ValueError(f"{where}: Quaternion der Laenge 0.")
    return pose_from_dict({"position": position, "orientation": orientation})


def _numbers(value, count: int) -> bool:
    """Return True if ``value`` is a list of ``count`` finite real numbers (no bools)."""
    return (
        isinstance(value, list)
        and len(value) == count
        and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
            for v in value
        )
    )


def _load(text: str, schema: str) -> dict:
    """Parse JSON text and check that its ``schema`` field matches."""
    try:
        data = json.loads(text or "")
    except json.JSONDecodeError as error:
        raise ValueError(f"Kein JSON: {error}") from None
    if not isinstance(data, dict) or data.get("schema") != schema:
        raise ValueError(f"Erwartet schema „{schema}“.")
    return data


def parse_run_request(text: str) -> RunRequest:
    """Read ``wsc.vision.layer2.run/1``; raises ``ValueError`` with a plain message."""
    data = _load(text, RUN_SCHEMA)
    robot_base = _pose(data.get("robotBase"), "robotBase")
    raw_modules = data.get("modules", [])
    if not isinstance(raw_modules, list):
        raise ValueError("modules muss eine Liste sein.")
    modules = []
    for index, raw in enumerate(raw_modules):
        where = f"modules[{index}]"
        if not isinstance(raw, dict) or not isinstance(raw.get("moduleId"), str):
            raise ValueError(f"{where}: moduleId fehlt.")
        raw_tags = raw.get("tags", [])
        if not isinstance(raw_tags, list):
            raise ValueError(f"{where}: tags muss eine Liste sein.")
        tags = []
        for tag_index, tag in enumerate(raw_tags):
            tag_where = f"{where}.tags[{tag_index}]"
            if not isinstance(tag, dict) or not isinstance(tag.get("tagId"), int):
                raise ValueError(f"{tag_where}: tagId fehlt.")
            tags.append(
                TagRequest(tag["tagId"], _pose(tag.get("poseInModule"), f"{tag_where}.poseInModule"))
            )
        modules.append(
            ModuleRequest(
                module_id=raw["moduleId"],
                name=str(raw.get("name") or raw["moduleId"]),
                pose_world_module=_pose(raw.get("pose"), f"{where}.pose"),
                tags=tuple(tags),
            )
        )
    options = data.get("options") or {}
    standoff_m = _positive(options.get("standoffM", DEFAULT_STANDOFF_M), "options.standoffM")
    reach_m = _positive(options.get("reachM", DEFAULT_REACH_M), "options.reachM")
    approach_m = options.get("approachStandoffM", DEFAULT_APPROACH_STANDOFF_M)
    if approach_m != 0:
        approach_m = _positive(approach_m, "options.approachStandoffM")
        if approach_m <= standoff_m:
            raise ValueError(
                "options.approachStandoffM muss groesser als standoffM sein (0 = aus)."
            )
    max_tilt = options.get("maxTiltDeg", DEFAULT_MAX_TILT_DEG)
    if isinstance(max_tilt, bool) or not isinstance(max_tilt, (int, float)) or not 0 <= max_tilt <= 80:
        raise ValueError("options.maxTiltDeg muss zwischen 0 und 80 liegen.")
    return RunRequest(
        robot_base, tuple(modules), standoff_m, reach_m, float(approach_m), float(max_tilt)
    )


def _positive(value, where: str) -> float:
    """Return ``value`` as float if it is a positive number, else raise ``ValueError``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0:
        raise ValueError(f"{where} muss eine positive Zahl sein.")
    return float(value)


def parse_report(text: str) -> RobotReport:
    """Read ``wsc.vision.layer2.report/1``; raises ``ValueError`` with a plain message."""
    data = _load(text, REPORT_SCHEMA)
    step_index = data.get("stepIndex")
    if not isinstance(step_index, int) or isinstance(step_index, bool):
        raise ValueError("stepIndex fehlt.")
    reached = data.get("reached")
    if not isinstance(reached, bool):
        raise ValueError("reached muss true oder false sein.")
    if not reached:
        return RobotReport(step_index, False, None, str(data.get("reason") or ""))
    return RobotReport(step_index, True, _pose(data.get("flangeInBase"), "flangeInBase"))


def pose_parameters(pose: Pose) -> list[str]:
    """Return the robot pose as job parameters: seven strings x, y, z, qx, qy, qz, qw.

    This is the format expected by ``detection.apriltag.robot_pose_from_parameters``.
    """
    data = pose_to_dict(pose)
    return [repr(float(value)) for value in data["position"] + data["orientation"]]

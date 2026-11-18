"""From tag to module. Pure composition, no OpenCV.

This is the **only** place in the project where the chain

    T_world_module = T_world_cam @ T_cam_tag @ T_tag_module

is evaluated. Both cameras call the same function. They differ only in what they see
(doc/project_docs/work-plans/apriltag-world-tag-concept.md):

* **Ceiling camera** -- sees the world tags, the robot and the modules in the same
  image. It gives the overview, and only here can the question "which world tag is the
  robot next to" be answered, because only this camera sees both at once.
* **Hand camera (eye-in-hand)** -- localizes itself **once** at the start of a run
  against a world tag, and afterwards measures each module within reach from close up.
  It does not keep the world tag in view: at 0.3 m in front of a module, the nearest
  world tag is one to two metres away and outside the field of view. The gap is bridged
  by the kinematics; see ``anchor_from_localization`` and ``localize_camera_from_anchor``.
  The algebra is in ``handeye.py``.

**Only the world tags are stationary.** Everything else in the cell moves: the modules
as well as the robot. The robot is a module like any other; it is just the one that is
always in use.

``T_world_cam`` can therefore come from two sources. ``CameraLocalization.origin`` says
which one: optically from the world tags (``ORIGIN_WORLD_TAGS``, the precise path, always
preferred when a world tag is in the image), or taken over from the anchor plus the
current robot pose (``ORIGIN_ROBOT_POSE``).
"""

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from .geometry import (
    Pose,
    average_poses,
    compose,
    identity,
    invert,
    rotation_distance_rad,
    translation_distance_m,
)
from .handeye import HandEye, RobotAnchor, anchor_world_base, camera_pose_from_robot
from .observations import TagPose
from .tagmap import ROBOT_ROLE, TagMap, merge_tag_poses, nearest_world_tag

_log = logging.getLogger(__name__)

#: Where a measurement comes from. The ceiling provides the overview, the flange camera
#: the exact value. This is exactly the ranking used by ``merge_locations``: an entry
#: later in this tuple wins.
SOURCE_CEILING = "ceiling"
SOURCE_FLANGE = "flange"
SOURCE_PRECEDENCE = (SOURCE_CEILING, SOURCE_FLANGE)

#: Where ``T_world_cam`` comes from. The optical path via the world tags is the precise
#: one and always wins when a world tag is in the image. The path via the anchor plus
#: kinematics is the fallback in between: it carries the robot from module to module
#: when no world tag is visible any more.
ORIGIN_WORLD_TAGS = "world_tags"
ORIGIN_ROBOT_POSE = "robot_pose"

#: How ``T_world_cam`` was computed from the world tags. ``joint``: one PnP over all
#: corners of all world tags. This is the precise path, as soon as two unambiguous world
#: tags are in the image. ``single``: one world tag. ``average``: mean of the per-tag
#: poses, used only as a fallback (without calibration, or when the joint PnP fails).
SOLVER_JOINT = "joint"
SOLVER_SINGLE = "single"
SOLVER_AVERAGE = "average"
#: The camera pose was not estimated from this image, but taken over from an earlier,
#: precise estimate (fixed-mounted camera). The one visible world tag then serves only
#: as a consistency check.
SOLVER_STORED = "stored"


@dataclass(frozen=True)
class CameraLocalization:
    """Where the camera is, and how well the world tags agree with it."""

    pose_world_cam: Pose
    #: Every world tag that contributed, in ascending order.
    world_tag_ids: tuple[int, ...]
    #: The nearest world tag. The hand camera aligns itself to this one.
    primary_tag_id: int
    #: Largest deviation of a single estimate from the merged pose. The honest quality
    #: figure: if the four world tags disagree, the measurement is wrong, and this number
    #: says so instead of disappearing into an average.
    spread_m: float = 0.0
    spread_rad: float = 0.0
    #: ``ORIGIN_WORLD_TAGS`` or ``ORIGIN_ROBOT_POSE``: how this pose was obtained. A
    #: consumer must be able to tell a directly measured pose from one carried over by
    #: kinematics; the two are not equally accurate.
    origin: str = ORIGIN_WORLD_TAGS
    #: ``SOLVER_JOINT``, ``SOLVER_SINGLE`` or ``SOLVER_AVERAGE``; ``""`` if taken over
    #: from the anchor.
    solver: str = ""
    #: Reprojection RMS of the camera pose in pixels: for ``joint`` over all corners of
    #: all world tags, for ``single`` that of the one tag. Otherwise NaN.
    reprojection_error_px: float = float("nan")
    #: Per world tag, the RMS of its corners under the joint pose, which shows which tag
    #: on the map contradicts the others. Empty unless the solver is ``joint``.
    tag_errors_px: dict[int, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ModuleLocation:
    """A localized module."""

    module_id: str
    instance_id: str
    pose: Pose
    frame_id: str
    tag_id: int
    reprojection_error_px: float = float("nan")
    ambiguous: bool = False
    confidence: float = 1.0
    #: Role from the tag map: ``module`` or ``robot``. The robot is reported like any other
    #: module; this field tells the two apart.
    role: str = ""
    #: Which camera made the measurement. See ``SOURCE_PRECEDENCE``.
    source: str = ""
    #: The world tag this module is next to, ``-1`` if unknown.
    reference_tag_id: int = -1
    #: ``T_worldtag_module``: the pose relative to this world tag. The cell is built
    #: around the world tags, so this is the number a module is set up and checked
    #: against. ``pose`` stays in the shared world frame.
    pose_in_reference_tag: Pose | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


def confidence_from(tag_pose: TagPose, *, max_reprojection_error_px: float = 3.0) -> float:
    """Compute the confidence in [0, 1] from reprojection error and ambiguity.

    Deliberately not a constant 1.0 as in earlier versions. A confidence that is always
    1.0 carries no information, yet downstream logic would still be tempted to trust it.
    """
    error = tag_pose.reprojection_error_px
    if math.isnan(error) or max_reprojection_error_px <= 0.0:
        quality = 0.5
    else:
        quality = max(0.0, 1.0 - error / max_reprojection_error_px)
    if tag_pose.is_ambiguous:
        quality *= 0.5
    return round(min(1.0, max(0.0, quality)), 4)


def localize_camera(
    tag_poses: Sequence[TagPose],
    tag_map: TagMap,
    *,
    max_reprojection_error_px: float = 3.0,
    calibration: Any = None,
) -> CameraLocalization | None:
    """Determine ``T_world_cam`` from the world tags visible in this image.

    For each world tag, ``T_world_cam = T_world_tag @ T_cam_tag^-1``. With a
    ``calibration`` and at least two world tags, the camera pose is then solved
    **jointly** from all of their corners in one PnP
    (``tagloc.pose.solve_camera_from_world_tags``). Four corners 20 cm apart determine
    the tilt of a single-tag estimate poorly from 2-3 m away, whereas the corners of all
    world tags span the whole cell. ``spread_m`` then shows how far the single-tag
    estimates deviate from the joint pose. It is a diagnostic, not the pose.

    Without a calibration (or if the joint solution fails), the single-tag estimates are
    averaged with confidence weights, as before. A tag seen far away and at a shallow
    angle must not pull as hard as one that fills the image. The joint solution is the
    only place where this module touches OpenCV, and only when a calibration is passed.

    ``primary_tag_id`` is the world tag closest to the camera. The hand camera aligns to
    it. When only one world tag is in the image, it is simply that tag.

    Returns:
        The localization, or ``None`` if no world tag is visible. In that case the
        caller stays in the camera frame, because guessing a world pose would be the
        worse answer.
    """
    references = tag_map.reference_poses()
    candidates: list[tuple[int, Pose, float, float]] = []
    for tag_pose in tag_poses:
        world_pose = references.get(tag_pose.tag_id)
        if world_pose is None:
            continue
        candidates.append(
            (
                int(tag_pose.tag_id),
                compose(world_pose, invert(tag_pose.pose_cam_tag)),
                confidence_from(
                    tag_pose, max_reprojection_error_px=max_reprojection_error_px
                ),
                # Camera-to-tag distance, i.e. the length of the measured translation.
                # The distance to the identity pose is exactly this norm, so no separate
                # numpy call is needed here.
                translation_distance_m(identity(), tag_pose.pose_cam_tag),
            )
        )
    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])
    estimates = [item[1] for item in candidates]
    merged = average_poses(estimates, [item[2] for item in candidates])
    solver = SOLVER_SINGLE if len(candidates) == 1 else SOLVER_AVERAGE
    error_px = float("nan")
    tag_errors: dict[int, float] = {}
    if len(candidates) == 1:
        error_px = next(
            tag_pose.reprojection_error_px
            for tag_pose in tag_poses
            if tag_pose.tag_id == candidates[0][0]
        )
    elif calibration is not None:
        joint = _solve_joint(tag_poses, tag_map, calibration, merged)
        if joint is not None:
            merged = invert(joint.pose_cam_world)
            solver = SOLVER_JOINT
            error_px = joint.reprojection_error_px
            tag_errors = dict(joint.tag_errors_px)
    if solver == SOLVER_AVERAGE:
        _log.debug("Kamerapose aus %d Welttags gemittelt", len(candidates))

    # A tie is broken by tag ID, so the choice is reproducible from image to image.
    primary = min(candidates, key=lambda item: (item[3], item[0]))[0]
    return CameraLocalization(
        pose_world_cam=merged,
        world_tag_ids=tuple(item[0] for item in candidates),
        primary_tag_id=primary,
        spread_m=max(translation_distance_m(merged, pose) for pose in estimates),
        spread_rad=max(rotation_distance_rad(merged, pose) for pose in estimates),
        solver=solver,
        reprojection_error_px=error_px,
        tag_errors_px=tag_errors,
    )


def _solve_joint(tag_poses, tag_map: TagMap, calibration, initial_world_cam: Pose):
    """Call ``solve_camera_from_world_tags``, returning ``None`` instead of raising.

    If the joint PnP fails, the average of the single tags remains: a worse pose is
    better than no world at all.
    """
    try:
        from .pose import solve_camera_from_world_tags

        references = tag_map.reference_poses()
        return solve_camera_from_world_tags(
            tag_poses,
            references,
            {tag_id: tag_map.size_for(tag_id, 0.0) for tag_id in references},
            calibration,
            initial_pose_cam_world=invert(initial_world_cam),
        )
    except Exception:
        _log.warning("Gemeinsames PnP ueber die Welttags gescheitert -- nehme das Mittel", exc_info=True)
        return None


def camera_pose_from_reference_tags(
    tag_poses: Sequence[TagPose], tag_map: TagMap
) -> Pose | None:
    """Return only ``T_world_cam``, without the quality figures.

    Kept for the CLI tools that transform a pose and have no use for the spread. New
    code should call ``localize_camera``: discarding ``spread_m`` would mean discarding
    the answer to whether the measurement is still valid.
    """
    localization = localize_camera(tag_poses, tag_map)
    return None if localization is None else localization.pose_world_cam


def locate_modules(
    tag_poses: Sequence[TagPose],
    tag_map: TagMap,
    *,
    localization: CameraLocalization | None,
    frame_id: str,
    source: str = "",
    max_reprojection_error_px: float = 3.0,
) -> list[ModuleLocation]:
    """Convert module tag poses into module poses.

    Tags without a map entry are still included, with their tag ID as the module ID and
    without a CAD offset. An unknown tag is information, not an error: most likely the
    map entry is simply missing.

    If the camera pose is known, each module also gets the world tag it stands next to,
    and its pose relative to that tag. The cell is built around the world tags, so this
    is the number a module is set up and checked against.
    """
    located: list[ModuleLocation] = []
    for tag_pose in tag_poses:
        entry = tag_map.get(tag_pose.tag_id)
        if entry is not None and entry.is_reference:
            continue  # World tags are the anchor points, not modules.
        chain = [tag_pose.pose_cam_tag]
        if entry is not None:
            chain.append(entry.tag_to_module)
        if localization is not None:
            chain.insert(0, localization.pose_world_cam)
        pose = compose(*chain)

        reference_tag_id = -1
        pose_in_reference_tag: Pose | None = None
        if localization is not None:
            nearest = nearest_world_tag(tag_map, pose)
            if nearest is not None:
                reference_tag_id = nearest[0]
                pose_in_reference_tag = compose(
                    invert(tag_map[reference_tag_id].pose_in_world), pose
                )

        located.append(
            ModuleLocation(
                module_id=(
                    entry.module_id if entry and entry.module_id else f"TAG-{tag_pose.tag_id}"
                ),
                instance_id=(
                    entry.instance_id if entry and entry.instance_id else f"tag-{tag_pose.tag_id}"
                ),
                pose=pose,
                frame_id=frame_id,
                tag_id=tag_pose.tag_id,
                reprojection_error_px=tag_pose.reprojection_error_px,
                ambiguous=tag_pose.is_ambiguous,
                confidence=confidence_from(
                    tag_pose, max_reprojection_error_px=max_reprojection_error_px
                ),
                role=entry.role if entry is not None else "",
                source=source,
                reference_tag_id=reference_tag_id,
                pose_in_reference_tag=pose_in_reference_tag,
                attributes=dict(tag_pose.attributes),
            )
        )
    return located


def _merge_reference_poses(group: Sequence[ModuleLocation]) -> Pose | None:
    """Average the poses relative to the world tag, but only within one world tag.

    A group whose members refer to different world tags has no common frame in which to
    average. The caller then keeps the world pose and discards the relative one, rather
    than mixing two frames into a plausible-looking nonsense pose.
    """
    poses = [
        location.pose_in_reference_tag
        for location in group
        if location.pose_in_reference_tag is not None
    ]
    if not poses or len(poses) != len(group):
        return None
    if len({location.reference_tag_id for location in group}) != 1:
        return None
    return merge_tag_poses(poses)


def merge_samples(samples: Sequence[Sequence[ModuleLocation]]) -> list[ModuleLocation]:
    """Merge several samples of the same job into one result per module.

    Averaging uses only the samples in which the same tag occurred. The count is stored
    as ``sampleCount`` in the attributes, so the result records how many images the pose
    is based on.
    """
    grouped: dict[int, list[ModuleLocation]] = {}
    order: list[int] = []
    for sample in samples:
        for location in sample:
            if location.tag_id not in grouped:
                grouped[location.tag_id] = []
                order.append(location.tag_id)
            grouped[location.tag_id].append(location)

    merged: list[ModuleLocation] = []
    for tag_id in order:
        group = grouped[tag_id]
        first = group[0]
        errors = [
            location.reprojection_error_px
            for location in group
            if not math.isnan(location.reprojection_error_px)
        ]
        attributes = dict(first.attributes)
        attributes["sampleCount"] = len(group)
        merged.append(
            replace(
                first,
                pose=merge_tag_poses([location.pose for location in group]),
                reprojection_error_px=(sum(errors) / len(errors)) if errors else float("nan"),
                ambiguous=any(location.ambiguous for location in group),
                confidence=min(location.confidence for location in group),
                pose_in_reference_tag=_merge_reference_poses(group),
                attributes=attributes,
            )
        )
    return merged


def merge_by_module(
    locations: Sequence[ModuleLocation], *, tag_map: TagMap | None = None
) -> list[ModuleLocation]:
    """Merge measurements that describe the same module into one result.

    The robot carries several tags for this purpose. From the ceiling a single tag is
    easily hidden by the arm itself, so the robot carries more than one, each with its
    own CAD offset to the robot base. They all measure the **same** pose, and averaging
    them is the point.

    Weighted by confidence. With a ``tag_map``, the nearest world tag is recomputed for
    the merged pose. The average can be closer to a different world tag than any single
    tag was.
    """
    grouped: dict[tuple[str, str], list[ModuleLocation]] = {}
    order: list[tuple[str, str]] = []
    for location in locations:
        key = (location.module_id, location.instance_id)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(location)

    merged: list[ModuleLocation] = []
    for key in order:
        group = grouped[key]
        if len(group) == 1:
            merged.append(group[0])
            continue
        # The smallest tag ID names the group, so the result does not depend on the order
        # in which the tags were detected.
        first = min(group, key=lambda location: location.tag_id)
        errors = [
            location.reprojection_error_px
            for location in group
            if not math.isnan(location.reprojection_error_px)
        ]
        pose = average_poses(
            [location.pose for location in group],
            [location.confidence for location in group],
        )
        attributes = dict(first.attributes)
        attributes["tagIds"] = sorted(location.tag_id for location in group)
        attributes["tagCount"] = len(group)

        reference_tag_id = first.reference_tag_id
        pose_in_reference_tag = _merge_reference_poses(group)
        if tag_map is not None:
            nearest = nearest_world_tag(tag_map, pose)
            if nearest is not None:
                reference_tag_id = nearest[0]
                pose_in_reference_tag = compose(
                    invert(tag_map[reference_tag_id].pose_in_world), pose
                )
        merged.append(
            replace(
                first,
                pose=pose,
                reprojection_error_px=(sum(errors) / len(errors)) if errors else float("nan"),
                ambiguous=any(location.ambiguous for location in group),
                confidence=min(location.confidence for location in group),
                reference_tag_id=reference_tag_id,
                pose_in_reference_tag=pose_in_reference_tag,
                attributes=attributes,
            )
        )
    return merged


def _rank(location: ModuleLocation) -> tuple[int, float]:
    """Rank a measurement: a known source beats an unknown one, then confidence decides."""
    try:
        precedence = SOURCE_PRECEDENCE.index(location.source)
    except ValueError:
        precedence = -1
    return (precedence, location.confidence)


def merge_locations(*groups: Sequence[ModuleLocation]) -> list[ModuleLocation]:
    """Merge the results of several cameras into one picture of the cell.

    The ceiling camera sees everything, but roughly. The hand camera sees little, but
    precisely. If both measured the same module, the precise measurement wins:
    ``SOURCE_PRECEDENCE`` decides, and confidence decides between equal sources.

    This is deliberately a pure function rather than a second network connection between
    the two Pis. Both servers keep publishing independently, and whoever consumes both
    calls this function.
    """
    best: dict[tuple[str, str], ModuleLocation] = {}
    order: list[tuple[str, str]] = []
    for group in groups:
        for location in group:
            key = (location.module_id, location.instance_id)
            if key not in best:
                best[key] = location
                order.append(key)
            elif _rank(location) > _rank(best[key]):
                best[key] = location
    return [best[key] for key in order]


def robot_locations(locations: Sequence[ModuleLocation]) -> list[ModuleLocation]:
    """Return the localized robots -- normally exactly one."""
    return [location for location in locations if location.role == ROBOT_ROLE]


def world_tag_for_robot(
    tag_map: TagMap, locations: Sequence[ModuleLocation]
) -> tuple[int, float] | None:
    """Return ``(tag_id, distance in m)`` of the world tag nearest to the robot.

    This is the question only the ceiling camera can answer, because only it sees the
    robot and the world tags at the same time. The robot then aims its hand camera at
    this tag to localize itself precisely.

    Returns:
        The tuple, or ``None`` if no robot was localized or the map knows no measured
        world tag.
    """
    robots = robot_locations(locations)
    if not robots:
        return None
    # Several robots would be a setup that does not exist. Take the best-measured one
    # rather than silently the one that happens to be first in the list.
    robot = max(robots, key=lambda location: location.confidence)
    return nearest_world_tag(tag_map, robot.pose)


def anchor_from_localization(
    localization: CameraLocalization,
    pose_base_flange: Pose,
    hand_eye: HandEye,
) -> RobotAnchor:
    """Anchor the robot base in the world frame from a single world-tag sighting.

    This is the anchoring step, **once at the start of a localization run**: the robot
    aims the hand camera at the world tag that the ceiling camera identified as nearest,
    and from that computes where its own base is in the world frame. After that it no
    longer needs the world tag in view.

    The anchor is never better than the measurement it comes from, so the spread of the
    world-tag localization carries over into the anchor.
    """
    return RobotAnchor(
        pose_world_base=anchor_world_base(
            localization.pose_world_cam, pose_base_flange, hand_eye.pose_flange_cam
        ),
        world_tag_id=localization.primary_tag_id,
        spread_m=localization.spread_m,
        spread_rad=localization.spread_rad,
    )


def localize_camera_from_anchor(
    anchor: RobotAnchor, pose_base_flange: Pose, hand_eye: HandEye
) -> CameraLocalization:
    """Carry the camera pose over to a new robot pose, without a world tag.

    This is the path from module to module: the camera stands close to a module, the
    nearest world tag is far outside the field of view, and the pose comes from the anchor
    plus the current kinematics.

    ``world_tag_ids`` stays empty: no world tag contributed to this pose, and claiming
    otherwise would be untrue. ``primary_tag_id`` still names the tag the anchor was
    taken from.
    """
    return CameraLocalization(
        pose_world_cam=camera_pose_from_robot(
            anchor.pose_world_base, pose_base_flange, hand_eye.pose_flange_cam
        ),
        world_tag_ids=(),
        primary_tag_id=anchor.world_tag_id,
        spread_m=anchor.spread_m,
        spread_rad=anchor.spread_rad,
        origin=ORIGIN_ROBOT_POSE,
    )


def anchor_drift(
    anchor: RobotAnchor,
    localization: CameraLocalization,
    pose_base_flange: Pose,
    hand_eye: HandEye,
) -> tuple[float, float]:
    """Return ``(m, rad)`` between the optical pose and the carried-over pose.

    When a world tag comes back into view during a run, the anchor can be checked rather
    than trusted: the deviation between the measured and the carried-over camera pose is
    the accumulated drift. It belongs in the result. A silent anchor that slowly wanders
    off is exactly the kind of error that only shows up when a grasp misses.
    """
    carried = localize_camera_from_anchor(anchor, pose_base_flange, hand_eye)
    return (
        translation_distance_m(localization.pose_world_cam, carried.pose_world_cam),
        rotation_distance_rad(localization.pose_world_cam, carried.pose_world_cam),
    )


def expected_but_missing(
    tag_map: TagMap, located: Sequence[ModuleLocation]
) -> list[str]:
    """Return the modules from the map that were not found.

    Answers the completeness abort criterion without any extra configuration: the
    list of expected modules is in the tag map.
    """
    seen = {location.tag_id for location in located}
    return sorted(
        entry.module_id or f"TAG-{entry.tag_id}"
        for entry in tag_map.module_entries()
        if entry.tag_id not in seen
    )


__all__ = [
    "ORIGIN_ROBOT_POSE",
    "ORIGIN_WORLD_TAGS",
    "SOURCE_CEILING",
    "SOURCE_FLANGE",
    "SOURCE_PRECEDENCE",
    "CameraLocalization",
    "ModuleLocation",
    "anchor_drift",
    "anchor_from_localization",
    "camera_pose_from_reference_tags",
    "confidence_from",
    "expected_but_missing",
    "locate_modules",
    "localize_camera",
    "localize_camera_from_anchor",
    "merge_by_module",
    "merge_locations",
    "merge_samples",
    "robot_locations",
    "world_tag_for_robot",
]

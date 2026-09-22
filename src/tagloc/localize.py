"""From tag to module. Pure composition, no OpenCV.

This is the **only** place in the project where the chain

    T_world_module = T_world_cam @ T_cam_tag @ T_tag_module

is executed. Both cameras call the same function; they differ only in what
they get to see (doc/arbeitsplaene/apriltag-welttag-konzept.md):

* **Ceiling camera** -- sees the world tags, the robot and the modules in the
  same image. That is the overview, and it is the only place the question
  "which world tag does the robot stand closest to" can be answered, because
  it is the only camera that sees both at once.
* **Eye-in-hand camera** -- localises itself against a world tag **once** at
  the start of a run, then measures every module within reach from close up.
  It does not keep the world tag in view: standing 0.3 m in front of a module,
  the nearest world tag is a metre or two away and out of frame. The gap is
  bridged by the kinematics -- see `anchor_from_localization` and
  `localize_camera_from_anchor`, and `handeye.py` for the algebra.

**Only the world tags are fixed.** Everything else in the cell is movable --
the modules and the robot alike. The robot is a module like any other; it is
simply the one that is always in use.

`T_world_cam` can therefore come from two places, and `CameraLocalization.origin`
says which: optically from the world tags (`ORIGIN_WORLD_TAGS`, the accurate
one, always preferred when a world tag is in the image) or carried over from
the anchor plus the current robot pose (`ORIGIN_ROBOT_POSE`).
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

#: Where a measurement came from. The ceiling gives the overview, the flange
#: camera the precise value -- which is exactly the precedence used by
#: `merge_locations`: later in this tuple wins.
SOURCE_CEILING = "ceiling"
SOURCE_FLANGE = "flange"
SOURCE_PRECEDENCE = (SOURCE_CEILING, SOURCE_FLANGE)

#: Woher `T_world_cam` stammt. Optisch aus den Welttags ist der genaue Weg und
#: gewinnt immer, wenn ein Welttag im Bild liegt. Aus dem Anker plus Kinematik
#: ist der Weg dazwischen -- er traegt den Roboter von Modul zu Modul, wenn
#: kein Welttag mehr zu sehen ist.
ORIGIN_WORLD_TAGS = "world_tags"
ORIGIN_ROBOT_POSE = "robot_pose"


@dataclass(frozen=True)
class CameraLocalization:
    """Where the camera stands, and how well the world tags agree on it."""

    pose_world_cam: Pose
    #: Every world tag that contributed, ascending.
    world_tag_ids: tuple[int, ...]
    #: The nearest world tag -- the one the eye-in-hand camera aligns to.
    primary_tag_id: int
    #: Largest deviation of a single estimate from the merged pose. The honest
    #: quality figure: if the four world tags disagree, the survey is wrong,
    #: and this number says so instead of disappearing into an average.
    spread_m: float = 0.0
    spread_rad: float = 0.0
    #: `ORIGIN_WORLD_TAGS` or `ORIGIN_ROBOT_POSE` -- how this pose was
    #: obtained. A consumer must be able to tell a directly measured pose from
    #: one carried over by the kinematics; they do not have the same accuracy.
    origin: str = ORIGIN_WORLD_TAGS


@dataclass(frozen=True)
class ModuleLocation:
    """A located module."""

    module_id: str
    instance_id: str
    pose: Pose
    frame_id: str
    tag_id: int
    reprojection_error_px: float = float("nan")
    ambiguous: bool = False
    confidence: float = 1.0
    #: Role from the tag map -- `module` or `robot`. The robot is reported
    #: like any other module; this field is what tells them apart.
    role: str = ""
    #: Which camera measured this. See `SOURCE_PRECEDENCE`.
    source: str = ""
    #: The world tag this module sits closest to, `-1` when unknown.
    reference_tag_id: int = -1
    #: `T_worldtag_module` -- the pose relative to that world tag. The cell is
    #: built around the world tags, so this is the number a module is set up
    #: and checked against; `pose` stays in the common world frame.
    pose_in_reference_tag: Pose | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


def confidence_from(tag_pose: TagPose, *, max_reprojection_error_px: float = 3.0) -> float:
    """Compute confidence from reprojection error and ambiguity, in [0, 1].

    Deliberately not a constant 1.0 as in earlier sources: a confidence
    that's always 1.0 carries no information and tempts downstream logic
    to trust it anyway.
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
) -> CameraLocalization | None:
    """Determine `T_world_cam` from the world tags visible in this image.

    For each world tag, `T_world_cam = T_world_tag @ T_cam_tag^-1`. Several
    world tags give several estimates, merged weighted by confidence -- a tag
    seen far away and at a shallow angle must not pull as hard as one filling
    the frame.

    `primary_tag_id` is the world tag closest to the camera. That is the one
    the eye-in-hand camera aligns itself to; with a single world tag in the
    image it is simply that tag.

    No world tag visible -> `None`, and the caller stays in the camera frame.
    Guessing a world pose would be the worse answer.
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
                # Distance camera -> tag, i.e. the length of the measured
                # translation. The distance to the identity pose is exactly
                # that norm, so this needs no numpy of its own.
                translation_distance_m(identity(), tag_pose.pose_cam_tag),
            )
        )
    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])
    estimates = [item[1] for item in candidates]
    merged = average_poses(estimates, [item[2] for item in candidates])
    if len(candidates) > 1:
        _log.debug("Kamerapose aus %d Welttags gemittelt", len(candidates))

    # Tie broken by tag id, so the choice is reproducible between images.
    primary = min(candidates, key=lambda item: (item[3], item[0]))[0]
    return CameraLocalization(
        pose_world_cam=merged,
        world_tag_ids=tuple(item[0] for item in candidates),
        primary_tag_id=primary,
        spread_m=max(translation_distance_m(merged, pose) for pose in estimates),
        spread_rad=max(rotation_distance_rad(merged, pose) for pose in estimates),
    )


def camera_pose_from_reference_tags(
    tag_poses: Sequence[TagPose], tag_map: TagMap
) -> Pose | None:
    """Return only `T_world_cam`, without the quality figures.

    Kept for the CLI tools, which transform a pose and have no use for the
    spread. New code takes `localize_camera` -- throwing `spread_m` away means
    throwing away the answer to whether the survey still holds.
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
    """Convert module tags into module poses.

    Tags without a map entry are still included -- with their tag ID as the
    module identifier and no CAD offset. An unknown tag is information, not
    an error: most likely the map entry is simply missing.

    With a known camera pose every module additionally gets the world tag it
    sits closest to, and its pose relative to that tag. The cell is built
    around the world tags, so that is the number a module is set up and
    checked against.
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
    """Average the world-tag-relative poses, but only within one world tag.

    A group whose members refer to different world tags has no common frame
    to average in; the caller then keeps the world pose and drops the
    relative one, rather than mixing two frames into a plausible-looking
    nonsense pose.
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

    Averaging only spans samples where the same tag appeared; the count ends
    up as `sampleCount` in the attributes, so the result records how many
    images the pose is based on.
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

    This is what several tags on the robot are for: seen from the ceiling a
    single tag is easily hidden by the arm itself, so the robot carries more
    than one, each with its own CAD offset to the robot base. All of them
    therefore measure *the same* pose, and averaging them is the point.

    Weighted by confidence. Given a `tag_map`, the nearest world tag is
    recomputed for the merged pose -- the average may well sit closer to a
    different world tag than any single tag did.
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
        # The lowest tag id names the group, so the result is reproducible
        # regardless of the order the tags were detected in.
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
    """Rank a measurement: a known source beats an unknown one, then confidence."""
    try:
        precedence = SOURCE_PRECEDENCE.index(location.source)
    except ValueError:
        precedence = -1
    return (precedence, location.confidence)


def merge_locations(*groups: Sequence[ModuleLocation]) -> list[ModuleLocation]:
    """Combine the results of several cameras into one picture of the cell.

    The ceiling camera sees everything but coarsely; the eye-in-hand camera
    sees little but precisely. Where both measured the same module, the
    precise measurement wins -- `SOURCE_PRECEDENCE` decides, and confidence
    breaks a tie between equal sources.

    Deliberately a pure function instead of a second network connection
    between the two Pis: both servers keep publishing independently, and
    whoever consumes both calls this.
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
    """Return the located robots -- normally exactly one."""
    return [location for location in locations if location.role == ROBOT_ROLE]


def world_tag_for_robot(
    tag_map: TagMap, locations: Sequence[ModuleLocation]
) -> tuple[int, float] | None:
    """Return `(tag_id, distance in m)` of the world tag nearest the robot.

    The question only the ceiling camera can answer, because it is the only
    one that sees the robot and the world tags at the same time. The robot
    then points its eye-in-hand camera at this tag to localise itself
    precisely.

    `None` when no robot was located or the map knows no surveyed world tag.
    """
    robots = robot_locations(locations)
    if not robots:
        return None
    # Several robots would be a build we do not have; take the best measured
    # one rather than silently picking whichever came first in the list.
    robot = max(robots, key=lambda location: location.confidence)
    return nearest_world_tag(tag_map, robot.pose)


def anchor_from_localization(
    localization: CameraLocalization,
    pose_base_flange: Pose,
    hand_eye: HandEye,
) -> RobotAnchor:
    """Anchor the robot base in the world frame from one world-tag sighting.

    Der Ankerschritt, **einmal zu Beginn eines Lokalisierungsvorgangs**: der
    Roboter richtet die Handkamera auf den Welttag, den die Deckenkamera als
    naechstgelegenen bestimmt hat, und rechnet daraus, wo seine eigene Basis
    im Welt-KS steht. Danach braucht er den Welttag nicht mehr im Bild.

    Der Anker ist nie besser als die Messung, aus der er stammt -- deshalb
    wandert die Streuung der Welttag-Lokalisierung in den Anker mit.
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

    Das ist der Weg von Modul zu Modul: die Kamera steht dicht vor einem
    Modul, der naechste Welttag liegt laengst ausserhalb des Bildfelds, und
    die Pose kommt aus dem Anker plus der aktuellen Kinematik.

    `world_tag_ids` bleibt leer -- es hat kein Welttag zu dieser Pose
    beigetragen, und das zu behaupten waere die Unwahrheit. `primary_tag_id`
    nennt weiterhin den Tag, an dem geankert wurde.
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
    """Return `(m, rad)` between the optical pose and the carried-over one.

    Kommt waehrend eines Vorgangs wieder ein Welttag ins Bild, laesst sich der
    Anker pruefen, statt ihm zu glauben: die Abweichung zwischen gemessener
    und weitergerechneter Kamerapose ist die aufgelaufene Drift. Sie gehoert
    ins Ergebnis -- ein stiller Anker, der langsam wegwandert, ist genau die
    Sorte Fehler, die erst beim Danebengreifen auffaellt.
    """
    carried = localize_camera_from_anchor(anchor, pose_base_flange, hand_eye)
    return (
        translation_distance_m(localization.pose_world_cam, carried.pose_world_cam),
        rotation_distance_rad(localization.pose_world_cam, carried.pose_world_cam),
    )


def expected_but_missing(
    tag_map: TagMap, located: Sequence[ModuleLocation]
) -> list[str]:
    """Return modules from the map that were not found.

    Answers the abort criterion from concept/offene_punkte.md no. 6 without
    extra configuration: the expected module list lives in the tag map.
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

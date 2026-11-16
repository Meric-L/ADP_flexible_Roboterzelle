"""Tag map: what each tag is and where it sits in the shared coordinate system.

Core idea of the whole concept (doc/projektdoku/apriltag-lokalisierung.md section 1):

    A tag carries a number, not a meaning. That tag 7 is module MOD-A, with its
    origin 40 mm below the tag center, is recorded here -- in a file -- not in
    code.

**Only the world tags are fixed.** Four of them sit at the edge of the cell and
span the world CS; everything else -- modules *and the robot* -- is movable and
gets measured. The robot is a module like any other; it is just the one that is
always in use.

Reference tags (world) and measured tags (modules, robot) go through the same
code. They differ only in whether their world pose is in the map or ``null``
(movable, measured).

No OpenCV: ``place_tags`` works only with poses, never with images.
"""

import json
import logging
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from .identity import tag_map_identity
from .geometry import (
    Pose,
    average_poses,
    compose,
    identity,
    invert,
    pose_from_dict,
    pose_to_dict,
    rotation_distance_rad,
    translation_distance_m,
)

_log = logging.getLogger(__name__)

SCHEMA = "wsc.vision.tagmap/2"

#: Schema 1 knew the roles `robot_table` and `reference` as *fixed* anchors.
#: That was the wrong concept -- see doc/projektdoku/arbeitsplaene/apriltag-welttag-konzept.md.
LEGACY_SCHEMA = "wsc.vision.tagmap/1"

#: The only fixed thing in the cell: a world tag at the cell edge.
WORLD_ROLE = "world"
#: A tag on the robot. The robot is a module like any other -- it is just
#: the one that is always in use.
ROBOT_ROLE = "robot"
MODULE_ROLE = "module"

#: Roles with a fixed world pose, used to determine `T_world_cam`.
#: **Only** the world tag. Everything else in the cell can be moved, so nothing
#: else may serve as an anchor.
REFERENCE_ROLES = frozenset({WORLD_ROLE})

#: Roles whose pose is measured instead of read. The robot is included on
#: purpose: it stands somewhere, and where it stands is the question.
MOVABLE_ROLES = frozenset({MODULE_ROLE, ROBOT_ROLE})

KNOWN_ROLES = REFERENCE_ROLES | MOVABLE_ROLES

#: The schema 1 roles and the roles they map to.
#:
#: `reference` was a fixed anchor with a world pose -- exactly what a world tag
#: is today. `robot_table` was the mistaken idea: the table counted as fixed.
#: It becomes the movable robot tag, and its world pose is dropped in the process.
LEGACY_ROLES = {"reference": WORLD_ROLE, "robot_table": ROBOT_ROLE}

#: Four world tags at the edge of the cell. Not a hard limit of the code --
#: `localize_camera` works with a single one -- but the expected setup, so that
#: a missing tag is noticed instead of silently costing accuracy.
EXPECTED_WORLD_TAG_COUNT = 4


@dataclass(frozen=True)
class TagEntry:
    """A tag and its meaning."""

    tag_id: int
    role: str
    size_m: float
    module_id: str = ""
    instance_id: str = ""
    #: ``None`` = movable, measured instead of read.
    pose_in_world: Pose | None = None
    #: Constant offset from the tag center to the module origin, from the CAD.
    tag_to_module: Pose = field(default_factory=identity)

    @property
    def is_world(self) -> bool:
        """Whether this is one of the fixed world tags of the cell."""
        return self.role == WORLD_ROLE

    @property
    def is_reference(self) -> bool:
        """Whether this tag may serve as an anchor for ``T_world_cam``.

        A world tag without a world pose is *not* an anchor. It is a world tag
        that has not been surveyed yet, and guessing would be worse than staying
        in the camera CS.
        """
        return self.is_world and self.pose_in_world is not None

    @property
    def is_robot(self) -> bool:
        return self.role == ROBOT_ROLE

    @property
    def is_module(self) -> bool:
        """Whether this tag marks a movable object that is measured.

        The robot counts as a module here. This is the core of the concept,
        not an oversight.
        """
        return self.role in MOVABLE_ROLES


#: Default tag family of the cell (name form as in ``detector.ARUCO_DICTIONARIES``).
#: ``vision_server/profiles.py`` keeps the same value as a literal, because it must
#: stay importable without numpy; ``tests/test_server_config.py`` compares both.
DEFAULT_TAG_FAMILY = "tag36h11"


@dataclass(frozen=True)
class TagMap:
    """All known tags of a cell, in one coordinate system."""

    frame_id: str = "world"
    anchor_tag_id: int = 0
    tag_family: str = DEFAULT_TAG_FAMILY
    entries: Mapping[int, TagEntry] = field(default_factory=dict)

    def __contains__(self, tag_id: int) -> bool:
        return int(tag_id) in self.entries

    def __getitem__(self, tag_id: int) -> TagEntry:
        return self.entries[int(tag_id)]

    def get(self, tag_id: int) -> TagEntry | None:
        return self.entries.get(int(tag_id))

    def size_for(self, tag_id: int, default_m: float) -> float:
        """Return the tag size from the map, otherwise the configured fallback.

        A wrong size scales the measured distance linearly and would otherwise go
        unnoticed -- so the map always wins when the tag is listed in it.
        """
        entry = self.get(tag_id)
        return entry.size_m if entry is not None else default_m

    def reference_poses(self) -> dict[int, Pose]:
        """Return all tags with a known world pose."""
        return {
            tag_id: entry.pose_in_world
            for tag_id, entry in self.entries.items()
            if entry.is_reference and entry.pose_in_world is not None
        }

    def world_tag_ids(self) -> list[int]:
        """Return the IDs of all tags with role ``world``, surveyed or not."""
        return sorted(tag_id for tag_id, entry in self.entries.items() if entry.is_world)

    def module_entries(self) -> list[TagEntry]:
        """Return all movable tags: the expected module list, robot included."""
        return [entry for entry in self.entries.values() if entry.is_module]

    def robot_entries(self) -> list[TagEntry]:
        """Return the tags mounted on the robot.

        Several are allowed and expected. From the ceiling, a single tag is easily
        hidden by the arm itself, and averaging over several is the purpose of
        ``localize.merge_by_module``.
        """
        return [entry for entry in self.entries.values() if entry.is_robot]


def validate_tag_map(tag_map: TagMap) -> list[str]:
    """Return the problems with this map; an empty list means it is valid.

    Deliberately a report instead of an exception: a cell still being set up
    must be allowed to measure. The caller decides whether to log or abort --
    ``AprilTagDetectionSource.open`` logs and continues.
    """
    problems: list[str] = []

    world_ids = tag_map.world_tag_ids()
    unsurveyed = sorted(
        tag_id for tag_id in world_ids if tag_map.entries[tag_id].pose_in_world is None
    )
    if len(world_ids) != EXPECTED_WORLD_TAG_COUNT:
        problems.append(
            f"Erwartet {EXPECTED_WORLD_TAG_COUNT} Welttags im Randbereich der Zelle, "
            f"gefunden {len(world_ids)}: {world_ids or 'keine'}"
        )
    if unsurveyed:
        problems.append(
            f"Welttags ohne Weltpose (nicht eingemessen): {unsurveyed}. "
            "Ohne 'poseInWorld' taugen sie nicht als Anker."
        )

    fixed_but_movable = sorted(
        tag_id
        for tag_id, entry in tag_map.entries.items()
        if entry.is_module and entry.pose_in_world is not None
    )
    if fixed_but_movable:
        problems.append(
            f"Bewegliche Tags mit fester Weltpose: {fixed_but_movable}. "
            "Nur Welttags stehen fest; die Pose wird ignoriert."
        )

    anchor = tag_map.get(tag_map.anchor_tag_id)
    if anchor is None:
        problems.append(
            f"Anker-Tag {tag_map.anchor_tag_id} steht nicht in der Karte"
        )
    elif not anchor.is_world:
        problems.append(
            f"Anker-Tag {tag_map.anchor_tag_id} hat die Rolle '{anchor.role}', "
            f"muss aber '{WORLD_ROLE}' sein -- der Ursprung des Welt-KS ist ein Welttag."
        )

    if not tag_map.robot_entries():
        problems.append(
            f"Kein Tag mit der Rolle '{ROBOT_ROLE}'. Der Roboter ist ein Modul, "
            "das immer verwendet wird -- ohne Robotertag findet die Deckenkamera ihn nicht."
        )
    return problems


def nearest_world_tag(tag_map: TagMap, pose_world: Pose) -> tuple[int, float] | None:
    """Return ``(tag_id, distance in m)`` of the world tag closest to ``pose_world``.

    This answers the question the ceiling camera asks for the robot: which of
    the four world tags should the hand camera look at to localize itself.
    ``None`` if the map has no surveyed world tag.
    """
    references = tag_map.reference_poses()
    if not references:
        return None
    distances = {
        tag_id: translation_distance_m(pose, pose_world)
        for tag_id, pose in references.items()
    }
    # Also sort by ID so that an exact tie is resolved deterministically,
    # instead of depending on dict order.
    best = min(distances, key=lambda tag_id: (distances[tag_id], tag_id))
    return best, float(distances[best])


def empty_tag_map(frame_id: str = "world", anchor_tag_id: int = 0) -> TagMap:
    """Return an empty map. Running without a map remains a normal case."""
    return TagMap(frame_id=frame_id, anchor_tag_id=anchor_tag_id, entries={})


def _migrate_role(role: str, tag_id: int, pose_in_world, path: Path):
    """Map a schema 1 role to the current one. Return ``(role, pose)``.

    Not silent: every reinterpretation is reported individually. That was
    exactly the reason old maps were originally rejected: a ``robot_table``
    world pose stays in the file and looks valid while it is no longer used.
    With one message per tag this is no longer a silent process, and an
    outdated map no longer blocks the whole vision system.
    """
    migrated = LEGACY_ROLES[role]
    if migrated in MOVABLE_ROLES and pose_in_world is not None:
        _log.warning(
            "Tag-Map %s, Tag %d: Rolle '%s' -> '%s'. Die eingetragene Weltpose "
            "wird IGNORIERT -- seit %s steht nur die Rolle '%s' fest, alles "
            "andere wird gemessen.",
            path, tag_id, role, migrated, SCHEMA, WORLD_ROLE,
        )
        return migrated, None
    _log.warning(
        "Tag-Map %s, Tag %d: Rolle '%s' -> '%s'.", path, tag_id, role, migrated
    )
    return migrated, pose_in_world


def load_tag_map(path: Path) -> TagMap:
    """Read a tag map from a JSON file."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Tag-Map nicht gefunden: {path}")
    return tag_map_from_json(path.read_text(encoding="utf-8"), source=str(path))


def tag_map_from_json(text: str, *, source: str = "<string>") -> TagMap:
    """Read a tag map from JSON text.

    A map in the old schema is **migrated, not rejected**. Rejecting it loudly
    sounds right, but in practice it silently stops all detection, because
    ``AprilTagDetectionSource.open`` fails on it. Module names and CAD offsets
    from the old map are kept this way. Changes in meaning are reported one by
    one by ``_migrate_role``.
    """
    path = source
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"Tag-Map {source} ist kein gueltiges JSON: {error}") from error
    if not isinstance(data, dict):
        raise ValueError(f"Tag-Map {source} muss ein JSON-Objekt sein")
    schema = data.get("schema")
    if schema == LEGACY_SCHEMA:
        _log.warning(
            "Tag-Map %s nutzt das alte Schema '%s' und wird nach '%s' migriert. "
            "Dauerhaft umstellen: einmal mit 'python -m tagloc.cli.build_tagmap' "
            "neu schreiben lassen, dann steht das neue Schema in der Datei.",
            path, LEGACY_SCHEMA, SCHEMA,
        )
    elif schema != SCHEMA:
        raise ValueError(f"Unbekanntes Tag-Map-Schema '{schema}' in {path} (erwartet {SCHEMA})")

    entries: dict[int, TagEntry] = {}
    for raw in data.get("tags", []):
        tag_id = int(raw["tagId"])
        pose_in_world = raw.get("poseInWorld")
        tag_to_module = raw.get("tagToModule")
        role = str(raw.get("role", MODULE_ROLE))
        if role in LEGACY_ROLES:
            role, pose_in_world = _migrate_role(role, tag_id, pose_in_world, path)
        elif role not in KNOWN_ROLES:
            # A typo stays an error -- otherwise a tag would silently be
            # neither anchor nor module.
            raise ValueError(
                f"Tag {tag_id} in {path} hat die unbekannte Rolle '{role}'. "
                f"Erlaubt sind: {sorted(KNOWN_ROLES)}"
            )
        entries[tag_id] = TagEntry(
            tag_id=tag_id,
            role=role,
            size_m=float(raw["sizeM"]),
            module_id=str(raw.get("moduleId", "")),
            instance_id=str(raw.get("instanceId", "")),
            pose_in_world=pose_from_dict(pose_in_world) if pose_in_world else None,
            tag_to_module=pose_from_dict(tag_to_module) if tag_to_module else identity(),
        )
    return TagMap(
        frame_id=str(data.get("frameId", "world")),
        anchor_tag_id=int(data.get("anchorTagId", 0)),
        tag_family=str(data.get("tagFamily", DEFAULT_TAG_FAMILY)),
        entries=entries,
    )


def save_tag_map(path: Path, tag_map: TagMap) -> None:
    """Write a tag map as JSON, sorted by tag ID."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tag_map_to_json(tag_map), encoding="utf-8")


def tag_map_to_json(tag_map: TagMap) -> str:
    """Return the map as JSON text -- exactly what ``save_tag_map`` writes.

    The same format is used for the file and the OPC UA method. What the
    backend sends can be stored unchanged, and the other way round.
    """
    tags = []
    for tag_id in sorted(tag_map.entries):
        entry = tag_map.entries[tag_id]
        raw: dict = {"tagId": tag_id, "role": entry.role, "sizeM": entry.size_m}
        if entry.module_id:
            raw["moduleId"] = entry.module_id
        if entry.instance_id:
            raw["instanceId"] = entry.instance_id
        if not np.allclose(entry.tag_to_module, identity()):
            raw["tagToModule"] = pose_to_dict(entry.tag_to_module)
        raw["poseInWorld"] = (
            pose_to_dict(entry.pose_in_world) if entry.pose_in_world is not None else None
        )
        tags.append(raw)
    payload = {
        "schema": SCHEMA,
        "frameId": tag_map.frame_id,
        "anchorTagId": tag_map.anchor_tag_id,
        "tagFamily": tag_map.tag_family,
        "tags": tags,
    }
    return json.dumps(payload, indent=2) + "\n"


# --------------------------------------------------------------------------
# Place tags in a shared coordinate system
# --------------------------------------------------------------------------

#: One capture: what was visible in **one** image at the same time.
Observation = Mapping[int, Pose]


def observed_tag_ids(observations: Sequence[Observation]) -> set[int]:
    """Return all tag IDs that occurred at all."""
    seen: set[int] = set()
    for observation in observations:
        seen.update(int(tag_id) for tag_id in observation)
    return seen


def relative_poses(observations: Sequence[Observation]) -> dict[tuple[int, int], list[Pose]]:
    """Build camera-free relations ``T_a_b`` from shared observations.

    Two tags in the same image give ``T_a_b = T_cam_a^-1 @ T_cam_b``. The camera
    pose cancels out, so the camera does not need to be known, and the captures
    may come from completely different viewpoints.
    """
    edges: dict[tuple[int, int], list[Pose]] = {}
    for observation in observations:
        tag_ids = sorted(int(tag_id) for tag_id in observation)
        for index, first in enumerate(tag_ids):
            for second in tag_ids[index + 1 :]:
                relative = compose(invert(observation[first]), observation[second])
                edges.setdefault((first, second), []).append(relative)
                edges.setdefault((second, first), []).append(invert(relative))
    return edges


def merge_tag_poses(poses: Sequence[Pose]) -> Pose:
    """Merge several estimates of the same pose."""
    return average_poses(list(poses))


def place_tags(
    observations: Sequence[Observation], *, anchor_tag_id: int
) -> dict[int, Pose]:
    """Place all observed tags in **one** coordinate system.

    The origin is the anchor tag. Method: shared observations span a graph
    (nodes = tag IDs, edges = pairs seen together). A breadth-first search from
    the anchor chains the relations into poses in the anchor CS. When a tag is
    reached through several already placed neighbours, all estimates are
    averaged instead of taking the first one.

    Tags with no path to the anchor do **not** appear in the result. This is
    intended: silently giving them a phantom pose would be the worse error. The
    caller compares with `observed_tag_ids` to report them.

    Accuracy limit: averaging covers the neighbours that are already placed at
    the time of the visit, not all edges of the finished graph. The result
    therefore depends on the (deterministic) visit order and is not a global
    adjustment. For a cell with few tags and short chains this is fine. If the
    chains get long, `residuals` shows whether it is still good enough. The next
    step would be a weighted least-squares adjustment over all edges.
    """
    anchor_tag_id = int(anchor_tag_id)
    edges = relative_poses(observations)
    if anchor_tag_id not in observed_tag_ids(observations):
        raise ValueError(f"Anker-Tag {anchor_tag_id} kommt in keiner Aufnahme vor")

    neighbours: dict[int, set[int]] = {}
    for first, second in edges:
        neighbours.setdefault(first, set()).add(second)

    placed: dict[int, Pose] = {anchor_tag_id: identity()}
    queue: deque[int] = deque([anchor_tag_id])
    while queue:
        current = queue.popleft()
        for neighbour in sorted(neighbours.get(current, ())):
            if neighbour in placed:
                continue
            # Let all already placed neighbours of this tag contribute,
            # not only the one we came from.
            candidates = [
                compose(placed[other], relative)
                for other in sorted(neighbours.get(neighbour, ()))
                if other in placed
                for relative in edges[(other, neighbour)]
            ]
            placed[neighbour] = merge_tag_poses(candidates)
            queue.append(neighbour)
    return placed


def residuals(
    observations: Sequence[Observation], placed: Mapping[int, Pose]
) -> dict[tuple[int, int], tuple[float, float]]:
    """Return the loop-closure error per edge: ``(position in m, angle in rad)``.

    This is the quality measure of the map. An image series that closes a loop
    must close it. If it does not, either the calibration or a recorded tag size
    is wrong.
    """
    result: dict[tuple[int, int], tuple[float, float]] = {}
    for (first, second), measurements in relative_poses(observations).items():
        if first >= second or first not in placed or second not in placed:
            continue
        reconstructed = compose(invert(placed[first]), placed[second])
        measured = merge_tag_poses(measurements)
        result[(first, second)] = (
            translation_distance_m(measured, reconstructed),
            rotation_distance_rad(measured, reconstructed),
        )
    return result


def with_world_poses(
    tag_map: TagMap,
    placed: Mapping[int, Pose],
    *,
    roles: Mapping[int, str] | None = None,
    sizes: Mapping[int, float] | None = None,
    default_size_m: float = 0.05,
) -> TagMap:
    """Write placed poses into a map as world poses.

    Existing entries keep their role, module assignment and CAD offset; only
    `pose_in_world` is set. Movable tags (`module`, `robot`) deliberately get
    **no** world pose: they stand where they stand, and where that is gets
    measured in every job.

    A tag not yet in the map becomes a world tag. Surveying a cell is the
    purpose of `build_tagmap`, and its output is exactly the fixed edge tags.
    """
    roles = roles or {}
    sizes = sizes or {}
    entries = dict(tag_map.entries)
    for tag_id, pose in placed.items():
        tag_id = int(tag_id)
        existing = entries.get(tag_id)
        if existing is None:
            existing = TagEntry(
                tag_id=tag_id,
                role=roles.get(tag_id, WORLD_ROLE),
                size_m=sizes.get(tag_id, default_size_m),
            )
        if existing.is_module:
            entries[tag_id] = existing
            continue
        entries[tag_id] = replace(existing, pose_in_world=pose)
    return replace(tag_map, entries=entries)


def missing_tags(
    observations: Sequence[Observation], placed: Mapping[int, Pose]
) -> list[int]:
    """Return observed tags that had no path to the anchor."""
    return sorted(observed_tag_ids(observations) - {int(tag_id) for tag_id in placed})


def format_residual_report(residual_map: Mapping[tuple[int, int], tuple[float, float]]) -> str:
    """Return a readable summary of the loop-closure errors, largest first."""
    if not residual_map:
        return "keine gemeinsamen Kanten"
    lines = ["Kante        Position     Winkel"]
    for (first, second), (position_m, angle_rad) in sorted(
        residual_map.items(), key=lambda item: -item[1][0]
    ):
        lines.append(
            f"{first:>4} - {second:<4} {position_m * 1000.0:8.2f} mm {np.degrees(angle_rad):7.3f} deg"
        )
    return "\n".join(lines)


__all__ = [
    "EXPECTED_WORLD_TAG_COUNT",
    "KNOWN_ROLES",
    "LEGACY_ROLES",
    "LEGACY_SCHEMA",
    "MODULE_ROLE",
    "MOVABLE_ROLES",
    "REFERENCE_ROLES",
    "ROBOT_ROLE",
    "SCHEMA",
    "WORLD_ROLE",
    "Observation",
    "TagEntry",
    "TagMap",
    "empty_tag_map",
    "format_residual_report",
    "load_tag_map",
    "merge_tag_poses",
    "missing_tags",
    "nearest_world_tag",
    "observed_tag_ids",
    "place_tags",
    "relative_poses",
    "residuals",
    "save_tag_map",
    "tag_map_from_json",
    "tag_map_identity",
    "tag_map_to_json",
    "validate_tag_map",
    "with_world_poses",
]

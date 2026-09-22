"""Tag map: what each tag is, and where it sits in the shared coordinate frame.

Core idea of the whole concept (doc/apriltag-lokalisierung.md section 1):

    A tag carries a number, not meaning. That tag 7 is module MOD-A, with
    its origin 40 mm below the tag centre, lives here -- in a file -- not
    in code.

**Only the world tags are fixed.** Four of them sit in the border area of the
cell and span the world frame; everything else -- modules *and the robot* --
is movable and gets measured. The robot is a module like any other; it is
simply the one that is always in use.

Reference tags (world) and measured tags (modules, robot) run through the
same code; they differ only in whether their world pose is in the map or
`null` (movable, to be measured).

No OpenCV: `place_tags` only works with poses, never images.
"""

import json
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

SCHEMA = "wsc.vision.tagmap/2"

#: Schema 1 knew the roles `robot_table` and `reference` as *fixed* anchors.
#: That was the wrong concept -- see doc/arbeitsplaene/apriltag-welttag-konzept.md.
LEGACY_SCHEMA = "wsc.vision.tagmap/1"

#: The only fixed thing in the cell: a world tag on the cell border.
WORLD_ROLE = "world"
#: A tag on the robot. The robot is a module like any other -- it just happens
#: to be the one that is always in use.
ROBOT_ROLE = "robot"
MODULE_ROLE = "module"

#: Roles with a fixed world pose -- used to determine `T_world_cam`.
#: **Only** the world tag. Everything else in the cell can be moved, so
#: nothing else may serve as an anchor.
REFERENCE_ROLES = frozenset({WORLD_ROLE})

#: Roles whose pose is measured rather than read. The robot is in here on
#: purpose: it stands somewhere, and where it stands is the question.
MOVABLE_ROLES = frozenset({MODULE_ROLE, ROBOT_ROLE})

KNOWN_ROLES = REFERENCE_ROLES | MOVABLE_ROLES

#: Four world tags in the border area of the cell. Not a limit of the code --
#: `localize_camera` works with a single one -- but the expected build, so a
#: missing tag is noticed instead of silently costing accuracy.
EXPECTED_WORLD_TAG_COUNT = 4


@dataclass(frozen=True)
class TagEntry:
    """A tag and what it means."""

    tag_id: int
    role: str
    size_m: float
    module_id: str = ""
    instance_id: str = ""
    #: `None` = movable, measured rather than read.
    pose_in_world: Pose | None = None
    #: Constant offset from tag centre to module origin, from CAD.
    tag_to_module: Pose = field(default_factory=identity)

    @property
    def is_world(self) -> bool:
        """Whether this is one of the cell's fixed world tags."""
        return self.role == WORLD_ROLE

    @property
    def is_reference(self) -> bool:
        """Whether this tag may serve as an anchor for `T_world_cam`.

        A world tag without a world pose is *not* an anchor: it is a world
        tag that has not been surveyed yet, and guessing would be worse than
        staying in the camera frame.
        """
        return self.is_world and self.pose_in_world is not None

    @property
    def is_robot(self) -> bool:
        return self.role == ROBOT_ROLE

    @property
    def is_module(self) -> bool:
        """Whether this tag denotes something movable that gets measured.

        The robot counts as a module here -- that is the whole point of the
        concept, not an oversight.
        """
        return self.role in MOVABLE_ROLES


@dataclass(frozen=True)
class TagMap:
    """All known tags of a cell, in one coordinate frame."""

    frame_id: str = "world"
    anchor_tag_id: int = 0
    tag_family: str = "tag36h11"
    entries: Mapping[int, TagEntry] = field(default_factory=dict)

    def __contains__(self, tag_id: int) -> bool:
        return int(tag_id) in self.entries

    def __getitem__(self, tag_id: int) -> TagEntry:
        return self.entries[int(tag_id)]

    def get(self, tag_id: int) -> TagEntry | None:
        return self.entries.get(int(tag_id))

    def size_for(self, tag_id: int, default_m: float) -> float:
        """Return the tag's size from the map, else the configured fallback.

        A wrong size scales the measured distance linearly and otherwise
        goes unnoticed -- so the map always wins when the tag is in it.
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
        """Return the IDs of all tags with role `world`, surveyed or not."""
        return sorted(tag_id for tag_id, entry in self.entries.items() if entry.is_world)

    def module_entries(self) -> list[TagEntry]:
        """Return all movable tags -- the expected module list, robot included."""
        return [entry for entry in self.entries.values() if entry.is_module]

    def robot_entries(self) -> list[TagEntry]:
        """Return the tags mounted on the robot.

        Several are allowed and expected: seen from the ceiling a single tag
        is easily hidden by the arm itself, and averaging over more of them
        is what `localize.merge_by_module` is for.
        """
        return [entry for entry in self.entries.values() if entry.is_robot]


def validate_tag_map(tag_map: TagMap) -> list[str]:
    """Return what is wrong with this map, empty list = fine.

    Deliberately a report rather than an exception: a cell under construction
    must still be allowed to measure. The caller decides whether to log or to
    abort -- `AprilTagDetectionSource.open` logs and continues.
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
    """Return `(tag_id, distance in m)` of the world tag closest to `pose_world`.

    This is the question the ceiling camera answers for the robot: which of
    the four world tags should the eye-in-hand camera look at to localise
    itself. `None` when the map knows no surveyed world tag.
    """
    references = tag_map.reference_poses()
    if not references:
        return None
    distances = {
        tag_id: translation_distance_m(pose, pose_world)
        for tag_id, pose in references.items()
    }
    # Sort by ID as well, so an exact tie resolves deterministically instead
    # of depending on dict order.
    best = min(distances, key=lambda tag_id: (distances[tag_id], tag_id))
    return best, float(distances[best])


def empty_tag_map(frame_id: str = "world", anchor_tag_id: int = 0) -> TagMap:
    """Return an empty map -- operation without a map stays a normal case."""
    return TagMap(frame_id=frame_id, anchor_tag_id=anchor_tag_id, entries={})


def load_tag_map(path: Path) -> TagMap:
    """Read a tag map from JSON."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Tag-Map nicht gefunden: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    schema = data.get("schema")
    if schema == LEGACY_SCHEMA:
        # Not silently migrated: a `robot_table` entry carries a world pose
        # that still looks perfectly valid. Reinterpreting it as movable would
        # keep that pose in the file and quietly stop using it -- the kind of
        # change that only shows up as a wrong result much later.
        raise ValueError(
            f"Tag-Map {path} nutzt das alte Schema '{LEGACY_SCHEMA}'. "
            f"Seit '{SCHEMA}' ist nur die Rolle '{WORLD_ROLE}' fest; "
            f"'robot_table' und 'reference' entfallen. Der Robotertisch ist "
            f"beweglich: Tags am Roboter bekommen die Rolle '{ROBOT_ROLE}' und "
            "'poseInWorld': null. Die vier Welttags werden mit "
            "'python -m tagloc.cli.build_tagmap' neu eingemessen."
        )
    if schema != SCHEMA:
        raise ValueError(f"Unbekanntes Tag-Map-Schema '{schema}' in {path} (erwartet {SCHEMA})")
    entries: dict[int, TagEntry] = {}
    for raw in data.get("tags", []):
        tag_id = int(raw["tagId"])
        pose_in_world = raw.get("poseInWorld")
        tag_to_module = raw.get("tagToModule")
        role = str(raw.get("role", MODULE_ROLE))
        if role not in KNOWN_ROLES:
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
        tag_family=str(data.get("tagFamily", "tag36h11")),
        entries=entries,
    )


def save_tag_map(path: Path, tag_map: TagMap) -> None:
    """Write a tag map as JSON, sorted by tag ID."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
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
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# Placing tags into a shared coordinate frame
# --------------------------------------------------------------------------

#: One sample: what was visible simultaneously in **one** image.
Observation = Mapping[int, Pose]


def observed_tag_ids(observations: Sequence[Observation]) -> set[int]:
    """Return all tag IDs that appeared at all."""
    seen: set[int] = set()
    for observation in observations:
        seen.update(int(tag_id) for tag_id in observation)
    return seen


def relative_poses(observations: Sequence[Observation]) -> dict[tuple[int, int], list[Pose]]:
    """Build camera-free relations `T_a_b` from co-observations.

    Two tags in the same image give `T_a_b = T_cam_a^-1 @ T_cam_b`. The
    camera pose cancels out -- so the camera doesn't need to be known, and
    samples may come from completely different viewpoints.
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
    """Place all observed tags into **one** coordinate frame.

    Origin is the anchor tag. Method: co-observations span a graph (nodes =
    tag IDs, edges = co-seen pairs). A breadth-first search from the anchor
    chains the relations into poses in the anchor frame. If a tag is reached
    via several already-placed neighbours, all estimates are averaged
    instead of taking the first one.

    Tags with no path to the anchor **do not** appear in the result. This is
    intentional: silently giving them a phantom pose would be the worse
    error. The caller compares against `observed_tag_ids` to report them.

    Accuracy limit: averaging happens over neighbours already placed at
    visit time, not over all edges of the finished graph. The result thus
    depends on the (deterministic) visit order and isn't a global
    adjustment. Fine for a cell with few tags and short chains; if chains
    get long, `residuals` measures whether it's still good enough. The next
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
            # Let all already-placed neighbours of this tag contribute, not
            # just the one we arrived through.
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
    """Return the closure error per edge: `(position in m, angle in rad)`.

    This is the map's quality figure. An image series that closes a loop
    must close; if it doesn't, either the calibration or a recorded tag
    size is wrong.
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

    Existing entries keep role, module assignment and CAD offset; only
    `pose_in_world` is set. Movable tags (`module`, `robot`) deliberately get
    **no** world pose -- they stand wherever they stand, and where that is
    gets measured on every job.

    A tag not yet in the map becomes a world tag: surveying a cell is what
    `build_tagmap` is for, and its output is exactly the fixed border tags.
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
    """Return a human-readable summary of closure errors, largest first."""
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
    "tag_map_identity",
    "validate_tag_map",
    "with_world_poses",
]

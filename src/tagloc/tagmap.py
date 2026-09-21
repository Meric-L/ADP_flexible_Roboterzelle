"""Tag map: what each tag is, and where it sits in the shared coordinate frame.

Core idea of the whole concept (doc/apriltag-lokalisierung.md section 1):

    A tag carries a number, not meaning. That tag 7 is module MOD-A, with
    its origin 40 mm below the tag centre, lives here -- in a file -- not
    in code.

Reference tags (world board, robot table) and measured tags (modules) run
through the same code; they differ only in whether their world pose is in
the map or `null` (movable, to be measured).

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

SCHEMA = "wsc.vision.tagmap/1"

#: Roles with a fixed world pose -- used to determine `T_world_cam`.
REFERENCE_ROLES = frozenset({"world", "robot_table", "reference"})
MODULE_ROLE = "module"


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
    def is_reference(self) -> bool:
        return self.role in REFERENCE_ROLES and self.pose_in_world is not None

    @property
    def is_module(self) -> bool:
        return self.role == MODULE_ROLE


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

    def module_entries(self) -> list[TagEntry]:
        """Return all tags that denote a module -- the expected module list."""
        return [entry for entry in self.entries.values() if entry.is_module]


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
    if schema != SCHEMA:
        raise ValueError(f"Unbekanntes Tag-Map-Schema '{schema}' in {path} (erwartet {SCHEMA})")
    entries: dict[int, TagEntry] = {}
    for raw in data.get("tags", []):
        tag_id = int(raw["tagId"])
        pose_in_world = raw.get("poseInWorld")
        tag_to_module = raw.get("tagToModule")
        entries[tag_id] = TagEntry(
            tag_id=tag_id,
            role=str(raw.get("role", MODULE_ROLE)),
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
    `pose_in_world` is set. Tags with role `module` deliberately get
    **no** world pose -- they are movable.
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
                role=roles.get(tag_id, "reference"),
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
    "MODULE_ROLE",
    "REFERENCE_ROLES",
    "SCHEMA",
    "Observation",
    "TagEntry",
    "TagMap",
    "empty_tag_map",
    "format_residual_report",
    "load_tag_map",
    "merge_tag_poses",
    "missing_tags",
    "observed_tag_ids",
    "place_tags",
    "relative_poses",
    "residuals",
    "save_tag_map",
    "tag_map_identity",
    "with_world_poses",
]

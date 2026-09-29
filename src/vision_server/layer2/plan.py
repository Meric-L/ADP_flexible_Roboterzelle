"""Fahrziele des Layer-2-Laufs: wohin der Flansch muss, damit die Handkamera
einen Tag sieht.

Welttag und Modultag gehen durch **dieselbe** Rechnung (`plan_step`). Sie
unterscheiden sich nur darin, woher die Tag-Pose im Welt-KS kommt:

* Welttag: aus der Tag-Map, exakt -- er definiert die Welt.
* Modultag: grobe Modulpose aus Layer 1 mal Tag-Lage aus dem Modulkatalog.

Die Kette je Ziel::

    T_tag_cam      = 180 Grad um x, `standoff_m` entlang +z des Tags
    T_world_cam    = T_world_tag · T_tag_cam
    T_world_flange = T_world_cam · inv(T_flansch_cam)
    T_base_flange  = inv(T_world_base) · T_world_flange     <- Fahrziel

`T_world_base` ist vor dem Ankern die grobe Basis aus Layer 1, danach die
gemessene aus `RobotAnchor` -- daher wird nach dem Ankern neu geplant.

Tag-Frame wie in `tagloc.pose`: +x rechts, +y zur Oberkante des Drucks, +z aus
dem Tag heraus. Kamera-Frame OpenCV: +z nach vorn, +x rechts, +y unten.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

import numpy as np

from tagloc.geometry import (
    Pose,
    compose,
    from_rotation_translation,
    invert,
    pose_from_dict,
    pose_to_dict,
)
from tagloc.tagmap import TagMap, nearest_world_tag

RUN_SCHEMA = "wsc.vision.layer2.run/1"
REPORT_SCHEMA = "wsc.vision.layer2.report/1"
TARGET_SCHEMA = "wsc.vision.layer2.target/1"
STATUS_SCHEMA = "wsc.vision.layer2.status/1"

#: Abstand der Kamera vor dem Tag. Der Wert, mit dem der Hand-Auge-Plan
#: arbeitet: nah genug fuer einen 50-mm-Tag, weit genug fuer die Schaerfe.
DEFAULT_STANDOFF_M = 0.3
#: Reichweite des Flanschs um die Basis -- UR5e 0,85 m, FR3 0,855 m. Nur ein
#: Hinweis: ein Ziel ausserhalb wird gezeigt und uebersprungen, nicht versteckt.
DEFAULT_REACH_M = 0.85

ANCHOR = "anchor"
MEASURE = "measure"

#: Kamera schaut auf den Tag: 180 Grad um x dreht +z des Tags gegen die
#: Blickrichtung der Kamera.
_FACING = np.diag([1.0, -1.0, -1.0])


@dataclass(frozen=True)
class TagRequest:
    tag_id: int
    #: `T_module_tag` aus dem Modulkatalog.
    pose_in_module: Pose


@dataclass(frozen=True)
class ModuleRequest:
    module_id: str
    name: str
    #: `T_world_module` aus Layer 1, grob.
    pose_world_module: Pose
    tags: tuple[TagRequest, ...]


@dataclass(frozen=True)
class RunRequest:
    #: `T_world_base` aus Layer 1, grob.
    robot_base: Pose
    modules: tuple[ModuleRequest, ...]
    standoff_m: float = DEFAULT_STANDOFF_M
    reach_m: float = DEFAULT_REACH_M


@dataclass(frozen=True)
class RobotReport:
    step_index: int
    reached: bool
    #: Die Pose, die der Roboter **meldet** -- nie das Ziel. `None` ohne Fahrt.
    flange_in_base: Pose | None = None
    reason: str = ""


@dataclass(frozen=True)
class StepSpec:
    """Was ein Schritt ansieht -- unabhaengig davon, wo die Basis steht."""

    purpose: str
    name: str
    module_id: str = ""
    #: Welttag: seine eine Pose. Modul: `None`, die Tags kommen aus `module`.
    world_tag: tuple[int, Pose] | None = None
    module: ModuleRequest | None = None

    def tag_candidates(self) -> list[tuple[int, Pose]]:
        """Alle Tags dieses Schritts mit ihrer Pose im Welt-KS."""
        if self.world_tag is not None:
            return [self.world_tag]
        assert self.module is not None
        return [
            (tag.tag_id, compose(self.module.pose_world_module, tag.pose_in_module))
            for tag in self.module.tags
        ]


@dataclass(frozen=True)
class ViewTarget:
    camera_in_world: Pose
    flange_in_world: Pose
    flange_in_base: Pose
    #: Abstand des Flanschs von der Basis, Meter.
    reach_m: float


@dataclass(frozen=True)
class PlannedStep:
    spec: StepSpec
    tag_id: int
    tag_in_world: Pose
    target: ViewTarget
    reachable: bool


def view_target(
    pose_world_tag: Pose, pose_world_base: Pose, pose_flange_cam: Pose, standoff_m: float
) -> ViewTarget:
    """Wo Kamera und Flansch stehen muessen, um den Tag frontal zu sehen."""
    pose_tag_cam = from_rotation_translation(_FACING, [0.0, 0.0, standoff_m])
    camera_in_world = compose(pose_world_tag, pose_tag_cam)
    flange_in_world = compose(camera_in_world, invert(pose_flange_cam))
    flange_in_base = compose(invert(pose_world_base), flange_in_world)
    return ViewTarget(
        camera_in_world=camera_in_world,
        flange_in_world=flange_in_world,
        flange_in_base=flange_in_base,
        reach_m=float(np.linalg.norm(flange_in_base[:3, 3])),
    )


def tag_facing_base(
    candidates: list[tuple[int, Pose]], base_position
) -> tuple[int, Pose]:
    """Der Tag, dessen Flaeche am meisten zur Basis zeigt.

    Ein Tag auf der Rueckseite hiesse, um das Modul herumzufahren; einer oben
    auf dem Modul ist von oben gut zu sehen.
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
) -> PlannedStep:
    """Ein Schritt, gleich ob Welttag oder Modul."""
    tag_id, tag_in_world = tag_facing_base(spec.tag_candidates(), pose_world_base[:3, 3])
    target = view_target(tag_in_world, pose_world_base, pose_flange_cam, standoff_m)
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
    """Schritt 0 am Welttag, danach die Module, das naechste zuerst.

    Geplant mit der groben Basis aus Layer 1; nach dem Ankern plant der Lauf
    die restlichen Schritte mit `plan_step` neu.

    Raises:
        ValueError: die Tag-Map kennt keinen vermessenen Welttag.
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
        plan_step(anchor, request.robot_base, pose_flange_cam, request.standoff_m, request.reach_m)
    ]
    modules = [
        plan_step(
            StepSpec(purpose=MEASURE, name=module.name, module_id=module.module_id, module=module),
            request.robot_base,
            pose_flange_cam,
            request.standoff_m,
            request.reach_m,
        )
        for module in request.modules
        if module.tags
    ]
    # Vom Nahen ins Weite: der Roboter arbeitet sich nach aussen, statt
    # kreuz und quer durch die Zelle zu fahren.
    modules.sort(key=lambda step: step.target.reach_m)
    return plan + modules


# --- JSON ------------------------------------------------------------------


def _pose(value, where: str) -> Pose:
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
    return (
        isinstance(value, list)
        and len(value) == count
        and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
            for v in value
        )
    )


def _load(text: str, schema: str) -> dict:
    try:
        data = json.loads(text or "")
    except json.JSONDecodeError as error:
        raise ValueError(f"Kein JSON: {error}") from None
    if not isinstance(data, dict) or data.get("schema") != schema:
        raise ValueError(f"Erwartet schema „{schema}“.")
    return data


def parse_run_request(text: str) -> RunRequest:
    """`wsc.vision.layer2.run/1` lesen; ValueError mit Klartext."""
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
    return RunRequest(robot_base, tuple(modules), standoff_m, reach_m)


def _positive(value, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0:
        raise ValueError(f"{where} muss eine positive Zahl sein.")
    return float(value)


def parse_report(text: str) -> RobotReport:
    """`wsc.vision.layer2.report/1` lesen; ValueError mit Klartext."""
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
    """Die Roboterpose als Job-Parameter: sieben Strings x, y, z, qx, qy, qz, qw
    -- das Format von `detection.apriltag.robot_pose_from_parameters`."""
    data = pose_to_dict(pose)
    return [repr(float(value)) for value in data["position"] + data["orientation"]]

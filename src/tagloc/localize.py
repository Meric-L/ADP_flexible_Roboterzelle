"""Vom Tag zum Modul. Reine Komposition, kein OpenCV.

Dies ist die **einzige** Stelle im Projekt, an der die Kette

    T_world_module = T_world_cam @ T_cam_tag @ T_tag_module

ausgefuehrt wird. Beide Kameras rufen dieselbe Funktion auf; sie unterscheiden
sich nur darin, was sie zu sehen bekommen
(doc/projektdoku/arbeitsplaene/apriltag-welttag-konzept.md):

* **Deckenkamera** -- sieht die Welttags, den Roboter und die Module im
  selben Bild. Das ist die Uebersicht, und nur hier laesst sich die Frage
  "neben welchem Welttag steht der Roboter" beantworten, weil nur diese Kamera
  beides gleichzeitig sieht.
* **Handkamera (eye-in-hand)** -- lokalisiert sich **einmal** zu Beginn eines
  Laufs gegen einen Welttag und vermisst danach jedes Modul in Reichweite aus
  der Naehe. Den Welttag behaelt sie nicht im Bild: steht sie 0,3 m vor einem
  Modul, ist der naechste Welttag ein bis zwei Meter entfernt und ausserhalb
  des Bildfelds. Die Luecke ueberbrueckt die Kinematik -- siehe
  `anchor_from_localization` und `localize_camera_from_anchor`, die Algebra
  steht in `handeye.py`.

**Nur die Welttags sind ortsfest.** Alles andere in der Zelle ist beweglich --
die Module ebenso wie der Roboter. Der Roboter ist ein Modul wie jedes andere;
er ist lediglich das, das immer im Einsatz ist.

`T_world_cam` kann deshalb aus zwei Quellen stammen, und
`CameraLocalization.origin` sagt, aus welcher: optisch aus den Welttags
(`ORIGIN_WORLD_TAGS`, der genaue Weg, immer bevorzugt, wenn ein Welttag im Bild
liegt) oder aus dem Anker plus der aktuellen Roboterpose uebernommen
(`ORIGIN_ROBOT_POSE`).
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

#: Woher eine Messung stammt. Die Decke liefert die Uebersicht, die
#: Flanschkamera den genauen Wert -- genau das ist die Rangfolge von
#: `merge_locations`: Was in diesem Tupel weiter hinten steht, gewinnt.
SOURCE_CEILING = "ceiling"
SOURCE_FLANGE = "flange"
SOURCE_PRECEDENCE = (SOURCE_CEILING, SOURCE_FLANGE)

#: Woher `T_world_cam` stammt. Optisch aus den Welttags ist der genaue Weg und
#: gewinnt immer, wenn ein Welttag im Bild liegt. Aus dem Anker plus Kinematik
#: ist der Weg dazwischen -- er traegt den Roboter von Modul zu Modul, wenn
#: kein Welttag mehr zu sehen ist.
ORIGIN_WORLD_TAGS = "world_tags"
ORIGIN_ROBOT_POSE = "robot_pose"

#: Wie `T_world_cam` aus den Welttags gerechnet wurde. `joint`: ein PnP ueber
#: alle Ecken aller Welttags -- der genaue Weg, sobald zwei eindeutige
#: Welttags im Bild sind. `single`: ein einzelner Welttag. `average`: Mittel
#: der Einzel-Tag-Posen, nur noch als Rueckfall (ohne Kalibrierung, oder
#: wenn das gemeinsame PnP scheitert).
SOLVER_JOINT = "joint"
SOLVER_SINGLE = "single"
SOLVER_AVERAGE = "average"
#: Die Kamerapose wurde nicht aus diesem Bild geschaetzt, sondern aus einer
#: frueheren, genauen Schaetzung uebernommen (fest montierte Kamera). Der
#: eine sichtbare Welttag dient dann nur noch zur Konsistenzpruefung.
SOLVER_STORED = "stored"


@dataclass(frozen=True)
class CameraLocalization:
    """Wo die Kamera steht und wie gut die Welttags darin uebereinstimmen."""

    pose_world_cam: Pose
    #: Jeder Welttag, der beigetragen hat, aufsteigend.
    world_tag_ids: tuple[int, ...]
    #: Der naechstgelegene Welttag -- an dem richtet sich die Handkamera aus.
    primary_tag_id: int
    #: Groesste Abweichung einer Einzelschaetzung von der zusammengefuehrten
    #: Pose. Die ehrliche Guetezahl: Widersprechen sich die vier Welttags, ist
    #: die Vermessung falsch, und diese Zahl sagt das, statt in einem Mittel
    #: zu verschwinden.
    spread_m: float = 0.0
    spread_rad: float = 0.0
    #: `ORIGIN_WORLD_TAGS` oder `ORIGIN_ROBOT_POSE` -- wie diese Pose
    #: gewonnen wurde. Ein Verbraucher muss eine direkt gemessene Pose von
    #: einer ueber die Kinematik uebernommenen unterscheiden koennen; beide
    #: sind nicht gleich genau.
    origin: str = ORIGIN_WORLD_TAGS
    #: `SOLVER_JOINT`, `SOLVER_SINGLE` oder `SOLVER_AVERAGE`; `""`, wenn aus
    #: dem Anker uebernommen.
    solver: str = ""
    #: Reprojektions-RMS der Kamerapose in Pixeln: bei `joint` ueber alle
    #: Ecken aller Welttags, bei `single` die des einen Tags. Sonst NaN.
    reprojection_error_px: float = float("nan")
    #: Je Welttag der RMS seiner Ecken unter der gemeinsamen Pose -- welcher
    #: Tag der Karte widerspricht. Leer, ausser bei `joint`.
    tag_errors_px: dict[int, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ModuleLocation:
    """Ein lokalisiertes Modul."""

    module_id: str
    instance_id: str
    pose: Pose
    frame_id: str
    tag_id: int
    reprojection_error_px: float = float("nan")
    ambiguous: bool = False
    confidence: float = 1.0
    #: Rolle aus der Tag-Karte -- `module` oder `robot`. Der Roboter wird wie
    #: jedes andere Modul gemeldet; dieses Feld unterscheidet die beiden.
    role: str = ""
    #: Welche Kamera das gemessen hat. Siehe `SOURCE_PRECEDENCE`.
    source: str = ""
    #: Der Welttag, neben dem dieses Modul steht, `-1`, wenn unbekannt.
    reference_tag_id: int = -1
    #: `T_worldtag_module` -- die Pose relativ zu diesem Welttag. Die Zelle ist
    #: um die Welttags herum aufgebaut, also ist das die Zahl, gegen die ein
    #: Modul eingerichtet und geprueft wird; `pose` bleibt im gemeinsamen
    #: Welt-KS.
    pose_in_reference_tag: Pose | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


def confidence_from(tag_pose: TagPose, *, max_reprojection_error_px: float = 3.0) -> float:
    """Berechnet die Konfidenz aus Reprojektionsfehler und Mehrdeutigkeit, in [0, 1].

    Bewusst keine Konstante 1.0 wie in frueheren Quellen: Eine Konfidenz, die
    immer 1.0 ist, traegt keine Information und verleitet die nachgelagerte
    Logik trotzdem dazu, ihr zu trauen.
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
    """Bestimmt `T_world_cam` aus den Welttags, die in diesem Bild sichtbar sind.

    Fuer jeden Welttag gilt `T_world_cam = T_world_tag @ T_cam_tag^-1`. Mit
    einer `calibration` und mindestens zwei Welttags wird die Kamerapose dann
    **gemeinsam** aus all ihren Ecken in einem PnP geloest
    (`tagloc.pose.solve_camera_from_world_tags`): Vier Ecken auf 20 cm lassen
    die Neigung einer Einzel-Tag-Schaetzung aus 2-3 m schlecht bestimmt, die
    Ecken aller Welttags spannen dagegen die ganze Zelle auf. `spread_m` sagt
    dann, wie weit die Einzel-Tag-Schaetzungen von der gemeinsamen Pose
    abweichen -- eine Diagnose, nicht die Pose.

    Ohne Kalibrierung (oder wenn die gemeinsame Loesung scheitert) werden die
    Einzel-Tag-Schaetzungen wie bisher nach Konfidenz gewichtet gemittelt --
    ein Tag, der weit weg und unter flachem Winkel gesehen wird, darf nicht so
    stark ziehen wie einer, der das Bild fuellt. Die gemeinsame Loesung ist
    die einzige Stelle, an der dieses Modul OpenCV beruehrt, und nur, wenn
    eine Kalibrierung uebergeben wird.

    `primary_tag_id` ist der Welttag, der der Kamera am naechsten liegt. An
    dem richtet sich die Handkamera aus; bei nur einem Welttag im Bild ist es
    schlicht dieser Tag.

    Kein Welttag sichtbar -> `None`, und der Aufrufer bleibt im Kamera-KS. Eine
    Weltpose zu raten waere die schlechtere Antwort.
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
                # Abstand Kamera -> Tag, also die Laenge der gemessenen
                # Translation. Der Abstand zur Identitaetspose ist genau diese
                # Norm, so braucht es hier kein eigenes numpy.
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

    # Gleichstand entscheidet die Tag-ID, damit die Wahl von Bild zu Bild
    # reproduzierbar ist.
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
    """`solve_camera_from_world_tags` -- `None` statt einer Ausnahme.

    Scheitert das gemeinsame PnP, bleibt das Mittel der Einzel-Tags: eine
    schlechtere Pose ist besser als gar keine Welt.
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
    """Liefert nur `T_world_cam`, ohne die Guetezahlen.

    Bleibt fuer die CLI-Werkzeuge erhalten, die eine Pose transformieren und
    mit der Streuung nichts anfangen. Neuer Code nimmt `localize_camera` --
    `spread_m` wegzuwerfen hiesse, die Antwort auf die Frage wegzuwerfen, ob
    die Vermessung noch stimmt.
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
    """Wandelt Modul-Tags in Modulposen um.

    Tags ohne Karteneintrag werden trotzdem aufgenommen -- mit ihrer Tag-ID als
    Modulkennung und ohne CAD-Versatz. Ein unbekannter Tag ist eine
    Information, kein Fehler: Wahrscheinlich fehlt schlicht der Karteneintrag.

    Bei bekannter Kamerapose bekommt jedes Modul zusaetzlich den Welttag, neben
    dem es steht, und seine Pose relativ zu diesem Tag. Die Zelle ist um die
    Welttags herum aufgebaut, also ist das die Zahl, gegen die ein Modul
    eingerichtet und geprueft wird.
    """
    located: list[ModuleLocation] = []
    for tag_pose in tag_poses:
        entry = tag_map.get(tag_pose.tag_id)
        if entry is not None and entry.is_reference:
            continue  # Welttags sind die Ankerpunkte, keine Module.
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
    """Mittelt die Posen relativ zum Welttag, aber nur innerhalb eines Welttags.

    Eine Gruppe, deren Mitglieder sich auf verschiedene Welttags beziehen, hat
    kein gemeinsames KS, in dem sich mitteln liesse; der Aufrufer behaelt dann
    die Weltpose und verwirft die relative, statt zwei KS zu einer
    plausibel aussehenden Unsinnspose zu vermischen.
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
    """Fuehrt mehrere Aufnahmen desselben Jobs zu einem Ergebnis je Modul zusammen.

    Gemittelt wird nur ueber Aufnahmen, in denen derselbe Tag vorkam; die
    Anzahl landet als `sampleCount` in den Attributen, damit das Ergebnis
    festhaelt, auf wie vielen Bildern die Pose beruht.
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
    """Fuehrt Messungen, die dasselbe Modul beschreiben, zu einem Ergebnis zusammen.

    Dafuer sind mehrere Tags am Roboter da: Von der Decke aus ist ein einzelner
    Tag leicht vom Arm selbst verdeckt, deshalb traegt der Roboter mehr als
    einen, jeden mit eigenem CAD-Versatz zur Roboterbasis. Alle messen daher
    *dieselbe* Pose, und sie zu mitteln ist der Sinn der Sache.

    Gewichtet nach Konfidenz. Mit einer `tag_map` wird der naechstgelegene
    Welttag fuer die zusammengefuehrte Pose neu bestimmt -- das Mittel kann
    durchaus naeher an einem anderen Welttag liegen als jeder einzelne Tag.
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
        # Die kleinste Tag-ID benennt die Gruppe, damit das Ergebnis
        # unabhaengig von der Erkennungsreihenfolge der Tags reproduzierbar ist.
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
    """Reiht eine Messung ein: eine bekannte Quelle schlaegt eine unbekannte, dann die Konfidenz."""
    try:
        precedence = SOURCE_PRECEDENCE.index(location.source)
    except ValueError:
        precedence = -1
    return (precedence, location.confidence)


def merge_locations(*groups: Sequence[ModuleLocation]) -> list[ModuleLocation]:
    """Fuegt die Ergebnisse mehrerer Kameras zu einem Bild der Zelle zusammen.

    Die Deckenkamera sieht alles, aber grob; die Handkamera sieht wenig, aber
    genau. Haben beide dasselbe Modul gemessen, gewinnt die genaue Messung --
    `SOURCE_PRECEDENCE` entscheidet, und die Konfidenz entscheidet bei
    gleichen Quellen.

    Bewusst eine reine Funktion statt einer zweiten Netzwerkverbindung
    zwischen den beiden Pis: Beide Server veroeffentlichen weiterhin
    unabhaengig voneinander, und wer beide konsumiert, ruft diese Funktion auf.
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
    """Liefert die lokalisierten Roboter -- normalerweise genau einen."""
    return [location for location in locations if location.role == ROBOT_ROLE]


def world_tag_for_robot(
    tag_map: TagMap, locations: Sequence[ModuleLocation]
) -> tuple[int, float] | None:
    """Liefert `(tag_id, Abstand in m)` des Welttags, der dem Roboter am naechsten liegt.

    Die Frage, die nur die Deckenkamera beantworten kann, weil nur sie den
    Roboter und die Welttags gleichzeitig sieht. Der Roboter richtet dann
    seine Handkamera auf diesen Tag aus, um sich genau zu lokalisieren.

    `None`, wenn kein Roboter lokalisiert wurde oder die Karte keinen
    vermessenen Welttag kennt.
    """
    robots = robot_locations(locations)
    if not robots:
        return None
    # Mehrere Roboter waeren ein Aufbau, den es nicht gibt; lieber den am
    # besten gemessenen nehmen, als stillschweigend den, der zufaellig zuerst
    # in der Liste steht.
    robot = max(robots, key=lambda location: location.confidence)
    return nearest_world_tag(tag_map, robot.pose)


def anchor_from_localization(
    localization: CameraLocalization,
    pose_base_flange: Pose,
    hand_eye: HandEye,
) -> RobotAnchor:
    """Verankert die Roboterbasis im Welt-KS aus einer einzigen Welttag-Sichtung.

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
    """Uebertraegt die Kamerapose auf eine neue Roboterpose, ohne Welttag.

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
    """Liefert `(m, rad)` zwischen der optischen und der uebernommenen Pose.

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
    """Liefert die Module aus der Karte, die nicht gefunden wurden.

    Beantwortet das Abbruchkriterium aus concept/offene_punkte.md Nr. 6 ohne
    zusaetzliche Konfiguration: Die Liste der erwarteten Module steht in der
    Tag-Karte.
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

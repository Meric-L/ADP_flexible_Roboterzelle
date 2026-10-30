"""Tag-Karte: was jeder Tag ist und wo er im gemeinsamen Koordinatensystem sitzt.

Kerngedanke des ganzen Konzepts (doc/projektdoku/apriltag-lokalisierung.md Abschnitt 1):

    Ein Tag traegt eine Nummer, keine Bedeutung. Dass Tag 7 das Modul MOD-A
    ist, mit seinem Ursprung 40 mm unter dem Tag-Mittelpunkt, steht hier --
    in einer Datei --, nicht im Code.

**Nur die Welttags stehen fest.** Vier davon sitzen im Randbereich der Zelle
und spannen das Welt-KS auf; alles andere -- Module *und der Roboter* -- ist
beweglich und wird gemessen. Der Roboter ist ein Modul wie jedes andere; er
ist lediglich das, das immer im Einsatz ist.

Referenztags (Welt) und gemessene Tags (Module, Roboter) laufen durch
denselben Code; sie unterscheiden sich nur darin, ob ihre Weltpose in der
Karte steht oder `null` ist (beweglich, wird gemessen).

Kein OpenCV: `place_tags` arbeitet nur mit Posen, nie mit Bildern.
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

#: Schema 1 kannte die Rollen `robot_table` und `reference` als *feste* Anker.
#: Das war das falsche Konzept -- siehe doc/projektdoku/arbeitsplaene/apriltag-welttag-konzept.md.
LEGACY_SCHEMA = "wsc.vision.tagmap/1"

#: Das Einzige, das in der Zelle feststeht: ein Welttag am Rand der Zelle.
WORLD_ROLE = "world"
#: Ein Tag am Roboter. Der Roboter ist ein Modul wie jedes andere -- er ist
#: lediglich das, das immer im Einsatz ist.
ROBOT_ROLE = "robot"
MODULE_ROLE = "module"

#: Rollen mit fester Weltpose -- dienen zur Bestimmung von `T_world_cam`.
#: **Nur** der Welttag. Alles andere in der Zelle laesst sich verschieben,
#: also darf nichts anderes als Anker dienen.
REFERENCE_ROLES = frozenset({WORLD_ROLE})

#: Rollen, deren Pose gemessen statt gelesen wird. Der Roboter gehoert mit
#: Absicht dazu: Er steht irgendwo, und wo er steht, ist die Frage.
MOVABLE_ROLES = frozenset({MODULE_ROLE, ROBOT_ROLE})

KNOWN_ROLES = REFERENCE_ROLES | MOVABLE_ROLES

#: Die Rollen aus Schema 1 und worauf sie abgebildet werden.
#:
#: `reference` war ein fester Anker mit Weltpose -- genau das, was heute ein
#: Welttag ist. `robot_table` war der Denkfehler: der Tisch galt als fest.
#: Er wird zum beweglichen Robotertag, und seine Weltpose faellt dabei weg.
LEGACY_ROLES = {"reference": WORLD_ROLE, "robot_table": ROBOT_ROLE}

#: Vier Welttags im Randbereich der Zelle. Keine Grenze des Codes --
#: `localize_camera` kommt mit einem einzigen aus --, aber der erwartete
#: Aufbau, damit ein fehlender Tag auffaellt, statt still Genauigkeit zu kosten.
EXPECTED_WORLD_TAG_COUNT = 4


@dataclass(frozen=True)
class TagEntry:
    """Ein Tag und seine Bedeutung."""

    tag_id: int
    role: str
    size_m: float
    module_id: str = ""
    instance_id: str = ""
    #: `None` = beweglich, wird gemessen statt gelesen.
    pose_in_world: Pose | None = None
    #: Konstanter Versatz vom Tag-Mittelpunkt zum Modulursprung, aus dem CAD.
    tag_to_module: Pose = field(default_factory=identity)

    @property
    def is_world(self) -> bool:
        """Ob dies einer der festen Welttags der Zelle ist."""
        return self.role == WORLD_ROLE

    @property
    def is_reference(self) -> bool:
        """Ob dieser Tag als Anker fuer `T_world_cam` dienen darf.

        Ein Welttag ohne Weltpose ist *kein* Anker: Er ist ein Welttag, der
        noch nicht eingemessen wurde, und Raten waere schlimmer, als im
        Kamera-KS zu bleiben.
        """
        return self.is_world and self.pose_in_world is not None

    @property
    def is_robot(self) -> bool:
        return self.role == ROBOT_ROLE

    @property
    def is_module(self) -> bool:
        """Ob dieser Tag etwas Bewegliches bezeichnet, das gemessen wird.

        Der Roboter zaehlt hier als Modul -- das ist der Kern des Konzepts,
        kein Versehen.
        """
        return self.role in MOVABLE_ROLES


#: Standard-Tag-Familie der Zelle (Namensform wie in `detector.ARUCO_DICTIONARIES`).
#: `vision_server/profiles.py` haelt denselben Wert als Literal, weil es ohne numpy
#: importierbar bleiben muss; `tests/test_server_config.py` vergleicht beide.
DEFAULT_TAG_FAMILY = "tag36h11"


@dataclass(frozen=True)
class TagMap:
    """Alle bekannten Tags einer Zelle, in einem Koordinatensystem."""

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
        """Liefert die Groesse des Tags aus der Karte, sonst den konfigurierten Rueckfall.

        Eine falsche Groesse skaliert den gemessenen Abstand linear und faellt
        sonst nicht auf -- deshalb gewinnt die Karte immer, wenn der Tag darin steht.
        """
        entry = self.get(tag_id)
        return entry.size_m if entry is not None else default_m

    def reference_poses(self) -> dict[int, Pose]:
        """Liefert alle Tags mit bekannter Weltpose."""
        return {
            tag_id: entry.pose_in_world
            for tag_id, entry in self.entries.items()
            if entry.is_reference and entry.pose_in_world is not None
        }

    def world_tag_ids(self) -> list[int]:
        """Liefert die IDs aller Tags mit der Rolle `world`, eingemessen oder nicht."""
        return sorted(tag_id for tag_id, entry in self.entries.items() if entry.is_world)

    def module_entries(self) -> list[TagEntry]:
        """Liefert alle beweglichen Tags -- die erwartete Modulliste, Roboter eingeschlossen."""
        return [entry for entry in self.entries.values() if entry.is_module]

    def robot_entries(self) -> list[TagEntry]:
        """Liefert die Tags, die am Roboter montiert sind.

        Mehrere sind erlaubt und erwartet: Von der Decke aus ist ein einzelner
        Tag leicht vom Arm selbst verdeckt, und ueber mehrere zu mitteln ist
        der Zweck von `localize.merge_by_module`.
        """
        return [entry for entry in self.entries.values() if entry.is_robot]


def validate_tag_map(tag_map: TagMap) -> list[str]:
    """Liefert, was an dieser Karte falsch ist; leere Liste = in Ordnung.

    Bewusst ein Bericht statt einer Ausnahme: Eine Zelle im Aufbau muss noch
    messen duerfen. Der Aufrufer entscheidet, ob er loggt oder abbricht --
    `AprilTagDetectionSource.open` loggt und macht weiter.
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
    """Liefert `(tag_id, Abstand in m)` des Welttags, der `pose_world` am naechsten liegt.

    Das ist die Frage, die die Deckenkamera fuer den Roboter beantwortet:
    Auf welchen der vier Welttags soll die Handkamera schauen, um sich zu
    lokalisieren. `None`, wenn die Karte keinen eingemessenen Welttag kennt.
    """
    references = tag_map.reference_poses()
    if not references:
        return None
    distances = {
        tag_id: translation_distance_m(pose, pose_world)
        for tag_id, pose in references.items()
    }
    # Zusaetzlich nach ID sortieren, damit ein exakter Gleichstand
    # deterministisch aufgeloest wird, statt von der dict-Reihenfolge abzuhaengen.
    best = min(distances, key=lambda tag_id: (distances[tag_id], tag_id))
    return best, float(distances[best])


def empty_tag_map(frame_id: str = "world", anchor_tag_id: int = 0) -> TagMap:
    """Liefert eine leere Karte -- der Betrieb ohne Karte bleibt ein Normalfall."""
    return TagMap(frame_id=frame_id, anchor_tag_id=anchor_tag_id, entries={})


def _migrate_role(role: str, tag_id: int, pose_in_world, path: Path):
    """Bildet eine Rolle aus Schema 1 auf die aktuellen ab. Liefert `(role, pose)`.

    Nicht still: jede Umdeutung wird einzeln gemeldet. Genau das war der Grund,
    alte Karten urspruenglich abzulehnen -- eine `robot_table`-Weltpose steht
    weiter in der Datei und sieht gueltig aus, waehrend sie nicht mehr benutzt
    wird. Mit einer Meldung je Tag ist das kein stiller Vorgang mehr, und eine
    veraltete Karte legt nicht mehr das ganze Vision-System lahm.
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
    """Liest eine Tag-Karte aus einer JSON-Datei."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Tag-Map nicht gefunden: {path}")
    return tag_map_from_json(path.read_text(encoding="utf-8"), source=str(path))


def tag_map_from_json(text: str, *, source: str = "<string>") -> TagMap:
    """Liest eine Tag-Karte aus JSON-Text.

    Eine Karte im alten Schema wird **migriert, nicht abgelehnt**: sie laut
    zurueckzuweisen klingt richtig, legt aber in der Praxis die ganze Erkennung
    still, weil `AprilTagDetectionSource.open` daran scheitert. Modulnamen und
    CAD-Versaetze aus der alten Karte bleiben damit erhalten; was sich in der
    Bedeutung aendert, meldet `_migrate_role` einzeln.
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
            # Ein Tippfehler bleibt ein Fehler -- sonst waere ein Tag stumm
            # weder Anker noch Modul.
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
    """Schreibt eine Tag-Karte als JSON, nach Tag-ID sortiert."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tag_map_to_json(tag_map), encoding="utf-8")


def tag_map_to_json(tag_map: TagMap) -> str:
    """Liefert die Karte als JSON-Text -- genau das, was `save_tag_map` schreibt.

    Dasselbe Format fuer Datei und OPC-UA-Methode: was das Backend schickt,
    laesst sich unveraendert ablegen und umgekehrt.
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
# Tags in ein gemeinsames Koordinatensystem setzen
# --------------------------------------------------------------------------

#: Eine Aufnahme: was in **einem** Bild gleichzeitig sichtbar war.
Observation = Mapping[int, Pose]


def observed_tag_ids(observations: Sequence[Observation]) -> set[int]:
    """Liefert alle Tag-IDs, die ueberhaupt vorkamen."""
    seen: set[int] = set()
    for observation in observations:
        seen.update(int(tag_id) for tag_id in observation)
    return seen


def relative_poses(observations: Sequence[Observation]) -> dict[tuple[int, int], list[Pose]]:
    """Bildet kamerafreie Relationen `T_a_b` aus gemeinsamen Beobachtungen.

    Zwei Tags im selben Bild ergeben `T_a_b = T_cam_a^-1 @ T_cam_b`. Die
    Kamerapose kuerzt sich heraus -- die Kamera muss also nicht bekannt sein,
    und die Aufnahmen duerfen aus voellig verschiedenen Blickwinkeln stammen.
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
    """Fuehrt mehrere Schaetzungen derselben Pose zusammen."""
    return average_poses(list(poses))


def place_tags(
    observations: Sequence[Observation], *, anchor_tag_id: int
) -> dict[int, Pose]:
    """Setzt alle beobachteten Tags in **ein** Koordinatensystem.

    Ursprung ist der Anker-Tag. Verfahren: Gemeinsame Beobachtungen spannen
    einen Graphen auf (Knoten = Tag-IDs, Kanten = gemeinsam gesehene Paare).
    Eine Breitensuche vom Anker aus verkettet die Relationen zu Posen im
    Anker-KS. Wird ein Tag ueber mehrere bereits gesetzte Nachbarn erreicht,
    werden alle Schaetzungen gemittelt, statt die erste zu nehmen.

    Tags ohne Weg zum Anker erscheinen **nicht** im Ergebnis. Das ist
    Absicht: Ihnen still eine Phantompose zu geben waere der schlimmere
    Fehler. Der Aufrufer vergleicht mit `observed_tag_ids`, um sie zu melden.

    Genauigkeitsgrenze: Gemittelt wird ueber die Nachbarn, die zum Zeitpunkt
    des Besuchs schon gesetzt sind, nicht ueber alle Kanten des fertigen
    Graphen. Das Ergebnis haengt damit von der (deterministischen)
    Besuchsreihenfolge ab und ist keine globale Ausgleichung. Fuer eine
    Zelle mit wenigen Tags und kurzen Ketten ist das in Ordnung; werden die
    Ketten lang, misst `residuals`, ob es noch gut genug ist. Der naechste
    Schritt waere eine gewichtete Ausgleichung nach kleinsten Quadraten
    ueber alle Kanten.
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
            # Alle bereits gesetzten Nachbarn dieses Tags beitragen lassen,
            # nicht nur den, ueber den wir gekommen sind.
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
    """Liefert den Schliessungsfehler je Kante: `(Position in m, Winkel in rad)`.

    Das ist die Guetezahl der Karte. Eine Bildserie, die eine Schleife
    schliesst, muss sie schliessen; tut sie es nicht, stimmt entweder die
    Kalibrierung oder eine eingetragene Tag-Groesse nicht.
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
    """Schreibt gesetzte Posen als Weltposen in eine Karte.

    Bestehende Eintraege behalten Rolle, Modulzuordnung und CAD-Versatz; nur
    `pose_in_world` wird gesetzt. Bewegliche Tags (`module`, `robot`)
    bekommen bewusst **keine** Weltpose -- sie stehen, wo sie stehen, und wo
    das ist, wird bei jedem Job gemessen.

    Ein Tag, der noch nicht in der Karte steht, wird zum Welttag: Eine Zelle
    einzumessen ist der Zweck von `build_tagmap`, und seine Ausgabe sind
    genau die festen Rand-Tags.
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
    """Liefert beobachtete Tags, die keinen Weg zum Anker hatten."""
    return sorted(observed_tag_ids(observations) - {int(tag_id) for tag_id in placed})


def format_residual_report(residual_map: Mapping[tuple[int, int], tuple[float, float]]) -> str:
    """Liefert eine lesbare Zusammenfassung der Schliessungsfehler, groesste zuerst."""
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

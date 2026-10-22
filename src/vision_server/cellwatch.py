"""Zellen-Ueberwachung (Layer 1, Deckenkamera): wurde ein Modul verschoben,
hinzugefuegt oder entfernt?

Der Pi vergleicht laufend, was die Deckenkamera sieht, mit einer Referenz --
dem Layer-1-Ergebnis, das das Frontend im Viewport hat, oder der aktuellen
stabilen Beobachtung ("Ignorieren"). Eine Aenderung meldet er erst, wenn sie
**bestaetigt** ist. Ein einzelnes schlechtes Bild, eine Person im Weg oder
der Roboterarm vor einem Tag duerfen keinen Alarm ausloesen.

Je Tag wird die Modulpose ueberwacht, die dieser Tag misst (Tag-Pose mal
CAD-Versatz aus der Tag-Map, wie im Payload). Die Regeln:

* Nur Bilder mit gemeinsam geloester Kamerapose (`joint`) und plausiblem
  Reprojektionsfehler zaehlen. Alle anderen gelten als "keine Aussage", nicht
  als "Tag fehlt".
* moved: Der Median der letzten `persist_s` Sekunden weicht laenger als
  `persist_s` um mehr als `moved_threshold_m` bzw. `rotated_threshold_deg`
  (Drehung um die Hochachse) ab. Bei mehrdeutigen Tags zaehlt nur die Position.
* appeared: Ein Tag ausserhalb der Referenz ist ueber `persist_s` in der
  Mehrzahl der Bilder zu sehen.
* disappeared: Ein Referenz-Tag fehlt laenger als `missing_persist_s`, und
  zwar in so vielen gueltigen Bildern hintereinander, dass Zufall bei seiner
  bisherigen Erkennungsrate ausgeschlossen ist (`required_misses`). Ein Tag,
  der ohnehin nur in jedem fuenften Bild erkannt wird, fehlt oft 20 s lang
  rein zufaellig -- er gilt als `unreliable` und braucht laenger.
* Hysterese: Eine Aenderung erlischt erst, wenn der Tag `persist_s` lang
  stabil zurueck ist (unter dem halben Schwellwert bzw. wieder gesehen).

Welttags werden nicht ueberwacht -- die Quelle liefert sie gar nicht erst.

Hier steckt kein asyncua und keine Kamera: Beobachtungen kommen als Daten
herein. Siehe `doc/projektdoku/arbeitsplaene/zellen-ueberwachung.md`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from .errors import VisionErrorCode

_log = logging.getLogger(__name__)

START_SCHEMA = "wsc.vision.cellwatch.start/1"
STATUS_SCHEMA = "wsc.vision.cellwatch.status/1"

OFF = "off"
WATCHING = "watching"
CHANGED = "changed"
PAUSED = "paused"
FAILED = "failed"

MOVED = "moved"
APPEARED = "appeared"
DISAPPEARED = "disappeared"

#: Ein Tag gilt erst als verschwunden, wenn er in so vielen gueltigen Bildern
#: gefehlt hat -- zwei Bilder in 20 s waeren keine Aussage.
MIN_FRAMES_FOR_MISSING = 3
#: So unwahrscheinlich muss es sein, dass ein vorhandener Tag die geforderten
#: Bilder hintereinander rein zufaellig verpasst. Bewusst sehr klein: geprueft
#: wird alle 2 s, ein Tag mit 40 % Erkennungsrate (gemessen am Decken-Pi,
#: 15.10.2026) beginnt ~700 Luecken pro Stunde. Mit 1 % gab es mehrere
#: Fehlalarme pro Stunde, mit 0,01 % in der Simulation noch einen in zehn
#: Stunden, mit 0,001 % keinen in 20 Stunden.
MISS_BY_CHANCE = 1e-5
#: Obergrenze der geforderten Fehlbilder -- auch ein schlecht erkannter Tag
#: soll irgendwann als fehlend gemeldet werden koennen (bei 2 s: 2 min).
MAX_REQUIRED_MISSES = 60
#: Unter dieser Erkennungsrate gilt ein Tag als unsicher erkannt.
UNRELIABLE_RATE = 0.5
#: Erst ab so vielen Bildern ist eine Erkennungsrate eine Aussage.
MIN_FRAMES_FOR_RATE = 5
#: Ein Modul gilt als flach liegend, wenn seine Hochachse hoechstens so weit
#: von der Welt-Z abweicht. Dann ist sein Gierwinkel auch bei mehrdeutiger
#: Pose verlaesslich: die beiden Loesungen unterscheiden sich nur im Kippen
#: (simuliert, Draufsicht aus 2,5 m: 225 von 300 Posen mehrdeutig, Gierfehler
#: beider Loesungen hoechstens 6 Grad).
FLAT_MAX_TILT_DEG = 30.0


def is_flat(pose) -> bool:
    """Liegt das Modul flach (Hochachse nahe Welt-Z, nach oben oder unten)?"""
    return abs(float(np.asarray(pose, dtype=np.float64)[2, 2])) >= math.cos(
        math.radians(FLAT_MAX_TILT_DEG)
    )


def conservative_rate(hits: int, frames: int) -> float:
    """Untere Grenze des 95-%-Wilson-Intervalls der Erkennungsrate.

    Die gemessene Rate aus wenigen Bildern streut stark: bei echten 40 % sind
    60 % aus 20 Bildern nicht selten -- und eine zu hoch geschaetzte Rate
    fordert zu wenige Fehlbilder (gefunden mit einer simulierten Stunde).
    Die untere Grenze schaetzt vorsichtig.
    """
    if frames <= 0:
        return 0.0
    z = 1.96
    p = hits / frames
    denominator = 1 + z * z / frames
    centre = p + z * z / (2 * frames)
    margin = z * math.sqrt(p * (1 - p) / frames + z * z / (4 * frames * frames))
    return max(0.0, (centre - margin) / denominator)


def required_misses(rate: float) -> int:
    """Fehlbilder hintereinander, bevor ein Tag mit dieser Erkennungsrate als
    verschwunden gilt: `(1 - rate)^n < MISS_BY_CHANCE`."""
    rate = min(max(rate, 0.05), 0.99)
    needed = math.ceil(math.log(MISS_BY_CHANCE) / math.log(1.0 - rate))
    return min(MAX_REQUIRED_MISSES, max(MIN_FRAMES_FOR_MISSING, needed))


@dataclass(frozen=True)
class CellWatchOptions:
    moved_threshold_m: float = 0.05
    rotated_threshold_deg: float = 10.0
    persist_s: float = 5.0
    missing_persist_s: float = 20.0
    #: Abstand der Auswertungen. Erkennung auf dem vollen Deckenkamera-Frame
    #: ist auf dem Pi teuer -- 2 s statt 1 s halbiert die Last.
    interval_s: float = 2.0
    #: Bilder mit groesserem RMS der Kamerapose gelten als "keine Aussage".
    #: 6 px statt 3: auf Pi 1 liegt das gemeinsame PnP bei einer Kalibrierung
    #: mit 0,4 px stabil bei 3,3 px -- der Rest ist ~1 cm Ungenauigkeit der
    #: Tag-Map. Das ist gegen 5 cm Verschiebe-Schwelle kein Grund, gar nichts
    #: mehr zu sehen; ein grober Fehler (falsche Taggroesse, verschobener
    #: Welttag) liegt weit darueber.
    max_camera_error_px: float = 6.0
    #: Mindestzahl Welttags im Bild. Ab 2 schaetzt der Pi die Kamerapose neu
    #: (gemeinsames PnP, auf ~6 mm genau). Mit 1 nutzt er die gespeicherte
    #: Pose der fest montierten Kamera und prueft den einen Welttag nur auf
    #: Konsistenz -- aus einem einzelnen Tag waere die Pose aus 2,5 m um
    #: Zentimeter ungenau (simuliert: Median 40 mm, Ausreisser bis 1,5 m durch
    #: gekippte Loesungen, 31-38 % der Bilder ueber 5 cm). Mit 2 ist es wie
    #: bisher: unter zwei Welttags wird pausiert.
    min_world_tags: int = 1
    #: So viel mehr Eckenfehler als beim Speichern der Pose darf der eine
    #: Welttag haben, bevor "Kamera bewegt?" gilt. Gemessen: unbewegt hoechstens
    #: ~1 px mehr, bei 5 mm Verschiebung ~1,4 px, bei 10 mm ~3,4 px, bei 0,1
    #: Grad ~1,4 px; 2 px erkennt also Verschiebungen ab ~7 mm.
    stored_pose_margin_px: float = 2.0

    def to_dict(self) -> dict:
        return {
            "movedThresholdM": self.moved_threshold_m,
            "rotatedThresholdDeg": self.rotated_threshold_deg,
            "persistS": self.persist_s,
            "missingPersistS": self.missing_persist_s,
            "intervalS": self.interval_s,
            "maxCameraErrorPx": self.max_camera_error_px,
            "minWorldTags": self.min_world_tags,
            "storedPoseMarginPx": self.stored_pose_margin_px,
        }


@dataclass(frozen=True)
class TagSighting:
    """Ein Tag in einem gueltigen Bild: die Modulpose, die er misst."""

    position: tuple[float, float, float]
    #: Drehung um die Hochachse des Welt-KS, Grad.
    yaw_deg: float
    ambiguous: bool = False
    module_id: str = ""
    #: Flach liegend: dann zaehlt der Gierwinkel auch bei `ambiguous`.
    flat: bool = False

    @property
    def has_yaw(self) -> bool:
        return not self.ambiguous or self.flat


@dataclass(frozen=True)
class ReferenceTag:
    position: tuple[float, float, float]
    yaw_deg: float
    module_id: str = ""
    #: Ohne verlaessliche Drehung (mehrdeutig) wird nur die Position verglichen.
    has_yaw: bool = True


@dataclass
class _TagState:
    exceeded_since: float | None = None
    #: Abweichung beim letzten Bild, fuer `pending`.
    last_distance: float | None = None
    last_rotation: float | None = None
    back_since: float | None = None
    last_seen: float | None = None
    last_seen_wall: datetime | None = None
    change: dict | None = None


@dataclass
class CellWatchTracker:
    """Wertet gueltige Bilder gegen eine Referenz aus. Rein, ohne Uhr: jede
    Beobachtung bringt ihren Zeitpunkt mit (`t` monoton, `wall` fuer den
    Status)."""

    options: CellWatchOptions
    reference: dict[int, ReferenceTag]
    started_at: float
    _frames: deque = field(default_factory=deque)
    _tags: dict[int, _TagState] = field(default_factory=dict)
    #: Letztes `(t, wall)` -- rechnet monotone Zeitpunkte in Uhrzeit um.
    _clock: tuple | None = None

    def carry_from(self, previous: "CellWatchTracker", *, same_reference: bool) -> None:
        """Verlauf einer vorigen Ueberwachung uebernehmen (neu scharf schalten).

        Der Bildverlauf -- und damit die Erkennungsrate je Tag -- gilt
        unabhaengig von der Referenz. Ohne ihn braeuchte "disappeared" nach
        jedem Aendern einer Zeit oder Schwelle bis zu 2 min. Bei derselben
        Referenz bleiben auch laufende Kandidaten und Meldungen bestehen; bei
        einer neuen zaehlen fruehere Bilder nicht als "fehlt".
        """
        self._frames = deque(previous._frames)
        self._clock = previous._clock
        if same_reference:
            self._tags = previous._tags
            self.started_at = previous.started_at

    def _wall_at(self, t: float) -> str | None:
        if self._clock is None:
            return None
        clock_t, clock_wall = self._clock
        return (clock_wall - timedelta(seconds=clock_t - t)).isoformat(timespec="seconds")

    def update(self, t: float, wall: datetime, sightings: dict[int, TagSighting]) -> None:
        """Ein gueltiges Bild. Ungueltige Bilder kommen gar nicht erst hierher."""
        self._clock = (t, wall)
        self._frames.append((t, sightings))
        # Lang genug fuer die Erkennungsrate vor einer Luecke und fuer bis zu
        # `MAX_REQUIRED_MISSES` Fehlbilder.
        horizon = t - max(
            4.0 * max(self.options.persist_s, self.options.missing_persist_s),
            3.0 * MAX_REQUIRED_MISSES * self.options.interval_s,
        )
        while self._frames and self._frames[0][0] < horizon:
            self._frames.popleft()
        for tag_id in set(self.reference) | set(sightings) | set(self._tags):
            state = self._tags.setdefault(tag_id, _TagState())
            if tag_id in sightings:
                state.last_seen = t
                state.last_seen_wall = wall
            if tag_id in self.reference:
                self._update_reference_tag(tag_id, state, t, wall)
            else:
                self._update_new_tag(tag_id, state, t, wall)

    def detection_rate(self, tag_id: int, until: float) -> tuple[float, int]:
        """Anteil der gueltigen Bilder mit dem Tag bis `until`, ueber den
        ganzen behaltenen Verlauf (bis `2 * MAX_REQUIRED_MISSES` Takte):
        `(Rate, Bilder)`."""
        frames = [sightings for frame_t, sightings in self._frames if frame_t <= until]
        if not frames:
            return 0.0, 0
        return sum(1 for sightings in frames if tag_id in sightings) / len(frames), len(frames)

    def visibility(self, t: float) -> list[dict]:
        """Je Tag (Referenz und gesehene): wie oft er in den letzten
        `missing_persist_s` Sekunden gueltiger Bilder erkannt wurde.

        Das ist die stabile Antwort auf "ist der Tag im Bild?" -- ein
        einzelnes Bild flackert bei einem Tag an der Erkennungsgrenze.
        """
        start = t - self.options.missing_persist_s
        window = [sightings for frame_t, sightings in self._frames if frame_t > start]
        tag_ids = set(self.reference) | {tag_id for sightings in window for tag_id in sightings}
        result = []
        for tag_id in sorted(tag_ids):
            seen = sum(1 for sightings in window if tag_id in sightings)
            rate, rated = self.detection_rate(tag_id, until=t)
            state = self._tags.get(tag_id)
            last_wall = state.last_seen_wall if state is not None else None
            result.append(
                {
                    "tagId": int(tag_id),
                    "seenRatio": seen / len(window) if window else None,
                    "framesChecked": len(window),
                    "lastSeenAt": last_wall.isoformat(timespec="seconds") if last_wall else None,
                    "unreliable": bool(
                        rated >= MIN_FRAMES_FOR_RATE and 0.0 < rate < UNRELIABLE_RATE
                    ),
                }
            )
        return result

    def pending(self, t: float) -> list[dict]:
        """Was gerade nach einer Aenderung aussieht, aber noch nicht gemeldet
        ist -- fuer die sofortige Anzeige. `confirmInS` ist die Zeit bis zur
        Meldung, falls es so bleibt (bei "disappeared" geschaetzt: die
        geforderte Zahl Fehlbilder haengt von der Erkennungsrate ab)."""
        options = self.options
        window = self._window(t)
        result = []
        for tag_id in sorted(set(self.reference) | set(self._tags)):
            state = self._tags.get(tag_id)
            if state is None or state.change is not None:
                continue
            seen = any(tag_id in sightings for _, sightings in window)
            if tag_id in self.reference:
                module_id = self.reference[tag_id].module_id
                if not seen:
                    last = state.last_seen if state.last_seen is not None else self.started_at
                    missing = sum(1 for frame_t, _ in self._frames if frame_t > last)
                    if missing < 2:
                        continue
                    rate, rated = self.detection_rate(tag_id, until=last)
                    needed = required_misses(conservative_rate(round(rate * rated), rated))
                    confirm = max(
                        options.missing_persist_s - (t - last),
                        (needed - missing) * options.interval_s,
                        0.0,
                    )
                    result.append(
                        _pending(tag_id, module_id, DISAPPEARED, self._wall_at(last), None, None, confirm)
                    )
                elif state.exceeded_since is not None:
                    confirm = max(options.persist_s - (t - state.exceeded_since), 0.0)
                    result.append(
                        _pending(
                            tag_id, module_id, MOVED, self._wall_at(state.exceeded_since),
                            state.last_distance, state.last_rotation, confirm,
                        )
                    )
            elif seen:
                first_seen = next(
                    (frame_t for frame_t, sightings in self._frames if tag_id in sightings), t
                )
                module_id = next(
                    (
                        sightings[tag_id].module_id
                        for _, sightings in reversed(self._frames)
                        if tag_id in sightings
                    ),
                    "",
                )
                confirm = max(options.persist_s - (t - first_seen), 0.0)
                result.append(
                    _pending(tag_id, module_id, APPEARED, self._wall_at(first_seen), None, None, confirm)
                )
        return result

    @property
    def changes(self) -> list[dict]:
        return [
            self._tags[tag_id].change
            for tag_id in sorted(self._tags)
            if self._tags[tag_id].change is not None
        ]

    def stable_observation(self, t: float) -> dict[int, ReferenceTag] | None:
        """Die Mediane der letzten `persist_s` Sekunden als neue Referenz --
        nur Tags, die in der Mehrzahl der Bilder zu sehen waren. `None`,
        solange die Beobachtung noch nicht `persist_s` lang reicht."""
        window = self._window(t)
        if not window or t - window[0][0] < self.options.persist_s * 0.8:
            return None
        tag_ids = {tag_id for _, sightings in window for tag_id in sightings}
        reference = {}
        for tag_id in tag_ids:
            seen = [sightings[tag_id] for _, sightings in window if tag_id in sightings]
            if len(seen) * 2 <= len(window):
                continue
            position, yaw, has_yaw = _median(seen)
            reference[tag_id] = ReferenceTag(position, yaw or 0.0, seen[-1].module_id, has_yaw)
        return reference

    # --- Regeln -----------------------------------------------------------

    def _window(self, t: float) -> list:
        return [frame for frame in self._frames if frame[0] > t - self.options.persist_s]

    def _update_reference_tag(self, tag_id: int, state: _TagState, t: float, wall) -> None:
        options = self.options
        reference = self.reference[tag_id]
        window = self._window(t)
        seen = [sightings[tag_id] for _, sightings in window if tag_id in sightings]

        # Verschwunden: lange nicht gesehen, in genug gueltigen Bildern.
        last = state.last_seen if state.last_seen is not None else self.started_at
        missing_frames = sum(1 for frame_t, _ in self._frames if frame_t > last)
        # Ohne Verlauf (z. B. direkt nach dem Scharfschalten) ist die
        # vorsichtige Rate 0 -- dann gilt die Obergrenze, nicht 3 Bilder.
        rate, rated_frames = self.detection_rate(tag_id, until=last)
        needed = required_misses(conservative_rate(round(rate * rated_frames), rated_frames))
        if not seen and t - last > options.missing_persist_s and missing_frames >= needed:
            if state.change is None or state.change["kind"] != DISAPPEARED:
                state.change = _change(tag_id, DISAPPEARED, reference, None, None, None, wall)
            return
        if state.change is not None and state.change["kind"] == DISAPPEARED:
            # Erst zurueck, wenn er wieder in der Mehrzahl der Bilder ist.
            if len(seen) * 2 > len(window) and t - window[0][0] >= options.persist_s * 0.8:
                state.change = None
            else:
                return
        if not seen:
            return

        position, yaw, has_yaw = _median(seen)
        distance = float(np.linalg.norm(np.subtract(position, reference.position)))
        rotation = (
            abs(_wrap_deg(yaw - reference.yaw_deg)) if has_yaw and reference.has_yaw else None
        )
        state.last_distance, state.last_rotation = distance, rotation
        exceeded = distance > options.moved_threshold_m or (
            rotation is not None and rotation > options.rotated_threshold_deg
        )
        back = distance < options.moved_threshold_m / 2 and (
            rotation is None or rotation < options.rotated_threshold_deg / 2
        )
        if exceeded:
            state.back_since = None
            if state.exceeded_since is None:
                state.exceeded_since = t
            if t - state.exceeded_since >= options.persist_s:
                since = state.change["since"] if state.change and state.change["kind"] == MOVED else None
                state.change = _change(tag_id, MOVED, reference, position, distance, rotation, wall)
                if since is not None:
                    state.change["since"] = since
            return
        state.exceeded_since = None
        if state.change is not None and state.change["kind"] == MOVED:
            if back:
                if state.back_since is None:
                    state.back_since = t
                if t - state.back_since >= options.persist_s:
                    state.change = None
                    state.back_since = None
            else:
                state.back_since = None
                # Zwischen den Schwellen: der Alarm bleibt, mit aktuellen Werten.
                since = state.change["since"]
                state.change = _change(tag_id, MOVED, reference, position, distance, rotation, wall)
                state.change["since"] = since

    def _update_new_tag(self, tag_id: int, state: _TagState, t: float, wall) -> None:
        options = self.options
        window = self._window(t)
        seen = [sightings[tag_id] for _, sightings in window if tag_id in sightings]
        if state.change is not None:
            # Erlischt erst, wenn er `persist_s` lang nicht mehr zu sehen war.
            if state.last_seen is not None and t - state.last_seen >= options.persist_s:
                state.change = None
            elif seen:
                position, _, _ = _median(seen)
                since = state.change["since"]
                state.change = _change(tag_id, APPEARED, None, position, None, None, wall, seen[-1].module_id)
                state.change["since"] = since
            return
        first_seen = next(
            (frame_t for frame_t, sightings in self._frames if tag_id in sightings), None
        )
        if (
            first_seen is not None
            and t - first_seen >= options.persist_s
            and len(seen) * 2 > len(window)
        ):
            position, _, _ = _median(seen)
            state.change = _change(tag_id, APPEARED, None, position, None, None, wall, seen[-1].module_id)


def _median(seen: list[TagSighting]) -> tuple[tuple[float, float, float], float | None, bool]:
    """Median der Position; Median der Drehung ueber die Sichtungen mit
    verlaesslichem Gierwinkel (eindeutig, oder flach liegend)."""
    position = tuple(float(v) for v in np.median([s.position for s in seen], axis=0))
    yaws = [s.yaw_deg for s in seen if s.has_yaw]
    if not yaws:
        return position, None, False
    # Um den ersten Wert herum mitteln, damit -179/+179 nicht 0 ergibt.
    anchor = yaws[0]
    yaw = anchor + float(np.median([_wrap_deg(y - anchor) for y in yaws]))
    return position, _wrap_deg(yaw), True


def _pending(tag_id, module_id, kind, since, distance, rotation, confirm_s) -> dict:
    return {
        "tagId": int(tag_id),
        "moduleId": module_id or "",
        "kind": kind,
        "since": since,
        "distanceM": distance,
        "rotationDeg": rotation,
        "confirmInS": round(float(confirm_s), 1),
    }


def _wrap_deg(angle: float) -> float:
    return (angle + 180.0) % 360.0 - 180.0


def _change(tag_id, kind, reference, position, distance, rotation, wall, module_id=""):
    return {
        "tagId": int(tag_id),
        "moduleId": (reference.module_id if reference is not None else module_id) or "",
        "kind": kind,
        "referencePosition": list(reference.position) if reference is not None else None,
        "currentPosition": list(position) if position is not None else None,
        "distanceM": distance,
        "rotationDeg": rotation,
        "since": wall.isoformat(timespec="seconds"),
    }


def yaw_deg(pose) -> float:
    """Drehung um die Hochachse (+z des Welt-KS), Grad."""
    pose = np.asarray(pose, dtype=np.float64)
    return math.degrees(math.atan2(pose[1, 0], pose[0, 0]))


# --- JSON ---------------------------------------------------------------------


def parse_start(text: str) -> tuple[str | None, CellWatchOptions]:
    """`wsc.vision.cellwatch.start/1` lesen: `(referenceJobId, Optionen)`."""
    try:
        data = json.loads(text or "")
    except json.JSONDecodeError as error:
        raise ValueError(f"Kein JSON: {error}") from None
    if not isinstance(data, dict) or data.get("schema", START_SCHEMA) != START_SCHEMA:
        raise ValueError(f"Erwartet schema „{START_SCHEMA}“.")
    job_id = data.get("referenceJobId")
    if job_id is not None and not isinstance(job_id, str):
        raise ValueError("referenceJobId muss ein String oder null sein.")
    defaults = CellWatchOptions()
    values = {}
    for key, name in (
        ("movedThresholdM", "moved_threshold_m"),
        ("rotatedThresholdDeg", "rotated_threshold_deg"),
        ("persistS", "persist_s"),
        ("missingPersistS", "missing_persist_s"),
        ("intervalS", "interval_s"),
        ("maxCameraErrorPx", "max_camera_error_px"),
        ("storedPoseMarginPx", "stored_pose_margin_px"),
    ):
        value = data.get(key, getattr(defaults, name))
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0:
            raise ValueError(f"{key} muss eine positive Zahl sein.")
        values[name] = float(value)
    min_world_tags = data.get("minWorldTags", defaults.min_world_tags)
    if (
        isinstance(min_world_tags, bool)
        or not isinstance(min_world_tags, int)
        or not 1 <= min_world_tags <= 4
    ):
        raise ValueError("minWorldTags muss eine Ganzzahl von 1 bis 4 sein.")
    return job_id or None, CellWatchOptions(min_world_tags=min_world_tags, **values)


def reference_from_payload(payload_json: str, world_frame: str) -> dict[int, ReferenceTag]:
    """Die Referenz aus einem Job-Ergebnis (`wsc.vision.detections/1`).

    Je Tag die Pose der Detektion, zu der er gehoert -- bei zusammengefassten
    Detektionen (`attributes.tagIds`) bekommt jeder Tag dieselbe.

    Raises:
        ValueError: kein Erfolgsergebnis oder nicht im Welt-KS.
    """
    from tagloc.geometry import pose_from_dict

    payload = json.loads(payload_json)
    if payload.get("resultState", 0) != 0 or payload.get("errorCode"):
        raise ValueError("Referenz-Job ist fehlgeschlagen.")
    if payload.get("frameId") != world_frame:
        raise ValueError(
            f"Referenz-Job liegt nicht im Welt-KS („{payload.get('frameId')}“) -- kein Welttag im Bild?"
        )
    reference = {}
    for detection in payload.get("detections") or []:
        attributes = detection.get("attributes") or {}
        if attributes.get("role") == "world":
            continue
        pose = pose_from_dict(
            {"position": detection["position"], "orientation": detection["orientation"]}
        )
        tag_ids = attributes.get("tagIds") or [attributes.get("tagId")]
        for tag_id in tag_ids:
            if isinstance(tag_id, int):
                reference[tag_id] = ReferenceTag(
                    position=tuple(float(v) for v in pose[:3, 3]),
                    yaw_deg=yaw_deg(pose),
                    module_id=str(detection.get("moduleId") or ""),
                    has_yaw=not attributes.get("ambiguous", False) or is_flat(pose),
                )
    return reference


def sightings_from_locations(locations, localization, options: CellWatchOptions):
    """Aus `locate_modules` (unzusammengefasst) die Sichtungen eines Bilds.

    `(None, Grund)`, wenn das Bild keine Aussage erlaubt: keine gemeinsam
    geloeste Kamerapose oder ein zu grosser Reprojektionsfehler.
    """
    from tagloc.localize import SOLVER_JOINT

    if localization is None:
        return None, NO_WORLD_TAG
    if localization.solver != SOLVER_JOINT:
        return None, "weniger als zwei Welt-Tags im Bild -- Kamerapose zu ungenau"
    error = localization.reprojection_error_px
    if not math.isfinite(error) or error > options.max_camera_error_px:
        return None, f"Kamerapose unplausibel (RMS {error:.1f} px){_worst_tag(localization)}"
    return _sightings(locations), ""


#: Pausengrund ohne Welttag im Bild. Alle Gruende, die an der Verortung
#: scheitern, nennen "Welt-Tag" -- daran erkennt das Frontend die Pause.
NO_WORLD_TAG = "kein Welt-Tag im Bild -- keine Verortung"


def _sightings(locations) -> dict:
    sightings = {}
    for location in locations:
        sightings[int(location.tag_id)] = TagSighting(
            position=tuple(float(v) for v in np.asarray(location.pose)[:3, 3]),
            yaw_deg=yaw_deg(location.pose),
            ambiguous=bool(location.ambiguous),
            module_id=location.module_id,
            flat=is_flat(location.pose),
        )
    return sightings


class CameraPoseKeeper:
    """Merkt sich die Pose der fest montierten Deckenkamera und beurteilt
    jedes Bild.

    * Ab zwei Welttags (gemeinsames PnP, Fehler im Rahmen) gilt die Pose aus
      dem Bild, und sie wird gemerkt: Mittel der letzten `HISTORY` Posen, dazu
      je Welttag sein Eckenfehler als Grundlinie (er enthaelt den Kartenfehler
      von ~1 cm, den die gemeinsame Loesung nicht wegrechnen kann).
    * Mit genau einem Welttag gilt die gemerkte Pose. Der Tag wird nur auf
      Konsistenz geprueft: Liegen seine Ecken um mehr als
      `stored_pose_margin_px` ueber der Grundlinie, hat sich die Kamera
      vermutlich bewegt -- das Bild zaehlt nicht. Nach `BAD_FRAMES_TO_DROP`
      solchen Bildern in Folge wird die Pose verworfen, und es braucht wieder
      zwei Welttags.
    * Ohne gemerkte Pose (Start, nach Verwerfen, neue Tag-Map) mit nur einem
      Welttag: Pause, kein Raten.

    Die Pose uebersteht Neustarts: Sie liegt in `path` (erst ab
    `SAVE_MIN_POSES` Posen, danach hoechstens alle `SAVE_INTERVAL_S`), und der
    Referenz-Scan eines `StartCellWatch` kann sie liefern (`adopt`). Beides
    ist durch die Konsistenzpruefung abgesichert: Wurde die Kamera waehrend
    der Pause bewegt, passt der eine Welttag nicht mehr, und die Pose faellt
    weg. Eine geaenderte Tag-Map entwertet sie ebenfalls.
    """

    SCHEMA = "wsc.vision.cellwatch.camerapose/1"
    SAVE_MIN_POSES = 5
    SAVE_INTERVAL_S = 600.0
    HISTORY = 20
    #: Weicht ein neues gemeinsames Ergebnis so weit von der gemerkten Pose ab,
    #: wurde die Kamera bewegt: der Verlauf faengt neu an, statt zu mitteln.
    RESET_TRANSLATION_M = 0.015
    RESET_ROTATION_DEG = 0.5
    BAD_FRAMES_TO_DROP = 5
    #: Gewicht des neuen Werts bei der Grundlinie (gleitender Mittelwert).
    BASELINE_ALPHA = 0.2

    def __init__(self, path: Path | None = None, clock=time.monotonic, wall=None) -> None:
        self._poses: deque = deque(maxlen=self.HISTORY)
        self._baseline: dict[int, float] = {}
        self._map_key: tuple | None = None
        self._bad_frames = 0
        self._path = Path(path) if path is not None else None
        self._clock = clock
        self._wall = wall or (lambda: datetime.now(timezone.utc))
        #: Woher die Pose stammt: `measured` (>= 2 Welttags in einem Bild),
        #: `file` (nach Neustart wiederhergestellt) oder `scan` (aus dem
        #: Referenz-Scan uebernommen). `None` ohne Pose.
        self._source: str | None = None
        self._saved_clock: float | None = None
        self._saved_at: str | None = None

    @staticmethod
    def _key(tag_map) -> tuple:
        return tuple(
            sorted(
                (int(tag_id), tuple(round(float(v), 4) for v in np.asarray(pose)[:3, 3]))
                for tag_id, pose in tag_map.reference_poses().items()
            )
        )

    def clear(self) -> None:
        self._poses.clear()
        self._baseline.clear()
        self._bad_frames = 0
        self._source = None

    def drop(self) -> None:
        """Pose verwerfen **und** die Datei loeschen: Die Pose gilt nicht mehr,
        ein Neustart darf sie nicht wiederherstellen."""
        self.clear()
        self._saved_clock = None
        if self._path is not None:
            try:
                self._path.unlink(missing_ok=True)
            except OSError:
                _log.warning("Gemerkte Kamerapose %s nicht loeschbar", self._path, exc_info=True)

    def info(self) -> dict:
        """Fuer den Status: woher die Pose stammt und wie fest sie steht."""
        return {
            "source": self._source,
            "poseCount": len(self._poses),
            "savedAt": self._saved_at,
        }

    # --- Datei ------------------------------------------------------------

    def load(self) -> bool:
        """Die gemerkte Pose aus der Datei holen. `False` ohne (brauchbare) Datei."""
        if self._path is None or not self._path.is_file():
            return False
        from tagloc.geometry import pose_from_dict

        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if data.get("schema") != self.SCHEMA:
                raise ValueError(f"Schema {data.get('schema')!r}")
            pose = pose_from_dict(data["poseWorldCam"])
            baseline = {int(k): float(v) for k, v in (data.get("baselinePx") or {}).items()}
            map_key = tuple(
                (int(tag_id), tuple(float(v) for v in position))
                for tag_id, position in data["mapKey"]
            )
            count = max(1, min(int(data.get("poseCount", 1)), self.HISTORY))
        except (OSError, ValueError, KeyError, TypeError) as error:
            _log.warning("Gemerkte Kamerapose %s unbrauchbar (%s) -- ignoriert", self._path, error)
            return False
        self.clear()
        # Mit dem Gewicht, das sie beim Speichern hatte: neue Messungen
        # ersetzen sie nach und nach, statt sie sofort zu verdraengen.
        self._poses.extend([pose] * count)
        self._baseline = baseline
        self._map_key = map_key
        self._source = "file"
        self._saved_clock = self._clock()
        self._saved_at = data.get("savedAt")
        _log.info("Gemerkte Kamerapose aus %s wiederhergestellt (%d Posen)", self._path, count)
        return True

    def _save(self) -> None:
        if self._path is None or not self._poses:
            return
        from tagloc.geometry import average_poses, pose_to_dict

        saved_at = self._wall().isoformat(timespec="seconds")
        payload = {
            "schema": self.SCHEMA,
            "savedAt": saved_at,
            "poseWorldCam": pose_to_dict(average_poses(list(self._poses))),
            "baselinePx": {str(tag_id): round(value, 4) for tag_id, value in self._baseline.items()},
            "mapKey": [[tag_id, list(position)] for tag_id, position in (self._map_key or ())],
            "poseCount": len(self._poses),
        }
        temporary = self._path.with_name(self._path.name + ".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, self._path)
        except OSError:
            # Auch ein Fehlschlag zaehlt als Versuch: sonst kaeme die Warnung
            # mit Traceback bei jedem Bild (alle 2 s), solange der Ort kaputt ist.
            self._saved_clock = self._clock()
            _log.warning("Kamerapose nicht gespeichert nach %s", self._path, exc_info=True)
            return
        self._saved_clock = self._clock()
        self._saved_at = saved_at

    def _maybe_save(self) -> None:
        """Erst ab `SAVE_MIN_POSES` Posen, dann hoechstens alle `SAVE_INTERVAL_S`:
        die Datei liegt auf einer SD-Karte, und die Pose aendert sich kaum."""
        if self._path is None or len(self._poses) < self.SAVE_MIN_POSES:
            return
        if self._saved_clock is None or self._clock() - self._saved_clock >= self.SAVE_INTERVAL_S:
            self._save()

    def adopt(self, pose, baseline: dict, tag_map) -> bool:
        """Die Kamerapose eines Referenz-Scans uebernehmen (`True`, wenn sie gilt).

        Der Scan hat die Kamera schon aus mehreren Welttags verortet, genau wie
        eine Messung hier. Steht schon eine gemerkte Pose, gewinnt sie, solange
        die beiden uebereinstimmen (sie ist ueber viele Bilder gemittelt). Weicht
        der frische Scan deutlich ab, wurde die Kamera bewegt -- dann zaehlt er.
        """
        from tagloc.geometry import (
            average_poses,
            rotation_distance_rad,
            translation_distance_m,
        )

        self.stored_for(tag_map)  # prueft die Tag-Map
        if self._poses:
            current = average_poses(list(self._poses))
            if (
                translation_distance_m(current, pose) <= self.RESET_TRANSLATION_M
                and math.degrees(rotation_distance_rad(current, pose)) <= self.RESET_ROTATION_DEG
            ):
                return False
            self.clear()
        self._poses.append(pose)
        self._baseline.update({int(k): float(v) for k, v in baseline.items()})
        self._source = "scan"
        self._save()
        _log.info("Kamerapose aus dem Referenz-Scan uebernommen")
        return True

    def stored_for(self, tag_map):
        """Die gemerkte Pose, oder `None`. Eine neue Tag-Map verwirft sie: sie
        gilt nur gegen die Weltposen, mit denen sie geschaetzt wurde."""
        key = self._key(tag_map)
        if key != self._map_key:
            if self._poses:
                _log.info("Tag-Map hat sich geaendert -- gemerkte Kamerapose verworfen")
            self.drop()
            self._map_key = key
        if not self._poses:
            return None
        from tagloc.geometry import average_poses

        return average_poses(list(self._poses))

    def judge(self, locations, localization, options: CellWatchOptions, tag_map):
        """`(Sichtungen, "")` oder `(None, Grund)`; wie `sightings_from_locations`."""
        from tagloc.localize import SOLVER_JOINT, SOLVER_STORED

        self.stored_for(tag_map)  # prueft die Tag-Map
        if localization is None:
            return None, NO_WORLD_TAG
        if localization.solver == SOLVER_JOINT:
            sightings, reason = sightings_from_locations(locations, localization, options)
            if sightings is not None:
                self._remember(localization)
            return sightings, reason
        if options.min_world_tags >= 2:
            return None, "weniger als zwei Welt-Tags im Bild -- Kamerapose zu ungenau"
        if localization.solver != SOLVER_STORED:
            return None, (
                "Kamerapose noch nicht bekannt -- erst muessen 2 Welt-Tags gleichzeitig "
                "zu sehen sein"
            )
        errors = localization.tag_errors_px
        excess = {
            tag_id: error - self._baseline.get(tag_id, self._mean_baseline())
            for tag_id, error in errors.items()
        }
        worst = max(excess, key=excess.get) if excess else None
        if worst is None or excess[worst] > options.stored_pose_margin_px:
            self._bad_frames += 1
            if self._bad_frames >= self.BAD_FRAMES_TO_DROP:
                self.drop()
                return None, (
                    "Kamera bewegt? Der Welt-Tag passt nicht mehr zur gespeicherten Kamerapose "
                    "-- Pose verworfen, 2 Welt-Tags gleichzeitig noetig"
                )
            if worst is None:
                return None, "Welt-Tag nicht auswertbar -- keine Verortung"
            return None, (
                f"Welt-Tag {worst} passt nicht zur gespeicherten Kamerapose "
                f"(+{excess[worst]:.1f} px) -- Kamera bewegt?"
            )
        self._bad_frames = 0
        return _sightings(locations), ""

    def _mean_baseline(self) -> float:
        return sum(self._baseline.values()) / len(self._baseline) if self._baseline else 0.0

    def _remember(self, localization) -> None:
        """Ein gutes gemeinsames Ergebnis in die gemerkte Pose uebernehmen."""
        from tagloc.geometry import (
            average_poses,
            rotation_distance_rad,
            translation_distance_m,
        )

        self._bad_frames = 0
        pose = localization.pose_world_cam
        if self._poses:
            current = average_poses(list(self._poses))
            if (
                translation_distance_m(current, pose) > self.RESET_TRANSLATION_M
                or math.degrees(rotation_distance_rad(current, pose)) > self.RESET_ROTATION_DEG
            ):
                self.clear()
        if not self._poses:
            self._source = "measured"
        self._poses.append(pose)
        for tag_id, error in localization.tag_errors_px.items():
            previous = self._baseline.get(tag_id)
            self._baseline[tag_id] = (
                error
                if previous is None
                else (1 - self.BASELINE_ALPHA) * previous + self.BASELINE_ALPHA * error
            )
        self._maybe_save()


def camera_pose_from_payload(payload_json: str):
    """`(Pose, Eckenfehler je Welttag)` der Kamera aus einem Job-Ergebnis, oder `None`.

    Nur wenn die Pose im Job aus mindestens zwei Welttags gemeinsam geschaetzt
    wurde (`cameraLocalization.solver == "joint"`): Aus einem einzelnen Tag
    waere sie um Zentimeter ungenau.
    """
    from tagloc.geometry import pose_from_dict

    try:
        summary = json.loads(payload_json).get("cameraLocalization") or {}
        if summary.get("solver") != "joint" or not summary.get("poseInWorld"):
            return None
        pose = pose_from_dict(summary["poseInWorld"])
        errors = {
            int(tag_id): float(value)
            for tag_id, value in (summary.get("tagErrorsPx") or {}).items()
            if value is not None
        }
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    return pose, errors


def _worst_tag(localization) -> str:
    """Der Welttag, der am wenigsten zur gemeinsamen Pose passt -- der
    Kandidat zum Nachmessen."""
    errors = getattr(localization, "tag_errors_px", None) or {}
    if not errors:
        return ""
    tag_id = max(errors, key=errors.get)
    return f" -- am schlechtesten passt Welt-Tag {tag_id} ({errors[tag_id]:.1f} px), nachmessen?"


# --- Ablauf -------------------------------------------------------------------


@dataclass(frozen=True)
class CellWatchPorts:
    """Was die Ueberwachung vom Server braucht."""

    #: Wertet das neueste Kamerabild aus: `(Sichtungen, "")` fuer ein gueltiges
    #: Bild, `(None, Grund)` fuer "keine Aussage", `(None, "")` = kein neues Bild.
    #: Optional als drittes Element die IDs aller erkannten Tags (Welt- und
    #: Modul-Tags) -- auch bei "keine Aussage", fuer `visibleTagIds`.
    observe: Callable[[CellWatchOptions], Awaitable[tuple]]
    #: Grund, warum die Kamera gerade anderweitig gebraucht wird, sonst `""`.
    busy_reason: Callable[[], str]
    #: Payload eines Jobs der letzten Zeit, `None` wenn unbekannt.
    result_for_job: Callable[[str], "str | None"]
    #: Koordinatensystem der Tag-Map ("world").
    world_frame: Callable[[], str]
    #: Schreibt `CellWatchStatus`.
    publish: Callable[[str], Awaitable[None]]
    monotonic: Callable[[], float] = time.monotonic
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    #: Nimmt die Kamerapose eines Referenz-Scans entgegen: `(Pose, Eckenfehler)`.
    adopt_pose: Callable[[Any, dict], Any] | None = None
    #: Woher die gemerkte Kamerapose stammt, fuer den Status.
    pose_info: Callable[[], dict] | None = None


class CellWatch:
    """Start, Stopp, Takt und Status um den `CellWatchTracker`."""

    def __init__(self, ports: CellWatchPorts) -> None:
        self._ports = ports
        self._state = OFF
        self._message = ""
        self._options = CellWatchOptions()
        self._tracker: CellWatchTracker | None = None
        self._reference_job_id: str | None = None
        self._reference_id: str | None = None
        self._reference_source = ""
        self._counter = 0
        self._last_check: datetime | None = None
        #: Alle im zuletzt geprueften Bild erkannten Tags; `None` vor dem ersten.
        self._visible: list[int] | None = None
        self._task: asyncio.Task | None = None

    @property
    def active(self) -> bool:
        return self._state not in (OFF, FAILED)

    async def start(self, options_json: str, *, run_loop: bool = True) -> VisionErrorCode:
        """Scharf schalten, oder mit neuer Referenz neu scharf schalten."""
        try:
            job_id, options = parse_start(options_json)
        except ValueError as error:
            _log.warning("StartCellWatch abgelehnt: %s", error)
            return VisionErrorCode.INVALID_ARGUMENT
        reference = None
        if job_id is not None:
            payload = self._ports.result_for_job(job_id)
            if payload is None:
                _log.warning("StartCellWatch abgelehnt: Job %s unbekannt", job_id)
                return VisionErrorCode.INVALID_ARGUMENT
            try:
                reference = reference_from_payload(payload, self._ports.world_frame())
            except (ValueError, KeyError, TypeError) as error:
                _log.warning("StartCellWatch abgelehnt: %s", error)
                return VisionErrorCode.INVALID_STATE
            # Der Scan hat die Kamera schon verortet: Mit seiner Pose genuegt
            # danach ein Welttag, auch gleich nach dem Start.
            scan_pose = camera_pose_from_payload(payload)
            if scan_pose is not None and self._ports.adopt_pose is not None:
                self._ports.adopt_pose(*scan_pose)

        now = self._ports.monotonic()
        previous = self._tracker if self.active else None
        # Dieselbe Referenz wie bisher -- typisch, wenn das Frontend nur eine
        # Zeit oder Schwelle aendert und mit demselben Job neu scharf schaltet.
        same_reference = (
            previous is not None
            and job_id is not None
            and job_id == self._reference_job_id
            and self._reference_source == "job"
        )
        self._options = options
        self._reference_job_id = job_id
        if not self.active:
            self._visible = None  # nach einem Stopp nichts Altes zeigen
        if reference is not None:
            if same_reference:
                # Gleiche Referenz-ID: es ist dieselbe Referenz, nur neu eingestellt.
                self._tracker = CellWatchTracker(options, reference, now)
            else:
                self._set_reference(reference, "job", now)
        else:
            # "Ignorieren": was gerade stabil zu sehen ist, wird die Referenz.
            # Laeuft die Ueberwachung schon, reicht ihre Beobachtung meist.
            observed = previous.stable_observation(now) if previous is not None else None
            if observed is not None:
                self._set_reference(observed, "observation", now)
            else:
                self._tracker = CellWatchTracker(options, {}, now)
                self._reference_id = None
                self._reference_source = "observation"
        if previous is not None:
            self._tracker.carry_from(previous, same_reference=same_reference)
        self._state = WATCHING
        self._message = "" if self._reference_id else "Referenz wird aufgenommen"
        _log.info(
            "Zellen-Ueberwachung scharf (%s)",
            f"Referenz Job {job_id}" if job_id else "Referenz aus der Beobachtung",
        )
        if run_loop and (self._task is None or self._task.done()):
            self._task = asyncio.ensure_future(self._loop())
        await self._publish()
        return VisionErrorCode.OK

    async def stop(self) -> VisionErrorCode:
        if not self.active:
            return VisionErrorCode.INVALID_STATE
        await self._halt()
        self._state = OFF
        self._message = "gestoppt"
        await self._publish()
        return VisionErrorCode.OK

    async def aclose(self) -> None:
        await self._halt()

    async def publish_idle(self) -> None:
        await self._publish()

    async def _halt(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def _set_reference(self, reference: dict, source: str, now: float) -> None:
        self._counter += 1
        self._tracker = CellWatchTracker(self._options, reference, now)
        self._reference_id = f"cw-{self._counter:06d}"
        self._reference_source = source

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # nie still sterben
                _log.exception("Zellen-Ueberwachung fehlgeschlagen")
                self._state = FAILED
                self._message = f"Fehler: {error}"
                await self._publish()
                return
            await asyncio.sleep(self._options.interval_s)

    async def tick(self) -> None:
        """Ein Auswerteschritt. Oeffentlich fuer Tests."""
        if not self.active:
            return
        reason = self._ports.busy_reason()
        if reason:
            await self._pause(reason)
            return
        result = await self._ports.observe(self._options)
        sightings, reason = result[0], result[1]
        visible_changed = False
        if len(result) > 2 and result[2] is not None:
            visible = sorted(int(tag_id) for tag_id in result[2])
            visible_changed = visible != self._visible
            self._visible = visible
        if sightings is None:
            if reason:
                await self._pause(reason, force=visible_changed)
            return
        now = self._ports.monotonic()
        wall = self._ports.now()
        self._last_check = wall
        tracker = self._tracker
        tracker.update(now, wall, sightings)
        if self._reference_id is None:
            observed = tracker.stable_observation(now)
            if observed is not None:
                self._set_reference(observed, "observation", now)
                _log.info("Zellen-Ueberwachung: Referenz aus %d Tags aufgenommen", len(observed))
        if self._reference_id is None:
            self._state, self._message = WATCHING, "Referenz wird aufgenommen"
        elif self._tracker.changes:
            self._state, self._message = CHANGED, _describe(self._tracker.changes)
        else:
            self._state, self._message = WATCHING, _unreliable_hint(tracker.visibility(now))
        await self._publish()

    async def _pause(self, reason: str, *, force: bool = False) -> None:
        # Nur bei einem Wechsel schreiben -- oder wenn sich geaendert hat,
        # welche Tags zu sehen sind: gerade beim Pausieren will der Bediener
        # sehen, welcher Welttag fehlt.
        if force or self._state != PAUSED or self._message != reason:
            self._state, self._message = PAUSED, reason
            await self._publish()

    def status_json(self) -> str:
        tracker = self._tracker
        referenced = tracker is not None and self._reference_id is not None
        return json.dumps(
            {
                "schema": STATUS_SCHEMA,
                "state": self._state,
                "message": self._message,
                "referenceId": self._reference_id,
                "referenceJobId": self._reference_job_id,
                "referenceSource": self._reference_source or None,
                "lastCheckAt": self._last_check.isoformat(timespec="seconds")
                if self._last_check
                else None,
                "watchedTagIds": sorted(tracker.reference) if referenced else [],
                "visibleTagIds": self._visible if self.active else None,
                "cameraPose": self._ports.pose_info() if self._ports.pose_info else None,
                "tagVisibility": tracker.visibility(self._ports.monotonic())
                if tracker is not None and self.active
                else [],
                "options": self._options.to_dict(),
                "changes": tracker.changes if referenced and self.active else [],
                # Noch nicht bestaetigt -- fuer die sofortige Anzeige.
                "pending": tracker.pending(self._ports.monotonic())
                if referenced and self.active
                else [],
            },
            allow_nan=False,
            default=float,
        )

    async def _publish(self) -> None:
        try:
            await self._ports.publish(self.status_json())
        except Exception:
            _log.exception("CellWatchStatus konnte nicht geschrieben werden")


def _unreliable_hint(visibility: list[dict]) -> str:
    """Hinweis auf Tags an der Erkennungsgrenze -- kein Alarm, aber der
    Grund, warum "nicht im Bild" flackert."""
    weak = [entry for entry in visibility if entry["unreliable"]]
    if not weak:
        return ""
    parts = ", ".join(
        f"Tag {entry['tagId']} ({round(100 * (entry['seenRatio'] or 0))} %)" for entry in weak
    )
    return f"unsicher erkannt: {parts} der Bilder -- Tag groesser drucken, umsetzen oder Licht pruefen"


def _describe(changes: list[dict]) -> str:
    words = {MOVED: "verschoben", APPEARED: "neu", DISAPPEARED: "fehlt"}
    return ", ".join(
        f"{change['moduleId'] or 'Tag ' + str(change['tagId'])} {words[change['kind']]}"
        for change in changes
    )

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
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

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

    def to_dict(self) -> dict:
        return {
            "movedThresholdM": self.moved_threshold_m,
            "rotatedThresholdDeg": self.rotated_threshold_deg,
            "persistS": self.persist_s,
            "missingPersistS": self.missing_persist_s,
            "intervalS": self.interval_s,
            "maxCameraErrorPx": self.max_camera_error_px,
        }


@dataclass(frozen=True)
class TagSighting:
    """Ein Tag in einem gueltigen Bild: die Modulpose, die er misst."""

    position: tuple[float, float, float]
    #: Drehung um die Hochachse des Welt-KS, Grad.
    yaw_deg: float
    ambiguous: bool = False
    module_id: str = ""


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

    def update(self, t: float, wall: datetime, sightings: dict[int, TagSighting]) -> None:
        """Ein gueltiges Bild. Ungueltige Bilder kommen gar nicht erst hierher."""
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
    """Median der Position; Median der Drehung nur ueber eindeutige Sichtungen."""
    position = tuple(float(v) for v in np.median([s.position for s in seen], axis=0))
    yaws = [s.yaw_deg for s in seen if not s.ambiguous]
    if not yaws:
        return position, None, False
    # Um den ersten Wert herum mitteln, damit -179/+179 nicht 0 ergibt.
    anchor = yaws[0]
    yaw = anchor + float(np.median([_wrap_deg(y - anchor) for y in yaws]))
    return position, _wrap_deg(yaw), True


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
    ):
        value = data.get(key, getattr(defaults, name))
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0:
            raise ValueError(f"{key} muss eine positive Zahl sein.")
        values[name] = float(value)
    return job_id or None, CellWatchOptions(**values)


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
                    has_yaw=not attributes.get("ambiguous", False),
                )
    return reference


def sightings_from_locations(locations, localization, options: CellWatchOptions):
    """Aus `locate_modules` (unzusammengefasst) die Sichtungen eines Bilds.

    `(None, Grund)`, wenn das Bild keine Aussage erlaubt: keine gemeinsam
    geloeste Kamerapose oder ein zu grosser Reprojektionsfehler.
    """
    from tagloc.localize import SOLVER_JOINT

    if localization is None:
        return None, "kein Welttag im Bild -- keine Verortung"
    if localization.solver != SOLVER_JOINT:
        return None, "weniger als zwei Welttags im Bild -- Kamerapose zu ungenau"
    error = localization.reprojection_error_px
    if not math.isfinite(error) or error > options.max_camera_error_px:
        return None, f"Kamerapose unplausibel (RMS {error:.1f} px){_worst_tag(localization)}"
    sightings = {}
    for location in locations:
        sightings[int(location.tag_id)] = TagSighting(
            position=tuple(float(v) for v in np.asarray(location.pose)[:3, 3]),
            yaw_deg=yaw_deg(location.pose),
            ambiguous=bool(location.ambiguous),
            module_id=location.module_id,
        )
    return sightings, ""


def _worst_tag(localization) -> str:
    """Der Welttag, der am wenigsten zur gemeinsamen Pose passt -- der
    Kandidat zum Nachmessen."""
    errors = getattr(localization, "tag_errors_px", None) or {}
    if not errors:
        return ""
    tag_id = max(errors, key=errors.get)
    return f" -- am schlechtesten passt Welttag {tag_id} ({errors[tag_id]:.1f} px), nachmessen?"


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

        now = self._ports.monotonic()
        previous = self._tracker if self.active else None
        self._options = options
        self._reference_job_id = job_id
        if not self.active:
            self._visible = None  # nach einem Stopp nichts Altes zeigen
        if reference is not None:
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
                "tagVisibility": tracker.visibility(self._ports.monotonic())
                if tracker is not None and self.active
                else [],
                "options": self._options.to_dict(),
                "changes": tracker.changes if referenced and self.active else [],
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

"""Kamerazustand als `DeviceHealth` nach OPC 40100-2 (AMCM).

Der Watchdog in `camera.py` weiss, ob die Kamera Bilder liefert, haengt oder
gerade neu geoeffnet wird. Dieses Modul uebersetzt das in DI's
`DeviceHealthEnumeration` und schreibt es in die Zustandsknoten der
Anlagensicht (`asset_model.py`). Damit sieht ein generischer OPC-UA-Client
ohne Kenntnis dieses Repos, ob die Kamera arbeitet.

Warum ein eigenes Modul und ein eigener Task, statt das an den Livestream zu
haengen wie den Kalibrier-Fortschritt:

* Der Livestream ist eine Debughilfe und kann fehlen (`camera_stream` nicht
  konfiguriert), waehrend die Erkennungsquelle sehr wohl eine Kamera haelt.
  Eine Instandhaltungssicht darf daran nicht haengen.
* Die Kadenzen passen nicht zusammen: der Stream schreibt bewusst jeden Tick,
  dieser Knoten nur bei Zustandswechsel.

Zur Aehnlichkeit mit der entfallenen Altlast A3 (`RaspiDevice/Counter`, ein
1-Hz-Zaehler): A3 war ein Wert *ohne Quelle*, der nur bewies, dass irgendeine
Schleife lief. Hier gibt es eine Quelle, und im Ruhezustand wird gar nichts
geschrieben -- nach dem ersten Frame bleibt der Knoten still, bis sich der
Zustand wirklich aendert.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from asyncua import Server, ua
from asyncua.common.node import Node

from .camera import CameraStatus, SharedCamera
from .nodeset_ids import DeviceHealth
from .profiles import CameraStreamConfig

_log = logging.getLogger(__name__)

#: Zeitlimit fuer das letzte FAILURE vor dem Prozessabbruch. Wir sind auf dem
#: Weg nach draussen -- ein haengender Schreibvorgang darf den Abbruch nicht
#: verhindern, sonst bleibt genau der Prozess stehen, den systemd neu starten
#: soll.
GIVE_UP_WRITE_TIMEOUT_S = 1.0

#: Schweregrad je Zustand (OPC UA: 1-1000). FAILURE ist der Ausfall, OFF_SPEC
#: die gemeldete Abweichung, CHECK_FUNCTION der angekuendigte Eingriff --
#: absteigend dringlich, damit ein HMI sie ohne Kenntnis von NE 107 sortieren
#: kann.
ALARM_SEVERITY: dict[DeviceHealth, int] = {
    DeviceHealth.FAILURE: 900,
    DeviceHealth.OFF_SPEC: 500,
    DeviceHealth.CHECK_FUNCTION: 300,
}

#: Klartext je Zustand fuers `Message`-Feld des Alarms.
ALARM_MESSAGE: dict[DeviceHealth, str] = {
    DeviceHealth.FAILURE: "Kamera liefert kein Bild",
    DeviceHealth.OFF_SPEC: "Kamerabild ist veraltet",
    DeviceHealth.CHECK_FUNCTION: "Kamera wird geoeffnet oder neu gestartet",
}


def device_health(status: CameraStatus, *, stale_frame_s: float) -> DeviceHealth:
    """Bildet den Kamerazustand auf DI's `DeviceHealthEnumeration` ab.

    Die Reihenfolge der Abfragen ist die Aussage: erst das Endgueltige, dann
    das Voruebergehende, zuletzt der Normalfall. Begruendung je Zeile aus
    NAMUR NE 107 bzw. OPC 10000-100:

    * `gave_up` -- das Reopen-Budget ist erschoepft, gleich beendet sich der
      Prozess. Kein gueltiges Ausgangssignal, und niemand arbeitet daran:
      FAILURE.
    * nicht `running` -- die Kamera wurde nie geoeffnet oder die Quelle ist
      nicht aufgegangen. Derselbe Fall, andere Ursache.
    * kein Handle -- ein `_reopen` laeuft. NE 107 nennt CHECK_FUNCTION
      woertlich "output signal temporarily invalid due to on-going work on
      the device".
    * noch kein Frame -- Warmup. Kein Fehler, nur noch kein Bild; deshalb ist
      CHECK_FUNCTION auch der Startwert der Knoten.
    * Frame zu alt -- der Watchdog hat einen Haenger erkannt, eskaliert aber
      noch nicht. Die Eigendiagnose meldet eine Abweichung: OFF_SPEC.
      FAILURE waere vertretbar, aber Haenger heilen sich nachweislich selbst
      (siehe `test_a_frame_between_hangs_resets_the_reopen_budget`); mit
      OFF_SPEC kann ein HMI "hakt gerade" von "tot" unterscheiden und bekommt
      rund `frame_timeout_s` Vorwarnzeit. Wer das anders will, aendert diese
      eine Zeile.

    `MAINTENANCE_REQUIRED` wird nie geliefert: dafuer braeuchte es einen
    Verschleisszaehler, und `RemainingLifeTime` wird bewusst nicht angelegt.
    """
    if status.gave_up:
        return DeviceHealth.FAILURE
    if not status.running:
        return DeviceHealth.FAILURE
    if not status.has_handle:
        return DeviceHealth.CHECK_FUNCTION
    if status.frame_age_s is None:
        return DeviceHealth.CHECK_FUNCTION
    if status.frame_age_s > stale_frame_s:
        return DeviceHealth.OFF_SPEC
    return DeviceHealth.NORMAL


async def write_device_health(nodes: Sequence[Node], value: DeviceHealth) -> None:
    """Schreibt denselben Wert in alle Zustandsknoten. Wirft nie.

    Ein nicht beschreibbarer Knoten ist ein Schoenheitsfehler in einer
    Zusatzsicht -- er darf weder den Job-Pfad noch die uebrigen Knoten
    mitreissen.
    """
    for node in nodes:
        try:
            await node.write_value(ua.Variant(int(value), ua.VariantType.Int32))
        except Exception:
            _log.exception(
                "DeviceHealth an %s nicht schreibbar", node.nodeid.to_string()
            )


class HealthAlarms:
    """Feuert je Zustand den passenden DI-Alarm und loescht den vorherigen.

    Der zweite Teil von DIs `IDeviceHealthType`. Die Zustandsvariable sagt,
    *was* gerade gilt; der Alarm meldet den Uebergang -- mit Zeitstempel,
    Quelle, Schweregrad und Meldungstext, ohne dass ein Client abfragen muss.

    Es ist immer hoechstens **einer** aktiv, denn der Zustand ist einer von
    fuenf. `NORMAL` feuert keinen Alarm, es loescht nur den vorherigen.

    Emittiert wird vom uebergebenen `emitter` -- in der Praxis `VisionMachine`,
    weil Clients ohnehin genau diesen Knoten abonnieren (siehe
    `doc/vision-server-interface.md` 7.2). `SourceNode` nennt trotzdem die
    Komponente, um die es geht.

    Wirft nie: ein fehlgeschlagener Alarm darf weder die Zustandsvariable noch
    den Job-Pfad mitreissen.
    """

    def __init__(
        self,
        server: Server,
        emitter: Node,
        alarms: Mapping[DeviceHealth, Node],
        source: Node | None = None,
    ) -> None:
        self._server = server
        self._emitter = emitter
        self._alarms = dict(alarms)
        self._source = source
        self._generators: dict[DeviceHealth, Any] = {}
        self._active: DeviceHealth | None = None

    async def _generator(self, value: DeviceHealth):
        """Event-Generator je Alarmtyp, beim ersten Gebrauch gebaut."""
        if value not in self._generators:
            node = self._alarms[value]
            etype = await node.read_type_definition()
            self._generators[value] = await self._server.get_event_generator(
                self._server.get_node(etype), self._emitter
            )
        return self._generators[value]

    async def _fire(self, value: DeviceHealth, *, active: bool, reason: str) -> None:
        if value not in self._alarms:
            return
        generator = await self._generator(value)
        event = generator.event
        event.Severity = ALARM_SEVERITY.get(value, 500)
        event.Retain = active
        event.ActiveState = ua.LocalizedText("Active" if active else "Inactive")
        event.Message = ua.LocalizedText(
            f"{ALARM_MESSAGE.get(value, value.name)} ({reason})"
            if active
            else f"{ALARM_MESSAGE.get(value, value.name)} behoben"
        )
        if self._source is not None:
            event.SourceNode = self._source.nodeid
        await generator.trigger()

    async def set(self, value: DeviceHealth, reason: str) -> None:
        """Uebernimmt den neuen Zustand: alten Alarm loeschen, neuen feuern."""
        if value == self._active:
            return
        try:
            if self._active is not None:
                await self._fire(self._active, active=False, reason=reason)
            if value is not DeviceHealth.NORMAL:
                await self._fire(value, active=True, reason=reason)
            self._active = value
        except Exception:
            _log.exception("Zustandsalarm fuer %s nicht ausloesbar", value.name)


class CameraHealthPublisher:
    """Spiegelt den Zustand einer `SharedCamera` nach `Health/DeviceHealth`.

    Schreibt nur bei Zustandswechsel: im Normalbetrieb genau einmal, wenn der
    erste Frame da ist, und danach Stille.
    """

    def __init__(
        self,
        camera: SharedCamera,
        nodes: Sequence[Node],
        config: CameraStreamConfig,
        alarms: HealthAlarms | None = None,
    ) -> None:
        self._camera = camera
        self._nodes = tuple(nodes)
        self._config = config
        #: Der normkonforme Ereignisweg; `None`, wenn keine Alarme angelegt
        #: werden konnten -- dann bleibt die Variable der einzige Meldeweg.
        self._alarms = alarms
        #: `None` heisst "noch nichts geschrieben" -- der erste Durchlauf
        #: schreibt deshalb immer, auch wenn er CHECK_FUNCTION ergibt und die
        #: Knoten schon damit angelegt wurden. Ein Abonnent, der spaeter
        #: dazukommt, soll denselben Wert bekommen wie einer von Anfang an.
        self._health: DeviceHealth | None = None
        self._task: asyncio.Task | None = None

    @property
    def health(self) -> DeviceHealth | None:
        """Der zuletzt geschriebene Wert, `None` vor dem ersten Durchlauf."""
        return self._health

    def start(self) -> None:
        self._task = asyncio.create_task(self._publish_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def publish_now(self) -> None:
        """Ein Durchlauf ausserhalb der Kadenz. Wirft nie."""
        try:
            status = self._camera.status(asyncio.get_running_loop().time())
            value = device_health(status, stale_frame_s=self._config.stale_frame_s)
            if value == self._health:
                return
            _log.info(
                "Kamerazustand %s -> %s (%s)",
                "-" if self._health is None else self._health.name,
                value.name,
                status.last_outcome,
            )
            await write_device_health(self._nodes, value)
            if self._alarms is not None:
                await self._alarms.set(value, status.last_outcome)
            self._health = value
        except Exception:
            _log.exception("Kamerazustand konnte nicht veroeffentlicht werden")

    async def _publish_loop(self) -> None:
        while True:
            await self.publish_now()
            await asyncio.sleep(self._config.health_interval_s)

    def give_up_handler(
        self, then: Callable[[], None]
    ) -> Callable[[], Awaitable[None]]:
        """Baut den `on_give_up`-Handler der Kamera: FAILURE, dann `then`.

        Noetig, weil der Standardhandler den Prozess sofort mit `os._exit`
        reisst -- ein pollender Task saehe `gave_up` nie. Das Schreiben laeuft
        deshalb mit Zeitlimit, und `then` wird in jedem Fall aufgerufen.
        """

        async def handler() -> None:
            try:
                await asyncio.wait_for(
                    write_device_health(self._nodes, DeviceHealth.FAILURE),
                    timeout=GIVE_UP_WRITE_TIMEOUT_S,
                )
                self._health = DeviceHealth.FAILURE
            except Exception:
                _log.exception("Letztes FAILURE konnte nicht geschrieben werden")
            if self._alarms is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(
                        self._alarms.set(DeviceHealth.FAILURE, "aufgegeben"),
                        timeout=GIVE_UP_WRITE_TIMEOUT_S,
                    )
            then()

        return handler


__all__ = [
    "CameraHealthPublisher",
    "HealthAlarms",
    "device_health",
    "write_device_health",
]

"""Zusammenbau des Vision-Systems, als Einbau oder als eigener Prozess."""

import asyncio
import json
import logging
from typing import Any

from asyncua import Node, Server, ua, uamethod

from ..address_space import attach_vision_system, configure_server
from ..aio import stop_event_on_signals
from ..asset_model import attach_asset_model
from ..calibration_session import CalibrationSession
from ..config import VisionServerConfig
from ..detection import build_detection_sources
from ..errors import VisionErrorCode
from ..events import create_event_generators
from ..job import JobRunner
from ..result_management import ResultStore
from ..state_machine import VisionStateMachines
from ..vision_program import install_vision_program
from .calibration import (
    CALIBRATION_PROGRESS_INTERVAL_S,
    _build_calibration_session,
    _calibration_info_payload,  # noqa: F401  (Tests)
    _write_calibration_info,
)
from .cell_watch import _install_cell_watch
from .hand_eye import _install_hand_eye_session
from .health import _start_camera_health
from .layer2 import _install_layer2_run
from .livestream import (
    _build_annotator,  # noqa: F401  (Tests)
    _start_camera_stream,
    _start_mjpeg_server,
)
from .machine import VisionMachine, _watch_loop_lag
from .measure_board import _install_measure_board
from .measure_tags import _Activity, _install_measure_tags
from .save_raw_image import _install_save_raw_image
from .save_use_case_image import (
    USE_CASE_FRESH_FRAME_TIMEOUT_S,  # noqa: F401  (frueher hier definiert)
    USE_CASE_MIN_FRAME_AGE_S,  # noqa: F401
    USE_CASE_POLL_INTERVAL_S,  # noqa: F401
    _install_save_use_case_image,
)
from .sources import _camera_owner, _open_source
from .tag_reference import _install_tag_reference

_log = logging.getLogger(__name__)


def _write_tag_map_file(path, text: str) -> None:
    """Schreibt die Karte, Verzeichnis wird bei Bedarf angelegt."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")








async def install_vision_machine(server: Server, config: VisionServerConfig) -> VisionMachine:
    """Baut das Vision-System in einen initialisierten Server ein.

    Muss nach `server.init()` und vor `server.start()` laufen. Der Server darf
    daneben beliebige eigene Knoten haben — Endpoint, ApplicationURI und
    ServerName bleiben unberuehrt.
    """
    space = await attach_vision_system(server, config)
    # Part 2 vor den Automaten: es haengt an nichts und soll auch dann stehen,
    # wenn der Job-Pfad spaeter nicht in Operational kommt -- gerade dann ist
    # die Frage "welche Kamera, welcher Zustand" interessant.
    assets = (
        await attach_asset_model(space, config.assets) if config.assets is not None else None
    )
    states = await VisionStateMachines.bind(space)
    events = await create_event_generators(space)
    results = await ResultStore.create(space)
    sources = build_detection_sources(config)
    jobs = JobRunner(config, states, events, results, sources)
    #: Erst spaeter auf ihren echten Wert gesetzt (siehe unten, nach dem
    #: Oeffnen der Quellen) -- die Closures hier greifen erst beim
    #: tatsaechlichen Methodenaufruf darauf zu, also lange danach. Python loest
    #: Namen in Closures spaet auf, das ist hier bewusst genutzt.
    calibration_session: CalibrationSession | None = None
    annotator: Any = None
    layer2_run: Any = None
    hand_eye_session: Any = None

    @uamethod
    async def start_single_job(parent, meas_id, part_id, recipe_id, product_id, parameters):
        """OPC-UA-Einstiegspunkt fuer 1:StartSingleJob.

        Muss `async` sein: synchrone Handler laufen bei asyncua in einem
        ThreadPoolExecutor ohne laufenden Event-Loop, dort scheitert das
        Starten des Job-Tasks. `jobs.start_single_job` bleibt synchron und
        wird ohne `await`-Punkt aufgerufen — die Zulassung bleibt atomar.

        Gibt JobId und Error als String bzw. Int32 zurueck, obwohl das Nodeset
        `JobIdDataType` deklariert — asyncua validiert Methodenargumente nicht,
        und ein Client koennte das ExtensionObject nicht dekodieren. Die
        Rueckgabe muss ein Tupel sein; eine Liste wuerde asyncua als einen
        einzigen Variant verpacken.
        """
        if calibration_session is not None and calibration_session.busy:
            _log.warning("StartSingleJob waehrend laufender Kalibrierung/Auswertung abgelehnt")
            return (
                ua.Variant("", ua.VariantType.String),
                ua.Variant(int(VisionErrorCode.BUSY), ua.VariantType.Int32),
            )
        job_id, error = jobs.start_single_job(
            meas_id, part_id, recipe_id, product_id, parameters
        )
        return (
            ua.Variant(job_id, ua.VariantType.String),
            ua.Variant(int(error), ua.VariantType.Int32),
        )

    server.link_method(space.start_single_job, start_single_job)

    @uamethod
    async def stop_job(parent, cause, cause_description):
        """OPC-UA-Einstiegspunkt fuer 1:Stop.

        `Cause`/`CauseDescription` schickt das Frontend fire-and-forget immer
        als 0/"" und wertet sie nicht aus; wir werten sie ebenfalls nicht aus.
        Muss wie `start_single_job` `async` sein.
        """
        # Erst den Lauf, dann den Job: sonst wertete der Lauf den
        # abgebrochenen Job noch aus und veroeffentlichte ein neues Ziel.
        if layer2_run is not None:
            await layer2_run.abort("Stop")
        error = await jobs.stop()
        return (ua.Variant(int(error), ua.VariantType.Int32),)

    server.link_method(space.stop, stop_job)

    @uamethod
    async def start_continuous(parent, meas_id, part_id, recipe_id, product_id, parameters):
        """1:StartContinuous -- Dauerbetrieb bis Stop oder Abort."""
        if calibration_session is not None and calibration_session.busy:
            _log.warning("StartContinuous waehrend laufender Kalibrierung/Auswertung abgelehnt")
            return (
                ua.Variant("", ua.VariantType.String),
                ua.Variant(int(VisionErrorCode.BUSY), ua.VariantType.Int32),
            )
        job_id, error = jobs.start_continuous(
            meas_id, part_id, recipe_id, product_id, parameters
        )
        return (
            ua.Variant(job_id, ua.VariantType.String),
            ua.Variant(int(error), ua.VariantType.Int32),
        )

    server.link_method(space.start_continuous, start_continuous)

    @uamethod
    async def abort_job(parent, cause, cause_description):
        """1:Abort -- wie Stop, aber ueber den Abort-Uebergang.

        Fuer uns ist der Unterschied nur der Zustandsuebergang und die Meldung
        im Ergebnis: es gibt keinen Zwischenstand, den ein Abbruch verwerfen
        koennte. Beide melden `CANCELLED`.
        """
        error = await jobs.stop(abort=True)
        return (ua.Variant(int(error), ua.VariantType.Int32),)

    server.link_method(space.abort, abort_job)

    @uamethod
    async def halt_system(parent, cause, cause_description):
        """1:Halt -- laufenden Job beenden, dann keine Jobs mehr annehmen.

        Aus Halted fuehrt nur `Reset` zurueck. Das ist der Sinn: Halt ist die
        Bremse fuer den Bediener, nicht ein weiterer Betriebszustand.
        """
        error = await jobs.stop()
        if error != VisionErrorCode.OK:
            return (ua.Variant(int(error), ua.VariantType.Int32),)
        try:
            await states.halt()
        except Exception:
            _log.exception("Halt fehlgeschlagen")
            return (ua.Variant(int(VisionErrorCode.INTERNAL), ua.VariantType.Int32),)
        return (ua.Variant(int(VisionErrorCode.OK), ua.VariantType.Int32),)

    server.link_method(space.halt, halt_system)

    @uamethod
    async def reset_system(parent, cause, cause_description):
        """1:Reset -- zurueck in den betriebsbereiten Zustand.

        Ueber Preoperational, weil das Nodeset keinen Uebergang
        Halted -> Operational kennt; `enter_operational` faehrt genau diesen
        konformen Weg.
        """
        try:
            await states.enter_operational()
        except Exception:
            _log.exception("Reset fehlgeschlagen")
            return (ua.Variant(int(VisionErrorCode.INTERNAL), ua.VariantType.Int32),)
        return (ua.Variant(int(VisionErrorCode.OK), ua.VariantType.Int32),)

    server.link_method(space.reset, reset_system)

    # Erst oeffnen, dann Operational: `Ready` soll "Hardware bereit" heissen.
    # Die Methode bleibt verlinkt, sonst antwortet der Server BadNothingToDo.
    opened = {profile: await _open_source(source) for profile, source in sources.items()}
    if all(opened.values()):
        await states.enter_operational()
    else:
        _log.error(
            "Vision-System '%s' bleibt in Preoperational, Quelle nicht bereit",
            config.vision_system_name,
        )

    calibration_session = _build_calibration_session(sources, opened, config)
    camera_stream, annotator = _start_camera_stream(space, sources, opened)
    if annotator is not None and hasattr(annotator, "set_busy"):
        # Wie die Zellen-Ueberwachung: waehrend Job oder Kalibrierung keine
        # eigene Overlay-Erkennung, der Job soll die CPU fuer sich haben.
        annotator.set_busy(
            lambda: jobs.busy
            or (calibration_session is not None and calibration_session.busy)
        )
    camera_health = await _start_camera_health(space, assets, sources, opened)
    camera_http = (
        await _start_mjpeg_server(space, camera_stream)
        if camera_stream is not None
        else None
    )

    apriltag_source = sources.get("apriltag")
    if config.apriltag is not None and opened.get("apriltag"):
        # Was `open()` gerade geladen hat (echte Datei oder Platzhalter) --
        # ohne das waere ActiveCalibrationInfo leer, bis zum ersten
        # StartCalibration.
        await _write_calibration_info(
            space.active_calibration_info,
            getattr(apriltag_source, "_calibration", None),
            config.apriltag.calibration_path,
        )
    if calibration_session is not None:

        async def _apply_live_calibration(calibration: Any) -> None:
            """Bringt Erkennung, Overlay und den Info-Knoten sofort auf den
            neuen Stand -- kein Server-Neustart noetig, siehe
            `AprilTagDetectionSource.apply_calibration`."""
            if apriltag_source is not None and hasattr(apriltag_source, "apply_calibration"):
                apriltag_source.apply_calibration(calibration)
            if annotator is not None and hasattr(annotator, "apply_calibration"):
                annotator.apply_calibration(calibration)
            await _write_calibration_info(
                space.active_calibration_info, calibration, config.apriltag.calibration_path
            )

        calibration_session.set_on_calibrated(_apply_live_calibration)

    #: Kalibriermethoden, die zusaetzlich unter `VisionProgram` aufrufbar
    #: werden -- gefuellt nur, wenn es eine Kalibrier-Session gibt.
    calibration_methods: dict[str, Node] = {}

    if calibration_session is not None and space.calibration_progress is not None:
        #: Praefix der Methoden-NodeIds. Explizit statt der laufenden Nummer,
        #: die `add_method(own_idx, ...)` vergeben wuerde: die verschiebt sich,
        #: sobald jemand davor einen Knoten einfuegt, und das Frontend spricht
        #: die Methoden ueber feste Adressen an.
        method_prefix = config.vision_system_name

        @uamethod
        async def start_calibration(parent):
            """1:StartCalibration -- setzt eine neue Session auf; Aufnahmen
            kommen danach ausschliesslich ueber `CaptureCalibrationSample`.

            Kein Kalibrierdurchlauf gegen einen laufenden Job oder eine
            zweite Session gleichzeitig -- beide teilen sich Kamera und
            Detektor.
            """
            if jobs.busy:
                _log.warning("StartCalibration waehrend laufendem Job abgelehnt")
                return (ua.Variant(int(VisionErrorCode.BUSY), ua.VariantType.Int32),)
            if layer2_run is not None and layer2_run.active:
                _log.warning("StartCalibration waehrend laufendem Layer-2-Lauf abgelehnt")
                return (ua.Variant(int(VisionErrorCode.BUSY), ua.VariantType.Int32),)
            if hand_eye_session is not None and hand_eye_session.active:
                _log.warning("StartCalibration waehrend Hand-Auge-Kalibrierung abgelehnt")
                return (ua.Variant(int(VisionErrorCode.BUSY), ua.VariantType.Int32),)
            if calibration_session.busy:
                # `.busy` statt `.running`: eine Hintergrund-Auswertung aus
                # einem vorherigen `FinishCalibration` liest noch aus dem
                # `_pending_dir` der Session -- ein neuer `start()` waehrend-
                # dessen wuerde den gerade genau darunter wegleeren.
                _log.warning("StartCalibration waehrend laufender Session/Auswertung abgelehnt")
                return (ua.Variant(int(VisionErrorCode.BUSY), ua.VariantType.Int32),)
            if not states.is_ready():
                return (ua.Variant(int(VisionErrorCode.INVALID_STATE), ua.VariantType.Int32),)
            calibration_session.start()
            if annotator is not None:
                annotator.set_calibration_session(calibration_session)
            _log.info("Kalibrier-Session gestartet")
            return (ua.Variant(int(VisionErrorCode.OK), ua.VariantType.Int32),)

        calibration_methods["StartCalibration"] = await space.vision_system.add_method(
            ua.NodeId(f"{method_prefix}.StartCalibration", space.own_idx),
            ua.QualifiedName("StartCalibration", space.own_idx),
            start_calibration,
            [],
            [ua.VariantType.Int32],
        )

        @uamethod
        async def capture_calibration_sample(parent):
            """1:CaptureCalibrationSample -- merkt sich das aktuelle
            Kamerabild, manuell ausgeloest (z. B. per Leertaste im
            Stream-Viewer). Keine Board-Erkennung in diesem Aufruf, die
            passiert erst nach `FinishCalibration` (siehe
            `CalibrationSession.capture`). `Error=OK` heisst nur "Bild
            gespeichert"; `INVALID_STATE` heisst keine Session aktiv oder
            gerade kein Kamera-Frame -- die Session laeuft weiter, ein
            erneuter Versuch ist ok.
            """
            if not calibration_session.running:
                return (ua.Variant(int(VisionErrorCode.INVALID_STATE), ua.VariantType.Int32),)
            stored = await calibration_session.capture()
            error = VisionErrorCode.OK if stored else VisionErrorCode.INVALID_STATE
            return (ua.Variant(int(error), ua.VariantType.Int32),)

        calibration_methods["CaptureCalibrationSample"] = (
            await space.vision_system.add_method(
                ua.NodeId(f"{method_prefix}.CaptureCalibrationSample", space.own_idx),
                ua.QualifiedName("CaptureCalibrationSample", space.own_idx),
                capture_calibration_sample,
                [],
                [ua.VariantType.Int32],
            )
        )

        async def _write_calibration_progress() -> None:
            """Schreibt `CalibrationProgress` direkt aus der Session.

            Sonst schreibt nur die Publish-Schleife des Livestreams diesen
            Knoten (`CameraStreamPublisher._publish_progress`) -- und genau
            die ist waehrend der Auswertung pausiert. Ohne diesen eigenen
            Schreibpfad bliebe der Knoten dann auf dem letzten Stand der
            Aufnahme-Phase stehen, `processing: true` waere nie sichtbar
            (gefunden 2026-09-28).
            """
            try:
                await space.calibration_progress.write_value(
                    json.dumps(calibration_session.progress)
                )
            except Exception:
                _log.exception("Kalibrier-Fortschritt konnte nicht veroeffentlicht werden")

        async def _pause_stream_during_processing() -> None:
            """Waehrend `CalibrationSession._process()` im Hintergrund
            rechnet, den Livestream pausieren, damit der Pi die volle CPU
            fuer die Ecken-Erkennung hat (2026-09-28, auf Vorschlag/mit
            Bestaetigung, nachdem die vorherige Deckenkamera-Auswertung
            wiederholt haengen blieb). Laeuft selbst als Hintergrund-Task --
            `finish_calibration` darf darauf nicht warten, siehe dort.

            `CalibrationProgress` wird in dieser Zeit hier geschrieben statt
            vom (pausierten) Livestream: einmal sofort (`processing: true`),
            danach im Takt `CALIBRATION_PROGRESS_INTERVAL_S` und ein letztes
            Mal mit `result`, bevor der Livestream wieder uebernimmt.
            """
            if camera_stream is not None:
                await camera_stream.stop()
            await _write_calibration_progress()
            while True:
                try:
                    await asyncio.wait_for(
                        calibration_session.wait_for_processing(),
                        timeout=CALIBRATION_PROGRESS_INTERVAL_S,
                    )
                    break
                except TimeoutError:
                    await _write_calibration_progress()
            await _write_calibration_progress()
            if camera_stream is not None:
                camera_stream.start()

        @uamethod
        async def finish_calibration(parent):
            """1:FinishCalibration -- beendet die Aufnahme-Phase und stoesst
            die Auswertung im Hintergrund an; kehrt sofort zurueck, OHNE auf
            das Ergebnis zu warten (`CalibrationSession.finish()`, siehe
            dort -- eine synchrone Auswertung mehrerer
            Vollaufloesungs-Aufnahmen haette auf der Deckenkamera dasselbe
            Timeout-Risiko wie eine einzelne haengende `detect_board`-
            Aufnahme, live 2026-09-28 gefunden). Das Frontend beobachtet das
            Ergebnis ausschliesslich ueber `CalibrationProgress`
            (`processing` -> `result`).

            `Summary` ist immer gueltiges JSON, auch im Fehlerfall (dann mit
            `message` statt `samples`/... ), damit das Frontend nicht
            zwischen Erfolgs- und Fehlerform unterscheiden muss.
            """
            if not calibration_session.running:
                return (
                    ua.Variant('{"message": "keine Session aktiv"}', ua.VariantType.String),
                    ua.Variant(int(VisionErrorCode.INVALID_STATE), ua.VariantType.Int32),
                )
            error, summary = await calibration_session.finish()
            # `annotator` bleibt in beiden Faellen bewusst an der Session
            # haengen (kein `set_calibration_session(None)` hier): der
            # Livestream veroeffentlicht so weiter `progress` samt `result`,
            # statt in den session-losen Zustand `{"running": false}`
            # zurueckzufallen -- das gilt auch fuer die sofortige Ablehnung
            # mit zu wenigen Aufnahmen, deren `result` sonst nie in
            # `CalibrationProgress` ankaeme. Geloest wird das erst durch das
            # naechste `StartCalibration`/`AbortCalibration`.
            if error == VisionErrorCode.OK:
                asyncio.ensure_future(_pause_stream_during_processing())
            else:
                await _write_calibration_progress()
            return (
                ua.Variant(json.dumps(summary), ua.VariantType.String),
                ua.Variant(int(error), ua.VariantType.Int32),
            )

        calibration_methods["FinishCalibration"] = await space.vision_system.add_method(
            ua.NodeId(f"{method_prefix}.FinishCalibration", space.own_idx),
            ua.QualifiedName("FinishCalibration", space.own_idx),
            finish_calibration,
            [],
            [ua.VariantType.String, ua.VariantType.Int32],
        )

        @uamethod
        async def abort_calibration(parent):
            """1:AbortCalibration -- stoppt ohne zu speichern."""
            if not calibration_session.running:
                return (ua.Variant(int(VisionErrorCode.INVALID_STATE), ua.VariantType.Int32),)
            await calibration_session.abort()
            if annotator is not None:
                annotator.set_calibration_session(None)
            _log.info("Kalibrier-Session abgebrochen")
            return (ua.Variant(int(VisionErrorCode.OK), ua.VariantType.Int32),)

        calibration_methods["AbortCalibration"] = await space.vision_system.add_method(
            ua.NodeId(f"{method_prefix}.AbortCalibration", space.own_idx),
            ua.QualifiedName("AbortCalibration", space.own_idx),
            abort_calibration,
            [],
            [ua.VariantType.Int32],
        )

    apriltag_source = sources.get("apriltag") if opened.get("apriltag") else None
    if apriltag_source is not None and space.tag_map_json is not None:
        from tagloc.tagmap import tag_map_from_json, tag_map_to_json

        method_prefix = config.vision_system_name

        async def _publish_tag_map() -> None:
            """Spiegelt die geladene Karte in den Leseknoten."""
            current = getattr(apriltag_source, "_tag_map", None)
            if current is not None and space.tag_map_json is not None:
                await space.tag_map_json.write_value(
                    ua.Variant(tag_map_to_json(current), ua.VariantType.String)
                )

        await _publish_tag_map()

        @uamethod
        async def set_tag_map(parent, tag_map_json: str):
            """1:SetTagMap -- setzt die Tag-Map der Zelle.

            Damit ist das Backend die Pflegestelle: eine neue Tag-Groesse oder
            ein verschobener Welttag brauchen keinen Dateizugriff auf dem Pi
            mehr. Die Datei bleibt trotzdem der lokale Zwischenspeicher, damit
            der Pi ohne Backend messen kann.

            Erst pruefen, dann uebernehmen: eine abgelehnte Karte laesst die
            laufende unangetastet.
            """
            if jobs.busy:
                _log.warning("SetTagMap waehrend laufendem Job abgelehnt")
                return (ua.Variant(int(VisionErrorCode.BUSY), ua.VariantType.Int32),)
            if calibration_session is not None and calibration_session.running:
                _log.warning("SetTagMap waehrend laufender Kalibrierung abgelehnt")
                return (ua.Variant(int(VisionErrorCode.BUSY), ua.VariantType.Int32),)
            if layer2_run is not None and layer2_run.active:
                # Eine neue Karte verwirft den Anker, auf dem der Lauf steht.
                _log.warning("SetTagMap waehrend laufendem Layer-2-Lauf abgelehnt")
                return (ua.Variant(int(VisionErrorCode.BUSY), ua.VariantType.Int32),)
            if hand_eye_session is not None and hand_eye_session.active:
                # Die Tag-Groesse aus der Karte skaliert jede Aufnahme.
                _log.warning("SetTagMap waehrend Hand-Auge-Kalibrierung abgelehnt")
                return (ua.Variant(int(VisionErrorCode.BUSY), ua.VariantType.Int32),)
            try:
                tag_map = tag_map_from_json(tag_map_json or "", source="SetTagMap")
            except ValueError as error:
                _log.warning("SetTagMap abgelehnt: %s", error)
                return (
                    ua.Variant(int(VisionErrorCode.INVALID_ARGUMENT), ua.VariantType.Int32),
                )

            for problem in apriltag_source.apply_tag_map(tag_map):
                # Bericht, kein Fehler -- eine Zelle im Aufbau darf messen.
                _log.warning("Tag-Map aus SetTagMap: %s", problem)
            await _publish_tag_map()

            path = config.apriltag.tag_map_path if config.apriltag is not None else None
            if path is None:
                return (ua.Variant(int(VisionErrorCode.OK), ua.VariantType.Int32),)
            try:
                await asyncio.to_thread(
                    _write_tag_map_file, path, tag_map_to_json(tag_map)
                )
            except OSError as error:
                # Messen geht vor Persistenz: die Karte ist bereits aktiv, nur
                # ein Neustart faellt auf die alte Datei zurueck. Das muss der
                # Aufrufer wissen, deshalb trotzdem ein Fehlercode.
                _log.error("Tag-Map konnte nicht nach %s geschrieben werden: %s", path, error)
                return (ua.Variant(int(VisionErrorCode.INTERNAL), ua.VariantType.Int32),)
            _log.info("Tag-Map gesetzt und nach %s geschrieben", path)
            return (ua.Variant(int(VisionErrorCode.OK), ua.VariantType.Int32),)

        calibration_methods["SetTagMap"] = await space.vision_system.add_method(
            ua.NodeId(f"{method_prefix}.SetTagMap", space.own_idx),
            ua.QualifiedName("SetTagMap", space.own_idx),
            set_tag_map,
            [ua.VariantType.String],
            [ua.VariantType.Int32],
        )

    if (
        apriltag_source is not None
        and space.layer2_target is not None
        and space.layer2_status is not None
    ):
        layer2_run = await _install_layer2_run(
            space, config, jobs, apriltag_source, results, calibration_methods,
            lambda: (calibration_session is not None and calibration_session.busy)
            or (hand_eye_session is not None and hand_eye_session.active),
        )
    if apriltag_source is not None and space.hand_eye_status is not None:
        hand_eye_session = await _install_hand_eye_session(
            space, config, apriltag_source, calibration_methods,
            lambda: jobs.busy
            or (calibration_session is not None and calibration_session.busy)
            or (layer2_run is not None and layer2_run.active),
        )
    if annotator is not None and hasattr(annotator, "set_overlay_paused") and apriltag_source is not None:
        # Auf dem Hand-Pi wuerde das Overlay waehrend einer Messung oder
        # Hand-Auge-Sitzung in voller Aufloesung mitrechnen, die CPU gehoert
        # der Messung. Der Stream zeigt dann das Rohbild.
        annotator.set_overlay_paused(
            lambda: apriltag_source.measure_calls > 0
            or (hand_eye_session is not None and hand_eye_session.active)
        )
    if apriltag_source is not None:
        # MeasureBoard an beiden Pis. Es liest nur Kamerabilder und aendert keinen
        # Zustand, laeuft deshalb auch waehrend einer Hand-Auge-Sitzung (die
        # Steuerung zentriert das Brett damit vor `CaptureHandEyeSample`) und in
        # der Aufnahmephase einer Kamera-Kalibrierung (die automatische
        # Kalibrierfahrt nimmt nur auf, wenn das ganze Brett im Bild ist).
        # Gesperrt fuer Jobs und waehrend der Auswertung der Kalibrierung.
        await _install_measure_board(
            space, config, apriltag_source, calibration_methods, states,
            lambda: jobs.busy
            or (calibration_session is not None and calibration_session.processing),
        )
    if apriltag_source is not None and space.last_tag_measurement is not None:
        # MeasureTags und MeasureTagReference nehmen beide frische Bilder; sie
        # sperren sich gegenseitig (und MeasureTagReference auch sich selbst).
        tags_activity = _Activity()
        reference_activity = _Activity()
        measuring_busy = (
            lambda: jobs.busy
            or (calibration_session is not None and calibration_session.busy)
        )
        # MeasureTags laeuft auch WAEHREND einer Hand-Auge-Sitzung: Es liest nur
        # Kamerabilder und aendert keinen Zustand. Eine haengengebliebene Sitzung
        # soll Layer 2 nicht sperren. Gesperrt nur fuer Job und Kamera-Kalibrierung.
        # MeasureTagReference faehrt den Roboter nicht, misst aber laenger; eine
        # Hand-Auge-Sitzung sperrt es weiterhin.
        reference_busy = (
            lambda: measuring_busy()
            or (hand_eye_session is not None and hand_eye_session.active)
        )
        await _install_measure_tags(
            space, config, apriltag_source, calibration_methods, states,
            measuring_busy,
            activity=tags_activity,
            blocked_by=lambda: reference_activity.active,
        )
        if space.last_tag_reference is not None:
            await _install_tag_reference(
                space, config, apriltag_source, calibration_methods, states,
                reference_busy,
                activity=reference_activity,
                blocked_by=lambda: tags_activity.active or reference_activity.active,
                annotator=annotator,
            )

    camera_owner = _camera_owner(sources, opened)
    if camera_owner is not None and config.raw_image_dir is not None:
        await _install_save_raw_image(
            space, config, camera_owner, calibration_methods, lambda: jobs.busy
        )
    if camera_owner is not None and config.use_case_dir is not None:
        await _install_save_use_case_image(
            space, config, camera_owner, calibration_methods, lambda: jobs.busy
        )

    cell_watch = None
    if apriltag_source is not None and space.cell_watch_status is not None:
        cell_watch = await _install_cell_watch(
            space, config, apriltag_source, results, calibration_methods, states,
            lambda: "Job laeuft" if jobs.busy else (
                "Kamera-Kalibrierung laeuft"
                if calibration_session is not None and calibration_session.busy
                else ""
            ),
        )

    # Part-10-Aufsatz auf denselben JobRunner. Muss nach den Zustaenden
    # stehen: das Programm spiegelt den Zustand des Vision-Systems und waere
    # sonst `Ready`, bevor feststeht, ob die Quelle ueberhaupt aufgeht.
    mirror_nodes = {"LatestResultJson": results.json_node}
    if space.latest_camera_frame is not None:
        mirror_nodes["LatestCameraFrame"] = space.latest_camera_frame
    if space.camera_stream_mode is not None:
        mirror_nodes["CameraStreamMode"] = space.camera_stream_mode
    if space.calibration_progress is not None:
        mirror_nodes["CalibrationProgress"] = space.calibration_progress
    if space.tag_map_json is not None:
        mirror_nodes["TagMapJson"] = space.tag_map_json
    if space.active_calibration_info is not None:
        mirror_nodes["ActiveCalibrationInfo"] = space.active_calibration_info
    if space.layer2_target is not None and space.layer2_status is not None:
        mirror_nodes["Layer2Target"] = space.layer2_target
        mirror_nodes["Layer2Status"] = space.layer2_status
    if space.hand_eye_status is not None:
        mirror_nodes["HandEyeStatus"] = space.hand_eye_status
    if space.last_tag_measurement is not None:
        mirror_nodes["LastTagMeasurement"] = space.last_tag_measurement
    if space.last_tag_reference is not None:
        mirror_nodes["LastTagReference"] = space.last_tag_reference
    if space.tag_reference_preview_status is not None:
        mirror_nodes["TagReferencePreviewStatus"] = space.tag_reference_preview_status
    if space.cell_watch_status is not None:
        mirror_nodes["CellWatchStatus"] = space.cell_watch_status
    program = await install_vision_program(
        server,
        server.nodes.objects,
        space.own_idx,
        jobs,
        known_recipes=config.known_recipe_ids,
        mirror_nodes=mirror_nodes,
        mirror_methods=calibration_methods,
    )
    if not states.is_ready():
        # Ein generischer Client soll nicht `Ready` sehen, wenn kein Job
        # angenommen wuerde. Teil 10 hat keinen Fehlerzustand -- `Halted` ist
        # die einzige ehrliche Entsprechung.
        sm = program.state_machine
        await sm.change_state(
            sm.halted, sm.ready_to_halted, "Vision-System nicht betriebsbereit"
        )

    lag_watchdog = asyncio.create_task(_watch_loop_lag())

    _log.info(
        "Vision-System '%s' bereit (Profile %s, Namespace %s)",
        config.vision_system_name,
        ", ".join(sorted(sources)),
        config.namespace_uri,
    )
    return VisionMachine(
        config=config,
        states=states,
        events=events,
        results=results,
        sources=sources,
        jobs=jobs,
        lag_watchdog=lag_watchdog,
        camera_stream=camera_stream,
        camera_health=camera_health,
        camera_http=camera_http,
        assets=assets,
        program=program,
        calibration_session=calibration_session,
        layer2_run=layer2_run,
        hand_eye_session=hand_eye_session,
        cell_watch=cell_watch,
    )


















async def run(config: VisionServerConfig) -> None:
    """Startet den Vision-Server als eigenen Prozess und haelt ihn am Leben.

    Produktiv haengt das Vision-System im Server der Roboterzelle; dieser Weg
    ist fuer Entwicklung und isolierte Tests.
    """
    server = Server()
    await configure_server(server, config)
    machine = await install_vision_machine(server, config)
    _log.info("Vision-Server laeuft auf %s", config.endpoint)
    stop = stop_event_on_signals()
    try:
        async with server:
            await stop.wait()
    finally:
        await machine.aclose()

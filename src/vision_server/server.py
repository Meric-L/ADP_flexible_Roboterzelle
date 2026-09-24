"""Der Server, wie er auf dem Raspberry Pi der Zelle laeuft.

Ein Server, ein Baum: `VisionMachine` (OPC 40100) unter `Objects/Machines` und
daneben `VisionProgram` (OPC UA Teil 10) als Bedienoberflaeche fuer das
Frontend. Beide bedienen denselben Job.

Dieses Modul ist der **Einbauort**, nicht das Vision-System selbst. Es haelt
nur, was von diesem Pi abhaengt: seine Identitaet, sein Kamera-Backend, seine
AprilTag- und Anlagenwerte, dazu mDNS-Ankuendigung und LDS-Anmeldung. Der
Adressraum entsteht vollstaendig in `runner.install_vision_machine`.

Starten:

    python3 -m vision_server.server

Die CPU-Temperatur-Demo aus der Anfangszeit -- `RaspiDevice`, die leere
Zweitinstanz `2:VisionSystem`, `CpuTemperatureResult` und der Namensraum
`http://launch-rm.de/raspi` -- ist entfernt (Altlasten A1-A6). Damit ruecken
alle Namespace-Indizes um eins nach unten; wer sie ueber
`get_namespace_index(uri)` aufloest, merkt davon nichts.
"""

import asyncio
import contextlib
import logging
import os
import signal
import socket
from pathlib import Path
from urllib.parse import urlparse

from asyncua import Server, ua

from .config import DEFAULT_NODESET_PATH, VisionServerConfig
from .discovery import lds, mdns
from .profiles import AprilTagProfileConfig, AssetConfig, CameraStreamConfig
from .runner import install_vision_machine

logging.basicConfig(level=logging.INFO)
_log = logging.getLogger("vision-cell-server")

NODESET_PATH = DEFAULT_NODESET_PATH

#: Rueckfall-Endpoint. Im Regelfall nennt der Endpoint zur Laufzeit die
#: LAN-IPv4 (siehe `lds.advertised_endpoint`), weil `register_to_discovery()`
#: genau diese Adresse als DiscoveryUrl an den Discovery-Server weitergibt --
#: mit `0.0.0.0` verbindet der Aggregation-Server ins Leere. Gelauscht wird
#: unabhaengig davon immer auf allen Schnittstellen (`Server.socket_address`),
#: damit lokale Werkzeuge weiter ueber 127.0.0.1 herankommen.
#: Ist keine LAN-IPv4 zu ermitteln, bleibt es bei diesem Wert -- dann laeuft der
#: Server ohne LDS-Anmeldung weiter.
#:
#: Der Pfad bleibt `/raspi/server/`, obwohl das Raspi-Interface entfernt ist: er
#: steht in der mDNS-Ankuendigung, in der LDS-Registrierung und in jeder
#: Client-Konfiguration. Ihn umzubenennen braeche jede vorhandene Verbindung,
#: ohne irgendetwas zu verbessern.
ENDPOINT = "opc.tcp://0.0.0.0:4840/raspi/server/"
SERVER_NAME = "Raspberry Pi OPC UA Server"

#: Port und Pfad fuer die mDNS-Ankuendigung, aus dem Endpoint abgeleitet statt
#: daneben gepflegt. Ein Client baut `opc.tcp://<ip>:<port><path>` zusammen --
#: ein abweichender Pfad ergibt bei jedem Client eine unbrauchbare URL.
_ENDPOINT_URL = urlparse(ENDPOINT)
MDNS_PORT = _ENDPOINT_URL.port or 4840
MDNS_PATH = _ENDPOINT_URL.path or "/"


#: Hostname -> Identitaet und Bezugsrahmen. `vision_system_name` darf NICHT
#: variieren: Interface-Doku und Frontend nageln `ns=<vision>;s=VisionMachine`
#: fest, ein pi-spezifischer BrowseName bricht jeden Client.
PI_IDENTITIES: dict[str, tuple[str, str]] = {
    "ADP-Roboter-Lokalisierung": ("vision-ceiling-01", "cam_ceiling"),
    "ADP-HandInEye-Kamera-Pi": ("vision-flange-01", "cam_flange"),
}

#: Hostname -> Kamera-Backend (siehe `CameraStreamConfig.backend`). Deckel-Pi
#: nutzt weiterhin Picamera2, der Hand-Pi eine Intel RealSense per
#: `pyrealsense2`. Fehlt ein Host hier, gilt "picamera2" als bisheriger
#: Default -- ein frisch aufgesetzter dritter Pi bricht damit nicht stumm.
PI_CAMERA_BACKENDS: dict[str, str] = {
    "ADP-Roboter-Lokalisierung": "picamera2",
    "ADP-HandInEye-Kamera-Pi": "realsense",
}


#: What distinguishes Layer 1 from Layer 2 -- nothing else. Both run the same
#: server with the same detection source; only these values differ.
#: Ceiling camera: large tags at a distance, full resolution, looser error
#: bound. Flange camera: small tags up close, more samples, tighter bound
#: since moves are made from its pose.
PI_APRILTAG_PRESETS: dict[str, dict] = {
    "cam_ceiling": {
        # Volle Sensoraufloesung der HQ-Kamera (IMX477, 12,3 MP). Der
        # Livestream rechnet nicht darauf, sondern auf dem kleinen lores-Strom
        # (PI_CAMERA_STREAM_PRESETS unten) -- sonst ruckelt das Overlay.
        "resolution": (4056, 3040),
        # Uebergang, bis bei 4056x3040 neu kalibriert ist: eine vorhandene
        # Kalibrierung fuer 2028x1520 stammt aus dem 2x2-Binning desselben
        # Sensors, also demselben Sichtfeld -- ihre Intrinsik laesst sich exakt
        # verdoppeln (`scale_to_resolution`). Nach der Neukalibrierung entfernen,
        # damit eine versehentlich falsche Aufloesung wieder auffaellt.
        "allow_resolution_mismatch": True,
        "tag_size_m": 0.100,
        "samples_per_job": 3,
        "max_reproj_error_px": 3.0,
        # Dasselbe gedruckte Board wie am Hand-Pi (siehe cam_flange unten) --
        # nur die Aufloesung/Kameradistanz unterscheidet sich, nicht das
        # Blatt. Noch nicht real durchgemessen; falls das Board bei der
        # Kalibrierfahrt nicht gefunden wird, war die Annahme falsch --
        # dann mit dem Diagnose-Skript aus der Hand-Pi-Kalibrierung mehrere
        # cols/rows-Kombinationen gegen einen echten Frame testen.
        "calibration_board_type": "chessboard",
        "calibration_board_cols": 7,
        "calibration_board_rows": 9,
        "calibration_board_square_size_m": 0.022,
    },
    "cam_flange": {
        # Muss zu CameraStreamConfig.realsense_resolution passen: Die echten
        # Frames kommen ueber SharedCamera in dieser Aufloesung an, nicht in
        # der hier eingetragenen. Eine falsche Abweichung faellt erst beim
        # ersten Job als ValueError ("Kalibrierung gilt fuer ...") auf.
        "resolution": (640, 480),
        "tag_size_m": 0.050,
        "samples_per_job": 5,
        "max_reproj_error_px": 1.5,
        # Echtes Board, am Hand-Pi durchgemessen (RMS 0,2945 px, Abdeckung
        # 96 %/95 %, siehe data/calibration/cam_flange.json): ein simples
        # Schachbrett aus dem Internet, 22 mm/Feld, 7x9 innere Ecken -- kein
        # ChArUco-Board, das braeuchte zusaetzliche ArUco-Marker im Druck.
        "calibration_board_type": "chessboard",
        "calibration_board_cols": 7,
        "calibration_board_rows": 9,
        "calibration_board_square_size_m": 0.022,
    },
}

#: Kamera-Einstellungen je Rahmen, nur fuer das Backend Picamera2 (die
#: RealSense am Hand-Pi hat eigene Felder in `CameraStreamConfig`).
#:
#: cam_ceiling nimmt mit 12 MP auf. Den Livestream speist der zweite,
#: vom ISP skalierte lores-Strom -- 960x720 ist die Stream-Breite
#: (`max_stream_width`) im Seitenverhaeltnis des Sensors (4056x3040 -> 719,5,
#: gerundet 720; 0,07 % Abweichung, unter der Schranke von
#: `scale_to_resolution`). 10 fps ist die Obergrenze des IMX477 im
#: Vollaufloesungsmodus; mehr anzufordern bringt nichts. Zwei Puffer statt der
#: sechs, die Picamera2 fuer Video anlegt: 6 x 37 MB passt nicht in den
#: CMA-Speicher des Pi.
PI_CAMERA_STREAM_PRESETS: dict[str, dict] = {
    "cam_ceiling": {
        "preview_resolution": (960, 720),
        "capture_fps": 10.0,
        "buffer_count": 2,
        # "off" kodiert im Stream weiterhin den vollen 12-MP-Frame statt den
        # kleinen lores-Strom (Absprache 2026-09-22, camera_stream.py.
        # _encoded) -- der globale Default von 2,0 s (profiles.py) reichte
        # dafuer nicht: allein das Kodieren eines 4056x3040-Bildes
        # ueberschritt ihn zuverlaessig, das Overlay fiel jeden Tick auf das
        # unmarkierte Rohbild zurueck (Bug: Overlay-Text fehlte komplett auf
        # Pi 1). "apriltag" verkleinert seit der Umstellung auf
        # Software-Downscale (siehe stream_overlay.py.annotate) VOR Erkennung
        # und Zeichnen auf `detection_max_width` -- braucht die Reserve nicht
        # mehr zwingend, der grosszuegige Wert bleibt als Sicherheitsmarge
        # bestehen. cam_flange braucht das nicht -- dort hat sich an der
        # Erkennungsaufloesung nichts geaendert (640x480 war schon immer der
        # volle Frame).
        "overlay_timeout_s": 8.0,
    },
}

#: Wurzel des Repos, von `src/vision_server/server.py` aus drei Ebenen
#: hoch. Zeigt auf `data/` und `config/` -- beide liegen bewusst neben dem
#: Quelltext, nicht im Paket: Kalibrierungen gehoeren zur Hardware, die Tag-Map
#: zur Zelle.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def apriltag_config(frame_id: str) -> AprilTagProfileConfig:
    """Return this Pi's AprilTag profile.

    Calibration belongs to the physical camera, hence named after the frame
    and stored under `data/` (not versioned). The tag map describes the
    cell, is the same for both Pis, and lives under `config/`.

    `VISION_ALLOW_PLACEHOLDER_CALIBRATION=1` is a deliberate, temporary
    bypass (`AprilTagProfileConfig.allow_placeholder_calibration`) for
    testing detection/overlay/job path before the real calibration run
    (Testplan Abschnitt 3.3) exists. Poses are then not to scale. Default
    off -- must be set explicitly per Pi, never baked into the committed
    default.
    """
    preset = PI_APRILTAG_PRESETS.get(frame_id, {})
    # Hand-Auge nur dort, wo eine Kamera am Roboter sitzt. Die Deckenkamera
    # ist fest montiert und hat keinen Flansch -- ein Pfad fuer sie waere eine
    # Datei, die nie entsteht und beim Start jedes Mal eine Warnung erzeugt.
    hand_eye_path = (
        REPO_ROOT / "data" / "handeye" / f"{frame_id}.json"
        if frame_id == "cam_flange"
        else None
    )
    return AprilTagProfileConfig(
        calibration_path=REPO_ROOT / "data" / "calibration" / f"{frame_id}.json",
        tag_map_path=REPO_ROOT / "config" / "tagmap.json",
        hand_eye_path=hand_eye_path,
        frame_id=frame_id,
        allow_placeholder_calibration=os.getenv("VISION_ALLOW_PLACEHOLDER_CALIBRATION") == "1",
        # Debug-Artefakt: jede uebernommene interaktive Aufnahme landet
        # zusaetzlich als PNG hier -- zum Nachpruefen/Neu-Rechnen abseits vom
        # Server (2026-09-23, waehrend Kalibrierprobleme auf Pi 1 untersucht
        # wurden). `data/` ist gitignored, kein Aufraeum-Mechanismus noetig,
        # nur von Hand leeren, wenn der Speicherplatz auf dem Pi knapp wird.
        calibration_capture_dir=REPO_ROOT / "data" / "calibration" / f"{frame_id}_captures",
        **preset,
    )


#: Woraus die beiden Vision-Systeme bestehen (OPC 40100-2). Nur eintragen, was
#: wirklich verbaut ist -- ein erfundenes Modell ist schlechter als ein leeres
#: Feld, weil die Anlagensicht fuer Service und Instandhaltung gedacht ist.
PI_ASSET_PRESETS: dict[str, dict] = {
    "cam_ceiling": {
        "computing_device_model": "Raspberry Pi",
        "image_sensor_model": "Raspberry Pi Camera Module",
    },
    "cam_flange": {
        "computing_device_model": "Raspberry Pi",
        #: Genaues Modell (D415? D435?) noch nicht bestaetigt -- leer waere
        #: hier schlechter als "irgendein RealSense", aber ein erfundenes
        #: Modell (z. B. "D315", existiert nicht) waere schlimmer als das.
        "image_sensor_model": "Intel RealSense",
    },
}


def asset_config(frame_id: str, vision_system_id: str) -> AssetConfig:
    """Anlagensicht dieses Pis. Die Seriennummer ist seine Vision-Identitaet."""
    return AssetConfig(
        serial_number=vision_system_id,
        **PI_ASSET_PRESETS.get(frame_id, {}),
    )


def vision_identity() -> tuple[str, str]:
    """Identitaet dieses Pis: Env, dann Hostname-Abbildung, dann Hostname.

    Der Hostname-Rueckfall ist wichtig, weil die systemd-Unit nirgends
    versioniert ist — ein frisch aufgesetzter Pi darf nicht stillschweigend
    dieselbe Id senden wie der andere.
    """
    host = socket.gethostname()
    mapped_id, mapped_frame = PI_IDENTITIES.get(host, (f"vision-{host}", "world"))
    return (
        os.getenv("VISION_SYSTEM_ID") or mapped_id,
        os.getenv("VISION_FRAME_ID") or mapped_frame,
    )


def vision_camera_backend() -> str:
    """Kamera-Backend dieses Pis: Env, dann Hostname-Abbildung, dann Picamera2."""
    host = socket.gethostname()
    return os.getenv("VISION_CAMERA_BACKEND") or PI_CAMERA_BACKENDS.get(host, "picamera2")


def mdns_instance_name() -> str:
    """Dienstname dieses Servers im lokalen Netz.

    Muss im Netz eindeutig sein: kuendigen beide Pis denselben Namen an,
    haengt zeroconf zur Konfliktaufloesung ein `-2` an und der Name wird
    unvorhersehbar. Die Vision-Identitaet ist bereits pro Pi eindeutig --
    deshalb keine zweite Namensquelle danebenstellen.
    """
    return os.getenv("OPCUA_MDNS_NAME") or vision_identity()[0]


#: Praefix der ApplicationUri, nach der Konvention der Zelle: der Roboterserver
#: meldet `urn:plcm:robot-server:ur5e`, wir entsprechend `camera-server`.
APPLICATION_URI_PREFIX = "urn:plcm:camera-server"

#: Vision-Identitaet -> Name in der ApplicationUri. Bewusst eine eigene Tabelle
#: und nicht die Identitaet selbst: die Uri benennt den *Einbauort* in der
#: Zelle, die Vision-Identitaet benennt das Erkennungssystem. Ein unbekannter
#: Pi faellt auf seine Identitaet zurueck, damit er nicht namenlos auftaucht.
PI_APPLICATION_NAMES: dict[str, str] = {
    "vision-ceiling-01": "ceiling-01",
    "vision-flange-01": "roboter-hand-01",
}


def application_uri() -> str:
    """Eindeutige ApplicationUri dieses Servers.

    Der Aggregation-Server der Zelle (`opc.tcp://10.10.38.27:48400/`) fuehrt
    seine Module unter ihrer ApplicationUri -- `urn:plcm:robot-server:ur5e`,
    `urn:smart-business-card-factory:conveyor-system` und so fort. Ohne eigene
    Uri meldete asyncua seinen Default `urn:freeopcua:python:server`:
    nichtssagend, und beide Pis meldeten denselben Wert.
    """
    vision_system_id = vision_identity()[0]
    name = PI_APPLICATION_NAMES.get(vision_system_id, vision_system_id)
    return os.getenv("OPCUA_APPLICATION_URI") or f"{APPLICATION_URI_PREFIX}:{name}"


def vision_config(endpoint: str = ENDPOINT) -> VisionServerConfig:
    """Konfiguration des eingebauten Vision-Systems.

    Endpoint, ApplicationURI und ServerName sind die dieses Servers; das
    Vision-System nutzt daraus nur seinen eigenen Namespace, den Instanznamen
    und den Nodeset-Pfad.
    """
    vision_system_id, frame_id = vision_identity()
    backend = vision_camera_backend()
    apriltag = apriltag_config(frame_id)
    _log.info(
        "Vision-Identitaet: %s (Rahmen %s, Kamera-Backend %s)",
        vision_system_id,
        frame_id,
        backend,
    )
    # Picamera2/OpenCV oeffnen die geteilte Kamera mit `CameraStreamConfig.
    # resolution` (siehe `camera.py:_open_picamera2`) -- weicht das vom
    # `AprilTagProfileConfig.resolution` ab, hat das erfasste Bild ein
    # anderes Seitenverhaeltnis als die (Platzhalter- oder echte)
    # Kalibrierung, und das Stream-Overlay scheitert mit "Seitenverhaeltnis
    # aendert sich" (`tagloc.calibration.scale_to_resolution`). RealSense
    # betroffen nicht: die hat mit `realsense_resolution` ein eigenes Feld,
    # das schon auf cam_flanges Aufloesung (640x480) abgestimmt ist.
    camera_stream = (
        None
        if os.getenv("VISION_DISABLE_STREAM") == "1"
        # Diagnose-Schalter (2026-09-24): komplett ohne Livestream starten,
        # um bei Job-Timeout-Verdacht per A/B-Test auszuschliessen, dass
        # der Stream-Publisher/-Overlay ueberhaupt beteiligt ist --
        # `config.camera_stream=None` legt gar keine Stream-Knoten an
        # (`address_space.py`), also kein Publisher, kein Overlay, kein
        # MJPEG-Server. Fuer den Dauerbetrieb NICHT setzen.
        else CameraStreamConfig(backend=backend)
        if backend == "realsense"
        else CameraStreamConfig(
            backend=backend,
            resolution=apriltag.resolution,
            **(PI_CAMERA_STREAM_PRESETS.get(frame_id, {}) if backend == "picamera2" else {}),
        )
    )
    return VisionServerConfig(
        endpoint=endpoint,
        server_name=SERVER_NAME,
        nodeset_path=NODESET_PATH,
        vision_system_id=vision_system_id,
        frame_id=frame_id,
        camera_stream=camera_stream,
        apriltag=apriltag,
        assets=asset_config(frame_id, vision_system_id),
    )


async def main():
    """Baut den Adressraum auf und haelt den Server am Leben."""
    server = Server()
    await server.init()

    # Der Endpoint nennt die LAN-IPv4, denn `register_to_discovery()` gibt
    # genau ihn als DiscoveryUrl an den Discovery-Server weiter. `0.0.0.0`
    # waere dort wertlos. Gelauscht wird trotzdem auf allen Schnittstellen,
    # sonst verlieren wir 127.0.0.1 -- darueber laeuft der Hello-World-Client
    # auf dem Pi.
    endpoint = lds.advertised_endpoint(MDNS_PORT, MDNS_PATH) or ENDPOINT
    server.set_endpoint(endpoint)
    server.socket_address = ("0.0.0.0", MDNS_PORT)
    server.set_server_name(SERVER_NAME)
    # Vor dem Aufbau des Adressraums: die ApplicationUri landet im
    # Namespace-Array auf ns=1 und ist der Name, unter dem der
    # Aggregation-Server dieses Modul fuehrt.
    await server.set_application_uri(application_uri())
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])

    # Der gesamte Adressraum entsteht in `install_vision_machine`: Nodesets,
    # `VisionMachine` unter `Objects/Machines` und `VisionProgram` daneben.
    # Dieser Server legt selbst keine Knoten mehr an -- die CPU-Temperatur-Demo
    # (`RaspiDevice`, `2:VisionSystem`, `CpuTemperatureResult`) ist entfernt,
    # samt ihrem Namensraum `http://launch-rm.de/raspi`.
    machine = await install_vision_machine(server, vision_config(endpoint))

    _log.info("Server startet auf %s", server.endpoint.geturl())

    # Ohne Signal-Handler laeuft `finally` unter systemd nicht: SIGTERM beendet
    # den Prozess, ohne dass asyncio.run aufraeumt.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    instance = mdns_instance_name()

    try:
        async with server:
            # Erst der Server, dann Ankuendigung und Registrierung -- wer den
            # Dienst findet, soll ihn auch erreichen. Beim Verlassen werden
            # beide zurueckgezogen.
            # Beide sind standardmaessig aus (`OPCUA_MDNS`, `OPCUA_LDS_URL`).
            async with (
                mdns.announce(instance, MDNS_PORT, MDNS_PATH),
                # Die Ankuendigung allein genuegt dem Aggregation-Server der
                # Zelle nicht: er nimmt nur auf, was beim Discovery-Server
                # angemeldet ist. Messung und Begruendung stehen in `discovery.lds`.
                # Standardmaessig aus -- ohne `OPCUA_LDS_URL` kehrt das sofort
                # zurueck, und das Modul erscheint dort nicht.
                lds.register(server),
            ):
                # Frueher lief hier eine 1-Hz-Schleife, die die Demo-Werte
                # aktuell hielt. Sie lag im selben Event-Loop wie das
                # Vision-System; der Loop-Lag-Watchdog in `runner.py` meldet
                # Blockaden jetzt zuverlaessiger, als ein Zaehler es je konnte.
                await stop.wait()
    finally:
        await machine.aclose()


if __name__ == "__main__":
    asyncio.run(main())

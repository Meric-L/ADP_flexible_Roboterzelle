"""Profilspezifische Konfiguration.

Auf Paketebene, damit `config.py` sie importieren kann, ohne dass eine
Erkennungsquelle mit OpenCV auf die Importkette des Zellenservers geraet.
Nur Stdlib-Typen: `tag_family` ist ein String und wird erst in der Quelle zu
einer `cv2.aruco.DICT_*`-Konstante aufgeloest.

Nie eine Matrix hier hinein — `K`/`D` sind numpy, unhashbar und gehoeren dem,
was sie aus `calibration_path` laedt.
"""

from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: Machine-specific, hence under `data/` (excluded by .gitignore).
DEFAULT_CALIBRATION_PATH = _REPO_ROOT / "data" / "calibration" / "camera.json"
#: Cell layout, hence versioned under `config/`.
DEFAULT_TAG_MAP_PATH = _REPO_ROOT / "config" / "tagmap.json"


@dataclass(frozen=True)
class AprilTagProfileConfig:
    """Betriebsparameter der AprilTag-Erkennung."""

    camera_index: int = 0
    use_picamera: bool = True
    resolution: tuple[int, int] = (2028, 1520)
    calibration_path: Path = DEFAULT_CALIBRATION_PATH
    tag_map_path: Path | None = DEFAULT_TAG_MAP_PATH
    tag_family: str = "tag36h11"
    tag_size_m: float = 0.05
    warmup_s: float = 2.0
    capture_timeout_s: float = 5.0
    samples_per_job: int = 3
    max_reproj_error_px: float = 3.0
    frame_id: str = "cam_ceiling"
    frame_convention: str = "z_forward_x_right_y_down"
    #: "aruco" (default), "pupil" or "auto" -- see tagloc.detector.
    detector_backend: str = "aruco"
    #: Scale intrinsics to the actual image size instead of aborting. Only
    #: enable when deliberately running at a resolution other than the one
    #: calibrated for.
    allow_resolution_mismatch: bool = False
    #: Bewusster Notausgang, kein Dauerzustand: laesst die Quelle ohne echte
    #: Kalibrierdatei starten (grob geschaetzte Intrinsik statt Preoperational).
    #: Damit lassen sich Detektor, Overlay und Job-Pfad pruefen, bevor die
    #: echte Kalibrierfahrt (Testplan Abschnitt 3.3) gemacht wurde. Posen sind
    #: dann plausibel orientiert, aber nicht masshaltig -- vor dem Rollout
    #: wieder auf `False`, sonst faellt eine fehlende echte Kalibrierung nie
    #: mehr auf.
    allow_placeholder_calibration: bool = False
    #: Geometrie des Kalibrierboards fuer `CalibrationSession`
    #: (`vision_server/calibration_session.py`). Skalare statt eines
    #: `tagloc.boards.BoardSpec`-Objekts, aus demselben Grund wie `tag_family`:
    #: diese Datei bleibt frei von einer `tagloc`-Abhaengigkeit, das Objekt
    #: wird erst dort gebaut, wo es gebraucht wird.
    calibration_board_type: str = "chessboard"
    calibration_board_cols: int = 9
    calibration_board_rows: int = 6
    calibration_board_square_size_m: float = 0.030
    calibration_board_marker_size_m: float = 0.022
    calibration_board_dictionary: str = "DICT_4X4_50"
    #: Ab wie vielen Aufnahmen `FinishCalibration` ueberhaupt versucht zu
    #: rechnen (CLI-Tool `tagloc.cli.calibrate` nennt das `MIN_SAMPLES`).
    calibration_min_samples: int = 15
    #: Sobald Abdeckung x UND y diesen Wert erreichen (und `calibration_
    #: min_samples` Aufnahmen vorliegen), rechnet und speichert die Session
    #: automatisch -- fuers Frontend, das nicht selbst wissen muss, wann
    #: "genug" ist. `None` schaltet das ab (nur manuelles `FinishCalibration`,
    #: wie es die CLI-Tools weiter nutzen). Deckt sich mit dem Abbruch-
    #: kriterium aus dem Testplan ("Abdeckung x und y ueber 70%").
    calibration_coverage_threshold: float | None = 0.7


#: Unterstuetzte Werte fuer `CameraStreamConfig.backend`.
CAMERA_BACKENDS: tuple[str, ...] = ("picamera2", "opencv", "realsense")


@dataclass(frozen=True)
class CameraStreamConfig:
    """Betriebsparameter der einen Kamera, die sich Erkennung und Livestream teilen.

    Eine einzige `SharedCamera`-Instanz (siehe `camera.py`) wird mit diesen
    Werten geoeffnet; sowohl die Erkennungsquelle (`detection/apriltag.py`)
    als auch der Node-Publisher (`camera_stream.py`) lesen von dort, statt
    selbst je einen eigenen Kamera-Handle zu oeffnen — sowohl Picamera2/libcamera
    als auch RealSense lassen pro Kamera nur einen offenen Zugriff gleichzeitig zu.

    `backend` waehlt die Hardware-Anbindung, siehe `CAMERA_BACKENDS`:
    "picamera2" (Pi-Kamera, z. B. Decken-Pi), "realsense" (Intel RealSense
    per `pyrealsense2`, z. B. Hand-Pi) oder "opencv" (`cv2.VideoCapture`,
    generischer Fallback/Entwicklung).
    """

    backend: str = "picamera2"
    camera_index: int = 0
    #: Nur fuer Picamera2/OpenCV. RealSense hat ein eigenes Feld
    #: (`realsense_resolution`), weil die Sensoren -- besonders ueber die auf
    #: dem Pi noetige RSUSB/libuvc-Backend-Anbindung -- nur bestimmte
    #: Aufloesung/FPS-Kombinationen unterstuetzen; `1280x720` ist dafuer zu
    #: bandbreitenhungrig und laesst `pipeline.start()` mit
    #: "Couldn't resolve requests" scheitern.
    resolution: tuple[int, int] = (1280, 720)
    #: Nur Picamera2: zweiter, kleiner Bildstrom ("lores") aus demselben
    #: Frame, im ISP skaliert -- praktisch ohne CPU-Last. Livestream und
    #: Overlay rechnen darauf, Jobs und Kalibrierung weiter auf `resolution`.
    #: `None` = kein zweiter Strom; der Stream verkleinert dann selbst
    #: (`max_stream_width`), wie bei RealSense und OpenCV.
    preview_resolution: tuple[int, int] | None = None
    #: Nur Picamera2: Anzahl Kamerapuffer, `None` = Picamera2-Standard (6 bei
    #: Video). Bei 12 MP waeren das ~220 MB CMA-Speicher, mehr als der Pi
    #: standardmaessig reserviert -- `configure()` scheitert dann.
    buffer_count: int | None = None
    warmup_s: float = 2.0
    #: So oft nimmt die `SharedCamera` auf. Obergrenze fuer den HTTP-Stream.
    capture_fps: float = 15.0
    #: Rate des OPC-UA-Knotens `LatestCameraFrame`. Bewusst niedrig: er ist nur
    #: noch der Rueckfallweg, Base64 ueber OPC UA und Backend taugt nicht fuer
    #: Video. Das Live-Bild kommt ueber `http_port`.
    stream_fps: float = 5.0
    #: Konservativ gewaehlt, damit die Pipeline auch ueber die RSUSB-Backend-
    #: Anbindung (noetig, weil der Pi-Kernel keinen brauchbaren UVC-Treiber
    #: fuer RealSense mitbringt) zuverlaessig startet. Bei Bedarf hochsetzen,
    #: sobald `_open_realsense`s Fehlermeldung die tatsaechlich unterstuetzten
    #: Profile der angeschlossenen Kamera zeigt.
    realsense_resolution: tuple[int, int] = (640, 480)
    #: Native Aufnahme-Framerate der RealSense-Pipeline; unabhaengig von
    #: `stream_fps`, weil die Sensoren nur bestimmte fps-Werte je Aufloesung
    #: unterstuetzen (typ. 6/15/30/60). `stream_fps` bleibt die Kadenz, mit
    #: der `SharedCamera` den jeweils neuesten Frame abholt.
    realsense_fps: int = 15
    jpeg_quality: int = 70
    #: Maximale Bildbreite im Stream; breitere Frames werden vor dem
    #: JPEG-Encode herunterskaliert (nur fuer den Stream -- Erkennung und
    #: Kalibrierung arbeiten weiter auf dem vollen Kamera-Frame). Ohne das
    #: kostet z. B. cam_ceiling (2028x1520) auf dem Pi pro Tick ein Encode
    #: eines ~3-MP-Bildes; die Framerate brach spuerbar ein. `None` schaltet
    #: die Skalierung ab.
    max_stream_width: int | None = 960
    #: Port des MJPEG-Streams (`http://<pi>:<port>/stream.mjpg`); 0 = aus.
    #: Das Frontend liest ihn aus `http_port_node_name` und baut die URL aus
    #: der Adresse, unter der es den OPC-UA-Server erreicht.
    http_port: int = 8080
    #: Bildrate des MJPEG-Streams, solange mindestens ein Zuschauer da ist.
    http_fps: float = 15.0
    http_port_node_name: str = "CameraStreamHttpPort"
    node_name: str = "LatestCameraFrame"
    #: Writable node through which the frontend selects the overlay mode.
    mode_node_name: str = "CameraStreamMode"
    #: Initial value; valid values are "off", "apriltag", "calibration"
    #: (tagloc.overlay).
    overlay_mode: str = "apriltag"
    #: Minimum gap between two detection runs for the overlay. The stream is
    #: meant to help debugging, not load the Pi's CPU -- between runs, the
    #: last result is redrawn.
    overlay_interval_s: float = 0.5
    #: Watchdog: so lange darf ein einzelnes `capture_array()` dauern. Danach
    #: gilt die Kamera als haengend -- Picamera2 wartet sonst ewig auf einen
    #: Frame, den libcamera nie liefert (Pi 1, 2026-09-22).
    frame_timeout_s: float = 3.0
    #: So viele Aufnahmefehler in Folge, bevor die Kamera neu geoeffnet wird.
    #: Ein Timeout zaehlt sofort voll: der Worker haengt, jeder weitere Aufruf
    #: stuende nur hinter ihm an.
    max_capture_failures: int = 3
    #: So viele Neu-Oeffnungen ohne einen einzigen Frame dazwischen, bevor der
    #: Prozess sich beendet und systemd (`Restart=always`) ihn neu startet.
    max_reopen_attempts: int = 2
    #: Aufnahmeseitig: ab diesem Alter gilt das letzte Bild als veraltet und
    #: `DeviceHealth` meldet OFF_SPEC (camera_health.py). Betrifft **nur** die
    #: Zustandsbewertung -- der Livestream veroeffentlicht unabhaengig davon
    #: weiter, ob die Kamera lebt, sagt die Anlagensicht und nicht das Bild.
    stale_frame_s: float = 2.0
    #: Laenger darf ein Overlay-Lauf nicht dauern, sonst geht das Rohbild raus.
    overlay_timeout_s: float = 2.0
    #: Kadenz der Zustandsabfrage fuer `DeviceHealth` (camera_health.py).
    #: Nicht gegriffen, sondern aus dem schmalsten Zustandsfenster abgeleitet:
    #: OFF_SPEC gilt, sobald der Frame aelter als `stale_frame_s` ist, und
    #: endet, wenn der Watchdog nach `frame_timeout_s` eskaliert -- mit den
    #: Vorgabewerten also 3,0 - 2,0 = 1,0 s. Bei 1 Hz abgetastet wuerde es oft
    #: verfehlt; halb so lang trifft es mindestens einmal. Kostet nichts: die
    #: Abfrage liest nur Zaehlerstaende, geschrieben wird erst bei Aenderung.
    health_interval_s: float = 0.5


@dataclass(frozen=True)
class AssetConfig:
    """Woraus dieses Vision-System besteht -- Part 2 (AMCM).

    Reine Stammdaten, wie die uebrigen Profile nur Stdlib-Typen und hashbar.
    Was sich zur Laufzeit aendert (CPU-Temperatur, Kamerazustand) steht nicht
    hier, sondern wird von `asset_model` aus den laufenden Objekten gelesen.
    """

    manufacturer: str = "TU Darmstadt PLCM"
    model: str = "Flexible Roboterzelle -- Vision"
    serial_number: str = ""
    software_revision: str = "0.1.0"
    #: Die Recheneinheit, auf der dieser Server laeuft.
    computing_device_model: str = "Raspberry Pi"
    #: Kameramodul und Objektiv. Leer lassen, was nicht bekannt ist -- ein
    #: erfundener Wert waere schlimmer als ein leeres Feld.
    image_sensor_model: str = ""
    lens_model: str = ""

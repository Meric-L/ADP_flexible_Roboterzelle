"""AprilTag-Erkennung ueber die mit dem Livestream geteilte Kamera.

Eine Quelle fuer beide Kameras. Die Deckenkamera und die Kamera am Roboter
(Eye-in-Hand) nutzen dieselbe Klasse und denselben Code; sie unterscheiden sich
nur im `AprilTagProfileConfig`, den `vision_server/server.py` je Pi setzt, und
darin, was sie zu sehen bekommen:

* sind Welttags aus der Karte im Bild, wird die Kamerapose daraus bestimmt,
  und jede Modulpose wird im Welt-KS gemeldet, zusammen mit dem Welttag, dem
  sie am naechsten steht
* sonst bleibt alles im Kamera-KS, und `frameConvention` sagt dem Leser, wohin
  +Z zeigt

**Nur die Welttags sind fest** -- vier davon am Rand der Zelle. Der Roboter ist
ein Modul wie jedes andere: er traegt eigene Tags und wird gemessen, nicht aus
einer Datei gelesen. Die Deckenkamera ist die einzige, die den Roboter und die
Welttags zugleich sieht, also beantwortet sie, neben welchem Welttag der Roboter
steht. Die Kamera am Roboter lokalisiert sich dann optisch gegen diesen Tag --
dafuer braucht es keine Hand-Auge-Kalibrierung.

Die eigentliche Rechnung steckt in `tagloc`; dieses Paket ist der
OPC-UA-Adapter. Es ist nach Aufgaben in Module geteilt, die als Mixins zur
einen Klasse `AprilTagDetectionSource` zusammengesetzt werden:

| Modul | Aufgabe |
| --- | --- |
| `resources.py` | Kalibrierung, Tag-Map, Hand-Auge, Detektor laden (`open`), austauschen (`apply_*`), `close` |
| `capture.py` | auf frische Bilder warten, Kalibrierung zur Bildgroesse, Overlay-Stand |
| `job.py` | `acquire_and_detect`: Bilder auswerten, Module lokalisieren, Detektionen bauen |
| `tags.py` | `MeasureTags` und `measure_tag` (Hand-Auge): Tags im Kamera-KS |
| `board.py` | `measure_board` (Hand-Auge) und `MeasureBoard`: das Schachbrett |
| `reference.py` | `MeasureTagReference` und `PlanTagReferenceView`: Bezugskanten |
| `cell.py` | `observe_cell`: ein Bild fuer die Zellen-Ueberwachung |
| `snapshot.py` | `TagSnapshot` fuers Livestream-Overlay |

Der Importpfad `vision_server.detection.apriltag` und alle bisher dort
importierbaren Namen bleiben erhalten.
"""

from collections.abc import Callable
from typing import Any

from ...camera import SharedCamera
from ...profiles import AprilTagProfileConfig, CameraStreamConfig
from ..base import DetectionSource
from .board import OBSERVE_MAX_SIDE, BoardMeasureMixin
from .capture import POLL_INTERVAL_S, CaptureMixin
from .cell import CellObserveMixin
from .job import (
    PAYLOAD_DIGITS,
    SOURCE_BY_FRAME,
    JobMixin,
    robot_pose_from_parameters,
)
# Nicht in `__all__`, aber von Tests und `cellwatch` unter diesem Pfad importiert:
from .job import _localization_summary, _merge_world_tags, _observed_world_tags  # noqa: F401
from .reference import ReferenceMixin
from .resources import ResourcesMixin
from .snapshot import TagSnapshot
from .tags import TagMeasureMixin

__all__ = [
    "OBSERVE_MAX_SIDE",
    "PAYLOAD_DIGITS",
    "POLL_INTERVAL_S",
    "SOURCE_BY_FRAME",
    "AprilTagDetectionSource",
    "TagSnapshot",
    "robot_pose_from_parameters",
]


class AprilTagDetectionSource(
    ResourcesMixin,
    CaptureMixin,
    JobMixin,
    TagMeasureMixin,
    BoardMeasureMixin,
    ReferenceMixin,
    CellObserveMixin,
    DetectionSource,
):
    """Lokalisiert Module an ihren AprilTags.

    Haelt den Zustand der Quelle (Kamera, Detektor, Kalibrierung, Karte,
    Anker); die Methoden stehen in den Mixins, siehe Modul-Docstring.
    """

    profile_id = "apriltag"
    is_simulated = False

    def __init__(
        self,
        config: AprilTagProfileConfig,
        camera_config: CameraStreamConfig | None = None,
        *,
        camera: SharedCamera | None = None,
        detector: Any = None,
        calibration: Any = None,
        tag_map: Any = None,
    ) -> None:
        self._config = config
        self._camera_config = camera_config or CameraStreamConfig()
        #: Von aussen uebergeben, wenn `camera_stream.py` dasselbe Handle
        #: braucht (siehe `runner.py`).
        self.camera = camera if camera is not None else SharedCamera(self._camera_config)
        #: Einspeisbar, damit Tests ohne OpenCV und ohne Dateien laufen.
        self._detector = detector
        self._calibration = calibration
        self._tag_map = tag_map
        #: Kamerapose des zuletzt ausgewerteten Bilds, von `_locate` gesetzt.
        #: `None` bis zum ersten Bild und wieder, sobald kein Welttag zu sehen
        #: war und kein Anker sie tragen konnte.
        self._localization = None
        #: Hand-Auge, einmal kalibriert -- die Kamera sitzt starr am Roboter.
        self._hand_eye = None
        #: Wo die Roboterbasis im Welt-KS steht. Wird beim ersten Welttag im
        #: Bild gesetzt und traegt danach ueber alle Module hinweg, auch wenn
        #: kein Welttag mehr zu sehen ist. Lebt bewusst ueber Jobs hinweg:
        #: geankert wird einmal je Lokalisierungsvorgang, nicht je Modul.
        self._anchor = None
        #: Letzte gemessene Drift gegen den Anker, `None` solange ungeprueft.
        self._drift = None
        #: Welttags des zuletzt ausgewerteten Bilds, von `_locate` gesetzt.
        self._world_tag_sample: list = []
        #: Laufende `MeasureTags`-Aufrufe (`runner.py`); das Stream-Overlay haelt
        #: solange an.
        self.measure_calls = 0
        #: Neuestes Ergebnis in voller Aufloesung, egal aus welchem Pfad
        #: (`TagSnapshot`). Wird nur ersetzt, nie veraendert -- das Overlay
        #: liest es aus seinem eigenen Thread ohne Sperre.
        self.latest_tags: TagSnapshot | None = None
        #: Wird je gemessenem Bild von `MeasureTagReference` mit
        #: `(debug, corners_px, image_size, title, layers)` aufgerufen (Mess-Thread),
        #: damit der Livestream die Kantensuche live zeigt. `None` = niemand hoert zu.
        self.reference_debug_sink: Callable[..., None] | None = None
        self.frame_id = config.frame_id
        self.frame_convention = config.frame_convention
        self.configuration_id = self._build_configuration_id()

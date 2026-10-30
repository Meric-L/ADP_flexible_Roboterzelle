"""Gemeinsamer Zustand beim Einbau des Vision-Systems (`Wiring`).

`install_vision_machine` legt die OPC-UA-Methoden an, bevor alle Teile stehen:
die Kalibrier-Session, den Annotator, den Layer-2-Lauf und die
Hand-Auge-Sitzung gibt es erst spaeter im Ablauf. Die Handler lesen sie erst
beim Aufruf einer Methode, also lange danach. Frueher loeste das der
spaete Namens-Zugriff von Closures auf lokale Variablen; seit der Zerlegung
in Module haelt dieses Objekt die Felder, und jeder Schritt setzt seine eigenen.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from asyncua import Node, Server

from ..address_space import VisionAddressSpace
from ..calibration_session import CalibrationSession
from ..camera_stream import CameraStreamPublisher
from ..config import VisionServerConfig
from ..detection import DetectionSource
from ..job import JobRunner
from ..result_management import ResultStore
from ..state_machine import VisionStateMachines


@dataclass
class Wiring:
    """Was die Einbau-Schritte untereinander teilen."""

    server: Server
    space: VisionAddressSpace
    config: VisionServerConfig
    states: VisionStateMachines
    results: ResultStore
    sources: Mapping[str, DetectionSource]
    jobs: JobRunner
    #: Je Profil: ob die Quelle geoeffnet werden konnte (siehe `install`).
    opened: dict[str, bool] = field(default_factory=dict)
    #: Die geoeffnete AprilTag-Quelle; `None`, wenn es sie nicht gibt oder
    #: sie nicht aufging.
    apriltag_source: Any = None
    #: Livestream-Publisher; `None` ohne Stream-Knoten oder ohne Kamera.
    camera_stream: CameraStreamPublisher | None = None
    #: Overlay des Livestreams (`stream_overlay.AprilTagStreamAnnotator`).
    annotator: Any = None
    #: `None`, wenn `config.apriltag` nicht gesetzt ist.
    calibration_session: CalibrationSession | None = None
    #: Layer-2-Lauf; `None` ausser auf dem Hand-Pi.
    layer2_run: Any = None
    #: Hand-Auge-Sitzung; `None` ausser auf dem Hand-Pi.
    hand_eye_session: Any = None
    #: Zusaetzliche Methoden-Knoten nach Namen. Landen als `mirror_methods`
    #: auch unter `VisionProgram`; die Reihenfolge der Eintraege bleibt die
    #: Reihenfolge der Anlage.
    methods: dict[str, Node] = field(default_factory=dict)

    @property
    def method_prefix(self) -> str:
        """Praefix der Methoden-NodeIds.

        Explizit statt der laufenden Nummer, die `add_method(own_idx, ...)`
        vergeben wuerde: die verschiebt sich, sobald jemand davor einen Knoten
        einfuegt, und das Frontend spricht die Methoden ueber feste Adressen an.
        """
        return self.config.vision_system_name

    def calibration_busy(self) -> bool:
        """Laeuft eine Kalibrier-Session oder ihre Hintergrund-Auswertung?"""
        return self.calibration_session is not None and self.calibration_session.busy

    def calibration_processing(self) -> bool:
        """Rechnet die Kalibrierung gerade im Hintergrund?"""
        return self.calibration_session is not None and self.calibration_session.processing

    def hand_eye_active(self) -> bool:
        """Ist eine Hand-Auge-Sitzung offen?"""
        return self.hand_eye_session is not None and self.hand_eye_session.active

    def layer2_active(self) -> bool:
        """Laeuft ein Layer-2-Lauf?"""
        return self.layer2_run is not None and self.layer2_run.active

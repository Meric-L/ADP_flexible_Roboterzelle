"""Datentypen zwischen Bild und Mathematik. Kein OpenCV.

Ausgelagert, weil `detector.py` und `pose.py` OpenCV brauchen, `localize.py`
aber nicht darf: Laegen diese Dataclasses in den cv2-Modulen, zoege
`localize` OpenCV mit hinein. Beide Typen werden aus `detector` und `pose`
erneut exportiert, damit Aufrufer sie dort finden, wo sie sie erwarten.
"""

import math
from dataclasses import dataclass, field
from typing import Any

from .geometry import Pose

#: Oberhalb dieses Verhaeltnisses gilt eine Pose als mehrdeutig.
AMBIGUITY_THRESHOLD = 0.6


@dataclass(frozen=True)
class TagObservation:
    """Ein im Bild gefundener Tag -- noch ohne Pose.

    `corners` sind vier Bildpunkte in **Uhrzeigerreihenfolge, oben links
    beginnend** (die Reihenfolge von `cv2.aruco`). Jeder Detektor-Adapter
    bringt seine Ausgabe in diese Reihenfolge; `pose.py` verlaesst sich darauf,
    weil `SOLVEPNP_IPPE_SQUARE` genau diese Zuordnung zu den Objektpunkten
    erwartet.
    """

    tag_id: int
    corners: tuple[tuple[float, float], ...]
    decision_margin: float | None = None

    def center(self) -> tuple[float, float]:
        xs = [corner[0] for corner in self.corners]
        ys = [corner[1] for corner in self.corners]
        return (sum(xs) / len(xs), sum(ys) / len(ys))

    def side_length_px(self) -> float:
        """Mittlere Kantenlaenge im Bild -- grobes Mass fuer die Erkennungsguete."""
        total = 0.0
        count = len(self.corners)
        for index in range(count):
            first = self.corners[index]
            second = self.corners[(index + 1) % count]
            total += math.dist(first, second)
        return total / count


@dataclass(frozen=True)
class TagPose:
    """Ein Tag mit geschaetzter Pose im Kamera-Koordinatensystem."""

    tag_id: int
    pose_cam_tag: Pose
    reprojection_error_px: float = float("nan")
    #: Fehler der besten Loesung geteilt durch den der zweitbesten, in (0, 1].
    #: Nahe 1 heisst, beide Loesungen erklaeren das Bild gleich gut.
    ambiguity_ratio: float = 0.0
    observation: TagObservation | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def is_ambiguous(self) -> bool:
        """Ein quadratischer Marker, unter flachem Winkel gesehen, hat zwei gute Loesungen.

        Mehrdeutig heisst nicht falsch, aber auf eine solche Pose darf nicht
        ungeprueft gehandelt werden, deshalb wird das Flag in den Nutzdaten
        mitgefuehrt.
        """
        return self.ambiguity_ratio > AMBIGUITY_THRESHOLD

    def is_usable(self, max_reprojection_error_px: float | None = None) -> bool:
        """Liefert, ob diese Pose zur Weitergabe taugt.

        Der NaN-Fall wird zuerst und ausdruecklich geprueft: `nan > threshold`
        ist in Python `False`, ein einfacher Vergleich liesse also eine
        entartete Loesung durch -- genau den Fall, vor dem diese Pruefung
        schuetzt. Nachgelagert wuerde `payload` beim Serialisieren
        (`allow_nan=False`) eine Ausnahme werfen, und der Job endete als
        INTERNAL statt als DETECTION_FAILED.
        """
        error = self.reprojection_error_px
        if math.isnan(error) or math.isinf(error):
            return False
        return max_reprojection_error_px is None or error <= max_reprojection_error_px

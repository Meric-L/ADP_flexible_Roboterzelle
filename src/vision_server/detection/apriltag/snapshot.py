"""`TagSnapshot`: die Tags eines Bilds fuers Livestream-Overlay."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TagSnapshot:
    """Die Tags eines Bilds in voller Kameraaufloesung -- fuers Livestream-Overlay.

    Der Livestream zeigt ein verkleinertes Bild, soll aber zeigen, was die
    Erkennung auf dem vollen Frame findet. Erkennt er dafuer selbst im
    Stream-Takt, frisst das am Pi eine Core (py-spy, 24.09.2026). Deshalb
    haelt die Quelle hier das neueste Ergebnis fest, das Job, Zellen-
    Ueberwachung oder Messung ohnehin berechnet haben, und das Overlay
    zeichnet es nur (`stream_overlay.py`). Die Ecken stehen in Pixeln von
    `image_size`; der Zeichner rechnet sie auf die Stream-Groesse um.
    """

    tag_poses: tuple
    image_size: tuple[int, int]
    #: "job" | "cellwatch" | "measure" | "overlay"
    origin: str
    #: `time.monotonic()` nach der Erkennung.
    taken_at: float

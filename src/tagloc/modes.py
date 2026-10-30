"""Overlay-Modi des Livestreams. Nur Standardbibliothek.

Ausgelagert, weil `vision_server.camera_stream` sie braucht, ohne numpy oder
OpenCV mitzuziehen: Der Livestream muss auch ohne konfigurierte Erkennung
weiterlaufen. `tagloc.overlay` exportiert beide erneut, damit Zeichencode sie
dort findet.
"""

#: Reihenfolge = Reihenfolge der Auswahl im Frontend.
OVERLAY_MODES = ("off", "apriltag", "calibration", "undistorted", "tagreference")
DEFAULT_OVERLAY_MODE = "apriltag"

#: Beschriftungen, die der Bediener sieht.
OVERLAY_MODE_LABELS = {
    "off": "Rohbild",
    "apriltag": "AprilTags markieren",
    "calibration": "Kalibrierboard markieren",
    "undistorted": "Entzerrt",
    "tagreference": "Tag einmessen",
}

#: Wird im Bild gezeigt, wenn "undistorted" keine echte Kalibrierung zur Verfuegung hat.
NO_CALIBRATION_HINT = "Keine Kalibrierung -- Rohbild"


def normalise_mode(mode) -> str:
    """Bildet einen von aussen gesetzten Modus auf einen gueltigen Wert ab.

    Der Wert kommt aus einem beschreibbaren OPC-UA-Knoten, also vom Frontend.
    Ein unbekannter, leerer oder falsch getippter Wert darf den Stream nicht
    anhalten -- stattdessen auf den Standardmodus zurueckfallen.
    """
    if not mode:
        return DEFAULT_OVERLAY_MODE
    candidate = str(mode).strip().lower()
    return candidate if candidate in OVERLAY_MODES else DEFAULT_OVERLAY_MODE

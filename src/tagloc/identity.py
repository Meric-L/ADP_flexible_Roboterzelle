"""Identitaet der wirksamen Konfiguration. Nur Standardbibliothek.

Ausgelagert, weil der Vision-Server die `configurationId` schon braucht,
waehrend er die Erkennungsquelle aufbaut -- bevor irgendetwas geladen ist,
also duerfen numpy und OpenCV nicht in der Importkette liegen.

Der Wert beantwortet spaeter, welche Kalibrierung und welche Karte eine
schlechte Pose erzeugt haben (doc/projektdoku/altlasten.md C4). Dateiname plus
mtime: beides gibt es umsonst ohne Parsen und aendert sich bei jeder
Neukalibrierung.
"""

from pathlib import Path


def file_identity(path, missing: str = "fehlt") -> str:
    """Liefert `"<name>#<mtime>"`, ohne die Datei zu oeffnen."""
    path = Path(path)
    try:
        return f"{path.stem}#{int(path.stat().st_mtime)}"
    except OSError:
        return f"{path.stem}#{missing}"


def calibration_identity(path) -> str:
    """Liefert die Identitaet der Kalibrierdatei."""
    return file_identity(path)


def tag_map_identity(path) -> str:
    """Liefert die Identitaet der Tag-Karte. `None` ist gueltig -- Betrieb ohne Karte."""
    if path is None:
        return "tagmap#keine"
    return file_identity(path)
